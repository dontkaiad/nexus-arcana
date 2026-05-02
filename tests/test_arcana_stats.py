"""tests/test_arcana_stats.py — юнит-тесты для compute-хелперов /api/arcana/stats."""
from miniapp.backend.routes.arcana_today import (
    _compute_accuracy,
    _count_pending,
    _avg_check_delay,
    _session_verdict,
    _ritual_verdict,
    _month_key,
)


def _sess(verdict_name=None, date_iso=None, last_edited=None):
    p = {"properties": {}}
    if verdict_name:
        p["properties"]["Сбылось"] = {"select": {"name": verdict_name}}
    if date_iso:
        p["properties"]["Дата"] = {"date": {"start": date_iso}}
    if last_edited:
        p["last_edited_time"] = last_edited
    return p


def _rit(verdict_name=None, date_iso=None, last_edited=None):
    p = {"properties": {}}
    if verdict_name:
        p["properties"]["Результат"] = {"select": {"name": verdict_name}}
    if date_iso:
        p["properties"]["Дата"] = {"date": {"start": date_iso}}
    if last_edited:
        p["last_edited_time"] = last_edited
    return p


def test_empty_stats():
    acc = _compute_accuracy([], [], "all")
    assert acc == {"pct": 0, "yes": 0, "half": 0, "no": 0, "total": 0}
    ps, pr = _count_pending([], [])
    assert ps == 0 and pr == 0


def test_three_sessions_two_checked():
    sessions = [
        _sess("✅ Да"),
        _sess("❌ Нет"),
        _sess(),  # pending
    ]
    acc = _compute_accuracy(sessions, [], "sessions")
    assert acc["yes"] == 1
    assert acc["no"] == 1
    assert acc["total"] == 2
    assert acc["pct"] == 50  # (1 + 0.5*0) / 2 = 0.5

    ps, _ = _count_pending(sessions, [])
    assert ps == 1


def test_partial_counts_half_weight():
    sessions = [_sess("✅ Да"), _sess("〰️ Частично")]
    acc = _compute_accuracy(sessions, [], "sessions")
    # (1 + 0.5) / 2 = 0.75 → 75%
    assert acc["pct"] == 75


def test_month_grouping_key():
    p = _sess("✅ Да", "2026-04-15T10:00:00Z")
    assert _month_key(p) == "2026-04"
    assert _month_key(_sess()) is None


def test_avg_check_delay():
    items = [
        _sess("✅ Да", "2026-04-01", "2026-04-15T00:00:00Z"),  # 14 дней
        _sess("❌ Нет", "2026-04-10", "2026-04-20T00:00:00Z"),  # 10 дней
        _sess(),  # pending — игнор
    ]
    avg = _avg_check_delay(items, _session_verdict)
    assert avg == 12.0


def test_avg_check_delay_no_data():
    assert _avg_check_delay([], _session_verdict) is None
    assert _avg_check_delay([_sess()], _session_verdict) is None


def test_mixed_overall_accuracy():
    sessions = [_sess("✅ Да"), _sess("✅ Да"), _sess("❌ Нет")]
    rituals = [_rit("✅ Сработало"), _rit("〰️ Частично")]
    acc = _compute_accuracy(sessions, rituals, "all")
    # yes=3, half=1, no=1; weighted=3.5/5=70
    assert acc["yes"] == 3
    assert acc["half"] == 1
    assert acc["no"] == 1
    assert acc["pct"] == 70
