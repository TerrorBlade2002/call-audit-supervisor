"""One-time R2 setup: create buckets + apply the 30-day lifecycle rule (FR4).

Run once per environment after R2 creds are configured:

    python scripts/setup_r2.py

Applies a 30-day expiry to the recordings + transcripts buckets; KB + reports are
retained (no rule). Idempotent — safe to re-run.
"""

from __future__ import annotations

from botocore.exceptions import ClientError

from app.config import settings
from app.storage import build_s3_client, ensure_lifecycle


def main() -> None:
    if not settings.r2_endpoint_url:
        raise SystemExit("R2 not configured (set R2_ENDPOINT_URL and creds).")

    client = build_s3_client(settings)
    expiring = [settings.r2_bucket_recordings, settings.r2_bucket_transcripts]
    retained = [settings.r2_bucket_kb, settings.r2_bucket_reports]

    for bucket in expiring + retained:
        try:
            client.create_bucket(Bucket=bucket)
            print(f"created bucket {bucket}")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                print(f"bucket {bucket} already exists")
            elif code == "AccessDenied":
                # Object-scoped token: can't create buckets, but they already exist. OK.
                print(f"bucket {bucket}: assuming it exists (token lacks CreateBucket)")
            else:
                raise

    for bucket in expiring:
        try:
            ensure_lifecycle(client, bucket, days=30)
            print(f"applied 30-day lifecycle to {bucket}")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "AccessDenied":
                print(
                    f"WARNING: could not set lifecycle on {bucket} (token lacks "
                    f"PutBucketLifecycle). Set a 30-day expiry rule in the Cloudflare "
                    f"dashboard, or use an Admin R2 token to run this script."
                )
            else:
                raise

    print(f"retained (no expiry): {', '.join(retained)}")


if __name__ == "__main__":
    main()
