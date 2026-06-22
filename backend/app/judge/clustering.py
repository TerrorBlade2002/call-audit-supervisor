"""Objection aggregation (§7.5): cluster embedded objections into the portfolio
"most common / never-cleared" view. Coaching targets the never-cleared clusters.

Greedy cosine clustering in Python — fine at pilot volume and simple to reason about. At
larger scale this moves to a pgvector ANN query over the HNSW index (same output shape).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, Objection, Report


@dataclass
class ObjectionCluster:
    representative_text: str
    count: int
    cleared_count: int
    examples: list[str]

    @property
    def never_cleared(self) -> bool:
        return self.cleared_count == 0


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def cluster_objections(
    session: AsyncSession, portfolio_id: uuid.UUID, *, threshold: float = 0.9
) -> list[ObjectionCluster]:
    rows = (
        await session.execute(
            select(Objection.text, Objection.cleared, Objection.embedding)
            .join(Report, Report.id == Objection.report_id)
            .join(Call, Call.id == Report.call_id)
            .where(Call.portfolio_id == portfolio_id)
        )
    ).all()

    clusters: list[ObjectionCluster] = []
    centroids: list[list[float]] = []
    for text, cleared, embedding in rows:
        vec = [float(x) for x in embedding] if embedding is not None else []
        placed = False
        for i, centroid in enumerate(centroids):
            if vec and _cosine(vec, centroid) >= threshold:
                c = clusters[i]
                c.count += 1
                c.cleared_count += 1 if cleared else 0
                if len(c.examples) < 3:
                    c.examples.append(text)
                placed = True
                break
        if not placed:
            clusters.append(
                ObjectionCluster(
                    representative_text=text,
                    count=1,
                    cleared_count=1 if cleared else 0,
                    examples=[text],
                )
            )
            centroids.append(vec)

    clusters.sort(key=lambda c: c.count, reverse=True)
    return clusters
