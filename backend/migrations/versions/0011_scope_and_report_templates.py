"""Scope prompts to (portfolio, folder) + report_templates table

Adds portfolio_id + agent_id (the folder) to agent_prompts so a prompt can be bound at the
folder level, the portfolio level (agent_id NULL), or globally (both NULL). The in_use uniqueness
becomes per (portfolio_id, agent_id, agent), NULLS NOT DISTINCT so the global/portfolio tiers can
only have one in_use each. Also creates report_templates (deterministic HTML report layout, same
scoping, one in_use per (portfolio_id, agent_id)).

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── agent_prompts: add the scope key (folder + portfolio), re-key the in_use index ──
    op.add_column(
        "agent_prompts",
        sa.Column(
            "portfolio_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=True,
        ),
    )
    op.add_column(
        "agent_prompts",
        sa.Column(
            "agent_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True,
        ),
    )
    op.drop_index("uq_agent_prompt_inuse", table_name="agent_prompts")
    # One in_use per (portfolio, folder, judge-agent). NULLS NOT DISTINCT (PG15+) so the
    # global tier (NULL, NULL) and a portfolio tier (pid, NULL) each allow only one in_use.
    op.create_index(
        "uq_agent_prompt_inuse", "agent_prompts",
        ["portfolio_id", "agent_id", "agent"], unique=True,
        postgresql_where=sa.text("in_use"), postgresql_nulls_not_distinct=True,
    )

    # ── report_templates: deterministic HTML layout, scoped per (portfolio, folder) ──
    op.create_table(
        "report_templates",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "portfolio_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column(
            "agent_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("in_use", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index(
        "uq_report_template_inuse", "report_templates",
        ["portfolio_id", "agent_id"], unique=True,
        postgresql_where=sa.text("in_use"), postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("uq_report_template_inuse", table_name="report_templates")
    op.drop_table("report_templates")
    op.drop_index("uq_agent_prompt_inuse", table_name="agent_prompts")
    op.drop_column("agent_prompts", "agent_id")
    op.drop_column("agent_prompts", "portfolio_id")
    op.create_index(
        "uq_agent_prompt_inuse", "agent_prompts", ["agent"], unique=True,
        postgresql_where=sa.text("in_use"),
    )
