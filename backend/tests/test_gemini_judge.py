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
from app.judge.gemini import AudioRef, response_schema_kwargs
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
