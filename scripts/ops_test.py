"""Живая проверка алертов ops-группы: аналог OPS_TEST из heylark-infra/ops/ops-notify.sh, для Python.

Шлёт настоящие сообщения (ошибки трёх ботов в свои топики, billing, всплеск транзиентных и «восстановлено»,
транскрипт, аудит брони Зари, метка Claude) в настоящие топики БЕЗ ЗВУКА, ждёт TTL секунд и удаляет каждое
сообщение через deleteMessage. Токен, chat_id и топики берутся из окружения (TG_LOG_BOT_TOKEN, TG_LOG_CHAT_ID,
TG_LOG_THREAD_NEXUS / _ARCANA / _BOOKING / _KAIMCP), как в ботах. message_id живёт только в памяти, токен нигде
не печатается. Код выхода 3, если хоть одно сообщение не отправилось или не удалилось.

    python -m scripts.ops_test [TTL]          # из /app в контейнере бота
"""
from __future__ import annotations

import asyncio
import sys

import httpx

from core import ops_alert
from core.config import config
from core.error_log import _format_for_group

TB = ('Traceback (most recent call last):\n  File "/app/core/x.py", line 42, in handler\n    raise KeyError(text)\n'
      "KeyError: <текст>\n")


def _cases():
    return [
        ("nexus", _format_for_group("", "processing_error", "", TB, "☀️ Nexus", "–", "ops-test")),
        ("arcana", _format_for_group("", "parse_error", "", TB, "🌒 Arcana", "–", "ops-test")),
        ("zarya", _format_for_group("", "processing_error", "", TB, "⭐ Zarya", "–", "ops-test")),
        ("nexus", ops_alert.format_alert("nexus", "🔴", "error", "ops-test: Anthropic: кончились кредиты, LLM не работает",
                                         "проверь баланс и ключ в консоли Anthropic")),
        ("arcana", ops_alert.format_alert("arcana", "🟠", "warning",
                                          "ops-test: Telegram API нестабилен: 5 сетевых ошибок за 10 мин",
                                          "TelegramNetworkError ×5")),
        ("arcana", ops_alert.format_alert("arcana", "🟢", "recovered", "ops-test: Telegram/сеть снова в норме (тихо 10 мин)",
                                          "за время сбоя 5 ошибок: TelegramNetworkError ×5")),
        ("arcana", ops_alert.format_event("arcana", "🎤", "voice", "транскрипт: ops-test, это проверка, не настоящая запись")),
        ("zarya", ops_alert.format_event("zarya", "📅", "booking", "ops-test: строка аудита брони")),
        ("kai-mcp", ops_alert.format_event("kai-mcp", "🤖", "claude", "ops-test: 🤖 Claude создал задачу: <b>проверка</b>")),
    ]


async def main(ttl: float) -> int:
    from core import bot_notify
    from core.bot_notify import _send_log

    # Отправка в топик Зари (1182) иногда не укладывается в боевые 5 с: сообщение могло бы дойти без
    # нашего message_id и остаться в топике. На время проверки таймаут больше (20 с), чтобы id был всегда.
    bot_notify._LOG_TIMEOUT = 20.0
    if not config.log_bot_token or not config.log_chat_id:
        print("OPS_TEST: TG_LOG_BOT_TOKEN / TG_LOG_CHAT_ID are not set", file=sys.stderr)
        return 3
    cases = _cases()
    ids = []
    for bot, text in cases:
        mid = await _send_log(text, ops_alert.thread_for(bot), silent=True)
        if mid:
            ids.append(mid)
    failures = len(cases) - len(ids)
    print(f"OPS_TEST: sent {len(ids)}/{len(cases)} silent messages")
    await asyncio.sleep(ttl)
    deleted = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for mid in ids:
            try:
                r = await client.post(f"https://api.telegram.org/bot{config.log_bot_token}/deleteMessage",
                                      json={"chat_id": config.log_chat_id, "message_id": mid})
                ok = r.status_code == 200 and r.json().get("ok") is True
            except Exception as exc:  # noqa: BLE001 — текст исключения httpx содержит URL с токеном
                print(f"OPS_TEST: delete error ({type(exc).__name__})", file=sys.stderr)
                ok = False
            if ok:
                deleted += 1
            else:
                failures += 1
                print("OPS_TEST: DELETE FAILED: a message is still in a topic", file=sys.stderr)
    print(f"OPS_TEST: deleted {deleted}/{len(ids)} after {ttl:g}s, failures={failures}")
    return 3 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 8.0)))
