"""Cloudflare R2 (S3-compatible) storage: presigned URLs + lifecycle (FR3, FR4).

Uploads go **browser → R2 via presigned PUT** — never proxied through the API (FR3.2).
Downloads (verifier) use short-TTL presigned GET (FR12/NFR6). A 30-day expiry lifecycle
rule is applied to the recordings + transcripts buckets; KB + reports buckets are retained.

boto3 presigning is offline (HMAC over the request) — no network call — so issuing a URL
is cheap and testable without hitting R2.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import boto3
from botocore.config import Config

from app.config import Settings
from app.stt import Transcript, Utterance


def build_s3_client(settings: Settings, *, fast_fail: bool = False) -> Any:
    """An S3 client pointed at the R2 endpoint. Region 'auto', SigV4 (R2 requirement).

    ``fast_fail`` shortens timeouts + disables retries (used for best-effort KB extraction
    so a missing/unreachable object errors quickly instead of blocking on boto3 retries).
    """
    cfg = Config(signature_version="s3v4")
    if fast_fail:
        cfg = Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 1},
        )
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url or None,
        aws_access_key_id=settings.r2_access_key_id or None,
        aws_secret_access_key=settings.r2_secret_access_key or None,
        region_name="auto",
        config=cfg,
    )


def recording_key(portfolio_id: uuid.UUID, agent_id: uuid.UUID, filename: str) -> str:
    """Deterministic, collision-free object key scoped to portfolio/agent.

    The portfolio/agent prefix lets the register step authorize a key by prefix and lets
    the lifecycle rule target the whole bucket. The random component prevents collisions.
    """
    suffix = PurePosixPath(filename).suffix.lower()
    return f"{portfolio_id}/{agent_id}/{uuid.uuid4()}{suffix}"


def presign_put(client: Any, bucket: str, key: str, *, ttl: int) -> str:
    """Presigned PUT URL for a direct browser upload. Signs Bucket+Key only (any
    content-type accepted) to minimise client friction; the URL is short-lived + key-scoped.
    """
    return str(
        client.generate_presigned_url(
            "put_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl
        )
    )


def presign_get(client: Any, bucket: str, key: str, *, ttl: int) -> str:
    """Presigned GET URL for a time-limited download (verifier recording download)."""
    return str(
        client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl
        )
    )


def lifecycle_config(days: int = 30) -> dict[str, Any]:
    """Lifecycle config that expires every object after ``days`` (FR4). Applied to the
    recordings + transcripts buckets only.
    """
    return {
        "Rules": [
            {
                "ID": f"expire-after-{days}d",
                "Filter": {"Prefix": ""},
                "Status": "Enabled",
                "Expiration": {"Days": days},
            }
        ]
    }


def ensure_lifecycle(client: Any, bucket: str, *, days: int = 30) -> None:
    """Apply the 30-day expiry rule to a bucket (idempotent)."""
    client.put_bucket_lifecycle_configuration(
        Bucket=bucket, LifecycleConfiguration=lifecycle_config(days)
    )


def kb_key(portfolio_id: uuid.UUID, filename: str) -> str:
    """Object key for a KB document, scoped to the portfolio."""
    suffix = PurePosixPath(filename).suffix.lower()
    return f"{portfolio_id}/{uuid.uuid4()}{suffix}"


def transcript_key(call_id: uuid.UUID) -> str:
    return f"{call_id}.json"


def report_key(call_id: uuid.UUID) -> str:
    return f"{call_id}.html"


def report_pdf_key(call_id: uuid.UUID) -> str:
    return f"{call_id}.pdf"


class StorageService(Protocol):
    """Storage operations the worker needs. Swappable: R2 in prod, fake in tests."""

    async def put_recording(self, key: str, data: bytes, content_type: str) -> str:
        """Store an uploaded recording server-side (upload proxy). Returns the key.

        Browser→R2 direct PUT needs a bucket CORS rule; proxying the bytes through the API
        avoids that dependency entirely (the object-scoped R2 token can write server-side).
        """
        ...

    def recording_exists(self, key: str) -> bool:
        """True if the recording object is present — a pre-submit guard so a missing upload
        fails with a clear message instead of a provider download error."""
        ...

    def presign_audio_get(self, key: str) -> str:
        """Short-TTL GET URL so AssemblyAI can download the recording from R2."""
        ...

    async def put_transcript(self, call_id: uuid.UUID, transcript: Transcript) -> str:
        """Persist the diarized transcript JSON; return its object key."""
        ...

    async def get_transcript(self, call_id: uuid.UUID) -> Transcript:
        """Read back a previously stored transcript (judge re-runs)."""
        ...

    async def get_audio_bytes(self, key: str) -> bytes | None:
        """Download the recording bytes for the multimodal judge (None if unavailable)."""
        ...

    async def delete_recording(self, key: str) -> None:
        """Delete a recording object (idempotent — a missing object is not an error)."""
        ...

    async def delete_transcript(self, call_id: uuid.UUID) -> None:
        """Delete a stored transcript object (idempotent)."""
        ...

    async def put_report(self, call_id: uuid.UUID, html: str) -> str:
        """Persist the rendered HTML report artifact; return its object key."""
        ...

    def presign_report_get(self, call_id: uuid.UUID) -> str:
        """Short-TTL GET URL for downloading the rendered report artifact."""
        ...

    async def put_report_pdf(self, call_id: uuid.UUID, pdf: bytes) -> str:
        """Persist the rendered PDF report artifact; return its object key."""
        ...

    def presign_report_pdf_get(self, call_id: uuid.UUID) -> str:
        """Short-TTL GET URL for downloading the rendered PDF report."""
        ...

    async def delete_report(self, call_id: uuid.UUID) -> None:
        """Delete the stored report artifacts (HTML + PDF; idempotent)."""
        ...


class R2Storage:
    """Real R2-backed storage service."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._s3 = build_s3_client(settings)

    async def put_recording(self, key: str, data: bytes, content_type: str) -> str:
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._settings.r2_bucket_recordings,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    def recording_exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self._settings.r2_bucket_recordings, Key=key)
            return True
        except ClientError:
            return False

    def presign_audio_get(self, key: str) -> str:
        return presign_get(
            self._s3,
            self._settings.r2_bucket_recordings,
            key,
            ttl=self._settings.r2_presign_ttl_seconds,
        )

    async def put_transcript(self, call_id: uuid.UUID, transcript: Transcript) -> str:
        key = transcript_key(call_id)
        # boto3 is sync; fine at pilot scale. Wrap in a thread if it becomes hot.
        self._s3.put_object(
            Bucket=self._settings.r2_bucket_transcripts,
            Key=key,
            Body=json.dumps(transcript.to_dict()).encode("utf-8"),
            ContentType="application/json",
        )
        return key

    async def get_transcript(self, call_id: uuid.UUID) -> Transcript:
        obj = self._s3.get_object(
            Bucket=self._settings.r2_bucket_transcripts, Key=transcript_key(call_id)
        )
        return _transcript_from_dict(json.loads(obj["Body"].read()))

    async def get_audio_bytes(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            obj = self._s3.get_object(Bucket=self._settings.r2_bucket_recordings, Key=key)
            return bytes(obj["Body"].read())
        except ClientError:
            return None  # object missing (e.g. browser upload blocked by CORS)

    async def delete_recording(self, key: str) -> None:
        # S3/R2 DELETE is idempotent — deleting a missing key returns 204, not an error.
        await asyncio.to_thread(
            self._s3.delete_object, Bucket=self._settings.r2_bucket_recordings, Key=key
        )

    async def delete_transcript(self, call_id: uuid.UUID) -> None:
        await asyncio.to_thread(
            self._s3.delete_object,
            Bucket=self._settings.r2_bucket_transcripts,
            Key=transcript_key(call_id),
        )

    async def put_report(self, call_id: uuid.UUID, html: str) -> str:
        key = report_key(call_id)
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._settings.r2_bucket_reports,
            Key=key,
            Body=html.encode("utf-8"),
            ContentType="text/html; charset=utf-8",
        )
        return key

    def presign_report_get(self, call_id: uuid.UUID) -> str:
        return presign_get(
            self._s3,
            self._settings.r2_bucket_reports,
            report_key(call_id),
            ttl=self._settings.r2_presign_ttl_seconds,
        )

    async def put_report_pdf(self, call_id: uuid.UUID, pdf: bytes) -> str:
        key = report_pdf_key(call_id)
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._settings.r2_bucket_reports,
            Key=key,
            Body=pdf,
            ContentType="application/pdf",
        )
        return key

    def presign_report_pdf_get(self, call_id: uuid.UUID) -> str:
        return presign_get(
            self._s3,
            self._settings.r2_bucket_reports,
            report_pdf_key(call_id),
            ttl=self._settings.r2_presign_ttl_seconds,
        )

    async def delete_report(self, call_id: uuid.UUID) -> None:
        for key in (report_key(call_id), report_pdf_key(call_id)):
            await asyncio.to_thread(
                self._s3.delete_object, Bucket=self._settings.r2_bucket_reports, Key=key
            )


class FakeStorage:
    """In-memory storage double for tests (per-process, not shared)."""

    def __init__(self) -> None:
        self.transcripts: dict[str, Transcript] = {}
        self.recordings: dict[str, bytes] = {}
        self.reports: dict[str, str] = {}

    async def put_recording(self, key: str, data: bytes, content_type: str) -> str:
        self.recordings[key] = data
        return key

    def recording_exists(self, key: str) -> bool:
        # Tests don't upload through the proxy; assume present so the transcription step runs.
        return True

    def presign_audio_get(self, key: str) -> str:
        return f"https://fake-r2.local/{key}"

    async def put_transcript(self, call_id: uuid.UUID, transcript: Transcript) -> str:
        key = transcript_key(call_id)
        self.transcripts[key] = transcript
        return key

    async def get_transcript(self, call_id: uuid.UUID) -> Transcript:
        return self.transcripts[transcript_key(call_id)]

    async def get_audio_bytes(self, key: str) -> bytes | None:
        return self.recordings.get(key)

    async def delete_recording(self, key: str) -> None:
        self.recordings.pop(key, None)

    async def delete_transcript(self, call_id: uuid.UUID) -> None:
        self.transcripts.pop(transcript_key(call_id), None)

    async def put_report(self, call_id: uuid.UUID, html: str) -> str:
        key = report_key(call_id)
        self.reports[key] = html
        return key

    def presign_report_get(self, call_id: uuid.UUID) -> str:
        return f"https://fake-r2.local/reports/{report_key(call_id)}"

    async def put_report_pdf(self, call_id: uuid.UUID, pdf: bytes) -> str:
        key = report_pdf_key(call_id)
        self.reports[key] = pdf.decode("latin-1")
        return key

    def presign_report_pdf_get(self, call_id: uuid.UUID) -> str:
        return f"https://fake-r2.local/reports/{report_pdf_key(call_id)}"

    async def delete_report(self, call_id: uuid.UUID) -> None:
        self.reports.pop(report_key(call_id), None)
        self.reports.pop(report_pdf_key(call_id), None)


class LocalDiskStorage:
    """Filesystem-backed storage for local dev without R2. Shared across the api + worker
    processes via a common directory so transcripts written by the worker are readable by
    the api (lazy narrative, transcript view). Recordings aren't stored (stub STT)."""

    def __init__(self, base_dir: str) -> None:
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _rec_path(self, key: str) -> Path:
        return self._dir / "recordings" / key

    async def put_recording(self, key: str, data: bytes, content_type: str) -> str:
        path = self._rec_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def recording_exists(self, key: str) -> bool:
        return self._rec_path(key).exists()

    def presign_audio_get(self, key: str) -> str:
        # No real audio in stub-pipeline dev; return a clearly-inert placeholder URL.
        return f"http://localhost:8000/_dev/no-audio/{key}"

    async def put_transcript(self, call_id: uuid.UUID, transcript: Transcript) -> str:
        key = transcript_key(call_id)
        (self._dir / key).write_text(json.dumps(transcript.to_dict()), encoding="utf-8")
        return key

    async def get_transcript(self, call_id: uuid.UUID) -> Transcript:
        raw = (self._dir / transcript_key(call_id)).read_text(encoding="utf-8")
        return _transcript_from_dict(json.loads(raw))

    async def get_audio_bytes(self, key: str) -> bytes | None:
        path = self._rec_path(key)
        return path.read_bytes() if path.exists() else None

    async def delete_recording(self, key: str) -> None:
        self._rec_path(key).unlink(missing_ok=True)

    async def delete_transcript(self, call_id: uuid.UUID) -> None:
        (self._dir / transcript_key(call_id)).unlink(missing_ok=True)

    async def put_report(self, call_id: uuid.UUID, html: str) -> str:
        key = report_key(call_id)
        (self._dir / key).write_text(html, encoding="utf-8")
        return key

    def presign_report_get(self, call_id: uuid.UUID) -> str:
        return f"http://localhost:8000/_dev/report/{report_key(call_id)}"

    async def put_report_pdf(self, call_id: uuid.UUID, pdf: bytes) -> str:
        key = report_pdf_key(call_id)
        (self._dir / key).write_bytes(pdf)
        return key

    def presign_report_pdf_get(self, call_id: uuid.UUID) -> str:
        return f"http://localhost:8000/_dev/report/{report_pdf_key(call_id)}"

    async def delete_report(self, call_id: uuid.UUID) -> None:
        (self._dir / report_key(call_id)).unlink(missing_ok=True)
        (self._dir / report_pdf_key(call_id)).unlink(missing_ok=True)


def build_storage(settings: Settings) -> StorageService:
    """R2 in prod; shared local-disk storage for local dev (no R2 configured)."""
    if settings.r2_endpoint_url:
        return R2Storage(settings)
    return LocalDiskStorage(settings.dev_storage_dir)


def _transcript_from_dict(data: dict[str, Any]) -> Transcript:
    return Transcript(
        transcript_id=data["transcript_id"],
        duration_sec=data.get("duration_sec"),
        text=data.get("text", ""),
        utterances=[Utterance(**u) for u in data.get("utterances", [])],
    )
