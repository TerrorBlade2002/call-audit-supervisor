"""checklists.requires_kb + documents.filename

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "checklists",
        sa.Column(
            "requires_kb", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.add_column("documents", sa.Column("filename", sa.String(400), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "filename")
    op.drop_column("checklists", "requires_kb")
