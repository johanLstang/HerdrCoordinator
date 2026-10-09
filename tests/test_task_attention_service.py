"""F31 real Git/SQLite chains; board, runtime and process observers are controlled."""

import asyncio
import io
from pathlib import Path

import pytest
from mcp import Client
from test_runtime_lifecycle import FakeProcesses
from test_task_start import setup as start_setup  # noqa: F401
from test_teamplayer_sync import EPIC, PROJECT, TASK, USER, Board
from test_worker_report_service import change_report
from test_worker_report_service import setup as report_setup  # noqa: F401

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_attention_service import AttentionError, TaskAttentionService
from orchestrator.application.task_review_service import TaskReviewService
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.domain.policy import Role
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.states import Kanban, TaskState
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def setup(report_setup, request):  # noqa: F811
    reports, worker, report, herdr, history, actor = report_setup
    mode = getattr(request, "param", "worker")
    settings = reports.settings
    decision = None
    if mode == "review":
        reports.collect(worker, worker.task_run_id)
        settings = settings.model_copy(
            update={
                "review_context": EpicReviewSpec(
                    version=1,
                    project_id="p",
                    epic_id="E",
                    requirements=["Retain result"],
                    acceptance_criteria=["Tests precede review"],
                    sources=["README.md"],
                )
            }
        )
        package = TaskReviewService(settings, reports.store).request(
            actor, worker.task_run_id, key="attention-review"
        )
        decision = dict(
            result="NEEDS_INPUT",
            context_id=package["context"]["context_id"],
            reason="Retention policy is unspecified",
            input_required="How many days to retain?",
            responsible_role="User",
        )
    else:
        change_report(
            history,
            report
            | {
                "status": "BLOCKED",
                "commit": None,
                "reason": "Fixture value unavailable",
                "input_required": "Provide the fixture retention days",
            },
        )
    board = Board(reports, worker)
    processes = FakeProcesses()
    herdr.exits = 0

    def exit_agent(name):
        assert name in herdr.agents
        herdr.exits += 1
        del herdr.agents[name]
        processes.alive = False

    herdr.exit_agent = exit_agent
    sync = TeamPlayerSyncService(
        settings,
        reports.store,
        board,
        project_id="p",
        external_project_id=PROJECT,
        user_id=USER,
        codex=history,
        processes=processes,
    )
    run(
        sync.bind_epic(actor.model_copy(update={"role": Role.COORDINATOR}), actor.epic_run_id, EPIC)
    )
    run(sync.bind_task(actor, worker.task_run_id, TASK))
    attention = TaskAttentionService(
        settings, reports.store, sync, herdr, history, processes=processes
    )
    yield attention, actor, worker, board, herdr, history, processes, decision


def park(s, a, w, decision=None):
    return (
        run(s.block_review(a, w.task_run_id, decision))
        if decision
        else run(s.park_blocked(a, w.task_run_id))
    )


@pytest.mark.parametrize("setup", ["worker", "review"], indirect=True)
def test_complete_blocker_sync_before_physical_park_then_exact_slot_release(setup):
    s, a, w, b, h, _, p, decision = setup
    before = s.store.get_task(w.task_run_id)
    head = s.git.head(before.branch)
    original_exit = h.exit_agent

    def observed_exit(name):
        current = s.store.get_task(before.id)
        assert current.internal_status == TaskState.BLOCKED and current.worker_slot == 1
        assert (
            b.task["status"] == "NeedsInput" and "Responsible role: User" in b.task["description"]
        )
        journal = s.store.get_operations(a.epic_run_id, kind=s.KIND)
        assert len(journal) == 1 and journal[0].result["reserved_slot"] == 1
        assert journal[0].result["details"]["input_required"] in b.task["description"]
        assert not s.store.get_operations(a.epic_run_id, kind="resume_runtime")
        original_exit(name)

    h.exit_agent = observed_exit
    result = park(s, a, w, decision)
    assert result["stage"] == "PARKED_AND_SYNCED"
    t = s.store.get_task(before.id)
    assert t.internal_status == TaskState.PARKED and t.kanban_status == Kanban.ATTENTION
    assert t.resume_state == (TaskState.REVIEWING if decision else TaskState.WORKING)
    assert t.worker_slot is None and h.exits == 1 and not p.alive
    assert (t.branch, t.worktree_path, t.codex_session_id, t.current_commit) == (
        before.branch,
        before.worktree_path,
        before.codex_session_id,
        before.current_commit,
    )
    assert Path(t.worktree_path).is_dir() and s.git.head(t.branch) == head
    assert b.task["executionOwnerKind"] == "User" and b.task["responsibleUserId"] == USER
    assert "stop_id" in b.task["description"] and not s.store.get_reviews(t.id)
    assert not s.store.get_operations(a.epic_run_id, kind="task_merge")


@pytest.mark.parametrize("setup", ["worker", "review"], indirect=True)
def test_repeat_and_reopen_only_existing_sync_never_collect_dead_runtime_or_stop_again(setup):
    s, a, w, b, h, history, p, decision = setup
    first = park(s, a, w, decision)
    counts = (len(b.applied), h.starts, h.exits, len(h.sent))
    with StateStore(s.settings.sqlite_path) as db:
        sync = TeamPlayerSyncService(
            s.settings,
            db,
            b,
            project_id="p",
            external_project_id=PROJECT,
            user_id=USER,
            codex=history,
            processes=p,
        )
        again = TaskAttentionService(s.settings, db, sync, h, history, processes=p)
        again.lifecycle.stop_task = lambda *a, **kw: pytest.fail("repeated physical stop")
        again.reports.collect = lambda *a, **kw: pytest.fail("read dead runtime")
        assert park(again, a, w, decision) == first
        assert len(db.get_operations(a.epic_run_id, kind=s.KIND)) == 1
        assert len(db.get_operations(a.epic_run_id, kind="stop_runtime")) == 1
    assert (len(b.applied), h.starts, h.exits, len(h.sent)) == counts


def test_previously_mirrored_legacy_block_is_augmented_without_rewriting_history(setup):
    s, a, w, b, _, _, _, _ = setup
    s.reports.collect(w, w.task_run_id, expected_status="BLOCKED")
    assert run(s.sync.sync_task(a, w.task_run_id))["status"] == "SYNCED"
    previous = b.task["description"]
    result = park(s, a, w)
    assert result["stage"] == "PARKED_AND_SYNCED"
    assert b.task["description"].startswith(previous + "\n\n")
    assert b.task["description"].count("<!-- herdr-event:") == 3


def test_unknown_physical_stop_retains_slot_then_observed_recovery_releases_once(setup):
    s, a, w, b, h, _, p, _ = setup
    p.lingering = True
    result = park(s, a, w)
    assert result["stage"] == "PARK_PENDING" and result["reason"] == "STOP_UNCONFIRMED"
    t = s.store.get_task(w.task_run_id)
    assert t.internal_status == TaskState.BLOCKED and t.worker_slot == 1
    assert b.task["status"] == "NeedsInput" and h.exits == 1
    p.lingering = False
    recovered = park(s, a, w)
    assert recovered["stage"] == "PARKED_AND_SYNCED" and h.exits == 1
    assert s.store.get_task(t.id).worker_slot is None
    assert len(s.store.get_operations(a.epic_run_id, kind="stop_runtime")) == 1


def test_network_outage_never_prevents_park_and_retry_only_missing_mirror(setup):
    s, a, w, b, h, _, _, _ = setup
    original = b.read

    async def offline(name, arguments):
        raise TeamPlayerError("TEAMPLAYER_TRANSPORT_UNAVAILABLE")

    b.read = offline
    result = park(s, a, w)
    assert result["stage"] == "SYNC_PENDING"
    assert s.store.get_task(w.task_run_id).worker_slot is None and h.exits == 1
    outbox = s.store.get_operations(a.epic_run_id, kind="teamplayer_sync")
    assert len(outbox) == 2 and outbox[0].status == "SUPERSEDED"
    b.read = original
    s.lifecycle.stop_task = lambda *a, **kw: pytest.fail("repeat stop after network recovery")
    assert park(s, a, w)["stage"] == "PARKED_AND_SYNCED"
    assert h.exits == 1 and b.task["status"] == "NeedsInput"


@pytest.mark.parametrize("role", [Role.WORKER, Role.COORDINATOR])
def test_wrong_role_before_block_journal_network_or_stop(setup, role):
    s, a, w, b, h, _, _, _ = setup
    before = len(b.calls)
    bad = a.model_copy(update={"role": role, "task_run_id": w.task_run_id})
    with pytest.raises(AttentionError, match="SCOPE_DENIED"):
        park(s, bad, w)
    assert len(b.calls) == before and h.exits == 0
    assert not s.store.get_operations(a.epic_run_id, kind=s.KIND)
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.WORKING


def test_foreign_scope_and_generation_change_never_release_or_stop(setup):
    s, a, w, b, h, _, _, _ = setup
    with pytest.raises(AttentionError):
        park(s, a.model_copy(update={"epic_run_id": "foreign"}), w)
    assert h.exits == 0
    p = s.store.get_operation("p", "start_runtime", w.task_run_id)
    original = b.write

    async def change_generation(name, args):
        answer = await original(name, args)
        s.store.update_operation(
            p.model_copy(update={"result": p.result | {"generation": "other"}})
        )
        return answer

    b.write = change_generation
    with pytest.raises(AttentionError, match="INTENT_CHANGED"):
        park(s, a, w)
    assert h.exits == 0 and s.store.get_task(w.task_run_id).worker_slot == 1


@pytest.mark.parametrize("setup", ["review"], indirect=True)
@pytest.mark.parametrize("change", ["context", "head", "input"])
def test_stale_or_invalid_review_decision_cannot_block_or_stop(setup, change):
    s, a, w, _, h, _, _, decision = setup
    if change == "context":
        decision = decision | {"context_id": "f" * 64}
    elif change == "input":
        decision = decision | {"input_required": " "}
    else:
        t = s.store.get_task(w.task_run_id)
        (Path(t.worktree_path) / "new.txt").write_text("Not reviewed\n")
        s.git.run("add", ".", cwd=t.worktree_path)
        s.git.run("commit", "-m", "new unreviewed work", cwd=t.worktree_path)
    with pytest.raises(AttentionError):
        park(s, a, w, decision)
    assert h.exits == 0 and s.store.get_task(w.task_run_id).internal_status == TaskState.REVIEWING
    assert not s.store.get_operations(a.epic_run_id, kind=s.KIND)


@pytest.mark.parametrize("setup", ["review"], indirect=True)
def test_historical_review_block_still_mirrors_after_independent_epic_advances(setup):
    s, a, w, b, _, _, _, decision = setup
    original = b.read

    async def offline(*args):
        from orchestrator.adapters.teamplayer_mcp import TeamPlayerError

        raise TeamPlayerError("TEAMPLAYER_TRANSPORT_UNAVAILABLE")

    b.read = offline
    assert park(s, a, w, decision)["stage"] == "SYNC_PENDING"
    epic = s.store.get_epic(a.epic_run_id)
    (Path(epic.worktree_path) / "independent.txt").write_text("Unrelated advance\n")
    s.git.run("add", ".", cwd=epic.worktree_path)
    s.git.run("commit", "-m", "independent epic work", cwd=epic.worktree_path)
    s.store.update_run_metadata(epic.model_copy(update={"current_commit": s.git.head(epic.branch)}))
    b.read = original
    assert park(s, a, w, decision)["stage"] == "PARKED_AND_SYNCED"
    assert "How many days to retain?" in b.task["description"]
    assert s.store.get_task(w.task_run_id).approved_source_commit is None


def test_attention_lock_rejects_overlapping_request_before_external_effect(setup):
    s, a, w, b, h, _, _, _ = setup
    before = len(b.calls)
    with s._lock(w.task_run_id):
        with pytest.raises(AttentionError, match="ATTENTION_BUSY"):
            park(s, a, w)
    assert len(b.calls) == before and h.exits == 0
    assert not s.store.get_operations(a.epic_run_id, kind=s.KIND)


def test_actual_sdk_mcp_strict_scope_and_no_raw_blocker_in_response_or_logs(setup):
    s, a, w, _, h, _, _, _ = setup
    stream = io.StringIO()
    runtime = RuntimeService(s.store, a, EventLog(stream=stream), task_attention=s)

    async def exercise():
        async with Client(create_server(runtime)) as client:
            listed = await client.list_tools()
            assert {t.name for t in listed.tools} == {
                "runtime_status",
                "policy_check",
                "task_park_blocked",
                "task_block_review",
            }
            target = {"project_id": "p", "task_run_id": w.task_run_id}
            invalid = await client.call_tool("task_park_blocked", target | {"role": "Integration"})
            assert invalid.is_error and invalid.structured_content["code"] == "INVALID_ARGUMENT"
            foreign = await client.call_tool(
                "task_park_blocked", target | {"task_run_id": "foreign"}
            )
            assert foreign.is_error and foreign.structured_content["code"] == "FORBIDDEN"
            good = await client.call_tool("task_park_blocked", target)
            assert (
                not good.is_error
                and good.structured_content["data"]["stage"] == "PARKED_AND_SYNCED"
            )
            assert "Fixture value" not in str(good.structured_content)

    run(exercise())
    assert h.exits == 1 and "Fixture value" not in stream.getvalue()
    denied = RuntimeService(s.store, w, EventLog(stream=io.StringIO()), task_attention=s)
    assert (
        run(
            denied.call_async(
                "task_park_blocked",
                {
                    "project_id": "p",
                    "task_run_id": w.task_run_id,
                },
            )
        ).code
        == "FORBIDDEN"
    )


def test_lost_result_after_successful_stop_recovers_actual_operation_without_repeat(
    setup, monkeypatch
):
    s, a, w, b, h, _, _, _ = setup
    save = s._save

    def checkpoint(op, **fields):
        if fields.get("stage") == "PARKED":
            raise RuntimeError("simulated checkpoint interruption")
        return save(op, **fields)

    monkeypatch.setattr(s, "_save", checkpoint)
    with pytest.raises(RuntimeError, match="checkpoint interruption"):
        park(s, a, w)
    assert h.exits == 1 and s.store.get_task(w.task_run_id).worker_slot is None
    assert s.store.get_operations(a.epic_run_id, kind="stop_runtime")[0].status == "SUCCEEDED"
    monkeypatch.setattr(s, "_save", save)
    s.lifecycle.stop_task = lambda *a, **kw: pytest.fail("repeat known successful stop")
    assert park(s, a, w)["stage"] == "PARKED_AND_SYNCED"
    assert b.task["status"] == "NeedsInput" and h.exits == 1


def test_mutated_journal_cannot_repeat_or_release_uncertain_slot(setup):
    s, a, w, _, h, _, p, _ = setup
    p.lingering = True
    assert park(s, a, w)["stage"] == "PARK_PENDING"
    op = s.store.get_operations(a.epic_run_id, kind=s.KIND)[0]
    s.store.update_operation(
        op.model_copy(
            update={
                "result": op.result
                | {"details": op.result["details"] | {"input_required": "forged"}},
            }
        )
    )
    p.lingering = False
    with pytest.raises(AttentionError, match="INTENT_CHANGED"):
        park(s, a, w)
    assert s.store.get_task(w.task_run_id).worker_slot == 1 and h.exits == 1


def test_changed_reserved_slot_before_stop_is_rejected_without_release(setup):
    s, a, w, b, h, _, _, _ = setup
    original = b.write

    async def changed(name, args):
        result = await original(name, args)
        task = s.store.get_task(w.task_run_id)
        s.store.update_runtime_metadata(task.model_copy(update={"worker_slot": 2}))
        return result

    b.write = changed
    with pytest.raises(AttentionError, match="INTENT_CHANGED"):
        park(s, a, w)
    assert h.exits == 0 and s.store.get_task(w.task_run_id).worker_slot == 2
    assert not s.store.get_operations(a.epic_run_id, kind="stop_runtime")


def test_complete_long_existing_v1_worker_blocker_is_preserved_without_truncation(setup):
    s, a, w, b, _, history, _, _ = setup
    import json

    report = json.loads(history.turns[-1]["items"][-1]["text"])
    reason, needed = "Observed missing policy. " + "r" * 20000, "Provide policy. " + "i" * 10000
    change_report(history, report | {"reason": reason, "input_required": needed})
    assert park(s, a, w)["stage"] == "PARKED_AND_SYNCED"
    op = s.store.get_operations(a.epic_run_id, kind=s.KIND)[0]
    assert (
        op.result["details"]["reason"] == reason
        and op.result["details"]["input_required"] == needed
    )
    assert reason in b.task["description"] and needed in b.task["description"]
