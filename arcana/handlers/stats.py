"""arcana/handlers/stats.py — Статистика сбывшихся раскладов и ритуалов."""
from __future__ import annotations

import json
import logging
import traceback as tb
from datetime import date, timedelta
from typing import List, Optional

from aiogram.types import Message

from core.claude_client import ask_claude
from core.notion_client import log_error
from arcana.repos.pg_rituals_repo import PgRitualsRepo
from arcana.repos.pg_sessions_repo import PgSessionsRepo
from arcana.repos.pg_clients_repo import PgClientsRepo

logger = logging.getLogger("arcana.stats")

_rituals_repo = PgRitualsRepo()
_sessions_repo = PgSessionsRepo()
_clients_repo = PgClientsRepo()

# ── Result maps ───────────────────────────────────────────────────────────────

# Расклады → PG session_outcome codes
SESSION_RESULT_MAP = {
    "сбылось":    "yes",
    "да":         "yes",
    "не сбылось": "no",
    "нет":        "no",
    "частично":   "partial",
}

# Ритуалы → PG outcome codes
RITUAL_RESULT_MAP = {
    "сбылось":      "positive",
    "сработало":    "positive",
    "да":           "positive",
    "не сбылось":   "negative",
    "не сработало": "negative",
    "нет":          "negative",
    "частично":     "partial",
}

# Display labels for PG codes (used in confirm messages)
_RESULT_DISPLAY = {
    "positive": "✅ Сработало",
    "negative": "❌ Не сработало",
    "partial":  "〰️ Частично",
}

_SESSION_RESULT_DISPLAY = {
    "yes":        "✅ Сбылось",
    "no":         "❌ Не сбылось",
    "partial":    "〰️ Частично",
    "unverified": "⏳ Не проверено",
}

# ── Промпты ───────────────────────────────────────────────────────────────────

PARSE_VERIFY_SYSTEM = (
    "Извлеки данные для отметки сбывшегося. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"client_name": "имя или null", "date": "YYYY-MM-DD или null", '
    '"description": "описание расклада/ритуала или null", '
    '"result": "сбылось/не сбылось/частично", '
    '"type": "расклад или ритуал"}'
)

# ── Вспомогательные ──────────────────────────────────────────────────────────

def _parse_json_safe(raw: str) -> Optional[dict]:
    try:
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except Exception:
        return None


def _extract_date(props: dict) -> str:
    """Извлечь дату из свойств страницы."""
    for field in ("Дата", "Дата и время"):
        d = props.get(field, {})
        if d:
            dt = d.get("date") or {}
            start = dt.get("start") or ""
            if start:
                return start[:10]
    return ""


def _date_matches(page_date: str, target_date: str, delta_days: int = 3) -> bool:
    """Нечёткое совпадение дат: ±delta_days."""
    if not page_date or not target_date:
        return False
    try:
        pd = date.fromisoformat(page_date[:10])
        td = date.fromisoformat(target_date[:10])
        return abs((pd - td).days) <= delta_days
    except ValueError:
        return False


def _pages_near_date(pages: List[dict], target_date: str) -> List[dict]:
    """Отфильтровать страницы с датой в пределах ±3 дней от target_date."""
    result = []
    for p in pages:
        d = _extract_date(p.get("properties", {}))
        if _date_matches(d, target_date):
            result.append(p)
    return result


def _rituals_near_date(ritual_list: list, target_date: str) -> list:
    """Отфильтровать Ritual-объекты с датой в пределах ±3 дней от target_date."""
    result = []
    for r in ritual_list:
        if r.date is None:
            continue
        d = r.date.strftime("%Y-%m-%d")
        if _date_matches(d, target_date):
            result.append(r)
    return result


def _pct(count: int, total: int) -> int:
    return round(count * 100 / total) if total else 0


# ── handle_verify ─────────────────────────────────────────────────────────────

async def handle_verify(
    message: Message, text: str, user_notion_id: str = ""
) -> None:
    """Отметить расклад или ритуал как сбывшийся/не сбывшийся."""
    try:
        # 1. Парсинг через Haiku
        raw = await ask_claude(text, system=PARSE_VERIFY_SYSTEM, max_tokens=200,
                               model="claude-haiku-4-5-20251001", temperature=0)
        data = _parse_json_safe(raw)
        if not data:
            await message.answer("⚠️ Не смог распознать данные. Напиши: «Анна 5 марта — сбылось»")
            return

        client_name: Optional[str] = data.get("client_name") or None
        target_date: Optional[str] = data.get("date") or None
        result_raw: str = (data.get("result") or "").lower().strip()
        entity_type: str = (data.get("type") or "расклад").lower()
        is_ritual = "ритуал" in entity_type

        # ── Ritual path (PG) ─────────────────────────────────────────────
        if is_ritual:
            result_code = RITUAL_RESULT_MAP.get(result_raw)
            if not result_code:
                for k, v in RITUAL_RESULT_MAP.items():
                    if k in result_raw or result_raw in k:
                        result_code = v
                        break
            result_code = result_code or "partial"

            ritual_list = []
            if client_name:
                client = await _clients_repo.find(client_name)
                if client:
                    ritual_list = await _rituals_repo.list_by_client(client.id)
            if not ritual_list:
                ritual_list = await _rituals_repo.list_all()

            if target_date:
                candidates_r = _rituals_near_date(ritual_list, target_date)
            else:
                candidates_r = ritual_list[:1]

            if not candidates_r:
                date_hint = f" от {target_date}" if target_date else ""
                client_hint = f" для {client_name}" if client_name else ""
                await message.answer(
                    f"🔍 Не нашла ритуал{client_hint}{date_hint}.\n"
                    "Проверь дату или имя клиента."
                )
                return

            ritual = candidates_r[0]
            ok = await _rituals_repo.set_result(ritual.id, result_code)

            if ok:
                client_str = f" для {client_name}" if client_name else ""
                ritual_date = ritual.date.strftime("%Y-%m-%d") if ritual.date else ""
                date_str = f" ({ritual_date})" if ritual_date else ""
                display = _RESULT_DISPLAY.get(result_code, result_code)
                await message.answer(
                    f"✅ Ритуал{client_str}{date_str} — отмечено: {display}\n"
                    f"«{ritual.name[:60]}»"
                )
            else:
                await message.answer("❌ Ошибка обновления · пусть Кай правит код")
            return

        # ── Session path (PG) ─────────────────────────────────────────────
        result_code = SESSION_RESULT_MAP.get(result_raw)
        if not result_code:
            for k, v in SESSION_RESULT_MAP.items():
                if k in result_raw or result_raw in k:
                    result_code = v
                    break
        result_code = result_code or "partial"

        session_list = []
        if client_name:
            client = await _clients_repo.find(client_name)
            if client:
                session_list = await _sessions_repo.list_all(user_notion_id=user_notion_id)
                session_list = [s for s in session_list if s.client_id == client.id]
        if not session_list:
            session_list = await _sessions_repo.list_all(user_notion_id=user_notion_id)

        if target_date:
            candidates = [s for s in session_list if _date_matches(s.date, target_date)]
        else:
            candidates = session_list[:1]

        if not candidates:
            date_hint = f" от {target_date}" if target_date else ""
            client_hint = f" для {client_name}" if client_name else ""
            await message.answer(
                f"🔍 Не нашла расклад{client_hint}{date_hint}.\n"
                "Проверь дату или имя клиента."
            )
            return

        session = candidates[0]
        ok = await _sessions_repo.set_outcome(session.id, result_code)

        if ok:
            client_str = f" для {client_name}" if client_name else ""
            date_str = f" ({session.date})" if session.date else ""
            display = _SESSION_RESULT_DISPLAY.get(result_code, result_code)
            await message.answer(
                f"✅ Расклад{client_str}{date_str} — отмечено: {display}\n"
                + (f"«{session.question[:60]}»" if session.question else "")
            )
        else:
            await message.answer("❌ Ошибка обновления · пусть Кай правит код")

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_verify error: %s", trace)
        err_str = str(e)
        if "529" in err_str:
            code, suffix = "529", "серверная ошибка Anthropic"
        elif any(x in err_str for x in ("500", "502", "503")):
            code, suffix = "5xx", "серверная ошибка"
        elif "timeout" in err_str.lower():
            code, suffix = "timeout", "запрос завис"
        elif any(x in err_str for x in ("401", "403", "404")):
            code, suffix = "4xx", "ошибка конфигурации"
        else:
            code, suffix = "–", "что-то сломалось"
        await log_error(
            (message.text or "")[:200], "processing_error",
            traceback=trace, bot_label="🌒 Arcana", error_code=code,
        )
        await message.answer(f"❌ {suffix} · попробуй позже")


# ── handle_stats ──────────────────────────────────────────────────────────────

async def handle_stats(message: Message, user_notion_id: str = "") -> None:
    """Статистика сбывшихся раскладов и ритуалов."""
    try:
        await message.answer("📊 Считаю статистику...")

        sessions = await _sessions_repo.list_all(user_notion_id=user_notion_id)
        rituals  = await _rituals_repo.list_all(user_notion_id=user_notion_id)

        # ── Статистика сеансов (PG codes) ────────────────────────────────
        s_total = len(sessions)
        s_yes = s_no = s_partial = s_unverified = 0
        months_sessions: dict = {}

        for s in sessions:
            val = s.outcome or "unverified"
            if val == "yes":
                s_yes += 1
            elif val == "no":
                s_no += 1
            elif val == "partial":
                s_partial += 1
            else:
                s_unverified += 1

            if s.date:
                ym = s.date[:7]
                m = months_sessions.setdefault(ym, {"total": 0, "verified": 0, "yes": 0, "partial": 0})
                m["total"] += 1
                if val not in ("", "unverified"):
                    m["verified"] += 1
                if val == "yes":
                    m["yes"] += 1
                elif val == "partial":
                    m["partial"] += 1

        s_verified = s_yes + s_no + s_partial

        # ── Статистика ритуалов (PG codes) ───────────────────────────────
        r_total = len(rituals)
        r_yes = r_no = r_partial = r_unverified = 0

        for ritual in rituals:
            if ritual.result == "positive":
                r_yes += 1
            elif ritual.result == "negative":
                r_no += 1
            elif ritual.result == "partial":
                r_partial += 1
            else:
                r_unverified += 1

        r_verified = r_yes + r_no + r_partial

        # ── Форматирование ────────────────────────────────────────────────
        lines = ["📊 <b>Статистика</b>\n"]

        # Расклады
        lines.append("🃏 <b>Расклады:</b>")
        lines.append(f"  Всего: {s_total}")
        if s_total:
            lines.append(f"  ✅ Сбылось: {s_yes} ({_pct(s_yes, s_verified)}%)")
            lines.append(f"  〰️ Частично: {s_partial} ({_pct(s_partial, s_verified)}%)")
            lines.append(f"  ❌ Не сбылось: {s_no} ({_pct(s_no, s_verified)}%)")
            if s_unverified:
                lines.append(f"  ⏳ Не проверено: {s_unverified}")

        # Ритуалы
        lines.append("\n🕯️ <b>Ритуалы:</b>")
        lines.append(f"  Всего: {r_total}")
        if r_total:
            lines.append(f"  ✅ Сработало: {r_yes} ({_pct(r_yes, r_verified)}%)")
            lines.append(f"  〰️ Частично: {r_partial} ({_pct(r_partial, r_verified)}%)")
            lines.append(f"  ❌ Не сработало: {r_no} ({_pct(r_no, r_verified)}%)")
            if r_unverified:
                lines.append(f"  ⏳ Не проверено: {r_unverified}")

        # По месяцам (последние 3 с данными)
        recent_months = sorted(months_sessions.keys(), reverse=True)[:3]
        if recent_months:
            lines.append("\n📅 <b>По месяцам (расклады):</b>")
            month_names = {
                "01": "Январь", "02": "Февраль", "03": "Март",
                "04": "Апрель", "05": "Май", "06": "Июнь",
                "07": "Июль", "08": "Август", "09": "Сентябрь",
                "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь",
            }
            for ym in recent_months:
                m = months_sessions[ym]
                month_num = ym[5:7]
                name = month_names.get(month_num, ym)
                verified = m["verified"]
                yes_and_partial = m["yes"] + m["partial"]
                pct_str = f" · {_pct(yes_and_partial, verified)}% сбылось" if verified else ""
                lines.append(f"  {name}: {m['total']} раскладов{pct_str}")

        await message.answer("\n".join(lines))

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_stats error: %s", trace)
        await log_error(
            "", "processing_error",
            traceback=trace, bot_label="🌒 Arcana", error_code="–",
        )
        await message.answer("❌ Не смогла посчитать статистику · попробуй позже")


# ── Утилита для cron ──────────────────────────────────────────────────────────

async def get_unverified_count(user_notion_id: str, older_than_days: int = 30) -> int:
    """Количество непроверенных раскладов старше N дней (PG)."""
    sessions = await _sessions_repo.list_all(user_notion_id=user_notion_id)
    cutoff = date.today() - timedelta(days=older_than_days)
    count = 0
    for s in sessions:
        if (s.outcome or "unverified") not in ("yes", "no", "partial"):
            if s.date:
                try:
                    if date.fromisoformat(s.date[:10]) <= cutoff:
                        count += 1
                except ValueError:
                    pass
    return count
