"""Backfill FAQ clusters from historical user chat messages.

Usage:
    python scripts/backfill_faqs.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow `python scripts/backfill_faqs.py` from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cohere
from sqlalchemy import select
from upstash_redis.asyncio import Redis

from api.core.config import get_settings
from api.core.database import AsyncSessionLocal, init_db
from api.models.db_models import Message
from api.services.embedding_service import EmbeddingService
from api.services.faq_service import FaqService
from api.services.redis_service import RedisService


async def main() -> None:
    settings = get_settings()
    await init_db()

    redis_client = Redis(
        url=settings.upstash_redis_rest_url,
        token=settings.upstash_redis_rest_token,
    )
    cohere_client = cohere.AsyncClient(settings.cohere_api_key)
    embedding_service = EmbeddingService(cohere_client, RedisService(redis_client))
    faq_service = FaqService(embedding_service)

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Message.content, Message.created_at)
                .where(Message.role == "user")
                .order_by(Message.created_at.asc())
            )
        ).all()

        print(f"Found {len(rows)} user messages")
        created = 0
        matched = 0
        skipped = 0

        for content, created_at in rows:
            text = (content or "").strip()
            result = await faq_service.observe(
                db,
                text,
                requires_retrieval=True,
            )
            if result is None:
                skipped += 1
                print(f"  skip  | {text[:80]}")
                continue
            if result.matched:
                matched += 1
                print(
                    f"  match | count={result.ask_count} sim={result.similarity} | {result.canonical_question[:80]}"
                )
            else:
                created += 1
                print(f"  new   | {result.canonical_question[:80]}")

        await db.commit()

    print(
        f"\nDone. new={created} matched={matched} skipped={skipped} "
        f"(from {len(rows)} messages)"
    )


if __name__ == "__main__":
    asyncio.run(main())
