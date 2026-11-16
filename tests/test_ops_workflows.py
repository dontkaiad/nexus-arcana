"""Workflow деплоя и тестов: сообщения в ops-группу через закреплённый по SHA dontkaiad/ops-notify, deploy_ok по
путям, изменённым С ПОСЛЕДНЕГО УСПЕШНОГО ДЕПЛОЯ (логика шага plan гоняется настоящим bash; `gh` подменён
шимом, который отдаёт заданный ответ и записывает, как его вызвали)."""
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


FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$GH_CALLS"
case "$1 $2" in
  "run list") printf '%s' "$FAKE_PREV" ;;
  "api repos"*) [ "${FAKE_API_RC:-0}" = 0 ] || exit "$FAKE_API_RC"
                printf '%s\\n' "${FAKE_STATUS:-ahead}"; printf '%s' "$FAKE_FILES" ;;
  *) exit 9 ;;
esac
"""
HEAD = "b" * 40
PREV = "a" * 40


def _run_plan(tmp_path, files=(), *, prev=PREV, head=HEAD, event="workflow_run", api_rc=0, status="ahead"):
    """Настоящий bash из deploy.yml; `gh run list` отдаёт sha прошлого успешного деплоя (`prev`, пусто = его нет),
    `gh api .../compare` отдаёт status и список файлов между ним и `head`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    out, calls = tmp_path / "out", tmp_path / "calls"
    out.write_text("")
    calls.write_text("")
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", EVENT=event, REPO="o/r", HEAD_SHA=head,
               GH_TOKEN="x", GITHUB_OUTPUT=str(out), GH_CALLS=str(calls), FAKE_PREV=prev,
               FAKE_FILES="\n".join(files), FAKE_API_RC=str(api_rc), FAKE_STATUS=status)
    subprocess.run(["bash", "-c", _plan_script()], cwd=tmp_path, check=True, capture_output=True, env=env)
    result = dict(line.split("=", 1) for line in out.read_text().splitlines())
    result["calls"] = calls.read_text().splitlines()
    return result


def _bots(o):
    return o["nexus"], o["arcana"], o["zarya"]


ALL = ("true", "true", "true")


def test_the_plan_step_needs_no_checkout_and_reads_only():
    job = DOC["deploy"]["jobs"]["deploy"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert not any("checkout" in str(s.get("uses", "")) for s in job["steps"])


@pytest.mark.parametrize("files,expected,note", [
    (["nexus/handlers/tasks.py"], ("true", "false", "false"), ""),
    (["arcana/handlers/sessions.py"], ("false", "true", "false"), ""),
    (["zarya/handlers.py"], ("false", "false", "true"), ""),
    (["miniapp/backend/app.py"], ("true", "false", "false"), ""),
    (["nexus/a.py", "zarya/b.py"], ("true", "false", "true"), ""),
    (["core/config.py"], ALL, "shared code: all bots"),
    (["requirements.txt"], ALL, "shared code: all bots"),
    (["alembic/versions/x.py"], ALL, "shared code: all bots"),
    (["docs/README.md"], ("true", "false", "false"), "no bot code changed (docs, tests, CI)"),
    (["tests/test_x.py", ".github/workflows/test.yml"], ("true", "false", "false"), "no bot code changed (docs, tests, CI)"),
])
def test_plan_routes_deploy_ok_by_changed_paths(tmp_path, files, expected, note):
    o = _run_plan(tmp_path, files)
    assert _bots(o) == expected and o["note"] == note


def test_two_commits_code_then_workflow_still_count_the_code(tmp_path):
    """Тот самый пуш 7ac9d1d: в диапазоне и core/, и workflow. Раньше шаг видел только последний коммит."""
    o = _run_plan(tmp_path, ["core/ops_alert.py", "tests/test_ops_alerts.py", ".github/workflows/deploy.yml"])
    assert _bots(o) == ALL and o["note"] == "shared code: all bots"


def test_one_commit_touching_only_zarya_reports_only_zarya(tmp_path):
    assert _bots(_run_plan(tmp_path, ["zarya/handlers.py"])) == ("false", "false", "true")


def test_only_docs_and_tests_since_the_last_deploy_report_nexus_with_the_note(tmp_path):
    o = _run_plan(tmp_path, ["docs/CHANGELOG.md", "tests/test_x.py"])
    assert _bots(o) == ("true", "false", "false") and o["note"] == "no bot code changed (docs, tests, CI)"


def test_no_previous_successful_deploy_means_all_three(tmp_path):
    o = _run_plan(tmp_path, ["docs/a.md"], prev="")
    assert _bots(o) == ALL and o["note"].startswith("no previous successful deploy")
    assert not any(c.startswith("api ") for c in o["calls"])            # nothing to compare with


def test_the_compare_is_between_the_last_successful_deploy_and_the_deployed_commit(tmp_path):
    o = _run_plan(tmp_path, ["zarya/a.py"])
    listing = next(c for c in o["calls"] if c.startswith("run list"))
    for part in ("--workflow deploy.yml", "--branch main", "--status success", "--limit 1"):
        assert part in listing
    assert f"api repos/o/r/compare/{PREV}...{HEAD}" in " ".join(o["calls"])


def test_the_same_commit_redeployed_means_all_three_without_a_compare(tmp_path):
    o = _run_plan(tmp_path, ["docs/a.md"], prev=HEAD)
    assert _bots(o) == ALL and o["note"].startswith("same commit")
    assert not any(c.startswith("api ") for c in o["calls"])


@pytest.mark.parametrize("kwargs", [
    {"api_rc": 1},                                                # the API failed
    {"status": "diverged", "files": ["docs/a.md"]},               # history rewritten: the list is not the real change
    {"status": "ahead", "files": []},                             # nothing listed
])
def test_an_unusable_compare_means_all_three(tmp_path, kwargs):
    o = _run_plan(tmp_path, kwargs.pop("files", ["docs/a.md"]), **kwargs)
    assert _bots(o) == ALL and o["note"].startswith("could not list the changes")


def test_a_cut_file_list_means_all_three(tmp_path):
    o = _run_plan(tmp_path, [f"docs/{i}.md" for i in range(300)])
    assert _bots(o) == ALL and o["note"].startswith("300 or more changed files")
    assert _bots(_run_plan(tmp_path / "s", [f"docs/{i}.md" for i in range(299)])) == ("true", "false", "false")


def test_a_manual_redeploy_reports_all_three_and_calls_nothing(tmp_path):
    o = _run_plan(tmp_path, ["docs/a.md"], event="workflow_dispatch")
    assert _bots(o) == ALL and o["note"] == "manual redeploy / all bots" and o["calls"] == []
