import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from test_git_integration import COMMAND, approve, commit, fixture_phase, git
from test_git_integration import setup as integration_setup

from orchestrator.application.git_integration_service import IntegrationError
from orchestrator.application.state_service import StateService
from orchestrator.application.task_cleanup_service import TaskCleanupService
from orchestrator.application.worktree_service import WorktreeError
from orchestrator.domain.models import ExternalReference, TaskRun, TransitionEvent
from orchestrator.domain.policy import Actor, Role, VerifiedFacts
from orchestrator.domain.states import TaskState as T
from orchestrator.persistence.store import StateStore


@pytest.fixture
def setup(tmp_path):
    yield from integration_setup.__wrapped__(tmp_path)


def delivered(setup, *, probe=None):
    original, i, _, epic, tasks = setup
    task = tasks[0]
    approve(original, i, task)
    merged = original.merge_task_to_epic(i, task.id, key="merge")
    code = subprocess.run(COMMAND, cwd=epic.worktree_path, check=False).returncode
    assert code == 0
    task = StateService(original.store).transition_task(
        task.id,
        T.DONE,
        expected=T.MERGING,
        event_id="done",
        actor=i,
        facts=VerifiedFacts(
            source_commit=merged.result["source_commit"],
            target_commit=merged.result["target_commit"],
            merge_commit=merged.result["merge_commit"],
            verification_commit=merged.result["merge_commit"],
            tests_passed=code == 0,
        ),
    )
    service = TaskCleanupService(
        original.worktrees.settings, original.store, inactivity_probe=probe
    )
    return service, i, epic, task


def fixture_metadata(store, task, **changes):
    new = TaskRun.model_validate(task.model_dump() | changes)
    store.record_transition(
        new,
        TransitionEvent(
            id=str(uuid4()),
            project_id=task.project_id,
            epic_run_id=task.epic_run_id,
            task_run_id=task.id,
            request={"runtime_fixture": True},
            result={},
        ),
    )
    return new


def test_verified_unused_worktree_is_removed_with_branch_and_history_retained(setup):
    s, i, e, task = delivered(setup)
    main = s.git.head("main")
    reviews = s.store.get_reviews(task.id)
    operations = s.store.get_operations(e.id, kind="merge_task_to_epic")
    old = s.store.get_task(task.id)
    op = s.remove_task_worktree(i, task.id, key="cleanup")
    assert op.status == "SUCCEEDED" and op.result["branch_retained"] is True
    assert op.result["retention"] == "retain-branches-and-history"
    assert not Path(task.worktree_path).exists()
    assert Path(task.worktree_path) not in s.git.worktrees()
    assert s.git.head(task.branch) == task.current_commit
    assert s.git.head("main") == main
    assert s.store.get_task(task.id) == old
    assert s.store.get_reviews(task.id) == reviews
    assert s.store.get_operations(e.id, kind="merge_task_to_epic") == operations
    assert (Path(e.worktree_path) / "task1.txt").exists()
    assert s.remove_task_worktree(i, task.id, key="cleanup") == op
    assert s.remove_task_worktree(i, task.id, key="new-key") == op
    with StateStore(s.worktrees.settings.sqlite_path) as db:
        reopened = TaskCleanupService(s.worktrees.settings, db)
        assert reopened.remove_task_worktree(i, task.id, key="cleanup") == op


@pytest.mark.parametrize("timing", ["before", "after"])
def test_crash_around_removal_reconciles_without_loss_or_repeat(setup, monkeypatch, timing):
    s, i, _, task = delivered(setup)
    remove = s.git.remove_worktree

    def crash(path):
        if timing == "after":
            remove(path)
        raise RuntimeError("injected interruption")

    monkeypatch.setattr(s.git, "remove_worktree", crash)
    with pytest.raises(RuntimeError, match="interruption"):
        s.remove_task_worktree(i, task.id, key="crash")
    assert s.store.get_operation("p", "remove_task_worktree", "crash").status == "PENDING"
    assert Path(task.worktree_path).exists() == (timing == "before")
    with StateStore(s.worktrees.settings.sqlite_path) as db:
        reopened = TaskCleanupService(s.worktrees.settings, db)
        op = reopened.remove_task_worktree(i, task.id, key="crash")
        assert op.status == "SUCCEEDED" and not Path(task.worktree_path).exists()
        assert reopened.remove_task_worktree(i, task.id, key="crash") == op
        assert reopened.git.head(task.branch) == task.current_commit


@pytest.mark.parametrize("change", ["tracked", "untracked", "ignored", "hidden", "extra-commit"])
def test_unsaved_or_unintegrated_work_is_preserved(setup, change):
    s, i, _, task = delivered(setup)
    path = Path(task.worktree_path)
    if change == "tracked":
        (path / "base.txt").write_text("dirty\n")
    elif change == "untracked":
        (path / "new.txt").write_text("unsaved\n")
    elif change == "ignored":
        exclude = s.git.common_dir / "info/exclude"
        exclude.write_text(exclude.read_text() + "\nprivate.txt\n")
        (path / "private.txt").write_text("ignored but valuable\n")
    elif change == "hidden":
        git(path, "update-index", "--assume-unchanged", "base.txt")
        (path / "base.txt").write_text("hidden\n")
    else:
        (path / "new.txt").write_text("not integrated\n")
        commit(path, "later work")
    with pytest.raises((IntegrationError, WorktreeError)):
        s.remove_task_worktree(i, task.id, key="unsafe")
    assert path.is_dir()
    assert s.store.get_operations(task.epic_run_id, kind="remove_task_worktree") == []


@pytest.mark.parametrize(
    "actor",
    [
        Actor(
            actor_id="w", role=Role.WORKER, project_id="p", epic_run_id="epic", task_run_id="task1"
        ),
        Actor(actor_id="c", role=Role.COORDINATOR, project_id="p"),
        Actor(actor_id="i", role=Role.INTEGRATION, project_id="other", epic_run_id="epic"),
        Actor(actor_id="i", role=Role.INTEGRATION, project_id="p", epic_run_id="other"),
    ],
)
def test_wrong_principal_cannot_remove(setup, actor):
    s, _, _, task = delivered(setup)
    with pytest.raises(IntegrationError, match="Integration"):
        s.remove_task_worktree(actor, task.id, key="bad")
    assert Path(task.worktree_path).is_dir()


@pytest.mark.parametrize("state", [T.READY_FOR_REVIEW, T.MERGING])
def test_unfinished_task_cannot_remove_even_if_git_is_integrated(setup, state):
    s, i, _, task = delivered(setup)
    fixture_phase(s.store, task.id, state)
    with pytest.raises(IntegrationError, match="Done"):
        s.remove_task_worktree(i, task.id, key="unfinished")
    assert Path(task.worktree_path).is_dir()


@pytest.mark.parametrize("probe", [None, lambda _: False, lambda _: "inactive"])
def test_registered_runtime_without_actual_inactivity_confirmation_is_protected(setup, probe):
    s, i, _, task = delivered(setup, probe=probe)
    fixture_metadata(s.store, task, codex_session_id="session", worker_agent_id="agent")
    with pytest.raises(IntegrationError, match="inactivity"):
        s.remove_task_worktree(i, task.id, key="running")
    assert Path(task.worktree_path).is_dir()


def test_trusted_runtime_probe_can_confirm_inactivity_but_not_release_reserved_slot(setup):
    seen = []

    def probe(task):
        seen.append(task.codex_session_id)
        return True  # Explicit adapter fixture; no real Herdr stop is claimed here.

    s, i, _, task = delivered(setup, probe=probe)
    active = fixture_metadata(s.store, task, codex_session_id="session", worker_slot=1)
    with pytest.raises(IntegrationError, match="slot"):
        s.remove_task_worktree(i, task.id, key="reserved")
    fixture_metadata(s.store, active, worker_slot=None)
    op = s.remove_task_worktree(i, task.id, key="inactive")
    assert op.status == "SUCCEEDED" and seen == ["session", "session"]


def test_runtime_external_reference_without_run_field_still_requires_probe(setup):
    s, i, e, task = delivered(setup)
    s.store.add_reference(
        ExternalReference(
            project_id="p",
            epic_run_id=e.id,
            task_run_id=task.id,
            provider="Herdr",
            kind="pane",
            external_id="pane",
        )
    )
    with pytest.raises(IntegrationError, match="inactivity"):
        s.remove_task_worktree(i, task.id, key="referenced")
    assert Path(task.worktree_path).exists()


def test_other_run_claim_and_changed_owner_are_protected(setup):
    s, i, _, task = delivered(setup)
    other = s.store.get_task("task2")
    fixture_metadata(s.store, other, worktree_path=task.worktree_path)
    with pytest.raises(IntegrationError, match="another persisted"):
        s.remove_task_worktree(i, task.id, key="shared")
    fixture_metadata(s.store, s.store.get_task("task2"), worktree_path=other.worktree_path)
    s.git.set_owner(task.branch, "foreign")
    with pytest.raises(IntegrationError, match="ownership"):
        s.remove_task_worktree(i, task.id, key="owner")
    assert Path(task.worktree_path).exists()


def test_missing_resource_without_intent_is_not_attributed_to_cleanup(setup):
    s, i, _, task = delivered(setup)
    s.git.remove_worktree(Path(task.worktree_path))
    with pytest.raises(IntegrationError, match="without a cleanup intent"):
        s.remove_task_worktree(i, task.id, key="unknown")
    assert s.store.get_operations(task.epic_run_id, kind="remove_task_worktree") == []


def test_reappeared_resource_after_success_is_not_removed_again(setup):
    s, i, _, task = delivered(setup)
    s.remove_task_worktree(i, task.id, key="cleanup")
    s.git.add_worktree(
        Path(task.worktree_path), task.branch, task.current_commit, branch_exists=True
    )
    with pytest.raises(IntegrationError, match="reappeared"):
        s.remove_task_worktree(i, task.id, key="cleanup")
    assert Path(task.worktree_path).exists()


def test_locked_worktree_and_failed_probe_are_protected(setup):
    s, i, _, task = delivered(setup)
    path = Path(task.worktree_path)
    git(s.worktrees.settings.repository, "worktree", "lock", str(path))
    with pytest.raises(IntegrationError, match="unlocked"):
        s.remove_task_worktree(i, task.id, key="locked")
    assert path.exists()
    git(s.worktrees.settings.repository, "worktree", "unlock", str(path))
    fixture_metadata(s.store, task, codex_session_id="session")

    def broken(_):
        raise RuntimeError("raw adapter output must not escape")

    s.inactivity_probe = broken
    with pytest.raises(IntegrationError, match="inactivity"):
        s.remove_task_worktree(i, task.id, key="probe-error")
    assert path.exists()


def test_completed_cleanup_replay_needs_no_new_runtime_probe(setup):
    calls = []

    def probe(task):
        calls.append(task.id)
        return True

    s, i, _, task = delivered(setup, probe=probe)
    fixture_metadata(s.store, task, codex_session_id="session")
    op = s.remove_task_worktree(i, task.id, key="cleanup")
    assert calls == [task.id, task.id]

    def unavailable(_):
        raise RuntimeError("runtime already stopped and archived")

    s.inactivity_probe = unavailable
    assert s.remove_task_worktree(i, task.id, key="cleanup") == op
    assert s.remove_task_worktree(i, task.id, key="new-key") == op
    assert calls == [task.id, task.id]
