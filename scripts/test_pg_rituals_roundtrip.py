"""scripts/test_pg_rituals_roundtrip.py — round-trip sanity check for PgRitualsRepo.

Usage:
    python3 scripts/test_pg_rituals_roundtrip.py

Creates one ritual, reads it back, prints the Ritual dataclass, then deletes it.
Requires: Docker Postgres running (docker compose up -d), DATABASE_URL in .env.
"""
from __future__ import annotations
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from arcana.repos.pg_rituals_repo import PgRitualsRepo


async def main() -> None:
    repo = PgRitualsRepo()

    print("=== CREATE ===")
    created = await repo.create(
        name="Ритуал тест round-trip",
        date="2026-06-14",
        ritual_type="Личный",
        goal="любовь",
        place="дома",
        payment_source="💵 Наличные",
        amount=1500,
        paid=1000,
        forces="Венера, Луна",
        structure="Свечи, заговор, медитация",
        notes="Тест — будет удалён",
    )
    print(f"  created: {created}")
    assert created is not None, "create() returned None"
    assert created.goal == "love",   f"goal mismatch: {created.goal}"
    assert created.place == "home",  f"place mismatch: {created.place}"
    assert created.result == "unverified", f"result mismatch: {created.result}"
    assert created.price == Decimal("1500"), f"price mismatch: {created.price}"
    assert created.paid  == Decimal("1000"), f"paid mismatch: {created.paid}"

    print("\n=== LIST_ALL ===")
    all_rituals = await repo.list_all()
    match = next((r for r in all_rituals if r.id == created.id), None)
    assert match is not None, "created ritual not found in list_all()"
    print(f"  found in list_all ({len(all_rituals)} total):")
    print(f"    id        = {match.id}")
    print(f"    name      = {match.name}")
    print(f"    date      = {match.date}")
    print(f"    result    = {match.result}  (type={type(match.result).__name__})")
    print(f"    goal      = {match.goal}")
    print(f"    place     = {match.place}")
    print(f"    price     = {match.price!r}  (type={type(match.price).__name__})")
    print(f"    paid      = {match.paid!r}   (type={type(match.paid).__name__})")
    print(f"    client_id = {match.client_id}")

    print("\n=== LIST_ALL with result_filter='unverified' ===")
    filtered = await repo.list_all(result_filter="unverified")
    match_f = next((r for r in filtered if r.id == created.id), None)
    assert match_f is not None, "ritual not found with result_filter=unverified"
    print(f"  found in filtered list ({len(filtered)} items), result={match_f.result}")

    print("\n=== DELETE ===")
    deleted = await repo.delete(created.id)
    assert deleted, "delete() returned False"
    after = await repo.list_all()
    assert not any(r.id == created.id for r in after), "row still present after delete"
    print(f"  deleted OK — row {created.id} removed")

    print("\n✅ Round-trip passed")


asyncio.run(main())
