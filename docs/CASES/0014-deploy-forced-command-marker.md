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
  `command="<deploy user>/deploy.sh"` — Actions authenticates with `SSH_PRIVATE_KEY` and
  regardless of the `script:` body the action sends, the server runs only `deploy.sh`.
  This is least-privilege: the Actions key cannot execute arbitrary commands on prod,
  only this one auditable script.

- **`deploy.sh` (runs on the VPS; versioned in the repo as `scripts/deploy.sh`):** performs `git pull origin main`,
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

- **`deploy.sh` runs on the VPS; its source is `scripts/deploy.sh`** (the copy on the VPS is installed by hand). The `script:` block in
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
- **`deploy.sh` is versioned in the repo** (`scripts/deploy.sh`). The forced command still runs the copy in the deploy user's home, so a change
  reaches the server only when that copy is replaced by hand (copy to a temporary file next to it, `bash -n`, then an atomic `mv`). The repo is the
  reviewed source; the least-privilege boundary of the key is unchanged.
- **Auto-migration (`alembic upgrade head`) runs on every deploy.** Fast forward path;
  rollback requires manual `alembic downgrade` on the VPS or a restore from the pre-migration backup (next item).
- **Pre-migration backup (implemented).** Right before `alembic upgrade head` the script runs `pg_dump -Fc` into a temporary file under `~/backups`
  (directory mode 700, dump mode 600), checks free space, checks that the dump is non-empty and that `pg_restore -l` lists entries, only then renames
  it to `predeploy_nexus_arcana_<date>_<time>.dump` and keeps the last 7 of **that prefix only**; the shared directory (cron dumps, manual snapshots)
  keeps its mode and content, only a directory created by the script gets mode 700. Any failure stops the script before `alembic` and `docker compose up -d`.
- **The `script:` block in `deploy.yml` is never executed.** It now contains a single
  comment pointing to this ADR, making the dead-code nature explicit rather than leaving
  realistic-looking shell that silently does nothing.
