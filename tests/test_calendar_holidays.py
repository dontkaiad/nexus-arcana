"""tests/test_calendar_holidays.py — holiday_days в /api/calendar."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id


FAKE_TG = 67686090
FAKE_NOTION = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_january_2026_holiday_days_contains_new_year_caникулы(client):
    with patch("miniapp.backend.routes.calendar.query_pages",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.calendar.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)), \
         patch("miniapp.backend._helpers.get_user_tz",
               AsyncMock(return_value=3)):
        r = client.get("/api/calendar?month=2026-01")
    assert r.status_code == 200, r.text
    body = r.json()
    holiday_days = body["holiday_days"]
    # Новогодние каникулы 1-8 января (включая Рождество 7-го) — все официальные.
    for d in [1, 2, 3, 4, 5, 6, 7, 8]:
        assert d in holiday_days, f"Day {d} missing from {holiday_days}"
