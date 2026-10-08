import pytest
from test_runtime_start import setup as startup_setup  # noqa: F401

from orchestrator.adapters.codex import CodexError
from orchestrator.adapters.herdr import HerdrError
from orchestrator.adapters.processes import ProcessObserver
from orchestrator.application.runtime_lifecycle_service import (
    LifecycleError,
    RuntimeLifecycleService,
)
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import TaskState
from orchestrator.persistence.store import StateStore

SID = "00000000-0000-4000-8000-000000000001"


class FakeProcesses:
    def __init__(self):
        self.alive = True
        self.lingering = False

    def capture(self, roots, shell_pid):
        return {"identities": roots + [{"pid": 30, "start_time": "456"}], "groups": [20]}

    def inactive(self, proof):
        assert {"pid": 30, "start_time": "456"} in proof["identities"]
        return not self.alive and not self.lingering

    combine = staticmethod(ProcessObserver.combine)


class FakeCodex:
    missing = False
    active = False

    def read_session(self, sid, cwd):
        if self.missing:
            raise CodexError("CODEX_METADATA_REJECTED")
        return {"session_id": sid, "id": sid, "cwd": cwd}

    def read_thread(self, sid, cwd):
        return {
            "id": sid,
            "cwd": cwd,
            "turns": [{"status": "inProgress" if self.active else "interrupted"}],
        }


@pytest.fixture
def setup(startup_setup):  # noqa: F811
    start, c, i, h = startup_setup
    t = start.start_task(i, "t1")
    start.store.update_runtime_metadata(t.model_copy(update={"codex_session_id": SID}))
    processes, codex = FakeProcesses(), FakeCodex()
    original_verify, original_info = h.verify_agent, h.process_info
    h.status, h.interrupts, h.exits, h.resumes = "idle", 0, 0, 0
    h.proc = [{"pid": 20, "start_time": "123"}]

    def verify(*args):
        return original_verify(*args) | {
            "session_id": SID,
            "status": h.status,
            "processes": h.proc,
            "ready": h.status in {"idle", "done"},
        }

    def info(pane):
        result = original_info(pane)
        if any(a["pane_id"] == pane for a in h.agents.values()):
            result["foreground_processes"] = [{"pid": h.proc[0]["pid"]}]
        return result

    def interrupt(name):
        assert name in h.agents
        h.interrupts += 1
        h.status, codex.active = "idle", False

    def exit_agent(name):
        assert name in h.agents
        h.exits += 1
        del h.agents[name]
        processes.alive = False

    def resume_agent(name, pane, cwd, sid):
        assert sid == SID
        h.resumes += 1
        h.agents[name] = h.workspaces[pane] | {"name": name}
        h.proc = [{"pid": 40 + h.resumes, "start_time": str(1000 + h.resumes)}]
        processes.alive = True

    h.verify_agent, h.process_info = verify, info
    h.interrupt, h.exit_agent, h.resume_agent = interrupt, exit_agent, resume_agent
    yield (
        RuntimeLifecycleService(start.settings, start.store, h, codex=codex, processes=processes),
        c,
        i,
        h,
        codex,
        processes,
    )


def test_reconnect_observes_same_session_without_new_start(setup):
    s, _, i, h, _, _ = setup
    t = s.store.get_task("t1")
    a = s.reconnect_task(i, "t1")
    assert a["status"] == "LIVE" and a["session_id"] == SID
    with StateStore(s.settings.sqlite_path) as db:
        other = RuntimeLifecycleService(s.settings, db, h, codex=s.codex, processes=s.processes)
        assert other.reconnect_task(i, "t1") == a
    assert s.store.get_task("t1") == t and h.starts == 1 and h.resumes == 0


def test_stop_park_release_repeat_resume_preserves_session_git_and_phase(setup):
    s, _, i, h, _, _ = setup
    before = s.store.get_task("t1")
    stop = s.stop_task(i, "t1", key="stop1", reason="Waiting for decision")
    t = s.store.get_task("t1")
    assert t.internal_status == TaskState.PARKED and t.resume_state == TaskState.STARTING
    assert t.worker_slot is None and t.codex_session_id == SID
    assert (t.branch, t.worktree_path) == (before.branch, before.worktree_path)
    assert s.stop_task(i, "t1", key="stop1", reason="Waiting for decision") == stop
    assert s.reconnect_task(i, "t1")["status"] == "STOPPED"
    resume = s.resume_task(i, "t1", key="resume1")
    t = s.store.get_task("t1")
    assert t.internal_status == TaskState.STARTING and t.resume_state is None and t.worker_slot == 1
    assert t.codex_session_id == SID
    assert s.resume_task(i, "t1", key="resume1") == resume
    assert s.reconnect_task(i, "t1")["session_id"] == SID
    assert h.exits == h.resumes == 1 and h.starts == 1
    with pytest.raises(LifecycleError, match="STALE_STOP"):
        s.stop_task(i, "t1", key="stop1", reason="Waiting for decision")
    assert h.exits == 1


def test_lost_exit_response_reconciles_actual_inactivity(setup):
    s, _, i, h, _, _ = setup
    original = h.exit_agent

    def lost(name):
        original(name)
        raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    h.exit_agent = lost
    assert s.stop_task(i, "t1", key="stop").status == "SUCCEEDED"
    assert s.store.get_task("t1").worker_slot is None and h.exits == 1


def test_lingering_child_blocks_release_until_actual_inactivity(setup, monkeypatch):
    s, _, i, h, _, p = setup
    p.lingering = True
    monkeypatch.setattr(
        "orchestrator.application.runtime_lifecycle_service.time.sleep", lambda _: None
    )
    clock = iter([0, 4])
    monkeypatch.setattr(
        "orchestrator.application.runtime_lifecycle_service.time.monotonic", lambda: next(clock, 4)
    )
    with pytest.raises(LifecycleError, match="STOP_UNCONFIRMED"):
        s.stop_task(i, "t1", key="stop")
    assert s.store.get_task("t1").worker_slot == 1
    assert s.store.get_task("t1").internal_status == TaskState.BLOCKED
    assert s.store.get_operation("p", "stop_runtime", "stop").error_code == "STOP_UNCONFIRMED"
    p.lingering = False
    assert s.stop_task(i, "t1", key="stop").status == "SUCCEEDED"
    assert h.exits == 1 and s.store.get_task("t1").worker_slot is None


def test_crash_after_exit_before_confirmation_reopens_without_second_exit(setup, monkeypatch):
    s, _, i, h, c, p = setup
    original = h.exit_agent

    def crash(name):
        original(name)
        raise RuntimeError("simulated crash after exit")

    monkeypatch.setattr(h, "exit_agent", crash)
    with pytest.raises(RuntimeError):
        s.stop_task(i, "t1", key="stop")
    assert s.store.get_task("t1").worker_slot == 1
    with StateStore(s.settings.sqlite_path) as db:
        result = RuntimeLifecycleService(s.settings, db, h, codex=c, processes=p).stop_task(
            i, "t1", key="stop"
        )
    assert result.status == "SUCCEEDED" and h.exits == 1


def test_active_turn_is_interrupted_before_tui_exit(setup):
    s, _, i, h, c, _ = setup
    h.status, c.active = "working", True
    assert s.stop_task(i, "t1", key="stop").status == "SUCCEEDED"
    assert h.interrupts == h.exits == 1


@pytest.mark.parametrize("status", ["blocked", "unknown"])
def test_no_input_to_approval_or_unknown_ui_and_slot_retained(setup, status):
    s, _, i, h, _, _ = setup
    h.status = status
    with pytest.raises(LifecycleError, match="NOT_STOPPABLE"):
        s.stop_task(i, "t1", key="stop")
    assert h.exits == h.interrupts == 0 and s.store.get_task("t1").worker_slot == 1


def test_missing_runtime_without_stop_intent_is_not_inactive_proof(setup):
    s, _, i, h, _, _ = setup
    h.agents.clear()
    for method in [s.reconnect_task, lambda a, t: s.stop_task(a, t, key="stop")]:
        with pytest.raises(LifecycleError, match="MISSING_UNCONFIRMED"):
            method(i, "t1")
    assert s.store.get_task("t1").worker_slot == 1 and h.exits == h.resumes == 0


def test_missing_saved_conversation_never_starts_replacement(setup):
    s, _, i, h, c, _ = setup
    s.stop_task(i, "t1", key="stop")
    c.missing = True
    with pytest.raises(LifecycleError, match="SESSION_UNAVAILABLE"):
        s.resume_task(i, "t1", key="resume")
    assert s.store.get_task("t1").worker_slot is None and h.resumes == 0


def test_capacity_checked_before_resume_effect_and_can_retry_same_key(setup):
    s, _, i, h, _, _ = setup
    s.stop_task(i, "t1", key="stop")
    for run, slot in [("t2", 1), ("t3", 2)]:
        s.store.update_runtime_metadata(
            s.store.get_task(run).model_copy(update={"worker_slot": slot})
        )
    with pytest.raises(LifecycleError, match="CAPACITY"):
        s.resume_task(i, "t1", key="resume")
    assert h.resumes == 0 and s.store.get_task("t1").worker_slot is None
    s.store.update_runtime_metadata(s.store.get_task("t3").model_copy(update={"worker_slot": None}))
    assert s.resume_task(i, "t1", key="resume").status == "SUCCEEDED"
    assert s.store.get_task("t1").worker_slot == 2


def test_crash_before_resume_call_is_unknown_and_never_blindly_starts(setup, monkeypatch):
    s, _, i, h, _, _ = setup
    s.stop_task(i, "t1", key="stop")

    def crash(*args):
        raise RuntimeError("simulated crash before start")

    monkeypatch.setattr(h, "resume_agent", crash)
    with pytest.raises(RuntimeError):
        s.resume_task(i, "t1", key="resume")
    with pytest.raises(LifecycleError, match="OUTCOME_UNKNOWN"):
        s.resume_task(i, "t1", key="resume")
    assert h.resumes == 0 and s.store.get_task("t1").worker_slot == 1
    with pytest.raises(LifecycleError, match="OPERATION_PENDING"):
        s.resume_task(i, "t1", key="other-resume")


def test_lost_resume_response_reuses_same_live_session(setup):
    s, _, i, h, _, _ = setup
    s.stop_task(i, "t1", key="stop")
    original = h.resume_agent

    def lost(*args):
        original(*args)
        raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    h.resume_agent = lost
    result = s.resume_task(i, "t1", key="resume")
    assert s.resume_task(i, "t1", key="resume") == result and h.resumes == 1


@pytest.mark.parametrize(
    "role,project,epic",
    [
        (Role.WORKER, "p", "e"),
        (Role.COORDINATOR, "p", None),
        (Role.INTEGRATION, "foreign", "e"),
        (Role.INTEGRATION, "p", "foreign"),
    ],
)
def test_wrong_principal_cannot_control_runtime(setup, role, project, epic):
    s, _, _, h, _, _ = setup
    a = Actor(actor_id="foreign", role=role, project_id=project, epic_run_id=epic, task_run_id="t1")
    count = s.store.db.total_changes
    with pytest.raises(LifecycleError):
        s.stop_task(a, "t1", key="stop")
    assert s.store.db.total_changes == count and h.exits == 0


def test_changed_process_and_binding_do_not_receive_exit(setup):
    s, _, i, h, _, _ = setup
    h.proc = [{"pid": 999, "start_time": "foreign"}]
    with pytest.raises(LifecycleError, match="PROCESS_CHANGED"):
        s.stop_task(i, "t1", key="stop")
    assert h.exits == 0 and s.store.get_task("t1").worker_slot == 1


def test_coordinator_can_stop_and_resume_owned_epic_only(setup):
    s, c, i, h, _, p = setup
    s.start.start_epic(c, "e")
    with pytest.raises(LifecycleError):
        s.stop_epic(i, "e", key="stop-epic")
    assert s.stop_epic(c, "e", key="stop-epic").status == "SUCCEEDED"
    assert s.resume_epic(c, "e", key="resume-epic").status == "SUCCEEDED"
    assert s.reconnect_epic(c, "e")["session_id"] == SID
    assert s.store.get_task("t1").worker_slot == 1


def test_process_groups_and_children_must_all_disappear_even_after_reparenting(monkeypatch):
    observer = ProcessObserver()
    records = {
        10: {"pid": 10, "parent": 1, "group": 10, "start_time": "shell"},
        20: {"pid": 20, "parent": 10, "group": 20, "start_time": "root"},
        30: {"pid": 30, "parent": 20, "group": 30, "start_time": "child"},
    }
    monkeypatch.setattr(observer, "scan", lambda: records)
    proof = observer.capture([{"pid": 20, "start_time": "root"}], 10)
    assert set(proof["groups"]) == {20, 30} and len(proof["identities"]) == 2
    del records[20]
    records[30]["parent"] = 1
    assert not observer.inactive(proof)
    del records[30]
    assert observer.inactive(proof)
    records[20] = {"pid": 20, "parent": 1, "group": 99, "start_time": "reused"}
    assert observer.inactive(proof)


def test_new_stop_key_after_inactivity_is_bound_to_that_generation(setup):
    s, _, i, h, _, _ = setup
    s.stop_task(i, "t1", key="stop1")
    alias = s.stop_task(i, "t1", key="stop2")
    assert alias.idempotency_key == "stop2" and h.exits == 1
    s.resume_task(i, "t1", key="resume1")
    with pytest.raises(LifecycleError, match="STALE_STOP"):
        s.stop_task(i, "t1", key="stop2")
    assert h.exits == 1


def test_concurrent_resume_operations_share_one_reserved_slot_and_one_start(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    s, _, i, h, c, p = setup
    s.stop_task(i, "t1", key="stop1")
    entered, release = Event(), Event()
    original = h.resume_agent

    def pause(*args):
        entered.set()
        assert release.wait(5)
        original(*args)

    monkeypatch.setattr(h, "resume_agent", pause)

    def resume():
        with StateStore(s.settings.sqlite_path) as db:
            return RuntimeLifecycleService(s.settings, db, h, codex=c, processes=p).resume_task(
                i, "t1", key="resume1"
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(resume)
        assert entered.wait(5)
        try:
            with pytest.raises(LifecycleError, match="OUTCOME_UNKNOWN"):
                s.resume_task(i, "t1", key="resume1")
            with pytest.raises(LifecycleError, match="OPERATION_PENDING"):
                s.resume_task(i, "t1", key="resume2")
        finally:
            release.set()
        result = future.result(timeout=10)
    assert s.resume_task(i, "t1", key="resume1") == result
    assert h.resumes == 1 and s.store.get_task("t1").worker_slot == 1
