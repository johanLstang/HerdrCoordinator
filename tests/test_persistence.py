import sqlite3
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orchestrator.domain.models import EpicRun, ExternalReference, Operation, Review, TaskRun
from orchestrator.persistence.store import StateStore, StoreError


def epic(**changes):
    return EpicRun(
        id="epic-run",
        project_id="test-project",
        epic_id="test-epic",
        branch="feature/epic-test",
        worktree_path="/tmp/herdr-test/epic",
        **changes,
    )


def task(**changes):
    fields = dict(
        id="task-run",
        project_id="test-project",
        epic_run_id="epic-run",
        task_id="test-task",
        branch="task/test/task",
        worktree_path="/tmp/herdr-test/task",
        internal_status="WORKING",
        kanban_status="Active",
        worker_slot=1,
        worker_agent_id="agent-test",
        herdr_workspace_id="workspace-test",
        codex_session_id="session-test",
        base_commit="a" * 40,
        current_commit="b" * 40,
        started_at=datetime(2026, 10, 7, 12, tzinfo=UTC),
    )
    return TaskRun(**(fields | changes))


def test_runs_reviews_operations_and_references_survive_reopen(tmp_path):
    path = tmp_path / "state.db"
    with StateStore(path) as store:
        store.add_epic(epic())
        store.add_task(task())
        for number, result in [(1, "CHANGES_REQUESTED"), (2, "APPROVED")]:
            store.add_review(
                Review(
                    task_run_id="task-run",
                    review_number=number,
                    review_result=result,
                    review_commit="b" * 40,
                    epic_commit="c" * 40,
                    feedback=f"review {number}",
                )
            )
        store.add_operation(
            Operation(
                project_id="test-project",
                epic_run_id="epic-run",
                task_run_id="task-run",
                kind="start",
                idempotency_key="start-1",
                result={"workspace_id": "workspace-test"},
            )
        )
        store.add_reference(
            ExternalReference(
                project_id="test-project",
                epic_run_id="epic-run",
                task_run_id="task-run",
                provider="herdr",
                kind="session",
                external_id="session-test",
            )
        )
    with StateStore(path) as reopened:
        saved = reopened.get_task("task-run")
        assert reopened.get_epic("epic-run").branch == "feature/epic-test"
        assert saved.project_id == "test-project"
        assert saved.epic_run_id == "epic-run"
        assert saved.worker_slot == 1
        assert saved.codex_session_id == "session-test"
        assert saved.base_commit == "a" * 40
        assert saved.current_commit == "b" * 40
        assert saved.started_at.isoformat() == "2026-10-07T12:00:00+00:00"
        assert [
            (r.review_number, r.review_result, r.feedback) for r in reopened.get_reviews("task-run")
        ] == [(1, "CHANGES_REQUESTED", "review 1"), (2, "APPROVED", "review 2")]
        assert reopened.get_operation("test-project", "start", "start-1").result == {
            "workspace_id": "workspace-test"
        }
        assert reopened.get_references("epic-run")[0].external_id == "session-test"
        reopened.initialize()
        assert reopened.get_task("task-run").codex_session_id == "session-test"


@pytest.mark.parametrize("changes", [{"epic_run_id": "missing"}, {"project_id": "other"}])
def test_invalid_parent_rolls_back_whole_unit_of_work(tmp_path, changes):
    with StateStore(tmp_path / "state.db") as store:
        with pytest.raises(StoreError, match="relation"):
            with store.transaction():
                store.add_epic(epic())
                store.add_task(task(**changes))
        assert store.get_epic("epic-run") is None
        assert store.get_task("task-run") is None


def test_two_connections_cannot_own_same_task(tmp_path):
    path = tmp_path / "state.db"
    with StateStore(path) as first, StateStore(path) as second:
        first.add_epic(epic())
        first.add_task(task())
        with pytest.raises(StoreError, match="uniqueness"):
            second.add_task(task(id="duplicate"))
        assert first.get_task("duplicate") is None
        assert first.get_task("task-run").worker_agent_id == "agent-test"


def test_unknown_schema_version_is_rejected_without_change(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
        db.execute("CREATE TABLE future (value TEXT)")
        db.execute("INSERT INTO future VALUES ('preserve')")
    with pytest.raises(StoreError, match="unsupported.*version"):
        StateStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99
        assert db.execute("SELECT value FROM future").fetchone()[0] == "preserve"
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='epic_runs'").fetchone() is None
        )


def test_injected_failure_rolls_back_and_reopen_is_clean(tmp_path):
    path = tmp_path / "state.db"
    with StateStore(path) as store:
        with pytest.raises(RuntimeError, match="injected"):
            with store.transaction():
                store.add_epic(epic())
                raise RuntimeError("injected")
    with StateStore(path) as store:
        assert store.get_epic("epic-run") is None


def test_review_sequence_and_external_ownership_constraints(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        store.add_epic(epic())
        store.add_task(task())
        review = Review(
            task_run_id="task-run",
            review_number=1,
            review_result="APPROVED",
            review_commit="a" * 40,
            epic_commit="b" * 40,
        )
        store.add_review(review)
        with pytest.raises(StoreError):
            store.add_review(review.model_copy(update={"id": "different"}))
        with pytest.raises(StoreError):
            store.add_reference(
                ExternalReference(
                    project_id="other-project",
                    epic_run_id="epic-run",
                    task_run_id="task-run",
                    provider="herdr",
                    kind="session",
                    external_id="session",
                )
            )
        assert len(store.get_reviews("task-run")) == 1
        assert store.get_references("epic-run") == []


def test_naive_times_are_rejected():
    with pytest.raises(ValidationError):
        task(started_at=datetime(2026, 10, 7))
