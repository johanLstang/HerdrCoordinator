import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from test_persistence import epic, task

from orchestrator.application.state_service import StateService
from orchestrator.domain.coordinator import CoordinatorRun, ProjectOperation
from orchestrator.domain.models import ExternalReference, Operation, Review, utc_now
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import EpicState
from orchestrator.persistence.store import StateStore, StoreError


def coordinator(**changes):
    return CoordinatorRun(**(dict(
        id="coordinator", project_id="project", actor_id="owner",
        worktree_path="/tmp/project", initial_commit="a" * 40,
        configuration_hash="b" * 64,
    ) | changes))


def test_project_records_survive_reopen_without_fabricated_epic(tmp_path):
    path = tmp_path / "state.db"
    run = coordinator()
    operation = ProjectOperation(
        project_id="project", coordinator_run_id=run.id, kind="claim",
        idempotency_key="request", result={"stage": "RESERVED"},
    )
    with StateStore(path) as store:
        store.add_coordinator(run)
        store.add_project_operation(operation)
    with StateStore(path) as store:
        assert store.get_coordinator(run.id) == run
        assert store.get_project_operation("project", "claim", "request") == operation
        assert store.project_operations(run.id, kind="claim") == [operation]
        assert store.get_runs() == []
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_database_alone_rejects_second_active_epic_claim_across_connections(tmp_path):
    path = tmp_path / "state.db"
    with StateStore(path) as first, StateStore(path) as second:
        first.add_coordinator(coordinator())
        claim = ProjectOperation(
            project_id="project", coordinator_run_id="coordinator", kind="epic_claim",
            idempotency_key="first", result={"stage": "RESERVED"},
        )
        first.add_project_operation(claim)
        with pytest.raises(StoreError, match="uniqueness"):
            second.add_project_operation(claim.model_copy(update={
                "id": "second", "idempotency_key": "second",
            }))
        first.update_project_operation(claim.model_copy(update={"status": "FAILED"}))
        with pytest.raises(StoreError, match="uniqueness"):
            second.add_project_operation(claim.model_copy(update={
                "id": "third", "idempotency_key": "third",
            }))
        assert len(first.project_operations("coordinator", kind="epic_claim")) == 1


def test_concurrent_connections_have_one_project_owner(tmp_path):
    path = tmp_path / "state.db"
    with StateStore(path):
        pass
    barrier = Barrier(2)

    def acquire(name):
        with StateStore(path) as store:
            barrier.wait(timeout=10)
            try:
                store.add_coordinator(coordinator(id=name))
                return True
            except StoreError:
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(acquire, name) for name in ("first", "second")]
        assert sorted(future.result(timeout=15) for future in futures) == [False, True]
    with StateStore(path) as store:
        assert len(store.project_coordinators("project")) == 1


@pytest.mark.parametrize("changes", [
    {"project_id": "foreign"}, {"coordinator_run_id": "missing"},
])
def test_project_operation_enforces_actual_project_parent(tmp_path, changes):
    with StateStore(tmp_path / "state.db") as store:
        store.add_coordinator(coordinator())
        with pytest.raises(StoreError, match="relation"):
            store.add_project_operation(ProjectOperation(**(dict(
                project_id="project", coordinator_run_id="coordinator", kind="claim",
                idempotency_key="request",
            ) | changes)))
        assert store.project_operations("coordinator", kind="claim") == []


def test_project_identity_and_stopped_owner_cannot_be_adopted(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        original = coordinator(coordinator_agent_id="native-owner")
        store.add_coordinator(original)
        for change in ({"actor_id": "foreign"}, {"coordinator_agent_id": "new-agent"}):
            with pytest.raises(StoreError, match="identity"):
                store.update_coordinator(original.model_copy(update=change))
        stopped = original.model_copy(update={"status": "STOPPED", "completed_at": utc_now()})
        store.update_coordinator(stopped)
        with pytest.raises(StoreError, match="reopened"):
            store.update_coordinator(original)
        store.add_coordinator(coordinator(id="successor"))
        assert store.get_coordinator(original.id) == stopped


@pytest.mark.parametrize("version", [1, 2])
def test_additive_migration_preserves_all_old_payload_bytes(tmp_path, version):
    path = tmp_path / "state.db"
    with StateStore(path) as store:
        store.add_epic(epic())
        store.add_task(task())
        store.add_review(Review(
            task_run_id="task-run", review_number=1, review_result="APPROVED",
            review_commit="b" * 40, epic_commit="a" * 40, feedback="Preserve Åäö\nline two",
        ))
        store.add_operation(Operation(
            project_id="test-project", epic_run_id="epic-run", task_run_id="task-run",
            kind="migration-fixture", idempotency_key="preserve", result={"literal": "e\u0301"},
        ))
        store.add_reference(ExternalReference(
            project_id="test-project", epic_run_id="epic-run", task_run_id="task-run",
            provider="fixture", kind="session", external_id="original-session",
        ))
        StateService(store).transition_epic(
            "epic-run", EpicState.ACTIVE, expected=EpicState.PLANNED, event_id="original-event",
            actor=Actor(actor_id="owner", role=Role.COORDINATOR, project_id="test-project"),
        )
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE project_operations")
        db.execute("DROP TABLE coordinator_runs")
        if version == 1:
            db.execute("DROP TABLE transition_events")
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        before = {name: db.execute(f"SELECT * FROM {name}").fetchall() for name in tables}
        ddl = {name: db.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (name,)
        ).fetchone()[0] for name in tables}
        db.execute(f"PRAGMA user_version = {version}")
    with StateStore(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3
        for name in tables:
            assert store.db.execute(f"SELECT * FROM {name}").fetchall() == before[name]
            assert store.db.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()[0] == ddl[name]
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        store.add_coordinator(coordinator())


def test_failed_migration_rolls_back_version_and_ddl(tmp_path):
    path = tmp_path / "state.db"
    with StateStore(path):
        pass
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE coordinator_runs")
        db.execute("PRAGMA user_version = 2")
    with pytest.raises(StoreError):
        StateStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='coordinator_runs'"
        ).fetchone() is None
