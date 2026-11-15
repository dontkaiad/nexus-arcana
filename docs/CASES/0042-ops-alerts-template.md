# ADR-0042 — Alerts to the ops group: one template, no user data, transient errors counted

- **Status:** Accepted
- **Date:** 2026-11-15
- **Relates to:** ADR-0014 (deploy.sh), ADR-0026 (Zarya), ADR-0039/0040 (MCP alerts: header changed, text kept)
- **Code conforms to:** `core/ops_alert.py`, `core/ops_transient.py`, `core/error_log.py`, `core/logging_notion.py`,
  `core/bot_notify.py`, `core/llm_health.py`, `core/heartbeat.py`, `zarya/bot.py`, `.github/workflows/deploy.yml`,
  `.github/workflows/test.yml`, `scripts/ops_test.py`, `tests/test_ops_alerts.py`, `tests/test_ops_workflows.py`

## Decision

1. **One look.** Every message to the log group is `<emoji> <b>☀️ nexus</b> · <word>`, a title line and an italic line (HTML), the same as
   `ops/ops-notify.sh` in heylark-infra. Transport, bot token, chat and topics are unchanged: Nexus 9, Arcana 10, Zarya 1182 (`TG_LOG_THREAD_BOOKING`), kai-mcp 2219.
2. **No user data in an ordinary error alert.** `_format_for_group` no longer prints `msg:`, `claude:` or the traceback text. The alert has the error
   type, the code, the exception CLASS, `file:line in function` and the handler name. The full text stays in `docker logs` (`log_error` writes it).
3. **Left as they were, template header only** (the owner's decision): voice transcripts in full (Arcana topic), the Claude marks from kai-mcp, the
   Zarya booking audit lines (`notify_booking_log`).
4. **Zarya** uses the common error handler (label ⭐ zarya, topic 1182).
5. **Transient Telegram/network errors** (aiogram "Failed to fetch updates", `TelegramNetworkError`, `RetryAfter`, aiohttp connection errors) are only counted
   per class per UTC day in `/tmp/ops_transient.json` (read by the host monitor's daily pulse). One alert at 5 or more in 10 minutes, one "recovered" after 10 quiet
   minutes (the heartbeat loop calls the tick). `TelegramBadRequest`, `Forbidden`, `Unauthorized`, `Conflict` etc. are never transient.
6. **Anthropic billing/auth**: the DM to every owner account stays, and the same event (same 6-hour throttle) goes to the Nexus topic.
7. **No startup pings** ("поднялся" in Nexus, Arcana, Zarya): a deploy is reported by CI, a crash or a restart loop by the host monitor. `log_version` still writes the version to the log.
8. **CI** uses `dontkaiad/ops-notify` (a public copy of heylark-infra/ops, pinned by SHA; this repository is public) instead of a hand-written `curl` with the
   token inside the script. `deploy_ok` goes to the topics of the bots whose paths changed (`nexus/`, `miniapp/` to Nexus, `arcana/`, `zarya/`; shared code,
   alembic, Docker, requirements to all three; docs and tests only to Nexus with a note); a failed deploy or red tests to all three topics.

## Rollback

`git revert` the two commits (code, workflow) and push: the old texts and the old `curl` steps come back. Nothing in the database or the volumes changes.
The GitHub secrets `TG_LOG_THREAD_NEXUS` and `TG_LOG_THREAD_ARCANA` are no longer read by the workflows (can be deleted).
