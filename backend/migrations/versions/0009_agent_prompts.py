"""agent_prompts — super-admin-editable agent prompt bodies with single in_use per agent

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_prompts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("agent", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("in_use", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_agent_prompts_agent", "agent_prompts", ["agent"])
    # At most one in_use row per agent (race-safe activation).
    op.create_index(
        "uq_agent_prompt_inuse", "agent_prompts", ["agent"], unique=True,
        postgresql_where=sa.text("in_use"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_prompt_inuse", table_name="agent_prompts")
    op.drop_index("ix_agent_prompts_agent", table_name="agent_prompts")
    op.drop_table("agent_prompts")
