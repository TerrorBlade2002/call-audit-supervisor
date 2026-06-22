"""output_schemas — super-admin custom Structured-Output schemas per (portfolio, folder, stage)

A judge stage (feedback/checklist/ideal/merged) can have a custom JSON schema bound at a folder,
portfolio, or globally — used as Gemini's response schema so the model output is deterministic and
shaped by the super-admin. One in_use per (portfolio, folder, stage), NULLS NOT DISTINCT.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "output_schemas",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("agent", sa.String(20), nullable=False),  # judge stage
        sa.Column(
            "portfolio_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column(
            "agent_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("content", postgresql.JSONB, nullable=False),  # the JSON schema
        sa.Column("in_use", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_output_schemas_agent", "output_schemas", ["agent"])
    op.create_index(
        "uq_output_schema_inuse", "output_schemas",
        ["portfolio_id", "agent_id", "agent"], unique=True,
        postgresql_where=sa.text("in_use"), postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("uq_output_schema_inuse", table_name="output_schemas")
    op.drop_index("ix_output_schemas_agent", table_name="output_schemas")
    op.drop_table("output_schemas")
