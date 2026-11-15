"""Workflow деплоя и тестов: сообщения в ops-группу через закреплённый по SHA dontkaiad/ops-notify, deploy_ok по
изменённым путям (логика шага plan гоняется настоящим bash на настоящем git-репозитории)."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent

WF = {n: (REPO / ".github/workflows" / f"{n}.yml").read_text(encoding="utf-8") for n in ("deploy", "test")}
DOC = {n: yaml.safe_load(t) for n, t in WF.items()}


@pytest.mark.parametrize("name", ["deploy", "test"])
def test_workflows_use_the_pinned_ops_action_and_never_expand_the_token_in_a_script(name):
    text = WF[name]
    uses = re.findall(r"uses:\s*(dontkaiad/ops-notify@\S+)", text)
    assert uses and all(re.fullmatch(r"dontkaiad/ops-notify@[0-9a-f]{40}", u) for u in uses)
    assert "TG_LOG_THREAD" not in text and "api.telegram.org" not in text
    # токен и chat id только входами `with:`, не внутри `run:`
    for job in DOC[name]["jobs"].values():
        for step in job["steps"]:
            assert "secrets." not in str(step.get("run", ""))


def test_deploy_reports_ok_per_bot_and_failure_to_all_three():
    steps = DOC["deploy"]["jobs"]["deploy"]["steps"]
    ok = {s["with"]["project"]: s for s in steps if s.get("with", {}).get("kind") == "deploy_ok"}
    fail = {s["with"]["project"]: s for s in steps if s.get("with", {}).get("kind") == "deploy_fail"}
    assert set(ok) == set(fail) == {"nexus", "arcana", "zarya"}
    for proj, s in ok.items():
        assert s["if"] == f"success() && steps.plan.outputs.{proj} == 'true'"
    assert all(s["if"] == "failure()" for s in fail.values())


def _plan_script():
    step = next(s for s in DOC["deploy"]["jobs"]["deploy"]["steps"] if s.get("id") == "plan")
    return step["run"]


def _run_plan(tmp_path, changed, event="workflow_run"):
    """Настоящий bash из deploy.yml на настоящем git-репозитории с одним коммитом, меняющим `changed`."""
    repo = tmp_path / "r"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

    def git(*a):
        subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    (repo / "base.txt").write_text("1")
    git("add", "-A")
    git("commit", "-q", "-m", "c0")
    for f in changed:
        p = repo / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    git("add", "-A")
    git("commit", "-q", "-m", "c1", "--allow-empty")
    out = tmp_path / "out"
    out.write_text("")
    subprocess.run(["bash", "-c", _plan_script()], cwd=repo, check=True, capture_output=True,
                   env=dict(env, EVENT=event, GITHUB_OUTPUT=str(out)))
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


@pytest.mark.parametrize("changed,expected,note", [
    (["nexus/handlers/tasks.py"], ("true", "false", "false"), ""),
    (["arcana/handlers/sessions.py"], ("false", "true", "false"), ""),
    (["zarya/handlers.py"], ("false", "false", "true"), ""),
    (["miniapp/backend/app.py"], ("true", "false", "false"), ""),
    (["nexus/a.py", "zarya/b.py"], ("true", "false", "true"), ""),
    (["core/config.py"], ("true", "true", "true"), "shared code: all bots"),
    (["requirements.txt"], ("true", "true", "true"), "shared code: all bots"),
    (["alembic/versions/x.py"], ("true", "true", "true"), "shared code: all bots"),
    (["docs/README.md"], ("true", "false", "false"), "no bot code changed (docs, tests, CI)"),
    (["tests/test_x.py", ".github/workflows/test.yml"], ("true", "false", "false"), "no bot code changed (docs, tests, CI)"),
])
def test_plan_routes_deploy_ok_by_changed_paths(tmp_path, changed, expected, note):
    o = _run_plan(tmp_path, changed)
    assert (o["nexus"], o["arcana"], o["zarya"]) == expected and o["note"] == note


def test_a_manual_redeploy_reports_all_three(tmp_path):
    o = _run_plan(tmp_path, ["docs/a.md"], event="workflow_dispatch")
    assert (o["nexus"], o["arcana"], o["zarya"]) == ("true", "true", "true")
