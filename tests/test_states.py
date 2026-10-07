import sqlite3

import pytest

from orchestrator.application.state_service import StateError, StateService
from orchestrator.domain.models import EpicRun, Review, TaskRun
from orchestrator.domain.policy import Actor, Role, VerifiedFacts
from orchestrator.domain.states import EpicState as E
from orchestrator.domain.states import TaskState as T
from orchestrator.domain.states import task_kanban
from orchestrator.persistence.store import SCHEMA_VERSION, StateStore


@pytest.fixture
def runtime(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        store.add_epic(
            EpicRun(
                id="epic",
                project_id="p",
                epic_id="e",
                branch="feature/e",
                worktree_path="/tmp/test/epic",
                status=E.ACTIVE,
                current_commit="c" * 40,
            )
        )
        store.add_task(
            TaskRun(
                id="task",
                project_id="p",
                epic_run_id="epic",
                task_id="t",
                branch="task/e/t",
                worktree_path="/tmp/test/task",
                current_commit="b" * 40,
                worker_slot=1,
                codex_session_id="session",
            )
        )
        yield store, StateService(store)


def integration(**changes):
    return Actor(
        **(
            dict(actor_id="integration", role=Role.INTEGRATION, project_id="p", epic_run_id="epic")
            | changes
        )
    )


def coordinator():
    return Actor(actor_id="coordinator", role=Role.COORDINATOR, project_id="p")


def move(service, target, expected, event, **facts):
    return service.transition_task(
        "task",
        target,
        expected=expected,
        event_id=event,
        actor=integration(),
        facts=VerifiedFacts(**facts),
    )


def working(service):
    move(service, T.CLAIMED, T.PLANNED, "claim", dependencies_ready=True)
    move(service, T.STARTING, T.CLAIMED, "start", slot_reserved=True)
    move(service, T.WORKING, T.STARTING, "confirmed", start_confirmed=True)


@pytest.mark.parametrize(
    "state,column",
    [
        ("PLANNED", "Planned"),
        ("CLAIMED", "Planned"),
        ("STARTING", "Planned"),
        ("WORKING", "Active"),
        ("READY_FOR_REVIEW", "Active"),
        ("REVIEWING", "Active"),
        ("CHANGES_REQUESTED", "Active"),
        ("APPROVED", "Active"),
        ("MERGING", "Active"),
        ("BLOCKED", "Attention"),
        ("PARKED", "Attention"),
        ("DONE", "Done"),
    ],
)
def test_documented_mapping(state, column):
    assert task_kanban(T(state)) == column


def test_working_cannot_be_done_and_state_persists(runtime):
    store, service = runtime
    working(service)
    with pytest.raises(StateError, match="not allowed"):
        move(service, T.DONE, T.WORKING, "illegal")
    assert store.get_task("task").internal_status == "WORKING"
    assert store.get_event("p", "illegal") is None


def test_review_fix_park_resume_and_idempotent_replay(runtime):
    store, service = runtime
    working(service)
    move(
        service,
        T.READY_FOR_REVIEW,
        T.WORKING,
        "ready",
        tests_passed=True,
        verification_commit="b" * 40,
    )
    move(service, T.REVIEWING, T.READY_FOR_REVIEW, "review")
    move(service, T.CHANGES_REQUESTED, T.REVIEWING, "fix", reason="missing acceptance")
    move(service, T.WORKING, T.CHANGES_REQUESTED, "back")
    first = move(service, T.BLOCKED, T.WORKING, "blocked", reason="input missing")
    with pytest.raises(StateError, match="inactivity"):
        move(service, T.PARKED, T.BLOCKED, "unsafe-park")
    move(service, T.PARKED, T.BLOCKED, "parked", inactivity_confirmed=True)
    move(service, T.WORKING, T.PARKED, "resume", slot_reserved=True, start_confirmed=True)
    replay = move(service, T.BLOCKED, T.WORKING, "blocked", reason="input missing")
    assert replay == first  # Historical result returned; current state is never downgraded.
    assert store.get_task("task").internal_status == "WORKING"
    assert (
        store.db.execute(
            "SELECT count(*) FROM transition_events WHERE event_id='blocked'"
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(StateError, match="another request"):
        move(service, T.BLOCKED, T.WORKING, "blocked", reason="different input")


def test_done_requires_current_review_actual_merge_and_test_commit(runtime):
    store, service = runtime
    working(service)
    move(
        service,
        T.READY_FOR_REVIEW,
        T.WORKING,
        "ready",
        tests_passed=True,
        verification_commit="b" * 40,
    )
    move(service, T.REVIEWING, T.READY_FOR_REVIEW, "review")
    with pytest.raises(StateError, match="missing"):
        move(service, T.APPROVED, T.REVIEWING, "no-review")
    store.add_review(
        Review(
            task_run_id="task",
            review_number=1,
            review_result="APPROVED",
            review_commit="a" * 40,
            epic_commit="c" * 40,
        )
    )
    with pytest.raises(StateError, match="current"):
        move(service, T.APPROVED, T.REVIEWING, "stale-review")
    store.add_review(
        Review(
            task_run_id="task",
            review_number=2,
            review_result="APPROVED",
            review_commit="b" * 40,
            epic_commit="c" * 40,
        )
    )
    move(service, T.APPROVED, T.REVIEWING, "approval")
    with pytest.raises(StateError, match="reviewed commits"):
        move(
            service,
            T.MERGING,
            T.APPROVED,
            "stale-merge",
            source_commit="a" * 40,
            target_commit="c" * 40,
        )
    move(service, T.MERGING, T.APPROVED, "merging", source_commit="b" * 40, target_commit="c" * 40)
    move(
        service,
        T.MERGING,
        T.MERGING,
        "partial",
        source_commit="b" * 40,
        target_commit="c" * 40,
        merge_commit="d" * 40,
    )
    assert store.get_task("task").merge_commit == "d" * 40
    with pytest.raises(StateError, match="conflicts"):
        move(
            service,
            T.MERGING,
            T.MERGING,
            "overwrite",
            source_commit="b" * 40,
            target_commit="c" * 40,
            merge_commit="e" * 40,
        )
    assert store.get_task("task").merge_commit == "d" * 40
    with pytest.raises(StateError, match="must not be repeated"):
        move(service, T.APPROVED, T.MERGING, "repeat-merge")
    with pytest.raises(StateError, match="verification"):
        move(
            service,
            T.DONE,
            T.MERGING,
            "failed-tests",
            source_commit="b" * 40,
            target_commit="c" * 40,
            merge_commit="d" * 40,
        )
    result = move(
        service,
        T.DONE,
        T.MERGING,
        "done",
        source_commit="b" * 40,
        target_commit="c" * 40,
        merge_commit="d" * 40,
        tests_passed=True,
        verification_commit="d" * 40,
    )
    assert result.completed_at is not None
    assert result.kanban_status == "Done"


def test_epic_done_without_main_merge_is_rejected(runtime):
    store, service = runtime
    with pytest.raises(StateError, match="not allowed"):
        service.transition_epic(
            "epic", E.DONE, expected=E.ACTIVE, event_id="no-main", actor=coordinator()
        )
    assert store.get_epic("epic").status == "ACTIVE"
    with pytest.raises(StateError, match="incomplete"):
        service.transition_epic(
            "epic",
            E.READY_FOR_REVIEW,
            expected=E.ACTIVE,
            event_id="incomplete",
            actor=integration(),
        )


def test_epic_merge_and_completion_require_exact_evidence(runtime):
    store, service = runtime
    # This fixture isolates final-review guards; adapters supply actual facts in later epics.
    store.db.execute(
        "UPDATE epic_runs SET status='MERGING', payload=? WHERE id='epic'",
        (
            store.get_epic("epic")
            .model_copy(
                update={
                    "status": E.MERGING,
                    "approved_source_commit": "c" * 40,
                    "approved_target_commit": "a" * 40,
                }
            )
            .model_dump_json(),
        ),
    )
    with pytest.raises(StateError, match="main merge"):
        service.transition_epic(
            "epic",
            E.DONE,
            expected=E.MERGING,
            event_id="no-merge",
            actor=coordinator(),
            facts=VerifiedFacts(source_commit="c" * 40, target_commit="a" * 40),
        )
    result = service.transition_epic(
        "epic",
        E.DONE,
        expected=E.MERGING,
        event_id="final",
        actor=coordinator(),
        facts=VerifiedFacts(
            source_commit="c" * 40,
            target_commit="a" * 40,
            merge_commit="f" * 40,
            tests_passed=True,
            verification_commit="f" * 40,
        ),
    )
    assert result.merge_commit == "f" * 40
    assert result.completed_at is not None


@pytest.mark.parametrize(
    "actor",
    [
        integration(project_id="other"),
        integration(epic_run_id="other"),
        Actor(
            actor_id="worker",
            role=Role.WORKER,
            project_id="p",
            epic_run_id="epic",
            task_run_id="other",
        ),
        coordinator(),
    ],
)
def test_wrong_role_or_scope_cannot_change_task(runtime, actor):
    store, service = runtime
    with pytest.raises(StateError):
        service.transition_task(
            "task",
            T.CLAIMED,
            expected=T.PLANNED,
            event_id="denied",
            actor=actor,
            facts=VerifiedFacts(dependencies_ready=True),
        )
    assert store.get_task("task").internal_status == "PLANNED"
    assert store.get_event("p", "denied") is None


def test_state_and_event_rollback_together(runtime, monkeypatch):
    store, service = runtime
    original = store.record_transition

    def injected(*args):
        original(*args)
        raise RuntimeError("injected after update and event")

    monkeypatch.setattr(store, "record_transition", injected)
    with pytest.raises(RuntimeError, match="injected"):
        move(service, T.CLAIMED, T.PLANNED, "failed", dependencies_ready=True)
    assert store.get_task("task").internal_status == "PLANNED"
    assert store.get_event("p", "failed") is None


def test_schema_one_migrates_and_replays_after_reopen(tmp_path):
    path = tmp_path / "state.db"
    with StateStore(path) as store:
        store.add_epic(
            EpicRun(id="e", project_id="p", epic_id="e", branch="feature/e", worktree_path="/tmp/e")
        )
        store.db.execute("DROP TABLE transition_events")
        store.db.execute("PRAGMA user_version=1")
    with StateStore(path) as store:
        service = StateService(store)
        service.transition_epic(
            "e", E.ACTIVE, expected=E.PLANNED, event_id="started", actor=coordinator()
        )
    with StateStore(path) as store:
        replay = StateService(store).transition_epic(
            "e", E.ACTIVE, expected=E.PLANNED, event_id="started", actor=coordinator()
        )
        assert replay.status == "ACTIVE"
        assert store.get_epic("e").branch == "feature/e"
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM transition_events").fetchone()[0] == 1


def test_unknown_state_is_not_mapped_to_active():
    with pytest.raises(ValueError):
        task_kanban("UNKNOWN")


def test_new_changes_requested_review_revokes_previous_approval(runtime):
    store, service = runtime
    approved = store.get_task("task").model_copy(
        update={
        "internal_status": T.APPROVED,
        "kanban_status": task_kanban(T.APPROVED),
            "approved_source_commit": "b" * 40,
            "approved_target_commit": "c" * 40,
        }
    )
    store.db.execute(
        "UPDATE task_runs SET internal_status='APPROVED', payload=? WHERE id='task'",
        (approved.model_dump_json(),),
    )
    store.add_review(
        Review(
            task_run_id="task",
            review_number=1,
            review_result="CHANGES_REQUESTED",
            review_commit="b" * 40,
            epic_commit="c" * 40,
        )
    )
    with pytest.raises(StateError, match="latest review"):
        move(
            service,
            T.MERGING,
            T.APPROVED,
            "revoked",
            source_commit="b" * 40,
            target_commit="c" * 40,
        )
    assert store.get_task("task").internal_status == "APPROVED"
