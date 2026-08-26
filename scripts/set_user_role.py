#!/usr/bin/env python3
"""Promote a user to agent or admin for Day 2 handoff features.

Usage:
  python scripts/set_user_role.py user@example.com agent
  python scripts/set_user_role.py user@example.com admin
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from api.core.database import AsyncSessionLocal, init_db
from api.models.db_models import User


async def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python scripts/set_user_role.py <email> <user|agent|admin>")
        sys.exit(1)
    email = sys.argv[1].strip().lower()
    role = sys.argv[2].strip().lower()
    if role not in ("user", "agent", "admin"):
        print("Role must be user|agent|admin")
        sys.exit(1)

    await init_db()
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            print(f"No user found for {email}")
            sys.exit(1)
        user.role = role
        await db.commit()
        print(f"Updated {email} → role={role}")


if __name__ == "__main__":
    asyncio.run(main())
