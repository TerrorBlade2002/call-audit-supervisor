"""reports.agent_name (extracted from transcript)

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("agent_name", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "agent_name")
