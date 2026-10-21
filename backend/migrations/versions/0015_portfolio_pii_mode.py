"""portfolios.pii_mode — per-portfolio PII redaction level

Redaction used to be one global env flag, which cannot express "this client requires it and
that one does not". It becomes an admin-set, portfolio-level choice of OFF / TRANSCRIPT /
TRANSCRIPT_AND_RECORDING, read live when a call is processed.

Existing rows default to OFF by product decision: redaction is an explicit per-client
decision rather than something that switches itself on. NOTE this is a change in posture —
transcripts were being redacted for everyone via the global flag, and after this deploy no
portfolio redacts until an admin turns it on.

Stored as a plain string, not a DB enum, so adding a level later is a code change rather
than an ALTER TYPE migration.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolios",
        sa.Column("pii_mode", sa.String(32), nullable=False, server_default="OFF"),
    )


def downgrade() -> None:
    op.drop_column("portfolios", "pii_mode")
