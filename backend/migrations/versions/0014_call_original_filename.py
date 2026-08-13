"""calls.original_filename — the uploader's original recording file name

Recording object keys are ``{portfolio}/{agent}/{uuid4}{ext}`` (see storage.recording_key), which
is collision-free but discards the name the user uploaded. That left reports identifiable only by
a call-id prefix, so nobody could tell which report belonged to which recording. Capture the name
at ingestion instead and surface it in the dashboard + downloadable reports.

Nullable with no backfill: for calls registered before this column existed the name was never
stored anywhere (the R2 key keeps only the extension), so there is nothing to recover. Those
rows keep falling back to the short call id.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("original_filename", sa.String(400), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "original_filename")
