"""FAQ clustering — merge paraphrased questions via embedding similarity."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.constants import (
    FAQ_LIST_DEFAULT_LIMIT,
    FAQ_LIST_MAX_LIMIT,
    FAQ_MIN_QUESTION_CHARS,
    FAQ_SIMILARITY_THRESHOLD,
    FAQ_STATUS_ACTIVE,
)
from api.core.logger import get_logger, log_exception
from api.models.db_models import FaqCluster
from api.models.schemas import FaqItem, FaqObserveResponse
from api.services.embedding_service import EmbeddingService

logger = get_logger(__name__)

_PHI_HINT = re.compile(
    r"\b(\d{3}-\d{2}-\d{4}|\d{8,}|(mrn|dob|ssn)\s*[:=])\b",
    re.IGNORECASE,
)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _should_track(question: str, *, requires_retrieval: bool) -> bool:
    text = (question or "").strip()
    if not requires_retrieval:
        return False
    if len(text) < FAQ_MIN_QUESTION_CHARS:
        return False
    if _PHI_HINT.search(text):
        return False
    return True


class FaqService:
    def __init__(self, embedding_service: EmbeddingService):
        self._embedding = embedding_service

    async def observe(
        self,
        db: AsyncSession,
        question: str,
        *,
        query_type: Optional[str] = None,
        requires_retrieval: bool = True,
        embedding: Optional[list[float]] = None,
    ) -> Optional[FaqObserveResponse]:
        if not _should_track(question, requires_retrieval=requires_retrieval):
            return None

        text = question.strip()
        try:
            vector = embedding or await self._embedding.embed_query(text)
        except Exception as exc:
            log_exception(logger, exc)
            logger.warning("FAQ observe skipped — embedding failed")
            return None

        clusters = await self._load_active(db)
        best: Optional[FaqCluster] = None
        best_score = -1.0
        for cluster in clusters:
            try:
                other = json.loads(cluster.embedding_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(other, list):
                continue
            score = _cosine(vector, [float(v) for v in other])
            if score > best_score:
                best_score = score
                best = cluster

        now = datetime.now(timezone.utc)
        if best is not None and best_score >= FAQ_SIMILARITY_THRESHOLD:
            best.ask_count = int(best.ask_count or 0) + 1
            best.last_asked_at = now
            best.updated_at = now
            best.last_similarity = float(best_score)
            if query_type and not best.query_type:
                best.query_type = query_type
            await db.flush()
            return FaqObserveResponse(
                faq_id=best.id,
                matched=True,
                similarity=round(float(best_score), 4),
                ask_count=best.ask_count,
                canonical_question=best.canonical_question,
            )

        cluster = FaqCluster(
            canonical_question=text,
            embedding_json=json.dumps(vector),
            ask_count=1,
            query_type=query_type,
            status=FAQ_STATUS_ACTIVE,
            last_similarity=None,
            last_asked_at=now,
        )
        db.add(cluster)
        await db.flush()
        return FaqObserveResponse(
            faq_id=cluster.id,
            matched=False,
            similarity=None,
            ask_count=1,
            canonical_question=cluster.canonical_question,
        )

    async def list_faqs(
        self,
        db: AsyncSession,
        *,
        limit: int = FAQ_LIST_DEFAULT_LIMIT,
        offset: int = 0,
        query_type: Optional[str] = None,
        sort: str = "count",
    ) -> tuple[list[FaqItem], int]:
        limit = max(1, min(int(limit or FAQ_LIST_DEFAULT_LIMIT), FAQ_LIST_MAX_LIMIT))
        offset = max(0, int(offset or 0))

        filters = [FaqCluster.status == FAQ_STATUS_ACTIVE]
        if query_type:
            filters.append(FaqCluster.query_type == query_type)

        count_stmt = select(func.count(FaqCluster.id)).where(*filters)
        total = int((await db.execute(count_stmt)).scalar_one() or 0)

        order = (
            FaqCluster.last_asked_at.desc()
            if sort == "recent"
            else FaqCluster.ask_count.desc()
        )
        stmt = (
            select(FaqCluster)
            .where(*filters)
            .order_by(order, FaqCluster.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list((await db.execute(stmt)).scalars().all())
        items = [
            FaqItem(
                id=row.id,
                canonical_question=row.canonical_question,
                ask_count=int(row.ask_count or 0),
                query_type=row.query_type,
                last_asked_at=row.last_asked_at.timestamp(),
                created_at=row.created_at.timestamp(),
            )
            for row in rows
        ]
        return items, total

    async def _load_active(self, db: AsyncSession) -> list[FaqCluster]:
        result = await db.execute(
            select(FaqCluster).where(FaqCluster.status == FAQ_STATUS_ACTIVE)
        )
        return list(result.scalars().all())
