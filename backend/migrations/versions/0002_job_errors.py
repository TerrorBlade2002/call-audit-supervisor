"""job_errors table — per-attempt failure traceback for debugging observability

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-16
"""

from alembic import op

from app.models import JobError

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create from the ORM definition (single source of truth, like 0001's create_all).
    JobError.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    JobError.__table__.drop(bind=op.get_bind(), checkfirst=True)
