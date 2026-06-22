"""R2 presigning + lifecycle config (Phase 1). Offline — no R2 network calls."""

from __future__ import annotations

import uuid

from app.config import get_settings
from app.storage import (
    build_s3_client,
    lifecycle_config,
    presign_get,
    presign_put,
    recording_key,
)

_DUMMY = get_settings().model_copy(
    update={
        "r2_endpoint_url": "https://example.r2.cloudflarestorage.com",
        "r2_access_key_id": "test-key",
        "r2_secret_access_key": "test-secret",
    }
)


def test_recording_key_is_scoped_and_keeps_extension() -> None:
    pid, aid = uuid.uuid4(), uuid.uuid4()
    key = recording_key(pid, aid, "Call Recording.MP3")
    assert key.startswith(f"{pid}/{aid}/")
    assert key.endswith(".mp3")  # lower-cased extension preserved


def test_presign_put_url_targets_endpoint_and_key() -> None:
    client = build_s3_client(_DUMMY)
    key = "p/a/file.wav"
    url = presign_put(client, "everest-recordings", key, ttl=900)
    assert url.startswith("https://example.r2.cloudflarestorage.com")
    assert key in url
    assert "X-Amz-Expires=900" in url
    assert "X-Amz-Signature=" in url


def test_presign_get_is_a_get_request() -> None:
    client = build_s3_client(_DUMMY)
    url = presign_get(client, "everest-recordings", "p/a/file.wav", ttl=60)
    assert "X-Amz-Expires=60" in url
    assert "X-Amz-Signature=" in url


def test_lifecycle_config_expires_after_30_days() -> None:
    cfg = lifecycle_config(30)
    rule = cfg["Rules"][0]
    assert rule["Status"] == "Enabled"
    assert rule["Expiration"]["Days"] == 30
