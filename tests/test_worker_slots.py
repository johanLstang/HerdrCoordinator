"""Real concurrent Git/SQLite calls and a controlled runtime; native parallelism is F30."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest
from test_runtime_lifecycle import setup as lifecycle_setup  # noqa: F401
from test_runtime_start import setup as startup_setup  # noqa: F401
from test_task_start import setup as start_setup  # noqa: F401

from orchestrator.adapters.git import GitError
from orchestrator.application.runtime_lifecycle_service import (
    LifecycleError,
    RuntimeLifecycleService,
)
from orchestrator.application.runtime_start_service import RuntimeStartError, RuntimeStartService
from orchestrator.application.task_start_service import TaskStartError, TaskStartService
from orchestrator.application.worker_slots import SlotError, WorkerSlots
from orchestrator.domain.states import TaskState
from orchestrator.persistence.store import StateStore


def bounded(start, h, history, max_workers=2):
    settings = start.settings.model_copy(update={"max_workers": max_workers})
    # Deliberately no native ACK/session. Pipeline remains STARTING and holds its slot.
    h.prompt = lambda name, text, **kwargs: h.sent.append(text)
    return TaskStartService(settings, start.store, h, history)


def claims(store):
    return {r.id: r.worker_slot for r in store.get_tasks("epic-run") if r.worker_slot is not None}


def test_three_simultaneous_new_starts_reserve_two_before_slow_runtime(start_setup):  # noqa: F811
    start, a, spec, h, history = start_setup
    service = bounded(start, h, history)
    barrier, allow, guard = Barrier(3), Event(), Lock()
    create, launch = h.create_workspace, h.start_agent
    samples = []

    def workspace(*args):
        with guard:
            return create(*args)

    def native_start(*args):
        with StateStore(service.settings.sqlite_path) as db:
            slots = list(claims(db).values())
            samples.append(slots)
            assert len(slots) <= 2 and len(set(slots)) == len(slots)
        assert allow.wait(timeout=5)
        with guard:
            launch(*args)

    h.create_workspace, h.start_agent = workspace, native_start

    def invoke(identity):
        with StateStore(service.settings.sqlite_path) as db:
            s = TaskStartService(service.settings, db, h, history)
            barrier.wait(timeout=5)
            try:
                return s.start(a, a.epic_run_id, spec | {"task_id": identity}, timeout_seconds=1)
            except TaskStartError as error:
                assert str(error) == "WORKER_CAPACITY_UNAVAILABLE"
                allow.set()
                return {"status": "CAPACITY"}

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(invoke, ["T", "T2", "T3"]))
    assert sorted(r["status"] for r in results) == ["CAPACITY", "WAITING", "WAITING"]
    assert sorted(claims(service.store).values()) == [1, 2] and len(h.agents) == h.starts == 2
    assert len(service.store.get_tasks(a.epic_run_id)) == 2
    assert samples and all(1 <= len(x) <= 2 for x in samples)
    assert any(len(x) == 2 for x in samples)
    for task in service.store.get_tasks(a.epic_run_id):
        assert task.internal_status == TaskState.STARTING and task.codex_session_id is None
        assert service.worktrees.git.head(task.branch) == task.base_commit


def test_two_scheduler_calls_for_same_task_reuse_one_slot_run_and_native_start(start_setup):  # noqa: F811
    start, a, spec, h, history = start_setup
    s = bounded(start, h, history)
    barrier = Barrier(2)

    def invoke(_):
        with StateStore(s.settings.sqlite_path) as db:
            local = TaskStartService(s.settings, db, h, history)
            barrier.wait(timeout=5)
            try:
                return local.start(a, a.epic_run_id, spec, timeout_seconds=1)
            except TaskStartError:
                return None  # overlapping external intent is observed on retry

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(invoke, range(2)))
    result = s.start(a, a.epic_run_id, spec, timeout_seconds=1)
    assert result["status"] == "WAITING"
    assert (
        len(claims(s.store))
        == len(s.store.get_tasks(a.epic_run_id))
        == h.starts
        == len(h.workspaces)
        == 1
    )
    assert len(h.sent) == 1
    assert len(s.store.get_operations(a.epic_run_id, kind="task_start")) == 1


def test_one_worker_configuration_is_respected(start_setup):  # noqa: F811
    start, a, spec, h, history = start_setup
    s = bounded(start, h, history, max_workers=1)
    s.start(a, a.epic_run_id, spec, timeout_seconds=1)
    with pytest.raises(TaskStartError, match="CAPACITY"):
        s.start(a, a.epic_run_id, spec | {"task_id": "T2"}, timeout_seconds=1)
    assert list(claims(s.store).values()) == [1] and h.starts == 1


@pytest.mark.parametrize("when", ["before_git", "after_git"])
def test_no_runtime_intent_failure_releases_then_retry_reuses_original_run(
    start_setup,  # noqa: F811
    monkeypatch,
    when,
):  # noqa: F811
    start, a, spec, h, history = start_setup
    s = bounded(start, h, history)
    add = s.worktrees.git.add_worktree

    def failure(*args, **kwargs):
        if when == "after_git":
            add(*args, **kwargs)
        raise GitError("controlled pre-runtime failure")

    monkeypatch.setattr(s.worktrees.git, "add_worktree", failure)
    with pytest.raises(TaskStartError):
        s.start(a, a.epic_run_id, spec, timeout_seconds=1)
    task = s.store.get_tasks(a.epic_run_id)[0]
    parent = s.store.get_operation("p", "task_start", "T")
    assert task.worker_slot is None and parent.result["reservation_released"]
    assert not s.store.get_operation("p", "start_runtime", task.id) and h.starts == 0
    monkeypatch.setattr(s.worktrees.git, "add_worktree", add)
    s.start(a, a.epic_run_id, spec | {"task_id": "T2"}, timeout_seconds=1)
    retried = s.start(a, a.epic_run_id, spec, timeout_seconds=1)
    assert retried["task"]["id"] == task.id and retried["task"]["worker_slot"] == 2
    assert s.store.get_operation("p", "task_start", "T").id == parent.id
    assert sorted(claims(s.store).values()) == [1, 2] and h.starts == len(h.workspaces) == 2
    assert s.store.get_task(task.id).base_commit == task.base_commit


@pytest.mark.parametrize("when", ["workspace", "agent"])
def test_unknown_runtime_outcome_retains_capacity_and_original_intent(start_setup, when):  # noqa: F811
    start, a, spec, h, history = start_setup
    s = bounded(start, h, history)
    if when == "workspace":
        h.fail_create = True
    else:
        h.start_failure = "UNKNOWN_RUNTIME_OUTCOME"
        h.blocked = True
    with pytest.raises(TaskStartError):
        s.start(a, a.epic_run_id, spec)
    task = s.store.get_tasks(a.epic_run_id)[0]
    op = s.store.get_operation("p", "start_runtime", task.id)
    assert task.worker_slot == 1 and op.status == "PENDING"
    assert not s.store.get_operation("p", "task_start", "T").result.get("reservation_released")
    h.fail_create, h.start_failure, h.blocked = False, None, False
    if when == "workspace":
        with pytest.raises(TaskStartError):
            s.start(a, a.epic_run_id, spec)
        assert len(h.workspaces) == 1 and h.starts == 0
    s.start(a, a.epic_run_id, spec | {"task_id": "T2"}, timeout_seconds=1)
    with pytest.raises(TaskStartError, match="CAPACITY"):
        s.start(a, a.epic_run_id, spec | {"task_id": "T3"})
    assert sorted(claims(s.store).values()) == [1, 2]
    assert s.store.get_operation("p", "start_runtime", task.id).id == op.id


def test_release_rejects_caller_claim_without_saved_failure_or_runtime_inactivity(start_setup):  # noqa: F811
    start, a, spec, h, history = start_setup
    s = bounded(start, h, history)
    s.start(a, a.epic_run_id, spec, timeout_seconds=1)
    t = s.store.get_tasks(a.epic_run_id)[0]
    op = s.store.get_operation("p", "task_start", "T")
    assert s.slots.release_unstarted(t, op.id) is False
    assert t.worker_slot == s.store.get_task(t.id).worker_slot == 1


@pytest.mark.parametrize("attack", ["duplicate", "missing-slot"])
def test_contradictory_reservations_stop_new_claim_without_repair(start_setup, attack):  # noqa: F811
    start, a, spec, h, history = start_setup
    s = bounded(start, h, history)
    s.start(a, a.epic_run_id, spec, timeout_seconds=1)
    t = s.store.get_tasks(a.epic_run_id)[0]
    if attack == "duplicate":
        other = s.worktrees.create_task_worktree(
            a, epic_run_id=a.epic_run_id, task_id="Other", run_id="other"
        )
        s.store.update_runtime_metadata(other.model_copy(update={"worker_slot": 1}))
    else:
        s.store.update_runtime_metadata(t.model_copy(update={"worker_slot": None}))
    before = len(s.store.get_tasks(a.epic_run_id)), h.starts
    with pytest.raises(TaskStartError, match="RESERVATION_CONFLICT|UNRESERVED_RUNTIME_UNVERIFIED"):
        s.start(a, a.epic_run_id, spec | {"task_id": "T2"})
    assert (len(s.store.get_tasks(a.epic_run_id)), h.starts) == before


def test_start_and_same_session_resume_race_for_last_slot(lifecycle_setup):  # noqa: F811
    life, _, a, h, codex, processes = lifecycle_setup
    stopped = life.stop_task(a, "t1", key="park", reason="Input needed")
    RuntimeStartService(life.settings, life.store, h, processes=processes).start_task(a, "t2")
    barrier = Barrier(2)

    def invoke(operation):
        with StateStore(life.settings.sqlite_path) as db:
            barrier.wait(timeout=5)
            try:
                if operation == "resume":
                    result = RuntimeLifecycleService(
                        life.settings, db, h, codex=codex, processes=processes
                    ).resume_task(a, "t1", key="resume-race")
                    assert result.status == "SUCCEEDED"
                else:
                    RuntimeStartService(life.settings, db, h, processes=processes).start_task(
                        a, "t3"
                    )
                return "OK"
            except (LifecycleError, RuntimeStartError) as error:
                assert "CAPACITY" in str(error)
                return "CAPACITY"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, ["resume", "start"]))
    assert sorted(results) == ["CAPACITY", "OK"]
    slots = [
        t.worker_slot for t in life.store.get_tasks(a.epic_run_id) if t.worker_slot is not None
    ]
    assert sorted(slots) == [1, 2] and h.starts + h.resumes == 3
    assert (
        life.store.get_operation("p", "stop_runtime", stopped.idempotency_key).status == "SUCCEEDED"
    )


@pytest.mark.parametrize(
    "attack",
    [
        "missing-stop",
        "empty-proof",
        "reappeared-process",
        "pending-resume",
        "incomplete-binding",
        "malformed-binding",
        "foreign-binding",
    ],
)
def test_parked_slot_is_only_free_with_current_physical_stop_evidence(lifecycle_setup, attack):  # noqa: F811
    life, _, a, h, _, processes = lifecycle_setup
    stop = life.stop_task(a, "t1", key="park", reason="Input needed")
    if attack == "missing-stop":
        life.store.update_operation(stop.model_copy(update={"status": "PENDING"}))
    elif attack == "empty-proof":
        life.store.update_operation(
            stop.model_copy(
                update={"result": stop.result | {"process_proof": {"identities": [], "groups": []}}}
            )
        )
    elif attack == "reappeared-process":
        processes.alive = True
    elif attack.endswith("binding"):
        start = life.store.get_operation("p", "start_runtime", "t1")
        binding = start.result["binding"]
        changed = (
            {"pane_id": binding["pane_id"]}
            if attack == "incomplete-binding"
            else "invalid"
            if attack == "malformed-binding"
            else binding | {"unexpected_field": "foreign"}
        )
        life.store.update_operation(
            start.model_copy(update={"result": start.result | {"binding": changed}})
        )
    else:
        from orchestrator.domain.models import Operation

        life.store.add_operation(
            Operation(
                project_id="p",
                epic_run_id=a.epic_run_id,
                task_run_id="t1",
                kind="resume_runtime",
                idempotency_key="unknown-resume",
            )
        )
    before = h.starts
    with pytest.raises(RuntimeStartError, match="UNRESERVED_RUNTIME_UNVERIFIED"):
        RuntimeStartService(life.settings, life.store, h, processes=processes).start_task(a, "t2")
    assert h.starts == before and life.store.get_task("t2").worker_slot is None


def test_config_lowering_does_not_allocate_around_retained_slot_two(start_setup):  # noqa: F811
    from orchestrator.domain.worker_contracts import LocalTaskSpec

    start, a, spec, h, history = start_setup
    s = bounded(start, h, history)
    # Two actual durable claims before external resources. Controlled controller failure
    # then releases only the first unstarted claim; the second still owns slot two.
    with s.integration._lock():
        first, parent = s._prepare(a, a.epic_run_id, LocalTaskSpec.model_validate(spec), 45)
        s._prepare(a, a.epic_run_id, LocalTaskSpec.model_validate(spec | {"task_id": "T2"}), 45)
    s._checkpoint(parent, "CLAIMED", error="PRE_RUNTIME_CONTROLLER_FAILURE")
    assert s.slots.release_unstarted(first, parent.id)
    third = s.worktrees.create_task_worktree(
        a, epic_run_id=a.epic_run_id, task_id="T3", run_id="third", prepare_only=True
    )
    single = WorkerSlots(s.settings.model_copy(update={"max_workers": 1}), s.store)
    with pytest.raises(SlotError, match="CAPACITY"):
        single.reserve_new(third)
    assert next(t for t in s.store.get_tasks(a.epic_run_id) if t.task_id == "T2").worker_slot == 2
    assert s.store.get_task(third.id).worker_slot is None and h.starts == 0
