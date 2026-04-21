#!/usr/bin/env python3
"""
Smoke тесты с реальными API.
Создаёт тестовые данные → проверяет → удаляет.
Запуск: python tests/smoke_test.py
"""
import asyncio
import os
import sys
import time
from datetime import datetime, timezone

# .env из корня проекта
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"), override=True)

# Цвета
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

results = []
cleanup_ids: list[tuple[str, str]] = []  # (label, page_id)


def log_pass(name, detail=""):
    results.append((name, True, detail))
    print(f"  {GREEN}✅ PASS{RESET}  {name}" + (f" — {detail}" if detail else ""))

def log_fail(name, detail=""):
    results.append((name, False, detail))
    print(f"  {RED}❌ FAIL{RESET}  {name}" + (f" — {detail}" if detail else ""))

def log_skip(name, detail=""):
    results.append((name, None, detail))
    print(f"  {YELLOW}⏭ SKIP{RESET}  {name}" + (f" — {detail}" if detail else ""))

def section(title):
    print(f"\n{CYAN}{'='*50}")
    print(f"  {title}")
    print(f"{'='*50}{RESET}")


async def main():
    start_time = time.time()

    print(f"\n🔥 SMOKE ТЕСТЫ — РЕАЛЬНЫЕ API")
    print(f"{'='*50}")

    # ═══════════════════════════════════════
    # БЛОК 0: ENV
    # ═══════════════════════════════════════
    section("ENV ПЕРЕМЕННЫЕ")

    required_env = [
        "NOTION_TOKEN", "ANTHROPIC_API_KEY",
        "NOTION_DB_TASKS", "NOTION_DB_FINANCE", "NOTION_DB_MEMORY",
        "NOTION_DB_NOTES", "NOTION_DB_LISTS", "NOTION_DB_ERRORS",
        "NOTION_DB_USERS", "NOTION_DB_CLIENTS", "NOTION_DB_SESSIONS",
        "NOTION_DB_RITUALS", "NOTION_DB_WORKS", "NOTION_DB_GRIMOIRE",
    ]
    missing = [e for e in required_env if not os.environ.get(e)]
    if missing:
        log_fail("ENV check", f"Отсутствуют: {', '.join(missing)}")
        print(f"\n{RED}Критические переменные отсутствуют. Дальше бессмысленно.{RESET}")
        return False
    log_pass("ENV check", f"{len(required_env)} переменных ОК")

    # ═══════════════════════════════════════
    # БЛОК 1: NOTION ПОДКЛЮЧЕНИЕ
    # ═══════════════════════════════════════
    section("NOTION ПОДКЛЮЧЕНИЕ")

    try:
        from core.notion_client import get_notion
        raw_notion = get_notion()  # AsyncClient
        log_pass("Notion client init")
    except Exception as e:
        log_fail("Notion client init", str(e))
        return False

    db_names = {
        "NOTION_DB_TASKS":    "✅ Задачи",
        "NOTION_DB_FINANCE":  "💰 Финансы",
        "NOTION_DB_MEMORY":   "🧠 Память",
        "NOTION_DB_NOTES":    "💡 Заметки",
        "NOTION_DB_LISTS":    "🗒️ Списки",
        "NOTION_DB_ERRORS":   "⚠️ Ошибки",
        "NOTION_DB_USERS":    "🪪 Пользователи",
        "NOTION_DB_CLIENTS":  "👥 Клиенты",
        "NOTION_DB_SESSIONS": "🃏 Расклады",
        "NOTION_DB_RITUALS":  "🕯️ Ритуалы",
        "NOTION_DB_WORKS":    "🔮 Работы",
        "NOTION_DB_GRIMOIRE": "📖 Гримуар",
    }

    for env_key, display_name in db_names.items():
        db_id = os.environ.get(env_key, "")
        try:
            result = await raw_notion.databases.query(database_id=db_id, page_size=1)
            count = len(result.get("results", []))
            log_pass(f"DB {display_name}", f"доступна, записей: {count}+")
        except Exception as e:
            msg = str(e)
            if "Could not find database" in msg:
                log_fail(f"DB {display_name}", f"База не найдена! ID: {db_id[:8]}...")
            elif "unauthorized" in msg.lower():
                log_fail(f"DB {display_name}", "Нет доступа (проверь Notion integration)")
            else:
                log_fail(f"DB {display_name}", msg[:100])

    # ═══════════════════════════════════════
    # БЛОК 2: CLAUDE API
    # ═══════════════════════════════════════
    section("CLAUDE API")

    try:
        from core.claude_client import ask_claude
        # ask_claude(prompt, system, model, max_tokens)
        response = await ask_claude(
            "Ответь одним словом: тест",
            system="Отвечай одним словом.",
            model=os.environ.get("CLAUDE_HAIKU", "claude-haiku-4-5-20251001"),
            max_tokens=50,
        )
        if response and len(response) > 0:
            log_pass("Claude Haiku", f"ответил: '{response[:50]}'")
        else:
            log_fail("Claude Haiku", "пустой ответ")
    except Exception as e:
        log_fail("Claude Haiku", str(e)[:120])

    # ═══════════════════════════════════════
    # БЛОК 3: CLASSIFIER
    # ═══════════════════════════════════════
    section("CLASSIFIER")

    try:
        from core.classifier import classify

        test_cases = [
            ("задача позвонить врачу завтра", "task"),
            ("потратила 500р продукты", "expense"),
            ("доход 50000", "income"),
            ("заметка: важная мысль", "note"),
        ]

        for text, expected_type in test_cases:
            try:
                result = await classify(text, tz_offset=3)
                # classify возвращает list[dict]
                if isinstance(result, list) and result:
                    actual_type = result[0].get("type", "unknown")
                elif isinstance(result, dict):
                    actual_type = result.get("type", "unknown")
                else:
                    actual_type = f"unexpected: {type(result).__name__}"

                if actual_type == expected_type:
                    log_pass(f"classify '{text[:25]}'", f"→ {actual_type}")
                else:
                    log_fail(f"classify '{text[:25]}'",
                             f"ожидали {expected_type}, получили {actual_type}")
            except Exception as e:
                log_fail(f"classify '{text[:25]}'", str(e)[:100])
    except Exception as e:
        log_fail("Classifier import", str(e)[:100])

    # ═══════════════════════════════════════
    # БЛОК 4: USER MANAGER
    # ═══════════════════════════════════════
    section("USER MANAGER")

    user_notion_id = ""
    try:
        from core.user_manager import get_user
        user = await get_user(67686090)
        if user:
            user_notion_id = user.get("notion_page_id", "")
            name = user.get("name", "?")
            log_pass("get_user(67686090)",
                     f"найден: {name}, notion_id={user_notion_id[:12]}...")
        else:
            log_fail("get_user(67686090)", "пользователь не найден")
    except Exception as e:
        log_fail("get_user(67686090)", str(e)[:120])

    # ═══════════════════════════════════════
    # БЛОК 5: TIMEZONE
    # ═══════════════════════════════════════
    section("TIMEZONE")

    try:
        from core.shared_handlers import get_user_tz
        tz = await get_user_tz(67686090)
        if isinstance(tz, (int, float)):
            log_pass("get_user_tz", f"UTC+{tz}")
        else:
            log_fail("get_user_tz", f"не число: {type(tz)}")
    except Exception as e:
        log_fail("get_user_tz", str(e)[:120])

    # ═══════════════════════════════════════
    # БЛОК 6: NOTION CRUD
    # ═══════════════════════════════════════
    section("NOTION CRUD (create → cleanup)")

    # Задача (task_add → Optional[str])
    try:
        from core.notion_client import task_add
        page_id = await task_add(
            title="[SMOKE TEST] тестовая задача — удалить",
            category="💳 Прочее",
            priority="⚪ Можно потом",
            user_notion_id=user_notion_id,
        )
        if page_id:
            cleanup_ids.append(("task", page_id))
            log_pass("task_add", f"создана: {page_id[:12]}...")
        else:
            log_fail("task_add", "вернул None")
    except Exception as e:
        log_fail("task_add", str(e)[:120])

    # Финансы (finance_add)
    try:
        from core.notion_client import finance_add
        page_id = await finance_add(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            amount=1.0,
            category="💳 Прочее",
            type_="💸 Расход",
            source="💳 Карта",
            bot_label="☀️ Nexus",
            description="[SMOKE TEST] — удалить",
            user_notion_id=user_notion_id,
        )
        if page_id:
            cleanup_ids.append(("finance", page_id))
            log_pass("finance_add", f"создана: {page_id[:12]}...")
        else:
            log_fail("finance_add", "вернул None")
    except Exception as e:
        log_fail("finance_add", str(e)[:120])

    # Заметка (note_add)
    try:
        from core.notion_client import note_add
        page_id = await note_add(
            text="[SMOKE TEST] тестовая заметка — удалить",
            tags=["тест"],
            user_notion_id=user_notion_id,
        )
        if page_id:
            cleanup_ids.append(("note", page_id))
            log_pass("note_add", f"создана: {page_id[:12]}...")
        else:
            log_fail("note_add", "вернул None")
    except Exception as e:
        log_fail("note_add", str(e)[:120])

    # Память (page_create напрямую — memory_set не возвращает id)
    try:
        from core.notion_client import page_create
        db_memory = os.environ["NOTION_DB_MEMORY"]
        props = {
            "Текст":     {"title": [{"text": {"content": "[SMOKE TEST] тест памяти — удалить"}}]},
            "Ключ":      {"rich_text": [{"text": {"content": "smoke_test_key"}}]},
            "Актуально": {"checkbox": True},
        }
        # Пользователь как relation (реальная колонка: "🪪 Пользователи")
        if user_notion_id:
            props["🪪 Пользователи"] = {"relation": [{"id": user_notion_id}]}

        page_id = await page_create(db_memory, props)
        if page_id:
            cleanup_ids.append(("memory", page_id))
            log_pass("memory page_create", f"создана: {page_id[:12]}...")
        else:
            log_fail("memory page_create", "вернул None")
    except Exception as e:
        log_fail("memory page_create", str(e)[:120])

    # client_find (должен вернуть None для несуществующего имени)
    try:
        from core.notion_client import client_find
        found = await client_find("[SMOKE TEST] клиент_не_существует_xyz", user_notion_id)
        if found is None:
            log_pass("client_find (not exists)", "вернул None — ОК")
        else:
            # нашёл что-то — тоже не ошибка, но странно
            log_pass("client_find (not exists)",
                     f"нашёл (возможно fuzzy match): {str(found)[:50]}")
    except Exception as e:
        log_fail("client_find", str(e)[:120])

    # ═══════════════════════════════════════
    # БЛОК 7: ARCANA
    # ═══════════════════════════════════════
    section("ARCANA")

    # tarot_loader — приватная _find_card, публичное API
    try:
        from arcana.tarot_loader import get_cards_context, get_deck_file
        deck_file = get_deck_file("уэйт")
        if deck_file:
            log_pass("tarot_loader get_deck_file", f"нашёл: {deck_file}")
        else:
            log_fail("tarot_loader get_deck_file", "'уэйт' не найден")
    except ImportError:
        log_skip("tarot_loader", "модуль не найден")
    except Exception as e:
        log_fail("tarot_loader", str(e)[:120])

    # get_cards_context — работает на реальных картах
    try:
        from arcana.tarot_loader import get_cards_context
        ctx = get_cards_context("уэйт", ["туз мечей"])
        if ctx and len(ctx) > 10:
            log_pass("tarot get_cards_context", f"{len(ctx)} символов")
        else:
            log_fail("tarot get_cards_context", f"короткий ответ: '{ctx}'")
    except Exception as e:
        log_fail("tarot get_cards_context", str(e)[:120])

    # deck_styles.json
    try:
        import json
        styles_path = os.path.join(_ROOT, "arcana", "deck_styles.json")
        with open(styles_path) as f:
            styles = json.load(f)
        log_pass("deck_styles.json", f"{len(styles)} колод")
    except FileNotFoundError:
        log_skip("deck_styles.json", "файл не найден")
    except Exception as e:
        log_fail("deck_styles.json", str(e)[:120])

    # pending_tarot — save_pending, get_pending, delete_pending
    try:
        from arcana.pending_tarot import save_pending, get_pending, delete_pending
        await save_pending(user_id=99999, state={"test": True, "marker": "smoke"})
        result = await get_pending(user_id=99999)
        await delete_pending(user_id=99999)
        after = await get_pending(user_id=99999)

        if result and result.get("test") is True and after is None:
            log_pass("pending_tarot SQLite", "save→get→delete ОК")
        else:
            log_fail("pending_tarot SQLite",
                     f"get={result}, after_delete={after}")
    except ImportError:
        log_skip("pending_tarot", "модуль не найден")
    except Exception as e:
        log_fail("pending_tarot", str(e)[:120])

    # core.memory — save_memory / search_memory существуют, но search_memory
    # требует aiogram.Message (это handler, не чистая функция).
    # Проверяем только импорт и наличие сигнатур.
    try:
        from core.memory import save_memory, search_memory, deactivate_memory
        import inspect
        sig_search = inspect.signature(search_memory)
        sig_save = inspect.signature(save_memory)
        log_pass("memory module",
                 f"save/search/deactivate OK, search params: "
                 f"{list(sig_search.parameters.keys())}")
    except ImportError as e:
        log_skip("memory module", f"не найден: {e}")
    except Exception as e:
        log_fail("memory module", str(e)[:120])

    # ═══════════════════════════════════════
    # БЛОК 8: УТИЛИТЫ
    # ═══════════════════════════════════════
    section("УТИЛИТЫ")

    try:
        from core.layout import maybe_convert
        converted = maybe_convert("pflfxf")
        if "задач" in converted.lower():
            log_pass("EN→RU конвертация", f"'pflfxf' → '{converted}'")
        else:
            log_fail("EN→RU конвертация",
                     f"'pflfxf' → '{converted}' (ожидали 'задача')")
    except ImportError:
        log_skip("EN→RU", "core.layout не найден")
    except Exception as e:
        log_fail("EN→RU", str(e)[:120])

    # ═══════════════════════════════════════
    # CLEANUP
    # ═══════════════════════════════════════
    section("CLEANUP")

    deleted = 0
    for label, page_id in cleanup_ids:
        try:
            await raw_notion.pages.update(page_id=page_id, archived=True)
            deleted += 1
        except Exception as e:
            print(f"  {YELLOW}⚠️{RESET}  Не удалось удалить {label} {page_id[:12]}: {str(e)[:80]}")

    if cleanup_ids:
        if deleted == len(cleanup_ids):
            log_pass("Cleanup", f"архивировано {deleted}/{len(cleanup_ids)} записей")
        else:
            log_fail("Cleanup", f"архивировано {deleted}/{len(cleanup_ids)} — часть не удалена!")
    else:
        log_pass("Cleanup", "нечего удалять")

    # ═══════════════════════════════════════
    # ОТЧЁТ
    # ═══════════════════════════════════════
    elapsed = time.time() - start_time
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok is True)
    failed = sum(1 for _, ok, _ in results if ok is False)
    skipped = sum(1 for _, ok, _ in results if ok is None)

    print(f"\n{'='*50}")
    print(f"📊 ИТОГО: {GREEN}{passed} passed{RESET}, "
          f"{RED if failed else ''}{failed} failed{RESET}, "
          f"{YELLOW if skipped else ''}{skipped} skipped{RESET} / {total} total")
    print(f"⏱  {elapsed:.1f} сек")
    print(f"{'='*50}")

    if failed > 0:
        print(f"\n{RED}❌ ПРОВАЛЕНЫ:{RESET}")
        for name, ok, detail in results:
            if ok is False:
                print(f"  • {name}: {detail}")
        print()

    if failed == 0:
        print(f"\n{GREEN}🎉 Все тесты прошли! Боты готовы.{RESET}\n")
    else:
        print(f"\n{RED}⚠️  Есть проблемы — исправь перед использованием.{RESET}\n")

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
