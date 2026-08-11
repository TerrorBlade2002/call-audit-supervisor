"""Gemini judge (SDK, multimodal) + embedder (REST). Offline via fakes/mocks."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from google import genai
from google.genai import models, types

from app.config import RateLimitSettings
from app.judge.client import GeminiJudge, JudgeItem
from app.judge.embeddings import GeminiEmbedder
from app.judge.gemini import (
    AudioRef,
    GeminiFileUploader,
    audio_part,
    response_schema_kwargs,
)
from app.judge.schema import FeedbackOut, IdealOut, JudgeOutput, MergedOut
from app.judge.schema_validate import DEFAULT_SCHEMAS, SchemaError, validate_output_schema
from app.stt import Transcript

_FAST = RateLimitSettings(RETRY_BASE_SECONDS=0.0, RETRY_CAP_SECONDS=0.0, RETRY_MAX_ATTEMPTS=3)
_TRANSCRIPT = Transcript(transcript_id="t", duration_sec=10.0, text="hello", utterances=[])


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, payload: str, capture: dict) -> None:
        self._payload = payload
        self._capture = capture

    async def generate_content(self, *, model, contents, config):  # noqa: ANN001
        self._capture["contents"] = contents
        self._capture["config"] = config
        return _FakeResp(self._payload)


class _FakeAio:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models


class _FakeClient:
    def __init__(self, payload: str, capture: dict) -> None:
        self.aio = _FakeAio(_FakeModels(payload, capture))


def _item() -> JudgeItem:
    return JudgeItem(
        checklist_item_id=uuid.uuid4(),
        section="Compliance",
        text="Not confrontational",
        answer_type="CHOICE",
        rubric="No confrontational language.",
        options=["Yes", "No", "NA"],
    )


async def test_gemini_judge_parses_structured_output() -> None:
    item = _item()
    payload = json.dumps(
        {
            "verdicts": [
                {
                    "checklist_item_id": str(item.checklist_item_id),
                    "answer": "FAIL",
                    "confidence": 0.82,
                    "evidence_quote": "If you spend it you owe it.",
                    "evidence_offset_sec": 294.0,
                    "comment": "Confrontational.",
                    "needs_review": False,
                }
            ],
            "objections": [{"text": "I can't pay", "category": "ability", "cleared": False}],
        }
    )
    capture: dict = {}
    judge = GeminiJudge(
        client=_FakeClient(payload, capture), model="gemini-3.1-pro-preview", retry=_FAST
    )
    out = await judge.evaluate(transcript=_TRANSCRIPT, items=[item])
    assert out.verdicts[0].answer == "FAIL"
    assert out.verdicts[0].checklist_item_id == item.checklist_item_id
    assert out.objections[0].text == "I can't pay"
    # transcript-only → a single text part
    assert len(capture["contents"][0].parts) == 1


async def test_gemini_judge_includes_audio_part_when_provided() -> None:
    item = _item()
    payload = json.dumps({"verdicts": [], "objections": []})
    capture: dict = {}
    judge = GeminiJudge(
        client=_FakeClient(payload, capture), model="gemini-3.1-pro-preview", retry=_FAST
    )
    await judge.evaluate(
        transcript=_TRANSCRIPT,
        items=[item],
        audio=AudioRef(data=b"\x00\x01audio", mime_type="audio/mp3"),
    )
    # multimodal → audio part + text part
    assert len(capture["contents"][0].parts) == 2


async def test_gemini_judge_uses_file_uri_when_audio_ref_is_files_api() -> None:
    payload = json.dumps({"verdicts": [], "objections": []})
    capture: dict = {}
    judge = GeminiJudge(
        client=_FakeClient(payload, capture), model="gemini-3.1-pro-preview", retry=_FAST
    )
    await judge.evaluate(
        transcript=_TRANSCRIPT,
        items=[_item()],
        audio=AudioRef(
            data=None, mime_type="audio/mp3",
            file_uri="https://generativelanguage.googleapis.com/v1beta/files/abc",
            file_name="files/abc",
        ),
    )
    first = capture["contents"][0].parts[0]
    assert first.file_data is not None
    assert first.file_data.file_uri.endswith("files/abc")
    assert first.inline_data is None


def test_audio_part_prefers_uri_over_inline_bytes() -> None:
    uri_part = audio_part(
        AudioRef(data=None, mime_type="audio/mp3", file_uri="uri://f", file_name="files/f")
    )
    inline_part = audio_part(AudioRef(data=b"xx", mime_type="audio/mp3"))
    assert uri_part.file_data is not None and uri_part.inline_data is None
    assert inline_part.inline_data is not None and inline_part.file_data is None


# --- Files API uploader ------------------------------------------------------------------


class _FakeFile:
    def __init__(self, name: str, uri: str, state: str) -> None:
        self.name = name
        self.uri = uri
        self.state = type("S", (), {"name": state})()


class _FakeFiles:
    """aio.files double: upload returns states[0]; each get pops the next state."""

    def __init__(self, states: list[str]) -> None:
        self._states = states
        self.deleted: list[str] = []
        self.gets = 0

    def _file(self, state: str) -> _FakeFile:
        return _FakeFile("files/abc", "uri://files/abc", state)

    async def upload(self, *, file, config):  # noqa: ANN001
        return self._file(self._states[0])

    async def get(self, *, name):  # noqa: ANN001
        self.gets += 1
        idx = min(self.gets, len(self._states) - 1)
        return self._file(self._states[idx])

    async def delete(self, *, name):  # noqa: ANN001
        self.deleted.append(name)


class _FakeFilesClient:
    def __init__(self, states: list[str]) -> None:
        self.files = _FakeFiles(states)
        self.aio = self


async def _no_sleep(_: float) -> None:
    return None


async def test_file_uploader_returns_uri_ref_when_active() -> None:
    client = _FakeFilesClient(["ACTIVE"])
    up = GeminiFileUploader(client=client, retry=_FAST, sleep_fn=_no_sleep)
    ref = await up.upload(b"\x00audio", "audio/mp3")
    assert ref.file_uri == "uri://files/abc"
    assert ref.file_name == "files/abc"
    assert ref.data is None
    assert ref.mime_type == "audio/mp3"
    assert client.files.gets == 0  # ACTIVE immediately → no polling


async def test_file_uploader_polls_processing_until_active() -> None:
    client = _FakeFilesClient(["PROCESSING", "PROCESSING", "ACTIVE"])
    up = GeminiFileUploader(client=client, retry=_FAST, sleep_fn=_no_sleep)
    ref = await up.upload(b"\x00audio", "audio/mp3")
    assert ref.file_uri == "uri://files/abc"
    assert client.files.gets >= 2


async def test_file_uploader_failed_state_raises_after_retries() -> None:
    from app.ratelimit.backoff import RetryableError

    client = _FakeFilesClient(["FAILED"])
    up = GeminiFileUploader(client=client, retry=_FAST, sleep_fn=_no_sleep)
    with pytest.raises(RetryableError):
        await up.upload(b"\x00audio", "audio/mp3")


async def test_file_uploader_delete_is_best_effort() -> None:
    client = _FakeFilesClient(["ACTIVE"])
    up = GeminiFileUploader(client=client, retry=_FAST, sleep_fn=_no_sleep)
    await up.delete("files/abc")
    assert client.files.deleted == ["files/abc"]

    async def _boom(*, name):  # noqa: ANN001
        raise RuntimeError("gone")

    client.files.delete = _boom  # type: ignore[method-assign]
    await up.delete("files/abc")  # must not raise


def test_developer_api_accepts_builtin_response_schemas() -> None:
    client = genai.Client(api_key="test-key", vertexai=False)

    for schema in (JudgeOutput, FeedbackOut, IdealOut, MergedOut):
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
        )
        params = types._GenerateContentParameters(
            model="gemini-test",
            contents="hello",
            config=config,
        )
        request = models._GenerateContentParameters_to_mldev(client._api_client, params)
        assert "responseSchema" in request["generationConfig"]


def test_custom_response_schema_strips_developer_api_unsupported_keywords() -> None:
    schema = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "nested": {
                "type": "object",
                "additional_properties": {"type": "string"},
                "properties": {"value": {"type": "string"}},
            }
        },
    }

    kwargs = response_schema_kwargs(JudgeOutput, schema)

    assert kwargs == {
        "response_json_schema": {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                }
            },
        }
    }
    assert "additionalProperties" in schema
    assert "additional_properties" in schema["properties"]["nested"]


def test_custom_output_schema_rejects_additional_properties() -> None:
    schema = {**DEFAULT_SCHEMAS["checklist"], "additionalProperties": True}

    with pytest.raises(SchemaError, match="additionalProperties"):
        validate_output_schema("checklist", schema)


async def test_gemini_embedder_returns_vectors() -> None:
    # embedContent (singular) is called once per text; assert the 768-dim request + parsing.
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        dim = body["outputDimensionality"]
        return httpx.Response(200, json={"embedding": {"values": [0.1] * dim}})

    client = GeminiEmbedder(
        api_key="k",
        model="gemini-embedding-001",
        retry=_FAST,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    vecs = await client.embed(["a", "b"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 768
    assert all(b["outputDimensionality"] == 768 for b in seen)
