"""seed SUPERVISOR + AGENT portfolio roles (2-role-per-portfolio model)

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-16
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO roles (name) VALUES ('SUPERVISOR'), ('AGENT')
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM roles WHERE name IN ('SUPERVISOR', 'AGENT')")
