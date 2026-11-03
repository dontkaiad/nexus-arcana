# ADR-0033 — Read-only service token for cat cards (`GET /api/cats`)

- **Status:** Accepted (Nexus side); the consumer is a separate private app (see "Not covered")
- **Date:** 2026-11-03
- **Relates to:** ADR-0012 (access model), ADR-0026 (booking service token — same pattern), Case 0032 (cats as a subject)
- **Code conforms to:** `miniapp/backend/cats_read_auth.py`, `miniapp/backend/auth.py` (`current_user_id`),
  `miniapp/backend/routes/cats.py`, `core/cats.py` (`safe_photo_url`), `core/config.py`, `.env.example`,
  `tests/test_cats_read_token.py`, `tests/test_cats_read_contract.py`, `tests/test_cats_read_safety.py`,
  `tests/contracts/cats_read_v1.json`
- Update this ADR in the same PR that changes who may read cat cards or what they contain.

## Context

Cat cards (name, nicknames, photo, facts by type) now live in Nexus (Case 0032) and are edited in the Mini App. A second
application, the cat-sitter guide in a separate private repository, must show the same cards instead of keeping its own
copy of the text. It runs in another container on the same Docker network, so it has no Telegram `initData` and no
browser cookie. Duplicating the data would bring back the problem the cards solved.

## Decision

A static bearer token, read-only, valid for exactly two routes.

| Rule | Detail |
|---|---|
| Scope | Only `GET /api/cats` (with `?with_facts=1`: cards and their facts in one request) and `GET /api/cats/{id}`. `/general`, every write (create, patch, photo, facts, attach) and the rest of the API stay owner-only; the token opens nothing there (tests). |
| Identity | The request runs as the owner `CATS_READ_OWNER_TG_ID`, which must be in `ALLOWED_TELEGRAM_IDS`; the existing per-owner scoping then applies unchanged. |
| Secret | `CATS_READ_TOKEN` from the server `.env` (`openssl rand -hex 32`, at least 32 characters). Unset, short, or owner not configured/allowed → the feature is off and every bearer request is 401. The repository holds only empty names in `.env.example`. |
| Comparison | `hmac.compare_digest` on bytes. An empty token never matches an empty setting. |
| No fall-through | If `Authorization` is present it must be valid, otherwise 401 (a wrong token is not rescued by a valid cookie). Without the header nothing changes: `initData` and the `hl_session` cookie work as before. |
| Brute force | Failed bearer attempts are counted per client IP: more than 10 in 60 s → `429` with `Retry-After`, including for the correct token from that IP until the window clears. Valid requests are never counted. Behind the reverse proxy the client is the last `X-Forwarded-For` address (appended by the proxy), only when the direct peer is a private/loopback address; memory is bounded. |
| Logging | The token value is never logged; failures are logged with the IP only. |
| Responses | `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, always `application/json`. |
| Output hygiene | `photo_url` is returned only if it is an absolute `https` URL without credentials, quotes, whitespace, angle brackets, backslash, backtick or control characters, at most 2048 characters; otherwise `null` (`javascript:`, `data:`, `http:`, `//host/x` are dropped). A storage answer with an unsafe URL is not saved (502). |

## Contract

`tests/contracts/cats_read_v1.json` is the golden response (fictional data) for `GET /api/cats?with_facts=1` and
`GET /api/cats/{id}`; ids and timestamps are normalised. The consumer copies the file and runs its own tests on it; a
change on either side breaks a test. Regenerate on purpose with `UPDATE_CONTRACT=1`.

Text fields (name, nicknames, fact text) are **untrusted plain text delivered as JSON data, not HTML**. The API does not
escape them, so the consumer must escape when it builds HTML (the consumer's renderer is tested with `<script>`,
quotes and `javascript:`).

## Alternatives rejected

- **Reuse the owner's `initData`/session for the second app:** cannot be produced by a container; sharing the session
  secret would give it write access to everything.
- **A dedicated `/api/service/cats` router:** smaller blast radius, but it duplicates the response shape and the
  owner-scoping. The gate on two existing GETs is a few lines and the scope is pinned by tests instead.
- **IP allow-list only:** the API is also reachable through the public proxy; a network position is not an identity.
- **Escaping text on the API side:** double escaping bugs in every non-HTML consumer; the consumer knows its context.
- **Per-request signed tokens (HMAC with timestamp):** more moving parts for one read-only consumer on a private network;
  rotation of one static secret is enough.

## Consequences

- No schema change, no migration. Rollback is a revert of the PR or removing `CATS_READ_TOKEN` from `.env` (bearer
  requests become 401; Mini App users are unaffected).
- Enabling it is an `.env` edit on the server followed by a recreate of `nexus-bot` (a deploy that changes the image does
  this); the token is also placed in the consumer's `.env`. Rotation: change both, recreate both; the consumer serves its
  last good copy during the gap.
- The token holder can read every cat card and its facts of the configured owner. That is the intent (the sitter guide);
  nothing else is readable.
- The public proxy also serves this route: a leaked token would work from outside until rotated. The token is 256 bits,
  read-only, and brute force is rate-limited.
- A shared in-memory limiter is per process; a second Nexus replica would count separately (single container today).

## Not covered

The consumer (client, cache, rendering) lives in the private repository and is specified there. Live end-to-end
verification against production is a post-deploy read-only smoke, not a CI test.
