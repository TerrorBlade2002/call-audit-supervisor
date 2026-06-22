"""widen report_items.raw_answer to TEXT (subjective items return full sentences)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("report_items", "raw_answer", type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    op.alter_column(
        "report_items", "raw_answer", type_=sa.String(60), existing_nullable=True
    )
