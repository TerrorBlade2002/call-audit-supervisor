"""initial schema (§9) + pgvector + queue indexes

Revision ID: 0001
Revises:
Create Date: 2026-06-12
"""

from alembic import op

from app.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector for objection clustering (§7.5). gen_random_uuid() is core in PG13+.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Baseline: create every table from the ORM metadata (single source of truth).
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    # Hot path for the worker claim query (§8.2): scan claimable, due, unlocked jobs.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_jobs_claimable
        ON jobs (state, next_attempt_at)
        WHERE state IN ('PENDING_TRANSCRIPTION', 'PENDING_JUDGE')
        """
    )

    # ANN index for objection clustering (cosine). HNSW = good recall, no training.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_objections_embedding
        ON objections USING hnsw (embedding vector_cosine_ops)
        """
    )

    # Seed the five role names (§2). IDs are server-generated; code looks roles up by name.
    op.execute(
        """
        INSERT INTO roles (name) VALUES
            ('ADMIN'), ('MANAGER'), ('ANALYST'), ('VERIFIER'), ('VIEWER')
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ix_objections_embedding")
    op.execute("DROP INDEX IF EXISTS ix_jobs_claimable")
    Base.metadata.drop_all(bind=bind)
