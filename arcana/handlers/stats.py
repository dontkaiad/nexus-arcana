"""arcana/handlers/stats.py — Статистика сбывшихся раскладов и ритуалов."""
from __future__ import annotations

import json
import logging
import traceback as tb
from datetime import date, timedelta
from typing import List, Optional

from aiogram.types import Message

from core.claude_client import ask_claude
from core.notion_client import (
    _extract_select,
    _extract_text,
    client_find,
    log_error,
    match_select,
    rituals_all,
    sessions_all,
    sessions_by_client,
    rituals_by_client,
    update_page_select,
)

logger = logging.getLogger("arcana.stats")

# ── Значения полей Notion ────────────────────────────────────────────────────

# Расклады → поле "Сбылось"
SESSION_RESULT_MAP = {
    "сбылось":    "✅ Да",
    "да":         "✅ Да",
    "не сбылось": "❌ Нет",
    "нет":        "❌ Нет",
    "частично":   "〰️ Частично",
}

# Ритуалы → поле "Результат"
RITUAL_RESULT_MAP = {
    "сбылось":          "✅ Сработало",
    "сработало":        "✅ Сработало",
    "да":               "✅ Сработало",
    "не сбылось":       "❌ Не сработало",
    "не сработало":     "❌ Не сработало",
    "нет":              "❌ Не сработало",
    "частично":         "〰️ Частично",
}

SESSION_UNVERIFIED = {"", "⏳ Не проверено"}

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
                               model="claude-haiku-4-5-20251001")
        data = _parse_json_safe(raw)
        if not data:
            await message.answer("⚠️ Не смог распознать данные. Напиши: «Анна 5 марта — сбылось»")
            return

        client_name: Optional[str] = data.get("client_name") or None
        target_date: Optional[str] = data.get("date") or None
        result_raw: str = (data.get("result") or "").lower().strip()
        entity_type: str = (data.get("type") or "расклад").lower()
        is_ritual = "ритуал" in entity_type

        # 2. Определить Notion-значение результата
        if is_ritual:
            result_value = RITUAL_RESULT_MAP.get(result_raw)
            if not result_value:
                # fuzzy по ключам
                for k, v in RITUAL_RESULT_MAP.items():
                    if k in result_raw or result_raw in k:
                        result_value = v
                        break
            result_value = result_value or "〰️ Частично"
            field_name = "Результат"
        else:
            result_value = SESSION_RESULT_MAP.get(result_raw)
            if not result_value:
                for k, v in SESSION_RESULT_MAP.items():
                    if k in result_raw or result_raw in k:
                        result_value = v
                        break
            result_value = result_value or "〰️ Частично"
            field_name = "Сбылось"

        # 3. Найти нужную запись
        pages: List[dict] = []
        if client_name:
            client = await client_find(client_name, user_notion_id=user_notion_id)
            if client:
                if is_ritual:
                    pages = await rituals_by_client(client["id"], user_notion_id=user_notion_id)
                else:
                    pages = await sessions_by_client(client["id"], user_notion_id=user_notion_id)

        if not pages:
            # Личный расклад или клиент не найден — берём все
            if is_ritual:
                pages = await rituals_all(user_notion_id=user_notion_id)
            else:
                pages = await sessions_all(user_notion_id=user_notion_id)

        # 4. Отфильтровать по дате
        if target_date:
            candidates = _pages_near_date(pages, target_date)
        else:
            candidates = pages[:1]  # берём последний

        if not candidates:
            entity_label = "ритуал" if is_ritual else "расклад"
            date_hint = f" от {target_date}" if target_date else ""
            client_hint = f" для {client_name}" if client_name else ""
            await message.answer(
                f"🔍 Не нашла {entity_label}{client_hint}{date_hint}.\n"
                "Проверь дату или имя клиента."
            )
            return

        # 5. Обновить запись (первый подходящий)
        page = candidates[0]
        page_id = page["id"]
        props = page.get("properties", {})

        # Определить заголовок записи для подтверждения
        title = _extract_text(props.get("Тема") or props.get("Название") or {})
        page_date = _extract_date(props)

        ok = await update_page_select(page_id, field_name, result_value)

        if ok:
            label = "Ритуал" if is_ritual else "Расклад"
            client_str = f" для {client_name}" if client_name else ""
            date_str = f" ({page_date})" if page_date else ""
            await message.answer(
                f"✅ {label}{client_str}{date_str} — отмечено: {result_value}\n"
                + (f"«{title[:60]}»" if title else "")
            )
        else:
            await message.answer("❌ Ошибка обновления Notion · пусть Кай правит код")

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

        sessions = await sessions_all(user_notion_id=user_notion_id)
        rituals  = await rituals_all(user_notion_id=user_notion_id)

        # ── Статистика сеансов ────────────────────────────────────────────
        s_total = len(sessions)
        s_yes = s_no = s_partial = s_unverified = 0
        months_sessions: dict = {}  # "YYYY-MM" → {"total": n, "verified": n, "yes": n}

        for page in sessions:
            props = page.get("properties", {})
            val = _extract_select(props.get("Сбылось") or {})
            if val == "✅ Да":
                s_yes += 1
            elif val == "❌ Нет":
                s_no += 1
            elif val == "〰️ Частично":
                s_partial += 1
            else:
                s_unverified += 1

            # По месяцам
            d = _extract_date(props)
            if d:
                ym = d[:7]  # "YYYY-MM"
                m = months_sessions.setdefault(ym, {"total": 0, "verified": 0, "yes": 0, "partial": 0})
                m["total"] += 1
                if val not in SESSION_UNVERIFIED:
                    m["verified"] += 1
                if val == "✅ Да":
                    m["yes"] += 1
                elif val == "〰️ Частично":
                    m["partial"] += 1

        s_verified = s_yes + s_no + s_partial

        # ── Статистика ритуалов ───────────────────────────────────────────
        r_total = len(rituals)
        r_yes = r_no = r_partial = r_unverified = 0

        for page in rituals:
            props = page.get("properties", {})
            val = _extract_select(props.get("Результат") or {})
            if val == "✅ Сработало":
                r_yes += 1
            elif val == "❌ Не сработало":
                r_no += 1
            elif val == "〰️ Частично":
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
    """Количество непроверенных раскладов старше N дней."""
    pages = await sessions_all(user_notion_id=user_notion_id)
    cutoff = date.today() - timedelta(days=older_than_days)
    count = 0
    for page in pages:
        props = page.get("properties", {})
        val = _extract_select(props.get("Сбылось") or {})
        if val in SESSION_UNVERIFIED:
            d = _extract_date(props)
            if d:
                try:
                    if date.fromisoformat(d[:10]) <= cutoff:
                        count += 1
                except ValueError:
                    pass
    return count
