"""F36 actual Git/process/SQLite/MCP; runtime and TeamPlayer are controlled adapters."""

import asyncio
import base64
import hashlib
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from mcp import Client
from test_integration_control import epic_setup, finish, setup, task  # noqa: F401
from test_task_scheduler import approve
from test_teamplayer_reader import T1, T2, T3

from orchestrator.application.epic_completion_service import (
    EpicCompletionError,
    EpicCompletionService,
)
from orchestrator.application.integration_control_service import IntegrationControlService
from orchestrator.domain.epic_completion import EpicCompletionSpec
from orchestrator.domain.policy import Role
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.mcp.server import create_server


def specification(control, **changes):
    registered = control.epic_start.spec
    success = dict(
        argv=(
            sys.executable,
            "-c",
            "from pathlib import Path; assert len(list(Path('.').glob('*.result')))==3",
        ),
        timeout=30,
    )
    data = dict(
        version=1,
        acceptance_version=1,
        project_id=registered.project_id,
        epic_id=registered.epic_id,
        requirements=registered.requirements,
        acceptance_criteria=registered.acceptance_criteria,
        sources=registered.sources,
        task_ids=tuple(t.task.task_id for t in registered.tasks),
        build=success,
        test=success,
        acceptance=success,
        known_limitations=("Controlled runtime/board; real Git, SQLite and processes",),
    )
    return EpicCompletionSpec.model_validate(data | changes)


def completion(control, **changes):
    return EpicCompletionService(
        control.epic_start, control.scheduler, specification(control, **changes)
    )


async def deliver(control, runtime, sessions):
    for identity, local in ((T1, "F-01"), (T2, "F-02"), (T3, "F-03")):
        response = await runtime.call_async(
            "task_start",
            dict(project_id="p", epic_run_id="e", task=control.scheduler.selection.specs[identity]),
        )
        assert response.ok, response
        finish(control, sessions, local)
        await runtime.call_async("task_schedule", dict(project_id="p", epic_run_id="e"))
        current = task(control, local)
        assert current.internal_status == TaskState.REVIEWING
        context = next(
            o.result["context"]
            for o in reversed(control.store.get_operations("e", kind="task_review_request"))
            if o.task_run_id == current.id
        )
        result = await runtime.call_async(
            "task_approve",
            dict(
                project_id="p",
                task_run_id=current.id,
                request_key="approve-" + local,
                decision=approve(context),
            ),
        )
        assert result.ok, result
        result = await runtime.call_async(
            "task_merge",
            dict(
                project_id="p",
                task_run_id=current.id,
                request_key="merge-" + local,
                verification_key="after-" + local,
            ),
        )
        assert result.ok, result
        assert task(control, local).internal_status == TaskState.DONE


@pytest.fixture
def delivered(setup):  # noqa: F811
    control, runtime, actor, board, sessions = setup
    asyncio.run(deliver(control, runtime, sessions))
    yield control, actor, board, sessions


def test_current_complete_handoff_sdk_persisted_diff_and_no_main_merge(delivered):
    control, actor, board, sessions = delivered
    service = completion(control)
    wrapped = IntegrationControlService(
        control.epic_start, control.scheduler, control.delegate.log, epic_completion=service
    )
    initial = service.git.head("main")

    async def run():
        async with Client(create_server(wrapped.runtime(control.delegate.log))) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert "epic_complete" in tools
            assert set(tools["epic_complete"].input_schema["properties"]) == {
                "project_id",
                "epic_run_id",
                "request_key",
            }
            denied = await client.call_tool(
                "epic_complete",
                dict(project_id="p", epic_run_id="e", request_key="bad", command=["true"]),
            )
            assert not denied.structured_content["ok"]
            result = await client.call_tool(
                "epic_complete", dict(project_id="p", epic_run_id="e", request_key="ready")
            )
            assert result.structured_content["ok"], result.structured_content
        saved = await service.require_current(actor, "e", key="ready")
        report = saved.result["report"]
        patch = base64.b64decode(report["diff"]["patch"])
        assert hashlib.sha256(patch).hexdigest() == report["diff"]["sha256"]
        actual = service.git._diff(
            Path(control.store.get_epic("e").worktree_path),
            (initial, report["source_commit"]),
            4 * 1024 * 1024,
        )
        assert patch == actual.patch and len(patch) == report["diff"]["bytes"]
        assert len(report["manifest"]) == 3
        assert {s["result"]["name"] for s in report["steps"]} == {"build", "test", "acceptance"}
        assert all(s["result"]["exit_code"] == 0 for s in report["steps"])
        for entry in report["manifest"]:
            assert entry["worker_report"]["summary"] == "Actual fixture Git result"
            assert entry["context"]["diff"]["complete"]
            assert entry["approval"]["decision"]["result"] == "APPROVED"
            assert entry["aftertest"]["result"]["exit_code"] == 0
            assert entry["runtime_status"] == "STOPPED" and entry["worker_slot"] is None
            assert service.git.parents(entry["merge_commit"])[1] == entry["source_commit"]
        assert await service.complete(actor, "e", key="ready") == saved
        # A fresh service instance reads the same persisted receipt; no new process steps.
        assert await completion(control).require_current(actor, "e", key="ready") == saved
        assert len(control.store.get_operations("e", kind=service.STEP)) == 3

    asyncio.run(run())
    assert control.store.get_epic("e").status == EpicState.READY_FOR_REVIEW
    assert control.store.get_epic("e").approved_source_commit is None
    assert service.git.head("main") == initial
    assert not control.store.get_operations("e", kind="merge_epic_to_main")
    assert board.epics[0]["status"] == "InProgress"
    assert all(t.worker_slot is None for t in control.store.get_tasks("e"))
    assert sessions.starts == 4


@pytest.mark.parametrize("name", ["build", "test", "acceptance"])
def test_failed_actual_command_never_hands_off_and_reuses_failure(delivered, name):
    control, actor, _, _ = delivered
    service = completion(
        control, **{name: dict(argv=(sys.executable, "-c", "raise SystemExit(7)"))}
    )
    initial = service.git.head("main")
    result = asyncio.run(service.complete(actor, "e", key="fail"))
    assert (
        result.status == "FAILED"
        and result.error_code == "EPIC_COMPLETION_" + name.upper() + "_FAILED"
    )
    assert result.result["steps"][-1]["result"]["exit_code"] == 7
    assert len(result.result["steps"]) == ("build", "test", "acceptance").index(name) + 1
    assert asyncio.run(service.complete(actor, "e", key="fail")) == result
    with pytest.raises(EpicCompletionError):
        asyncio.run(service.require_current(actor, "e", key="fail"))
    assert control.store.get_epic("e").status == EpicState.ACTIVE
    assert service.git.head("main") == initial


@pytest.mark.parametrize(
    "change",
    [
        "new_task",
        "reopened",
        "cancelled",
        "owner",
        "criteria",
        "test",
        "stop",
        "review",
        "merge",
        "report",
    ],
)
def test_incomplete_or_changed_membership_evidence_denies_handoff(delivered, change):
    control, actor, board, _ = delivered
    service = completion(control)
    first = control.store.get_tasks("e")[0]
    row = next(t for t in board.tasks if t["taskId"] == control.scheduler._identity(first))
    if change == "new_task":
        board.tasks.append(deepcopy(row) | {"taskId": "00000000-0000-0000-0000-000000000099"})
    elif change == "reopened":
        row["status"] = "Pending"
    elif change == "cancelled":
        row["status"] = "Cancelled"
    elif change == "owner":
        row["executionOwnerKind"] = "Agent"
    elif change == "criteria":
        row["acceptanceCriteria"] = ["New acceptance"]
    else:
        kind = {
            "test": "task_delivery_test",
            "stop": "stop_runtime",
            "review": "task_review_request",
            "merge": "merge_task_to_epic",
            "report": "worker_report",
        }[change]
        op = next(
            o for o in control.store.get_operations("e", kind=kind) if o.task_run_id == first.id
        )
        control.store.update_operation(op.model_copy(update={"status": "FAILED"}))
    with pytest.raises(RuntimeError):
        asyncio.run(service.complete(actor, "e", key="invalid"))
    assert not control.store.get_operations("e", kind=service.KIND)
    assert control.store.get_epic("e").status == EpicState.ACTIVE


@pytest.mark.parametrize("change", ["epic", "main", "dirty", "version", "membership"])
def test_old_ready_receipt_requires_fresh_source_manifest_and_acceptance(delivered, change):
    control, actor, board, _ = delivered
    service = completion(control)
    asyncio.run(service.complete(actor, "e", key="ready"))
    if change in {"epic", "main", "dirty"}:
        path = (
            service.settings.repository
            if change == "main"
            else Path(control.store.get_epic("e").worktree_path)
        )
        (path / "new.txt").write_text("new\n")
        if change != "dirty":
            service.git.run("add", "new.txt", cwd=path)
            service.git.run("commit", "-m", "actual later change", cwd=path)
    elif change == "version":
        service = completion(control, acceptance_version=2)
    else:
        board.tasks.pop()
    with pytest.raises(RuntimeError):
        asyncio.run(service.require_current(actor, "e", key="ready"))
    with pytest.raises(RuntimeError):
        asyncio.run(service.complete(actor, "e", key="ready"))
    assert len(control.store.get_operations("e", kind=service.STEP)) == 3
    if change == "epic":
        newer = asyncio.run(service.complete(actor, "e", key="new-source"))
        assert newer.status == "SUCCEEDED"
        assert newer.result["report"]["source_commit"] == service.git.head(
            control.store.get_epic("e").branch
        )
        assert len(control.store.get_operations("e", kind=service.STEP)) == 6
        with pytest.raises(EpicCompletionError):
            asyncio.run(service.require_current(actor, "e", key="ready"))


@pytest.mark.parametrize("role", [Role.WORKER, Role.COORDINATOR])
def test_wrong_role_cannot_execute_aggregate(setup, role):  # noqa: F811
    control, _, actor, _, sessions = setup
    service = completion(control)
    with pytest.raises(EpicCompletionError, match="SCOPE_DENIED"):
        asyncio.run(service.complete(actor.model_copy(update={"role": role}), "e", key="bad"))
    assert sessions.starts == 1 and not control.store.get_operations("e", kind=service.STEP)


def test_empty_scope_and_missing_criteria_are_not_valid_configuration(setup):  # noqa: F811
    control, _, _, _, _ = setup
    for change in ({"task_ids": ()}, {"acceptance_criteria": []}, {"requirements": []}):
        with pytest.raises(ValueError):
            completion(control, **change)
    spec = specification(control)
    spec.acceptance_criteria.append("mutated")
    with pytest.raises(EpicCompletionError, match="CONFIGURATION_INVALID"):
        EpicCompletionService(control.epic_start, control.scheduler, spec)
    service = completion(control)
    service.spec.acceptance_criteria.append("not registered")
    assert service.spec.acceptance_criteria == control.epic_start.spec.acceptance_criteria


@pytest.mark.parametrize("timing", ["unknown", "known"])
def test_crash_step_checkpoint_preserves_intent_without_repeating_process(
    delivered, monkeypatch, timing
):
    control, actor, _, _ = delivered
    service = completion(control)
    original = service._save
    calls = []

    def crash(op, **changes):
        if op.kind == service.STEP and not calls:
            calls.append(op.id)
            if timing == "known":
                original(op, **changes)
            raise RuntimeError("injected checkpoint interruption")
        return original(op, **changes)

    monkeypatch.setattr(service, "_save", crash)
    with pytest.raises(RuntimeError, match="injected"):
        asyncio.run(service.complete(actor, "e", key="restart"))
    steps = control.store.get_operations("e", kind=service.STEP)
    assert len(steps) == 1
    assert steps[0].status == ("PENDING" if timing == "unknown" else "SUCCEEDED")
    other = completion(control)
    if timing == "unknown":
        with pytest.raises(EpicCompletionError, match="PROCESS_UNKNOWN"):
            asyncio.run(other.complete(actor, "e", key="restart"))
        assert len(control.store.get_operations("e", kind=service.STEP)) == 1
    else:
        assert asyncio.run(other.complete(actor, "e", key="restart")).status == "SUCCEEDED"
        assert len(control.store.get_operations("e", kind=service.STEP)) == 3


def test_new_failed_verification_invalidates_old_ready_and_leaves_active(delivered):
    control, actor, _, _ = delivered
    old = completion(control)
    asyncio.run(old.complete(actor, "e", key="old"))
    newer = completion(control, build=dict(argv=(sys.executable, "-c", "raise SystemExit(9)")))
    assert asyncio.run(newer.complete(actor, "e", key="new")).status == "FAILED"
    assert control.store.get_epic("e").status == EpicState.ACTIVE
    with pytest.raises(EpicCompletionError):
        asyncio.run(old.require_current(actor, "e", key="old"))


def test_modified_step_invalidates_persisted_ready_receipt(delivered):
    control, actor, _, _ = delivered
    service = completion(control)
    asyncio.run(service.complete(actor, "e", key="ready"))
    step = control.store.get_operations("e", kind=service.STEP)[0]
    control.store.update_operation(step.model_copy(update={"status": "FAILED"}))
    with pytest.raises(EpicCompletionError, match="STEP_CHANGED"):
        asyncio.run(service.require_current(actor, "e", key="ready"))


def test_not_done_empty_runtime_and_unregistered_connection_deny_completion(setup):  # noqa: F811
    control, runtime, actor, _, _ = setup
    service = completion(control)
    with pytest.raises(EpicCompletionError, match="TASK_SCOPE_CHANGED"):
        asyncio.run(service.complete(actor, "e", key="empty"))
    started = asyncio.run(
        runtime.call_async(
            "task_start",
            dict(project_id="p", epic_run_id="e", task=control.scheduler.selection.specs[T1]),
        )
    )
    assert started.ok
    with pytest.raises(EpicCompletionError):
        asyncio.run(service.complete(actor, "e", key="working"))
    registration = control.store.get_operation("p", control.epic_start.KIND, "e")
    control.store.update_operation(registration.model_copy(update={"status": "PENDING"}))
    with pytest.raises(RuntimeError):
        asyncio.run(service.complete(actor, "e", key="unregistered"))
    assert not control.store.get_operations("e", kind=service.STEP)


def test_current_physical_worker_evidence_not_just_saved_stop(delivered):
    control, actor, _, sessions = delivered
    service = completion(control)
    stop = control.store.get_operations("e", kind="stop_runtime")[0]
    proof = stop.result["process_proof"]
    sessions.agents["returned-process"] = {"pid": proof["groups"][0]}
    with pytest.raises(RuntimeError):
        asyncio.run(service.complete(actor, "e", key="not-physically-stopped"))
    assert not control.store.get_operations("e", kind=service.STEP)
