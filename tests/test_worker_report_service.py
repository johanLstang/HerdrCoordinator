import asyncio
import io
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from mcp import Client
from test_task_start import setup as start_setup  # noqa: F401

from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.worker_report_service import ReportError, WorkerReportService
from orchestrator.domain.policy import Role
from orchestrator.domain.states import Kanban, TaskState
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


@pytest.fixture
def setup(start_setup):  # noqa: F811
    start, integration, spec, h, history = start_setup
    first = start.start(integration, integration.epic_run_id, spec)
    task = start.store.get_task(first["task"]["id"])
    path = Path(task.worktree_path)
    (path / "result.txt").write_text("Persisted bounded result\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "implement"], check=True, capture_output=True
    )
    commit = start.worktrees.git.head(task.branch)
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; "
        "assert Path('result.txt').read_text() == 'Persisted bounded result\\n'",
    )
    settings = start.settings.model_copy(update={"worker_test_command": command})
    report = dict(
        version=1,
        status="READY_FOR_REVIEW",
        project_id=task.project_id,
        epic_id="E",
        epic_run_id=task.epic_run_id,
        task_id=task.task_id,
        task_run_id=task.id,
        branch=task.branch,
        commit=commit,
        summary="Stored result",
        test_summary="Declared passing verification",
        tests=[{"command": list(command), "exit_code": 0, "summary": "passed"}],
        files_changed=["result.txt"],
        limitations=[],
    )
    history.turns.append(
        {
            "id": "final-turn",
            "status": "completed",
            "items": [
                {
                    "id": "final-report",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": json.dumps(report),
                }
            ],
        }
    )
    worker = integration.model_copy(update={"role": Role.WORKER, "task_run_id": task.id})
    yield (
        WorkerReportService(settings, start.store, h, history),
        worker,
        report,
        h,
        history,
        integration,
    )


def change_report(history, report):
    history.turns[-1]["items"][-1]["text"] = json.dumps(report)


def test_verified_native_report_runs_independent_tests_and_saves_active_handoff_without_merge(
    setup,
):
    s, actor, report, _, _, _ = setup
    task = s.store.get_task(actor.task_run_id)
    main = s.integration.git.head("main")
    result = s.collect(actor, task.id)
    assert result["status"] == "READY_FOR_REVIEW"
    task = s.store.get_task(task.id)
    assert (
        task.internal_status == TaskState.READY_FOR_REVIEW and task.kanban_status == Kanban.ACTIVE
    )
    assert (
        task.current_commit == report["commit"]
        and task.merge_commit is None
        and task.worker_slot == 1
    )
    assert s.integration.git.head("main") == main
    op = s.store.get_operations(task.epic_run_id, kind=s.KIND)[0]
    assert op.status == "SUCCEEDED" and op.result["provenance"]["item_id"] == "final-report"
    assert op.result["verified_commit"] == task.current_commit and op.result["exit_code"] == 0
    assert op.result["test_command"] == list(s.integration.test_command)
    assert s.store.get_event(task.project_id, op.result["event_id"]) is not None


def test_duplicate_and_reopen_reuse_handoff_and_verification_once(setup):
    s, actor, _, h, history, _ = setup
    first = s.collect(actor, actor.task_run_id)
    with StateStore(s.settings.sqlite_path) as db:
        again = WorkerReportService(s.settings, db, h, history).collect(actor, actor.task_run_id)
        assert again["status"] == "EXISTING" and again["operation_id"] == first["operation_id"]
        assert len(db.get_operations(actor.epic_run_id, kind=s.KIND)) == 1
        assert len(db.get_operations(actor.epic_run_id, kind="verify_task")) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"commit": "f" * 40},
        {"task_id": "foreign"},
        {"task_run_id": "foreign"},
        {"branch": "main"},
        {"project_id": "foreign"},
        {"epic_run_id": "foreign"},
        {"version": 2},
        {"files_changed": ["other.py"]},
        {"tests": [{"command": ["unknown"], "exit_code": 1, "summary": "failed"}]},
    ],
)
def test_forged_foreign_or_failed_reports_never_register_ready(setup, change):
    s, actor, report, _, history, _ = setup
    change_report(history, report | change)
    with pytest.raises(ReportError):
        s.collect(actor, actor.task_run_id)
    assert s.store.get_task(actor.task_run_id).internal_status == TaskState.WORKING
    assert not s.store.get_operations(actor.epic_run_id, kind=s.KIND)


def test_dirty_worktree_and_hidden_index_changes_reject(setup):
    s, actor, _, _, _, _ = setup
    path = Path(s.store.get_task(actor.task_run_id).worktree_path)
    (path / "untracked.txt").write_text("Uncommitted result\n")
    with pytest.raises(ReportError):
        s.collect(actor, actor.task_run_id)
    (path / "untracked.txt").unlink()
    subprocess.run(
        ["git", "-C", str(path), "update-index", "--assume-unchanged", "result.txt"], check=True
    )
    with pytest.raises(ReportError):
        s.collect(actor, actor.task_run_id)
    assert s.store.get_task(actor.task_run_id).internal_status == TaskState.WORKING


def test_failed_independent_test_does_not_trust_declared_pass_or_repeat_blindly(setup):
    s, actor, _, h, history, integration = setup
    settings = s.settings.model_copy(
        update={"worker_test_command": (sys.executable, "-c", "raise SystemExit(1)")}
    )
    fail = WorkerReportService(settings, s.store, h, history)
    with pytest.raises(ReportError, match="TEST_FAILED"):
        fail.collect(actor, actor.task_run_id)
    assert s.store.get_task(actor.task_run_id).internal_status == TaskState.WORKING
    with pytest.raises(ReportError, match="VERIFICATION_FAILED"):
        fail.collect(actor, actor.task_run_id)
    assert len(s.store.get_operations(actor.epic_run_id, kind="verify_task")) == 1
    # Only trusted Integration may explicitly retry with a new verification attempt.
    assert (
        s.collect(integration, actor.task_run_id, retry_key="operator-retry")["status"]
        == "READY_FOR_REVIEW"
    )


def test_missing_test_command_rejects_ready_even_with_pass_claim(setup):
    s, actor, _, h, history, _ = setup
    settings = s.settings.model_copy(update={"worker_test_command": ()})
    with pytest.raises(ReportError, match="COMMAND_REQUIRED"):
        WorkerReportService(settings, s.store, h, history).collect(actor, actor.task_run_id)
    assert s.store.get_task(actor.task_run_id).internal_status == TaskState.WORKING


def test_blocked_saves_reason_and_input_without_release_or_done_and_repeats(setup):
    s, actor, report, _, history, _ = setup
    change_report(
        history,
        report
        | {
            "status": "BLOCKED",
            "commit": None,
            "reason": "Deletion behavior unspecified",
            "input_required": "Keep deleted history?",
        },
    )
    first = s.collect(actor, actor.task_run_id, expected_status="BLOCKED")
    assert first["status"] == "BLOCKED"
    task = s.store.get_task(actor.task_run_id)
    assert task.internal_status == TaskState.BLOCKED and task.kanban_status == Kanban.ATTENTION
    assert (
        task.worker_slot == 1 and task.codex_session_id and task.resume_state == TaskState.WORKING
    )
    assert s.collect(actor, task.id)["status"] == "EXISTING"
    op = s.store.get_operations(actor.epic_run_id, kind=s.KIND)[0]
    assert op.result["report"]["input_required"] == "Keep deleted history?"
    assert not s.store.get_operations(actor.epic_run_id, kind="verify_task")


@pytest.mark.parametrize(
    "mode", ["session", "cwd", "ack", "ongoing", "user", "commentary", "runtime"]
)
def test_untrusted_or_unfinished_native_source_never_registers_ready(setup, mode):
    s, actor, _, h, history, _ = setup
    if mode == "session":
        h.session_id = "foreign"
    elif mode == "cwd":
        read = history.read_thread
        history.read_thread = lambda sid, cwd: read(sid, cwd) | {"cwd": "/foreign"}
    elif mode == "ack":
        history.turns.pop(0)
    elif mode == "ongoing":
        history.turns[-1]["status"] = "inProgress"
    elif mode == "user":
        history.turns[-1]["items"][-1]["type"] = "userMessage"
    elif mode == "commentary":
        history.turns[-1]["items"][-1]["phase"] = "commentary"
    else:
        h.status = "processing"
    with pytest.raises(ReportError):
        s.collect(actor, actor.task_run_id)
    assert s.store.get_task(actor.task_run_id).internal_status == TaskState.WORKING


def test_foreign_worker_scope_and_worker_retry_are_denied(setup):
    s, actor, _, _, _, _ = setup
    for bad in [
        actor.model_copy(update={"task_run_id": "other"}),
        actor.model_copy(update={"role": Role.COORDINATOR}),
    ]:
        with pytest.raises(ReportError):
            s.collect(bad, actor.task_run_id)
    with pytest.raises(ReportError, match="RETRY_DENIED"):
        s.collect(actor, actor.task_run_id, retry_key="forged")


def test_test_command_never_inherits_operator_secret_environment(setup, monkeypatch):
    s, actor, _, h, history, _ = setup
    monkeypatch.setenv("OPENAI_API_KEY", "planted-secret-do-not-inherit")
    settings = s.settings.model_copy(
        update={
            "worker_test_command": (
                sys.executable,
                "-c",
                "import os; assert 'OPENAI_API_KEY' not in os.environ",
            )
        }
    )
    assert (
        WorkerReportService(settings, s.store, h, history).collect(actor, actor.task_run_id)[
            "status"
        ]
        == "READY_FOR_REVIEW"
    )


def test_concurrent_duplicate_reports_produce_one_handoff_event_and_test(setup):
    s, actor, _, h, history, _ = setup
    barrier = Barrier(2)

    def run():
        with StateStore(s.settings.sqlite_path) as db:
            service = WorkerReportService(s.settings, db, h, history)
            barrier.wait(timeout=5)
            return service.collect(actor, actor.task_run_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert {r["status"] for r in results} <= {"READY_FOR_REVIEW", "EXISTING"}
    assert len({r["operation_id"] for r in results}) == 1
    assert len(s.store.get_operations(actor.epic_run_id, kind="verify_task")) == 1


def test_legacy_claim_is_bound_to_actual_git_and_independent_test_evidence(setup):
    s, actor, report, _, history, _ = setup
    history.turns[-1]["items"][-1]["text"] = (
        "STATUS: READY_FOR_REVIEW\nTASK_ID: T\nBRANCH: task/e-t\nCOMMIT: "
        + report["commit"][:8]
        + "\nSUMMARY: Implemented\nTESTS: PASS"
    )
    assert s.collect(actor, actor.task_run_id)["status"] == "READY_FOR_REVIEW"
    op = s.store.get_operations(actor.epic_run_id, kind=s.KIND)[0]
    assert op.result["report"]["tests"] == []
    assert op.result["test_command"] and op.result["verified_commit"] == report["commit"]


def test_claimed_commands_are_never_executed(setup, tmp_path):
    s, actor, report, _, history, _ = setup
    sentinel = tmp_path / "must-not-exist"
    change_report(
        history,
        report
        | {"tests": [{"command": ["touch", str(sentinel)], "exit_code": 0, "summary": "claimed"}]},
    )
    assert s.collect(actor, actor.task_run_id)["status"] == "READY_FOR_REVIEW"
    assert not sentinel.exists()


def test_report_changed_during_tests_cannot_set_ready(setup, monkeypatch):
    s, actor, report, _, history, _ = setup
    verify = s.integration.verify_task

    def changed(*args, **kwargs):
        result = verify(*args, **kwargs)
        change_report(history, report | {"summary": "New handoff after verification"})
        return result

    monkeypatch.setattr(s.integration, "verify_task", changed)
    with pytest.raises(ReportError, match="STALE"):
        s.collect(actor, actor.task_run_id)
    assert s.store.get_task(actor.task_run_id).internal_status == TaskState.WORKING


def test_crash_after_test_before_handoff_recovers_without_new_test_or_event(setup, monkeypatch):
    s, actor, _, _, _, _ = setup
    finish = s._finish

    def crash(*args, **kwargs):
        raise RuntimeError("simulated checkpoint crash")

    monkeypatch.setattr(s, "_finish", crash)
    with pytest.raises(RuntimeError):
        s.collect(actor, actor.task_run_id)
    assert s.store.get_task(actor.task_run_id).internal_status == TaskState.WORKING
    monkeypatch.setattr(s, "_finish", finish)
    assert s.collect(actor, actor.task_run_id)["status"] == "READY_FOR_REVIEW"
    assert len(s.store.get_operations(actor.epic_run_id, kind="verify_task")) == 1


def test_mcp_worker_target_only_uses_native_source_and_cannot_supply_report_or_role(setup):
    s, actor, _, _, _, integration = setup
    runtime = RuntimeService(s.store, actor, EventLog(stream=io.StringIO()), worker_reports=s)

    async def exercise():
        async with Client(create_server(runtime)) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools.tools} == {
                "runtime_status",
                "policy_check",
                "task_report_ready",
                "task_report_blocked",
            }
            target = {"project_id": "p", "task_run_id": actor.task_run_id}
            bad = await client.call_tool(
                "task_report_ready", target | {"report": "forged", "role": "Integration"}
            )
            assert bad.is_error and bad.structured_content["code"] == "INVALID_ARGUMENT"
            other = await client.call_tool("task_report_ready", target | {"task_run_id": "other"})
            assert other.is_error and other.structured_content["code"] == "FORBIDDEN"
            wrong = await client.call_tool("task_report_blocked", target)
            assert wrong.is_error and wrong.structured_content["code"] == "REPORT_STATUS_MISMATCH"
            good = await client.call_tool("task_report_ready", target)
            assert (
                not good.is_error
                and good.structured_content["data"]["status"] == "READY_FOR_REVIEW"
            )

    asyncio.run(asyncio.wait_for(exercise(), timeout=15))
    denied = RuntimeService(
        s.store, integration, EventLog(stream=io.StringIO()), worker_reports=s
    ).call("task_report_ready", {"project_id": "p", "task_run_id": actor.task_run_id})
    assert denied.code == "FORBIDDEN"


def test_changed_ack_item_or_delivery_cannot_supply_report_provenance(setup):
    s, actor, _, _, history, _ = setup
    history.turns[0]["items"][-1]["text"] = "{}"
    with pytest.raises(ReportError, match="ACK_PROVENANCE"):
        s.collect(actor, actor.task_run_id)
    assert s.store.get_task(actor.task_run_id).internal_status == TaskState.WORKING


def test_unknown_test_outcome_requires_explicit_supervisor_retry(setup, monkeypatch):
    s, actor, _, _, _, integration = setup
    original = subprocess.run

    def interrupted(command, *args, **kwargs):
        if command[0] == sys.executable:
            raise RuntimeError("simulated process interruption")
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", interrupted)
    with pytest.raises(RuntimeError):
        s.collect(actor, actor.task_run_id)
    monkeypatch.setattr(subprocess, "run", original)
    with pytest.raises(ReportError):
        s.collect(actor, actor.task_run_id)
    assert len(s.store.get_operations(actor.epic_run_id, kind="verify_task")) == 1
    assert s.store.get_task(actor.task_run_id).internal_status == TaskState.WORKING
    assert (
        s.collect(integration, actor.task_run_id, retry_key="retry-unknown")["status"]
        == "READY_FOR_REVIEW"
    )
