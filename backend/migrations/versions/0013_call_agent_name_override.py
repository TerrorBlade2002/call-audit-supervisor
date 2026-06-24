"""calls.agent_name_override — durable auditor override of the agent name

The agent name is auto-extracted from the call (stored on the report). An auditor can override it
when extraction is wrong/missing. The override lives on the CALL (not the report) so it survives
re-processing — a re-judge deletes & rebuilds the report row, but the call (and column) persist.
Effective name everywhere = ``calls.agent_name_override`` ?? ``reports.agent_name``.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("agent_name_override", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "agent_name_override")
