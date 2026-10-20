"""F42: actual temporary Git/SQLite; explicit simulated native/board boundaries."""

import asyncio

# Fixture injection intentionally shadows imported fixture names.
# ruff: noqa: F811
from pathlib import Path

import pytest
from test_codex_execution import execution_policy  # noqa: F401
from test_coordinator_claim import setup as claims_setup  # noqa: F401
from test_coordinator_runtime import setup as runtime_setup  # noqa: F401
from test_epic_start import setup as epic_setup  # noqa: F401
from test_integration_control import setup as control_setup  # noqa: F401
from test_task_start import setup as startup_setup  # noqa: F401
from test_teamplayer_reader import T1, T2
from test_worker_commit import broker  # noqa: F401

from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.reconciliation_service import (
    IntegrationObserver,
    LifecycleObserver,
    ReconciliationError,
    ReconciliationService,
)
from orchestrator.application.runtime_lifecycle_service import RuntimeLifecycleService
from orchestrator.application.task_scheduler_service import SchedulerError
from orchestrator.domain.coordinator import ProjectOperation
from orchestrator.domain.models import ExternalReference, Operation, TaskRun
from orchestrator.persistence.store import StateStore


def run(awaitable):
    return asyncio.run(awaitable)


def recovery(control, actor, *, db=None):
    s = control.scheduler
    store = db or s.store
    original = control.epic_start
    parent = EpicStartService(
        s.settings,
        store,
        original.herdr,
        original.spec,
        original.principal,
        codex=original.codex,
        processes=original.lifecycle.processes,
    )
    lifecycle = RuntimeLifecycleService(
        s.settings,
        store,
        s.start.runtime.herdr,
        codex=s.merge.lifecycle.codex,
        processes=s.merge.lifecycle.processes,
    )
    return ReconciliationService(
        s.settings,
        store,
        s.selection.reader,
        project_id=actor.project_id,
        observers=lambda r: (
            LifecycleObserver(lifecycle, actor)
            if isinstance(r, TaskRun)
            else IntegrationObserver(parent)
        ),
        codex=lifecycle.codex,
        processes=lifecycle.processes,
    )


def row(report, identity):
    return next(r for r in report["runs"] if r["run_id"] == identity)


def codes(report):
    return {
        f["code"] for r in [report, *report["runs"], *report["operations"]] for f in r["findings"]
    }


def start_one(control, actor):
    s = control.scheduler
    result = run(s.start_selected(actor, actor.epic_run_id, s.selection.specs[T1]))
    return s.store.get_task(result["task"]["id"])


def test_matching_runs_read_only_same_resources_sessions_state_after_reopen(control_setup):
    control, _, a, _, native = control_setup
    s = control.scheduler
    run(s.tick(a, a.epic_run_id))
    before = s.store.project_inventory("p"), s.store.db.total_changes, s.selection.git.worktrees()
    service = recovery(control, a)
    result = run(service.inspect(epic_run_id=a.epic_run_id))
    assert result["scheduling_allowed"] and result["classification"] == "VERIFIED", result
    assert len(result["runs"]) == 3 and len(result["reservations"]) == 2
    for r in result["runs"]:
        persisted = s.store.get_task(r["run_id"]) or s.store.get_epic(r["run_id"])
        assert r["session_id"] == persisted.codex_session_id
        assert r["branch"] == persisted.branch and r["worktree_path"] == persisted.worktree_path
        assert r["observed_commit"] == persisted.current_commit
        assert r["runtime_observation"] == "LIVE"
    with StateStore(s.settings.sqlite_path) as db:
        reopened = recovery(control, a, db=db)
        assert run(reopened.inspect(epic_run_id=a.epic_run_id)) == result
        assert db.db.total_changes == 0
    assert before == (
        s.store.project_inventory("p"),
        s.store.db.total_changes,
        s.selection.git.worktrees(),
    )
    assert native.starts == 3  # one Integration plus two Workers, no replacements


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "WORKTREE_MISSING_OR_REPLACED"),
        ("head", "HEAD_CHANGED"),
        ("done", "DONE_WITHOUT_DELIVERY_PROOF"),
        ("session", "SESSION_CHANGED_OR_UNREGISTERED"),
    ],
)
def test_drift_is_concrete_and_never_repairs_resources(control_setup, change, code):
    control, _, a, board, native = control_setup
    t = start_one(control, a)
    s = control.scheduler
    path = Path(t.worktree_path)
    moved = path.with_name(path.name + "-preserved")
    service = recovery(control, a)
    if change == "missing":
        path.rename(moved)
    elif change == "head":
        (path / "README.md").write_text("Unexpected external commit\n")
        s.selection.git.run("add", "README.md", cwd=path)
        s.selection.git.run("commit", "-m", "External fixture change", cwd=path)
    elif change == "done":
        next(r for r in board.tasks if r["taskId"] == T1)["status"] = "Done"
    else:
        original = service.observers
        service.observers = lambda r: (
            (lambda _: dict(status="LIVE", session_id="foreign")) if r.id == t.id else original(r)
        )
    before = s.store.project_inventory("p"), s.store.db.total_changes, native.starts
    try:
        result = run(service.inspect(epic_run_id=a.epic_run_id))
        assert not result["scheduling_allowed"] and code in codes(result), result
        assert row(result, t.id)["classification"] in {"DRIFT", "UNCERTAIN"}
        assert before == (s.store.project_inventory("p"), s.store.db.total_changes, native.starts)
        assert s.store.get_task(t.id) == t
    finally:
        if moved.exists():
            moved.rename(path)


def test_uncommitted_worker_files_are_preserved_and_not_inferred_done(control_setup):
    control, _, a, _, _ = control_setup
    t = start_one(control, a)
    path = Path(t.worktree_path) / "unfinished.txt"
    path.write_bytes(b"Unfinished local work\n")
    result = run(recovery(control, a).inspect(epic_run_id=a.epic_run_id))
    assert row(result, t.id)["uncommitted_changes"] is True
    assert row(result, t.id)["classification"] == "VERIFIED"
    assert path.read_bytes() == b"Unfinished local work\n"
    assert control.store.get_task(t.id).internal_status == "WORKING"


@pytest.mark.parametrize("unavailable", ["herdr", "teamplayer"])
def test_unavailable_adapter_pauses_new_scheduling_without_lost_session_claim(
    control_setup,
    monkeypatch,
    unavailable,
):
    control, _, a, board, native = control_setup
    t = start_one(control, a)
    s = control.scheduler
    service = recovery(control, a)
    if unavailable == "herdr":

        def fail(*args, **kwargs):
            raise HerdrError("PRIVATE_TOKEN_FROM_FAILED_TRANSPORT")

        monkeypatch.setattr(native, "get_agent", fail)
        expected = "HERDR_UNAVAILABLE"
    else:

        async def fail(*args, **kwargs):
            raise TimeoutError("PRIVATE_TOKEN_FROM_FAILED_TRANSPORT")

        monkeypatch.setattr(board, "read", fail)
        expected = "TEAMPLAYER_UNAVAILABLE"
    before = s.store.project_inventory("p"), s.store.db.total_changes, native.starts
    heads = {b: s.selection.git.head(b) for b in ("main", t.branch)}
    result = run(service.inspect(epic_run_id=a.epic_run_id))
    assert result["classification"] == "UNCERTAIN" and not result["scheduling_allowed"]
    assert expected in codes(result) and "RUNTIME_MISSING_UNCONFIRMED" not in codes(result)
    assert "PRIVATE_TOKEN" not in str(result)
    with pytest.raises(ReconciliationError, match="RECONCILIATION_REQUIRED"):
        run(service.require_scheduling(epic_run_id=a.epic_run_id))
    if unavailable == "herdr":
        with pytest.raises(SchedulerError, match="RECONCILIATION_REQUIRED"):
            run(s.start_selected(a, a.epic_run_id, s.selection.specs[T2]))
        assert s.store.get_operation("p", "task_start", "F-02") is None
    assert before == (s.store.project_inventory("p"), s.store.db.total_changes, native.starts)
    assert heads == {b: s.selection.git.head(b) for b in heads}
    assert s.store.get_task(t.id).codex_session_id == t.codex_session_id
    assert s.store.get_task(t.id).worker_slot == t.worker_slot


def test_pending_effect_keeps_original_key_and_blocks_new_claim(control_setup):
    control, _, a, _, native = control_setup
    s = control.scheduler
    op = Operation(
        project_id="p",
        epic_run_id=a.epic_run_id,
        kind="unknown_external_effect",
        idempotency_key="original-key",
        result={"stage": "SENT_UNKNOWN"},
    )
    s.store.add_operation(op)
    before = s.store.db.total_changes, native.starts
    report = run(recovery(control, a).inspect(epic_run_id=a.epic_run_id))
    assert report["classification"] == "RESUMABLE"
    assert report["operations"] == [
        dict(
            operation_id=op.id,
            kind=op.kind,
            idempotency_key="original-key",
            run_id=a.epic_run_id,
            status="PENDING",
            stage="SENT_UNKNOWN",
            relevant=True,
            classification="RESUMABLE",
            blocks_new_work=True,
            findings=[
                dict(
                    classification="RESUMABLE",
                    code="OPERATION_PENDING",
                    next_action="RECONCILE_ORIGINAL_OPERATION",
                )
            ],
        )
    ]
    with pytest.raises(SchedulerError, match="RECONCILIATION_REQUIRED"):
        run(s.start_selected(a, a.epic_run_id, s.selection.specs[T1]))
    assert before == (s.store.db.total_changes, native.starts)
    assert not s.store.get_tasks(a.epic_run_id)


def test_stopped_coordinator_pending_journal_not_hidden_by_completed_run(claims_setup):
    claims, a, _, _, _, _ = claims_setup
    stopped = claims.runtime.stop(a, "controlled-stop")
    op = ProjectOperation(
        project_id="p",
        coordinator_run_id=stopped.id,
        kind="unknown_sync",
        idempotency_key="original-sync",
    )
    claims.store.add_project_operation(op)
    report = run(claims.reconcile())
    assert row(report, stopped.id)["state"] == "STOPPED"
    assert any(r["operation_id"] == op.id for r in report["operations"])
    assert not report["scheduling_allowed"]


def test_coordinator_new_epic_gate_denies_pending_effect_before_factory(claims_setup):
    claims, a, _, _, natives, _ = claims_setup
    op = ProjectOperation(
        project_id="p",
        coordinator_run_id=claims.runtime.run_id,
        kind="unknown_sync",
        idempotency_key="original-sync",
    )
    claims.store.add_project_operation(op)
    before = claims.store.db.total_changes
    result = run(claims.claim(a, "p", "new-epic"))
    assert result["status"] == "WAIT" and result["reason"] == "STARTUP_RECONCILIATION_REQUIRED"
    assert natives == {} and claims.store.get_runs() == []
    assert claims.store.db.total_changes == before


def test_external_state_change_during_observation_is_uncertain(control_setup):
    control, _, a, _, _ = control_setup
    t = start_one(control, a)
    service = recovery(control, a)
    original = service.observers
    changed = False

    def observations(r):
        def observe(value):
            nonlocal changed
            result = original(r)(value)
            if not changed:
                changed = True
                control.store.add_reference(
                    ExternalReference(
                        project_id="p",
                        epic_run_id=a.epic_run_id,
                        provider="fixture-observer",
                        kind="external-write",
                        external_id="changed",
                    )
                )
            return result

        return observe

    service.observers = observations
    report = run(service.inspect(epic_run_id=a.epic_run_id))
    assert report["classification"] == "UNCERTAIN"
    assert "STATE_CHANGED_DURING_RECONCILIATION" in codes(report)
    assert control.store.get_task(t.id).codex_session_id == t.codex_session_id
    assert not service.current(report)


def test_query_only_sqlite_allows_inventory_and_native_observation_without_writes(control_setup):
    control, _, a, _, _ = control_setup
    start_one(control, a)
    service = recovery(control, a)
    before = control.store.db.total_changes
    control.store.db.execute("PRAGMA query_only=ON")
    try:
        result = run(service.inspect(epic_run_id=a.epic_run_id))
        assert result["scheduling_allowed"], result
        assert control.store.db.total_changes == before
    finally:
        control.store.db.execute("PRAGMA query_only=OFF")


def test_git_status_filter_is_denied_before_any_command_executes(control_setup, tmp_path):
    control, _, a, _, _ = control_setup
    t = start_one(control, a)
    g = control.scheduler.selection.git
    sentinel = tmp_path / "filter-must-not-run"
    g.run("config", "filter.fixture.clean", "touch " + str(sentinel) + "; cat")
    (Path(t.worktree_path) / ".gitattributes").write_text("* filter=fixture\n")
    (Path(t.worktree_path) / "README.md").write_text("Uncommitted protected work\n")
    result = run(recovery(control, a).inspect(epic_run_id=a.epic_run_id))
    assert not result["scheduling_allowed"]
    assert "GIT_FILTER_CONFIGURATION_UNSAFE" in codes(result)
    assert not sentinel.exists()
    assert (Path(t.worktree_path) / "README.md").read_text() == "Uncommitted protected work\n"


def test_verified_parked_task_keeps_session_and_allows_independent_worker(control_setup):
    control, _, a, board, native = control_setup
    t = start_one(control, a)
    s = control.scheduler
    s.merge.lifecycle.stop_task(a, t.id, key="controlled-park", reason="Need fixture input")
    next(r for r in board.tasks if r["taskId"] == T1)["status"] = "NeedsInput"
    parked = s.store.get_task(t.id)
    assert parked.internal_status == "PARKED" and parked.worker_slot is None
    report = run(recovery(control, a).inspect(epic_run_id=a.epic_run_id))
    assert report["scheduling_allowed"], report
    assert row(report, t.id)["classification"] == "RESUMABLE"
    assert row(report, t.id)["blocks_new_work"] is False
    run(s.start_selected(a, a.epic_run_id, s.selection.specs[T2]))
    assert s.store.get_task(t.id) == parked
    assert parked.codex_session_id == t.codex_session_id
    assert native.starts == 3 and native.exits == 1


def test_board_change_after_native_observation_cannot_authorize_start(control_setup):
    control, _, a, board, _ = control_setup
    t = start_one(control, a)
    service = recovery(control, a)
    original = service.observers

    def observers(r):
        def observe(value):
            result = original(r)(value)
            next(n for n in board.tasks if n["taskId"] == T1)["version"] += 1
            return result

        return observe

    service.observers = observers
    report = run(service.inspect(epic_run_id=a.epic_run_id))
    assert not report["scheduling_allowed"]
    assert "BOARD_CHANGED_DURING_RECONCILIATION" in codes(report)
    assert control.store.get_task(t.id).codex_session_id == t.codex_session_id


def test_git_change_after_observation_is_uncertain_and_keeps_external_commit(control_setup):
    control, _, a, _, _ = control_setup
    t = start_one(control, a)
    service = recovery(control, a)
    original = service.observers
    changed = False

    def observers(r):
        def observe(value):
            nonlocal changed
            result = original(r)(value)
            if r.id == t.id and not changed:
                changed = True
                path = Path(t.worktree_path)
                (path / "README.md").write_text("External concurrent commit\n")
                service.git.run("add", "README.md", cwd=path)
                service.git.run("commit", "-m", "External concurrent write", cwd=path)
            return result

        return observe

    service.observers = observers
    report = run(service.inspect(epic_run_id=a.epic_run_id))
    assert not report["scheduling_allowed"]
    assert "GIT_CHANGED_DURING_RECONCILIATION" in codes(report)
    assert service.git.head(t.branch) != t.current_commit
    assert (Path(t.worktree_path) / "README.md").read_text() == "External concurrent commit\n"


@pytest.mark.parametrize("change", ["none", "rewind", "older-known", "foreign-start"])
def test_recorded_broker_commit_proof_and_tip_rewind(broker, change):
    from test_task_selection import selection

    b = broker
    s = b.service
    (b.path / "result.txt").write_text("Actual broker commit\n")
    delivered = s.commit(b.worker, "actual-broker", "Save own Worker result")
    history = b.start.assignment.codex
    selected, board = selection(
        s.settings,
        s.store,
        b.integration,
        s.store.get_operation("p", "task_start", "T").result["spec"],
        history=history,
    )
    next(r for r in board.tasks if r["taskId"] == T1)["status"] = "InProgress"

    class Metadata:
        def read_session(self, sid, cwd):
            thread = history.read_thread(sid, cwd)
            return dict(session_id=thread["id"], cwd=thread["cwd"])

    lifecycle = RuntimeLifecycleService(s.settings, s.store, b.native, codex=Metadata())
    service = ReconciliationService(
        s.settings,
        s.store,
        selected.reader,
        project_id="p",
        observers=lambda r: (
            LifecycleObserver(lifecycle, b.integration) if isinstance(r, TaskRun) else None
        ),
    )
    if change == "rewind":
        s.git.run(
            "update-ref", "refs/heads/" + b.task.branch, b.task.base_commit, delivered["commit"]
        )
    elif change == "older-known":
        (b.path / "result.txt").write_text("Newer recorded broker commit\n")
        newer = s.commit(b.worker, "newer-broker", "Save later own Worker result")
        s.git.run("update-ref", "refs/heads/" + b.task.branch, delivered["commit"], newer["commit"])
    elif change == "foreign-start":
        op = s.store.get_operations(b.task.epic_run_id, kind="worker_commit")[0]
        s.store.update_operation(
            op.model_copy(update={"result": op.result | {"start_operation_id": "foreign"}})
        )
    changes = s.store.db.total_changes
    result = run(service.inspect(epic_run_id=b.task.epic_run_id))
    task = row(result, b.task.id)
    if change == "none":
        assert task["classification"] == "VERIFIED", task
        assert task["expected_commit"] == task["observed_commit"] == delivered["commit"]
    else:
        assert "HEAD_CHANGED" in {f["code"] for f in task["findings"]}
    assert s.store.db.total_changes == changes
    assert s.store.get_task(b.task.id).current_commit == b.task.current_commit


def test_integration_projection_keeps_foreign_session_and_paths_private(control_setup):
    from orchestrator.application.reconciliation_service import integration_report

    control, _, a, _, _ = control_setup
    start_one(control, a)
    report = run(recovery(control, a).inspect(epic_run_id=a.epic_run_id))
    report["runs"].append(
        dict(
            run_id="foreign-run",
            epic_run_id=None,
            session_id="foreign-private-session",
            worktree_path="/private/foreign-path",
            relevant=False,
            blocks_new_work=False,
            findings=[],
        )
    )
    public = integration_report(report, a.epic_run_id)
    assert "foreign-private-session" not in str(public)
    assert "/private/foreign-path" not in str(public)
    assert all(r["epic_run_id"] == a.epic_run_id for r in public["runs"])
    assert report["runs"][-1]["session_id"] == "foreign-private-session"


def test_wrong_sqlite_configuration_rejected_before_external_reads(control_setup, tmp_path):
    control, _, a, board, _ = control_setup
    before = len(board.calls)
    wrong = control.settings.model_copy(update={"sqlite_path": tmp_path / "different.db"})
    with pytest.raises(ReconciliationError, match="DATABASE_MISMATCH"):
        ReconciliationService(
            wrong, control.store, control.scheduler.selection.reader, project_id=a.project_id
        )
    assert len(board.calls) == before and not wrong.sqlite_path.exists()


def test_coordinator_selection_board_must_match_reconciliation_before_reservation(
    claims_setup,
    monkeypatch,
):
    from test_teamplayer_reader import OTHER

    claims, a, board, _, natives, _ = claims_setup
    original = claims.reconcile

    async def change_after_selection(**kwargs):
        board.tasks[0]["responsibleUserId"] = OTHER
        return await original(**kwargs)

    monkeypatch.setattr(claims, "reconcile", change_after_selection)
    before = claims.store.db.total_changes
    result = run(claims.claim(a, "p", "board-race"))
    assert result["status"] == "WAIT"
    assert "BOARD_CHANGED_SINCE_SELECTION" in codes(result["reconciliation"])
    assert claims.store.db.total_changes == before
    assert not natives and claims.store.get_runs() == []


def test_scheduler_selection_board_must_match_reconciliation_before_reservation(
    control_setup,
    monkeypatch,
):
    control, _, a, board, native = control_setup
    s = control.scheduler
    original = s.reconcile

    async def change_after_selection(*args):
        board.tasks[0]["version"] += 1
        return await original(*args)

    monkeypatch.setattr(s, "reconcile", change_after_selection)
    before = s.store.db.total_changes, native.starts
    with pytest.raises(SchedulerError, match="RECONCILIATION_REQUIRED"):
        run(s.start_selected(a, a.epic_run_id, s.selection.specs[T1]))
    assert "BOARD_CHANGED_SINCE_SELECTION" in codes(s.last_reconciliation)
    assert before == (s.store.db.total_changes, native.starts)
    assert not s.store.get_tasks(a.epic_run_id)


def test_continuation_key_and_release_label_without_actual_delivery_do_not_authorize(claims_setup):
    claims, a, _, _, natives, _ = claims_setup
    delivery = ProjectOperation(
        project_id="p",
        coordinator_run_id=claims.runtime.run_id,
        kind="coordinator_epic_delivery",
        idempotency_key="fake-delivery",
        status="SUCCEEDED",
        result={"stage": "RELEASED", "epic_run_id": "unverified-epic"},
    )
    pending = ProjectOperation(
        project_id="p",
        coordinator_run_id=claims.runtime.run_id,
        kind="coordinator_epic_continuation",
        idempotency_key=delivery.id,
        result={"stage": "INTENT", "delivery_id": delivery.id, "claim_key": "exact-key"},
    )
    claims.store.add_project_operation(delivery)
    claims.store.add_project_operation(pending)
    before = claims.store.db.total_changes
    result = run(claims.claim(a, "p", "exact-key"))
    assert result["status"] == "WAIT" and not natives
    assert claims.store.db.total_changes == before
    observed = next(
        o for o in result["reconciliation"]["operations"] if o["operation_id"] == pending.id
    )
    assert observed["blocks_new_work"] is True


def test_known_epic_observation_never_calls_start_factory(claims_setup, monkeypatch):
    claims, a, _, _, _, _ = claims_setup
    result = run(claims.claim(a, "p", "actual-start"))
    assert result["status"] == "REGISTERED"

    def forbidden(*args, **kwargs):
        pytest.fail("A read-only audit must never invoke the start factory")

    monkeypatch.setattr(claims, "factory", forbidden)
    before = claims.store.project_inventory("p"), claims.store.db.total_changes
    report = run(claims.reconcile())
    assert report["scheduling_allowed"], report
    assert before == (claims.store.project_inventory("p"), claims.store.db.total_changes)


def test_reopened_observer_registry_requires_original_context_without_creating_it(claims_setup):
    from orchestrator.application.coordinator_claim_service import CoordinatorClaimService

    claims, a, _, _, _, _ = claims_setup
    result = run(claims.claim(a, "p", "actual-start"))
    parent = claims.epic_observers[result["epic_run_id"]].startup

    def forbidden(*args, **kwargs):
        pytest.fail("Missing context must give uncertainty, not a factory call")

    reopened = CoordinatorClaimService(claims.runtime, claims.reader, forbidden)
    before = claims.store.db.total_changes
    report = run(reopened.reconcile())
    assert not report["scheduling_allowed"]
    assert "RUNTIME_OBSERVER_UNAVAILABLE" in codes(report)
    reopened.register_epic_observer(parent)
    report = run(reopened.reconcile())
    assert report["scheduling_allowed"], report
    assert claims.store.db.total_changes == before
    assert (
        row(report, parent.principal.epic_run_id)["session_id"]
        == parent.store.get_epic(parent.principal.epic_run_id).codex_session_id
    )


def test_confirmed_stopped_integration_is_resumable_without_worker_slot_or_new_agent(control_setup):
    control, _, a, _, native = control_setup
    parent = control.epic_start
    op = control.store.get_operation(a.project_id, parent.KIND, a.epic_run_id)
    # These peers have simulated PIDs; observe them with their own process adapter.
    parent.lifecycle.processes = native
    parent.lifecycle.stop_epic(parent._coordinator(op), a.epic_run_id, key="controlled-epic-stop")
    before = control.store.project_inventory("p"), control.store.db.total_changes, native.starts
    report = run(recovery(control, a).inspect(epic_run_id=a.epic_run_id))
    observed = row(report, a.epic_run_id)
    assert observed["classification"] == "RESUMABLE"
    assert observed["runtime_observation"] == "STOPPED" and observed["worker_slot"] is None
    assert observed["findings"] == [
        dict(
            classification="RESUMABLE",
            code="ORIGINAL_RUNTIME_STOPPED",
            next_action="RECONNECT_ORIGINAL_INTEGRATION",
        )
    ]
    assert not report["scheduling_allowed"]
    assert before == (
        control.store.project_inventory("p"),
        control.store.db.total_changes,
        native.starts,
    )
