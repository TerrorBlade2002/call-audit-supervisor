"""Judge-audio resolution (Files API vs inline) in the judge handler.

The contract under test: with an uploader the agents receive a Files API reference;
if the upload fails (or no uploader is configured) they receive inline bytes — the
exact pre-Files-API behavior; with no audio object at all they receive None.
"""

from __future__ import annotations

import uuid

from app.judge.gemini import AudioRef
from app.orchestration.handlers import JobView, _resolve_audio
from app.storage import FakeStorage


def _job(audio_uri: str = "p/a/rec.mp3") -> JobView:
    return JobView(
        id=uuid.uuid4(),
        call_id=uuid.uuid4(),
        portfolio_id=uuid.uuid4(),
        transcript_id="t1",
        audio_uri=audio_uri,
        transcript_uri=None,
    )


class _OkUploader:
    def __init__(self) -> None:
        self.uploads: list[tuple[int, str]] = []
        self.deleted: list[str] = []

    async def upload(self, data: bytes, mime_type: str) -> AudioRef:
        self.uploads.append((len(data), mime_type))
        return AudioRef(
            data=None, mime_type=mime_type, file_uri="uri://files/x", file_name="files/x"
        )

    async def delete(self, file_name: str) -> None:
        self.deleted.append(file_name)


class _FailingUploader(_OkUploader):
    async def upload(self, data: bytes, mime_type: str) -> AudioRef:
        raise RuntimeError("files api down")


async def test_resolve_audio_uses_files_api_reference() -> None:
    job = _job()
    storage = FakeStorage()
    storage.recordings[job.audio_uri] = b"\x00\x01audio-bytes"
    uploader = _OkUploader()

    audio = await _resolve_audio(job, storage, uploader)

    assert audio is not None
    assert audio.file_uri == "uri://files/x"
    assert audio.file_name == "files/x"
    assert audio.data is None
    assert audio.mime_type == "audio/mp3"
    assert uploader.uploads == [(len(b"\x00\x01audio-bytes"), "audio/mp3")]


async def test_resolve_audio_falls_back_to_inline_on_upload_failure() -> None:
    job = _job()
    storage = FakeStorage()
    storage.recordings[job.audio_uri] = b"\x00\x01audio-bytes"

    audio = await _resolve_audio(job, storage, _FailingUploader())

    assert audio is not None
    assert audio.file_uri is None
    assert audio.data == b"\x00\x01audio-bytes"  # exact pre-Files-API behavior


async def test_resolve_audio_inline_when_no_uploader() -> None:
    job = _job()
    storage = FakeStorage()
    storage.recordings[job.audio_uri] = b"\x00\x01audio-bytes"

    audio = await _resolve_audio(job, storage, None)

    assert audio is not None
    assert audio.file_uri is None
    assert audio.data == b"\x00\x01audio-bytes"


async def test_resolve_audio_none_when_recording_missing() -> None:
    job = _job()
    uploader = _OkUploader()

    audio = await _resolve_audio(job, FakeStorage(), uploader)

    assert audio is None
    assert uploader.uploads == []
