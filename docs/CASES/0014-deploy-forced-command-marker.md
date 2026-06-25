# ADR-0014 — Automated deployment: forced command + deploy marker

- **Status:** Accepted
- **Date:** 2026-06-25
- **Relates to:** ADR-0010 (direct cutover; `deploy.sh` runs `alembic upgrade head`)
- **Code conforms to:** `.github/workflows/deploy.yml`
- Update this ADR in the same PR that changes the deploy workflow or the
  forced-command contract on the VPS.

## Context

Every push to `main` needs to reach the VPS without a manual SSH session — for speed
and because a skipped manual step is how "green locally" silently diverges from prod
(container not rebuilt, migration not applied).

Two failure modes shaped the design:

1. **`appleboy/ssh-action` swallows exit codes** — the step reports success even when
   the remote command exits non-zero. Checking the step's own outcome doesn't work.
2. **"SSH connected" ≠ "deploy succeeded"** — a container that didn't rebuild still
   runs; a clean SSH connection is not sufficient evidence.

## Decision

**GitHub Actions → forced command → `deploy.sh` on the VPS, verified by a deploy
marker printed to stdout.**

- **Forced command in `~/.ssh/authorized_keys`:**
  `command="/home/kai/deploy.sh"` — Actions authenticates with `SSH_PRIVATE_KEY` and
  regardless of the `script:` body the action sends, the server runs only `deploy.sh`.
  This is least-privilege: the Actions key cannot execute arbitrary commands on prod,
  only this one auditable script.

- **`deploy.sh` (on the VPS, outside this repo):** performs `git pull origin main`,
  `docker compose up -d --build`, and `alembic upgrade head` (auto-migration baked in,
  not driven by Actions). Per the inline documentation in `deploy.yml`, the script uses
  `set -euo pipefail` and prints the literal string `deploy done` as its **final line,
  and only on success** — any earlier failure aborts before the print.

- **Marker-based failure detection:** `capture_stdout: true` surfaces SSH output into
  `steps.deploy.outputs.stdout`. The "Verify deploy marker" step (`if: always()`) greps
  for `deploy done`; its absence means the deploy failed, and the step exits 1. This
  works around ssh-action's exit-code swallowing.

- **Telegram alert on failure:** `if: failure()` fires a message with commit SHA,
  actor, and a direct link to the Actions run into both Nexus and Arcana log threads.
  No alert on success — signal-to-noise.

- **`deploy.sh` lives outside this repo** (on the VPS). The `script:` block in
  `deploy.yml` is a placeholder ignored by the forced command — it is documented as
  such with a single comment pointing here (see §Consequences).

## Alternatives considered

- **Manual SSH deploy** — rejected: gets skipped under time pressure, produces
  non-atomic deploys (pull without rebuild, migration without restart), and leaves
  local/prod divergence undetected until a user-visible failure.
- **Full SSH access for Actions** (no forced command) — rejected: gives the deploy key
  arbitrary shell on the prod box; one leaked secret = full compromise. The forced
  command constrains the key to one known, auditable operation.
- **Separate CD service** (Coolify, Woodpecker, self-hosted runner) — rejected:
  overkill for one VPS running two bots; adds an always-on daemon with its own attack
  surface and maintenance burden.
- **Relying on ssh-action exit codes** — not viable: the action swallows non-zero exit
  codes; the marker pattern is the workaround.

## Consequences

- Every push to `main` produces a deterministic, audited deploy with a marker-verified
  success signal and a Telegram alert on failure.
- **`deploy.sh` is not version-controlled** — changes to it require a direct VPS edit
  and are not tracked in git history. Accepted trade-off: the security boundary
  (least-privilege key) outweighs auditability of the script itself on a
  single-operator personal project.
- **Auto-migration (`alembic upgrade head`) runs on every deploy.** Fast forward path;
  rollback requires manual `alembic downgrade` on the VPS. Acceptable for a personal
  project; would need a pre-deploy backup step before a team deployment.
- **The `script:` block in `deploy.yml` is never executed.** It now contains a single
  comment pointing to this ADR, making the dead-code nature explicit rather than leaving
  realistic-looking shell that silently does nothing.
