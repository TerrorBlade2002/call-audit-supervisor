"""per-call processing option + checklist/KB selection; report option; nullable report checklist

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # calls: the OPTION chosen at upload + the checklist/KB docs selected for it.
    op.add_column(
        "calls",
        sa.Column("option", sa.String(20), nullable=False, server_default="FULL"),
    )
    op.add_column(
        "calls",
        sa.Column("checklist_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "calls_checklist_id_fkey", "calls", "checklists", ["checklist_id"], ["id"]
    )
    op.add_column("calls", sa.Column("kb_doc_ids", postgresql.JSONB(), nullable=True))

    # reports: the option that produced it; checklist becomes optional (feedback-only reports).
    op.add_column("reports", sa.Column("option", sa.String(20), nullable=True))
    op.alter_column("reports", "checklist_id", existing_type=postgresql.UUID(as_uuid=True),
                    nullable=True)


def downgrade() -> None:
    op.alter_column("reports", "checklist_id", existing_type=postgresql.UUID(as_uuid=True),
                    nullable=False)
    op.drop_column("reports", "option")
    op.drop_column("calls", "kb_doc_ids")
    op.drop_constraint("calls_checklist_id_fkey", "calls", type_="foreignkey")
    op.drop_column("calls", "checklist_id")
    op.drop_column("calls", "option")
