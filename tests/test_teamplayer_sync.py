"""Real Git/SQLite services with an injected board transport; native MCP is probed separately."""

import asyncio
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_task_merge_service import (  # noqa: F401
    changes_setup,
    deliver,
    report_setup,
    review_setup,
    start_setup,
)
from test_task_merge_service import setup as delivery_setup  # noqa: F401

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.application.epic_integration_service import EpicIntegrationService
from orchestrator.application.state_service import StateService
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.domain.policy import Role, VerifiedFacts
from orchestrator.domain.states import EpicState
from orchestrator.persistence.store import StateStore

PROJECT, USER, EPIC, TASK = (str(uuid4()) for _ in range(4))


def run(awaitable):
    return asyncio.run(awaitable)


class Board:
    def __init__(self, service, worker):
        op = service.store.get_operation("p", "task_start", "T")
        spec = op.result["spec"]
        self.epic = dict(
            id=EPIC,
            projectId=PROJECT,
            name="Explicit fixture",
            description="Manual epic",
            status="Pending",
            version=1,
        )
        self.task = dict(
            taskId=TASK,
            projectId=PROJECT,
            epicId=EPIC,
            name=spec["name"],
            description="Manual scope: Åäö / 東京\nPreserve this exactly.",
            status="Pending",
            version=1,
            taskType="Feature",
            priority=2,
            acceptanceCriteria=spec["acceptance_criteria"],
            dependencyTaskIds=[],
            traceabilityArtifactIds=[],
            executionOwnerKind="User",
            responsibleUserId=USER,
            responsibleAgentId=str(uuid4()),
        )
        self.calls, self.writes, self.applied = [], [], []
        self.failure = None
        self.grant, self.user = "Write", USER
        self.mutate = None

    async def read(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        if name == "get_me":
            return {"userId": self.user}
        if name == "list_projects":
            return [{"projectId": PROJECT, "name": "Fixture", "access": self.grant}]
        if name == "list_epics":
            return [deepcopy(self.epic)]
        if name == "get_task":
            assert arguments["taskId"] == TASK
            return deepcopy(self.task)
        if name == "list_tasks":
            return {"projectId": PROJECT, "tasks": [deepcopy(self.task)]}
        raise AssertionError(name)

    async def write(self, name, arguments):
        self.writes.append((name, deepcopy(arguments)))
        arg = arguments.get("request", arguments)
        node = self.epic if name == "update_epic_status" else self.task
        assert arg["projectId"] == PROJECT
        assert arg.get("epicId", arg.get("taskId")) == node.get("id", node.get("taskId"))
        if self.failure == (name, "before"):
            self.failure = None
            raise TeamPlayerError("TEAMPLAYER_WRITE_OUTCOME_UNKNOWN")
        if self.failure == (name, "conflict"):
            self.failure = None
            node["version"] += 1
            node["description"] += "\nConcurrent manual edit."
            raise TeamPlayerError("TEAMPLAYER_VERSION_CONFLICT", current_version=node["version"])
        if arg["version"] != node["version"]:
            raise TeamPlayerError("TEAMPLAYER_VERSION_CONFLICT", current_version=node["version"])
        node.update({k: v for k, v in arg.items() if k in {"status", "description"}})
        node["version"] += 1
        self.applied.append((name, deepcopy(arg)))
        if self.mutate:
            mutate, self.mutate = self.mutate, None
            mutate()
        if self.failure == (name, "after"):
            self.failure = None
            raise TeamPlayerError("TEAMPLAYER_WRITE_OUTCOME_UNKNOWN")
        return deepcopy(node)


@pytest.fixture
def setup(delivery_setup):  # noqa: F811
    s, actor, worker, herdr, history, processes = delivery_setup
    board = Board(s, worker)
    sync = TeamPlayerSyncService(
        s.settings,
        s.store,
        board,
        project_id="p",
        external_project_id=PROJECT,
        user_id=USER,
        scopes={actor.epic_run_id: ("T",)},
        codex=history,
        processes=processes,
    )
    coordinator = actor.model_copy(update={"role": Role.COORDINATOR})
    run(sync.bind_epic(coordinator, actor.epic_run_id, EPIC))
    run(sync.bind_task(actor, worker.task_run_id, TASK))
    board.calls.clear()
    yield sync, board, s, actor, worker, coordinator, herdr, history, processes


def invariant(s, herdr):
    epic = s.store.get_epic("epic-run")
    kinds = ("merge_task_to_epic", "task_delivery_test", "stop_runtime", "dispatch_assignment")
    return (
        s.git_manager.git.head(epic.branch),
        s.git_manager.git.head("main"),
        tuple(len(s.store.get_operations(epic.id, kind=k)) for k in kinds),
        herdr.starts,
        herdr.exits,
        len(herdr.sent),
    )


def test_confirmed_assignment_active_history_preserves_scope_and_replays_once(setup):
    sync, b, s, a, w, _, h, _, _ = setup
    original = b.task["description"]
    before = invariant(s, h)
    answer = run(sync.sync_task(a, w.task_run_id))
    assert answer["status"] == "SYNCED" and b.task["status"] == "InProgress"
    assert b.task["description"].startswith(original + "\n\n")
    assert "assignment_id" in b.task["description"] and "session_id" in b.task["description"]
    assert run(sync.sync_task(a, w.task_run_id))["status"] == "EXISTING"
    assert len(b.applied) == 2 and b.task["description"].count("<!-- herdr-event:") == 1
    assert invariant(s, h) == before


@pytest.mark.parametrize("tool", ["update_task_status", "update_task_details"])
@pytest.mark.parametrize("when", ["before", "after", "conflict"])
def test_network_loss_or_known_cas_conflict_reopens_only_missing_sync_step(setup, tool, when):
    sync, b, s, a, w, _, h, history, p = setup
    assert deliver(s, a, w)["status"] == "DONE"
    original = b.task["description"]
    before = invariant(s, h)
    b.failure = tool, when
    pending = run(sync.sync_task(a, w.task_run_id))
    assert pending["status"] == "PENDING"
    with StateStore(s.settings.sqlite_path) as db:
        reopened = TeamPlayerSyncService(
            s.settings,
            db,
            b,
            project_id="p",
            external_project_id=PROJECT,
            user_id=USER,
            codex=history,
            processes=p,
        )
        answer = run(reopened.sync_task(a, w.task_run_id))
        assert answer["status"] == "SYNCED" and answer["operation_id"] == pending["operation_id"]
        assert run(reopened.sync_task(a, w.task_run_id))["status"] == "EXISTING"
    assert b.task["status"] == "Done" and b.task["description"].startswith(original)
    assert "test_id" in b.task["description"] and "stop_id" in b.task["description"]
    if when == "conflict":
        assert "Concurrent manual edit." in b.task["description"]
    assert [n for n, _ in b.applied].count("update_task_status") == 1
    assert [n for n, _ in b.applied].count("update_task_details") == 1
    assert invariant(s, h) == before


@pytest.mark.parametrize("tool", ["update_task_status", "update_task_details"])
def test_unknown_outcome_with_later_manual_change_requires_reconciliation(setup, tool):
    sync, b, s, a, w, _, h, _, _ = setup
    deliver(s, a, w)
    b.failure = tool, "before"
    assert run(sync.sync_task(a, w.task_run_id))["status"] == "PENDING"
    b.task["version"] += 1
    b.task["description"] += "\nManual newer version"
    before = invariant(s, h), len(b.writes)
    answer = run(sync.sync_task(a, w.task_run_id))
    assert answer["status"] == "PENDING" and "OUTCOME_DIVERGED" in answer["reason"]
    assert (invariant(s, h), len(b.writes)) == before


@pytest.mark.parametrize("attack", ["worker", "coordinator", "foreign-project", "foreign-epic"])
def test_wrong_principal_denied_before_journal_or_network(setup, attack):
    sync, b, s, a, w, c, _, _, _ = setup
    bad = {
        "worker": w,
        "coordinator": c,
        "foreign-project": a.model_copy(update={"project_id": "other"}),
        "foreign-epic": a.model_copy(update={"epic_run_id": "other"}),
    }[attack]
    with pytest.raises(TeamPlayerError, match="DENIED"):
        run(sync.sync_task(bad, w.task_run_id))
    assert (
        not b.calls and not b.writes and not s.store.get_operations(a.epic_run_id, kind=sync.KIND)
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("responsibleUserId", str(uuid4())),
        ("executionOwnerKind", "Agent"),
        ("name", "Edited scope"),
        ("acceptanceCriteria", ["Different acceptance"]),
        ("dependencyTaskIds", [str(uuid4())]),
        ("epicId", str(uuid4())),
    ],
)
def test_external_identity_owner_and_contract_must_still_match(setup, field, value):
    sync, b, _, a, w, _, _, _, _ = setup
    b.task[field] = value
    answer = run(sync.sync_task(a, w.task_run_id))
    assert answer["status"] == "PENDING" and not b.writes


@pytest.mark.parametrize(
    "attack", ["ack", "test", "merge", "stop", "alive", "event", "task-commit"]
)
def test_done_requires_current_actual_delivery_test_stop_and_domain_event(setup, attack):
    sync, b, s, a, w, _, _, history, processes = setup
    deliver(s, a, w)
    if attack in {"test", "merge", "stop"}:
        kind = {
            "test": "task_delivery_test",
            "merge": "merge_task_to_epic",
            "stop": "stop_runtime",
        }[attack]
        op = s.store.get_operations(a.epic_run_id, kind=kind)[0]
        s.store.update_operation(op.model_copy(update={"status": "PENDING"}))
    elif attack == "ack":
        # A Done merge already contains the reviewed ACK-derived provenance; it does
        # not require a running Codex server or re-query stopped history.
        history.read_thread = lambda *args: (_ for _ in ()).throw(RuntimeError("offline"))
        assert run(sync.sync_task(a, w.task_run_id))["status"] == "SYNCED"
        return
    elif attack == "alive":
        processes.alive = True
    elif attack == "event":
        s.store.db.execute("DELETE FROM transition_events WHERE event_id LIKE 'delivery-done:%'")
    elif attack == "task-commit":
        t = s.store.get_task(w.task_run_id)
        (Path(t.worktree_path) / "unexpected.txt").write_text("Changed after approval")
        s.git_manager.git.run("add", ".", cwd=t.worktree_path)
        s.git_manager.git.run("commit", "-m", "Changed", cwd=t.worktree_path)
    with pytest.raises(TeamPlayerError, match="SOURCE_UNVERIFIED"):
        run(sync.sync_task(a, w.task_run_id))
    assert not b.writes


def test_task_attention_retains_session_branch_worktree_and_epic_active(setup):
    sync, b, s, a, w, c, h, _, _ = setup
    task = s.store.get_task(w.task_run_id)
    stopped = s.lifecycle.stop_task(
        a, task.id, key="input", reason="Missing test resource; operator supplies file."
    )
    assert stopped.status == "SUCCEEDED" and stopped.result["stage"] == "STOPPED"
    alias = s.lifecycle.stop_task(
        a, task.id, key="alias-input", reason="Same already parked runtime"
    )
    assert alias.result["prior_stop_id"] == stopped.id and h.exits == 1
    assert (
        run(sync.sync_task(a, task.id))["status"] == "SYNCED" and b.task["status"] == "NeedsInput"
    )
    assert "Missing test resource" in b.task["description"]
    assert (
        run(sync.sync_epic(c, a.epic_run_id))["status"] == "SYNCED"
        and b.epic["status"] == "InProgress"
    )
    now = s.store.get_task(task.id)
    assert now.codex_session_id == task.codex_session_id and now.branch == task.branch
    assert now.worker_slot is None and Path(now.worktree_path).exists() and h.exits == 1


def test_newer_local_event_supersedes_unfinished_active_mirror_without_repeating_merge(setup):
    sync, b, s, a, w, _, h, _, _ = setup
    b.failure = "update_task_status", "before"
    old = run(sync.sync_task(a, w.task_run_id))
    assert old["status"] == "PENDING"
    deliver(s, a, w)
    before = invariant(s, h)
    assert (
        run(sync.sync_task(a, w.task_run_id))["status"] == "SYNCED" and b.task["status"] == "Done"
    )
    operations = s.store.get_operations(a.epic_run_id, kind=sync.KIND)
    assert {o.status for o in operations} == {"SUPERSEDED", "SUCCEEDED"}
    assert invariant(s, h) == before


@pytest.mark.parametrize("status", ["NeedsApproval", "Cancelled", "Paused", "NeedsReview"])
def test_manual_board_decisions_are_not_automatically_overwritten(setup, status):
    sync, b, _, a, w, _, _, _, _ = setup
    b.task.update(status=status, version=2)
    answer = run(sync.sync_task(a, w.task_run_id))
    assert answer["status"] == "PENDING" and not b.writes


def test_history_removed_or_duplicated_after_success_is_not_blindly_recreated(setup):
    sync, b, _, a, w, _, _, _, _ = setup
    answer = run(sync.sync_task(a, w.task_run_id))
    text = b.task["description"]
    b.task["description"] += "\n" + sync._block(
        sync.store.get_operations(a.epic_run_id, kind=sync.KIND)[0]
    )
    assert run(sync.sync_task(a, w.task_run_id))["reason"] == "TEAMPLAYER_HISTORY_EVENT_CONFLICT"
    b.task["description"] = "Manual removed history"
    assert run(sync.sync_task(a, w.task_run_id))["status"] == "PENDING"
    assert len(b.writes) == 2
    b.task["description"] = text
    assert run(sync.sync_task(a, w.task_run_id))["operation_id"] == answer["operation_id"]


def test_lock_prevents_parallel_external_writes_and_releases_after_failure(setup):
    sync, b, _, a, w, _, _, _, _ = setup
    with sync._lock():
        with pytest.raises(TeamPlayerError, match="BUSY"):
            run(sync.sync_task(a, w.task_run_id))
    assert not b.writes
    assert run(sync.sync_task(a, w.task_run_id))["status"] == "SYNCED"


def finish_epic(sync, s, a, w, c, *, criteria=True, final=True):
    deliver(s, a, w)
    manager = EpicIntegrationService(
        s.settings, s.store, expected_task_ids=("T",), test_command=s.settings.worker_test_command
    )
    test = manager.verify_epic(c, a.epic_run_id, key="aggregate")
    review = manager.register_epic_review(
        c,
        a.epic_run_id,
        verification_key=test.idempotency_key,
        key="operator-review",
        approved=True,
        verified_criteria=s.settings.review_context.acceptance_criteria if criteria else None,
    )
    merge = manager.merge_epic_to_main(c, a.epic_run_id, key="actual-main")
    if final:
        assert (
            manager.verify_main_merge(c, a.epic_run_id, merge_key="actual-main", key="final").status
            == "SUCCEEDED"
        )
    # Sequential trusted test operator records Done from actual Git/test facts;
    # F37's completion service has not yet been implemented.
    StateService(s.store).transition_epic(
        a.epic_run_id,
        EpicState.DONE,
        expected=EpicState.MERGING,
        event_id="operator-epic-done",
        actor=c,
        facts=VerifiedFacts(
            source_commit=review.result["source_commit"],
            target_commit=review.result["target_commit"],
            merge_commit=merge.result["merge_commit"],
            verification_commit=merge.result["merge_commit"],
            tests_passed=True,
            scope_complete=True,
            review_approved=True,
        ),
    )


def test_epic_all_tasks_done_remains_active_until_actual_main_review_and_final_verification(setup):
    sync, b, s, a, w, c, _, _, _ = setup
    deliver(s, a, w)
    assert run(sync.sync_task(a, w.task_run_id))["status"] == "SYNCED"
    assert run(sync.sync_epic(c, a.epic_run_id))["status"] == "SYNCED"
    assert b.epic["status"] == "InProgress"
    finish_epic(sync, s, a, w, c)
    assert (
        run(sync.sync_epic(c, a.epic_run_id))["status"] == "SYNCED" and b.epic["status"] == "Done"
    )
    before = len(b.writes)
    assert run(sync.sync_epic(c, a.epic_run_id))["status"] == "EXISTING" and len(b.writes) == before


@pytest.mark.parametrize("missing", ["criteria", "final", "native-task-done", "scope"])
def test_epic_done_is_rejected_when_acceptance_final_test_or_scope_missing(setup, missing):
    sync, b, s, a, w, c, _, _, _ = setup
    finish_epic(sync, s, a, w, c, criteria=missing != "criteria", final=missing != "final")
    if missing != "native-task-done":
        run(sync.sync_task(a, w.task_run_id))
    if missing == "scope":
        sync.evidence.scopes[a.epic_run_id] = ("T", "missing-task")
    if missing in {"criteria", "final", "scope"}:
        with pytest.raises(TeamPlayerError, match="SOURCE_UNVERIFIED"):
            run(sync.sync_epic(c, a.epic_run_id))
    else:
        assert run(sync.sync_epic(c, a.epic_run_id))["status"] == "PENDING"
    assert b.epic["status"] == "Pending" and not any(n == "update_epic_status" for n, _ in b.writes)


def test_planned_epic_mirror_requires_actual_f05_creation_and_no_start_event(setup):
    from orchestrator.application.worktree_service import WorktreeService

    sync, b, s, _, _, c, _, _, _ = setup
    new = c.model_copy(update={"epic_run_id": "epic-two"})
    epic = WorktreeService(s.settings, s.store).create_epic_worktree(
        new, epic_id="E2", run_id="epic-two"
    )
    assert epic.status == EpicState.PLANNED
    b.epic["id"] = str(uuid4())
    run(sync.bind_epic(new, "epic-two", b.epic["id"]))
    assert (
        run(sync.sync_epic(new, "epic-two"))["status"] == "SYNCED" and b.epic["status"] == "Pending"
    )
    assert not b.writes


@pytest.mark.parametrize("checkpoint", ["STATUS_CONFIRMED", "SYNCED"])
def test_lost_local_checkpoint_after_native_effect_reconciles_without_duplicate(
    setup, monkeypatch, checkpoint
):
    sync, b, s, a, w, _, h, _, _ = setup
    deliver(s, a, w)
    before = invariant(s, h)
    original = sync._save
    lost = False

    def save(*args, **kwargs):
        nonlocal lost
        if kwargs.get("stage") == checkpoint and not lost:
            lost = True
            raise RuntimeError("Process lost before SQLite checkpoint")
        return original(*args, **kwargs)

    monkeypatch.setattr(sync, "_save", save)
    assert run(sync.sync_task(a, w.task_run_id))["status"] == "PENDING" and lost
    assert run(sync.sync_task(a, w.task_run_id))["status"] == "SYNCED"
    assert len(b.applied) == 2 and b.task["description"].count("<!-- herdr-event:") == 1
    assert invariant(s, h) == before


def test_actual_mcp_connection_scopes_sync_and_rejects_agent_chosen_status(setup):
    import io

    from mcp import Client

    from orchestrator.application.runtime_service import RuntimeService
    from orchestrator.event_log import EventLog
    from orchestrator.mcp.server import create_server

    sync, b, s, a, w, c, _, _, _ = setup

    async def exercise(actor):
        runtime = RuntimeService(
            s.store, actor, EventLog(stream=io.StringIO()), teamplayer_sync=sync
        )
        async with Client(create_server(runtime)) as client:
            args = {"project_id": "p", "task_run_id": w.task_run_id}
            result = await client.call_tool("set_task_status", args)
            assert result.structured_content["code"] == ("OK" if actor == a else "FORBIDDEN")
            injected = await client.call_tool(
                "set_task_status", args | {"status": "Done", "role": "Integration"}
            )
            assert injected.structured_content["code"] in {"INVALID_ARGUMENT", "FORBIDDEN"}
            if actor == c:
                result = await client.call_tool(
                    "set_epic_status", {"project_id": "p", "epic_run_id": a.epic_run_id}
                )
                assert result.structured_content["code"] == "OK"
            denied = await client.call_tool("set_task_status", args | {"project_id": "other"})
            assert denied.structured_content["code"] == "FORBIDDEN"

    run(exercise(w))
    assert not b.writes
    run(exercise(a))
    run(exercise(c))
    assert b.task["status"] == "InProgress" and b.epic["status"] == "InProgress"


def test_partial_epic_acceptance_cannot_register_a_complete_review(setup):
    from orchestrator.application.git_integration_service import IntegrationError

    _, _, s, a, w, c, _, _, _ = setup
    deliver(s, a, w)
    manager = EpicIntegrationService(
        s.settings, s.store, expected_task_ids=("T",), test_command=s.settings.worker_test_command
    )
    assert manager.verify_epic(c, a.epic_run_id, key="aggregate").status == "SUCCEEDED"
    with pytest.raises(IntegrationError, match="complete registered criteria"):
        manager.register_epic_review(
            c,
            a.epic_run_id,
            verification_key="aggregate",
            key="partial",
            approved=True,
            verified_criteria=["Different or incomplete acceptance"],
        )
    assert not s.store.get_operations(a.epic_run_id, kind="epic_review")
    assert s.store.get_epic(a.epic_run_id).status == EpicState.ACTIVE


def test_done_stop_alias_does_not_rewrite_done_event_or_repeat_native_history(setup):
    sync, b, s, a, w, _, h, _, _ = setup
    deliver(s, a, w)
    task = s.store.get_task(w.task_run_id)
    parent = s.store.get_operation("p", "task_merge", "deliver")
    alias = s.lifecycle.stop_task(a, task.id, key="already-delivered", reason="Already inactive")
    assert alias.result["prior_stop_id"] == parent.result["stop_id"] and h.exits == 1
    assert run(sync.sync_task(a, task.id))["status"] == "SYNCED" and b.task["status"] == "Done"
    assert run(sync.sync_task(a, task.id))["status"] == "EXISTING" and len(b.applied) == 2
