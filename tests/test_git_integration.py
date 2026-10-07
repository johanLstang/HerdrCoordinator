import io
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from orchestrator.adapters.git import GitError
from orchestrator.application.git_integration_service import GitIntegrationService, IntegrationError
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.models import Review, TaskRun, TransitionEvent
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import TaskState, task_kanban
from orchestrator.event_log import EventLog
from orchestrator.persistence.store import StateStore

COMMAND = (sys.executable, "-c", "from pathlib import Path; assert Path('base.txt').is_file()")


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(path, name="change"):
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", name)
    return git(path, "rev-parse", "HEAD")


def fixture_phase(store, task_id, phase):
    """Only runtime handoff is a fixture; all Git/SQLite/test processes are real."""
    old = store.get_task(task_id)
    new = TaskRun.model_validate(
        old.model_dump()
        | {
            "internal_status": phase,
            "kanban_status": task_kanban(phase),
        }
    )
    store.record_transition(
        new,
        TransitionEvent(
            id=str(uuid4()),
            project_id=old.project_id,
            epic_run_id=old.epic_run_id,
            task_run_id=old.id,
            request={"runtime_handoff_fixture": True},
            result={},
        ),
    )


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "repository with spaces"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "base.txt").write_text("base\n")
    commit(repo, "base")
    settings = Settings(
        repository=repo,
        worktree_root=tmp_path / "trees",
        sqlite_path=tmp_path / "state.db",
    )
    with StateStore(settings.sqlite_path) as store:
        creation = WorktreeService(settings, store, log=EventLog(stream=io.StringIO()))
        c = Actor(actor_id="c", role=Role.COORDINATOR, project_id="p")
        i = Actor(actor_id="i", role=Role.INTEGRATION, project_id="p", epic_run_id="epic")
        epic = creation.create_epic_worktree(c, epic_id="E-01", run_id="epic")
        tasks = [
            creation.create_task_worktree(
                i,
                epic_run_id=epic.id,
                task_id=f"F-0{n}",
                run_id=f"task{n}",
            )
            for n in (1, 2)
        ]
        for task in tasks:
            path = Path(task.worktree_path)
            (path / f"{task.id}.txt").write_text(f"{task.id}\n")
            commit(path)
            fixture_phase(store, task.id, TaskState.READY_FOR_REVIEW)
        yield GitIntegrationService(settings, store, test_command=COMMAND), i, c, epic, tasks


def approve(service, actor, task, suffix=""):
    verified = service.verify_task(actor, task.id, key=f"verify-{task.id}{suffix}")
    assert verified.status == "SUCCEEDED" and verified.result["exit_code"] == 0
    return service.register_task_review(
        actor,
        task.id,
        verification_key=verified.idempotency_key,
        key=f"review-{task.id}{suffix}",
        approved=True,
    )


def test_parallel_tasks_sync_invalidates_old_approval_and_delivers_both(setup):
    service, i, _, epic, (a, b) = setup
    approve(service, i, a)
    old_review = approve(service, i, b)
    merged_a = service.merge_task_to_epic(i, a.id, key="merge-a")
    with pytest.raises(IntegrationError, match="actual current commits"):
        service.merge_task_to_epic(i, b.id, key="stale-b")
    synced = service.sync_task_with_epic(i, b.id, key="sync-b")
    b_record = service.store.get_task(b.id)
    assert synced.status == "SUCCEEDED"
    assert b_record.current_commit != old_review.review_commit
    assert b_record.approved_source_commit is None and b_record.approved_target_commit is None
    assert b_record.internal_status == TaskState.CHANGES_REQUESTED
    assert service.git.contains_commit(b.branch, merged_a.result["merge_commit"])
    assert (Path(b.worktree_path) / "task1.txt").read_text() == "task1\n"
    with pytest.raises(IntegrationError):
        service.merge_task_to_epic(i, b.id, key="still-stale-b")
    fixture_phase(service.store, b.id, TaskState.READY_FOR_REVIEW)
    approve(service, i, b, "-synced")
    merged_b = service.merge_task_to_epic(i, b.id, key="merge-b")
    assert service.git.parents(merged_b.result["merge_commit"]) == (
        merged_a.result["merge_commit"],
        b_record.current_commit,
    )
    assert service.store.get_task(b.id).internal_status == TaskState.MERGING
    assert service.store.get_task(b.id).completed_at is None
    assert (Path(epic.worktree_path) / "task1.txt").exists()
    assert (Path(epic.worktree_path) / "task2.txt").exists()


def test_delivery_and_sync_are_idempotent_after_process_reopen(setup):
    service, i, _, epic, (task, _) = setup
    synced = service.sync_task_with_epic(i, task.id, key="sync")
    assert synced.result["merge_commit"] == git(Path(task.worktree_path), "rev-parse", "HEAD")
    approve(service, i, task)
    delivered = service.merge_task_to_epic(i, task.id, key="merge")
    head = git(Path(epic.worktree_path), "rev-parse", "HEAD")
    with StateStore(service.worktrees.settings.sqlite_path) as reopened:
        other = GitIntegrationService(service.worktrees.settings, reopened, test_command=COMMAND)
        assert other.merge_task_to_epic(i, task.id, key="merge") == delivered
        assert other.sync_task_with_epic(i, task.id, key="sync") == synced
        assert git(Path(epic.worktree_path), "rev-parse", "HEAD") == head


def test_crash_after_git_merge_is_recovered_without_second_merge(setup, monkeypatch):
    service, i, _, epic, (task, _) = setup
    approve(service, i, task)
    original = service.git.merge_commit

    def interrupted(*args):
        original(*args)
        raise RuntimeError("simulated process death after Git")

    monkeypatch.setattr(service.git, "merge_commit", interrupted)
    with pytest.raises(RuntimeError, match="simulated process death"):
        service.merge_task_to_epic(i, task.id, key="merge")
    head = git(Path(epic.worktree_path), "rev-parse", "HEAD")
    assert service.store.get_operation("p", "merge_task_to_epic", "merge").status == "PENDING"
    with StateStore(service.worktrees.settings.sqlite_path) as reopened:
        other = GitIntegrationService(service.worktrees.settings, reopened, test_command=COMMAND)
        recovered = other.merge_task_to_epic(i, task.id, key="merge")
        assert recovered.status == "SUCCEEDED" and recovered.result["merge_commit"] == head
        assert reopened.get_task(task.id).internal_status == TaskState.MERGING
        assert reopened.get_task(task.id).completed_at is None
        assert reopened.get_task(task.id).merge_commit == head
    assert git(Path(epic.worktree_path), "rev-parse", "HEAD") == head


def test_sync_crash_after_git_can_be_recovered(setup, monkeypatch):
    service, i, _, _, (a, b) = setup
    approve(service, i, a)
    service.merge_task_to_epic(i, a.id, key="merge-a")
    original = service.git.merge_commit

    def interrupted(*args):
        original(*args)
        raise RuntimeError("sync interrupted")

    monkeypatch.setattr(service.git, "merge_commit", interrupted)
    with pytest.raises(RuntimeError, match="sync interrupted"):
        service.sync_task_with_epic(i, b.id, key="sync-b")
    head = git(Path(b.worktree_path), "rev-parse", "HEAD")
    with StateStore(service.worktrees.settings.sqlite_path) as reopened:
        other = GitIntegrationService(service.worktrees.settings, reopened, test_command=COMMAND)
        recovered = other.sync_task_with_epic(i, b.id, key="sync-b")
        assert recovered.result["merge_commit"] == head
        assert reopened.get_task(b.id).current_commit == head


def test_crash_before_git_reuses_committed_intent(setup, monkeypatch):
    service, i, _, epic, (task, _) = setup
    approve(service, i, task)
    original = service.git.merge_commit

    def interrupted(*args):
        raise RuntimeError("before Git")

    monkeypatch.setattr(service.git, "merge_commit", interrupted)
    with pytest.raises(RuntimeError, match="before Git"):
        service.merge_task_to_epic(i, task.id, key="merge")
    op = service.store.get_operation("p", "merge_task_to_epic", "merge")
    assert op.status == "PENDING"
    assert git(Path(epic.worktree_path), "rev-list", "--count", "--merges", "HEAD") == "0"
    monkeypatch.setattr(service.git, "merge_commit", original)
    result = service.merge_task_to_epic(i, task.id, key="merge")
    assert result.id == op.id and result.status == "SUCCEEDED"
    assert git(Path(epic.worktree_path), "rev-list", "--count", "--merges", "HEAD") == "1"


def test_runtime_approval_without_actual_test_and_review_operations_cannot_merge(setup):
    service, i, _, epic, (task, _) = setup
    source = git(Path(task.worktree_path), "rev-parse", "HEAD")
    target = git(Path(epic.worktree_path), "rev-parse", "HEAD")
    fixture_phase(service.store, task.id, TaskState.APPROVED)
    service._patch(
        service.store.get_task(task.id),
        current_commit=source,
        approved_source_commit=source,
        approved_target_commit=target,
    )
    service.store.add_review(
        Review(
            task_run_id=task.id,
            review_number=1,
            review_result="APPROVED",
            review_commit=source,
            epic_commit=target,
        )
    )
    with pytest.raises(IntegrationError, match="actual current commits"):
        service.merge_task_to_epic(i, task.id, key="fake-evidence")
    assert git(Path(epic.worktree_path), "rev-parse", "HEAD") == target


def test_review_or_test_configuration_changes_cannot_reuse_old_evidence(setup):
    service, i, _, _, (task, _) = setup
    approve(service, i, task)
    other = GitIntegrationService(
        service.worktrees.settings,
        service.store,
        test_command=(sys.executable, "-c", "assert True"),
    )
    with pytest.raises(IntegrationError, match="actual current commits"):
        other.merge_task_to_epic(i, task.id, key="different-tests")


def test_sync_conflict_is_preserved_and_task_not_done(setup):
    service, i, _, epic, (a, b) = setup
    for task in (a, b):
        (Path(task.worktree_path) / "base.txt").write_text(f"conflicting {task.id}\n")
        commit(Path(task.worktree_path))
    approve(service, i, a)
    service.merge_task_to_epic(i, a.id, key="merge-a")
    main_before = git(service.git.repository, "rev-parse", "main")
    conflicted = service.sync_task_with_epic(i, b.id, key="sync-b")
    assert conflicted.status == "CONFLICT" and conflicted.error_code == "MERGE_CONFLICT"
    assert service.store.get_task(b.id).internal_status == TaskState.BLOCKED
    assert service.store.get_task(b.id).completed_at is None
    assert service.git.in_progress(Path(b.worktree_path))
    assert "<<<<<<<" in (Path(b.worktree_path) / "base.txt").read_text()
    assert not service.git.in_progress(Path(epic.worktree_path))
    assert git(service.git.repository, "rev-parse", "main") == main_before
    with pytest.raises(IntegrationError):
        service.merge_task_to_epic(i, b.id, key="blocked-merge")


@pytest.mark.parametrize(
    "role,project,epic_id",
    [
        (Role.WORKER, "p", "epic"),
        (Role.COORDINATOR, "p", "epic"),
        (Role.INTEGRATION, "other", "epic"),
        (Role.INTEGRATION, "p", "wrong"),
    ],
)
def test_wrong_role_or_scope_has_no_git_or_database_effects(setup, role, project, epic_id):
    service, _, _, epic, (task, _) = setup
    actor = Actor(actor_id="wrong", role=role, project_id=project, epic_run_id=epic_id)
    before = service.store.db.total_changes
    head = git(Path(epic.worktree_path), "rev-parse", "HEAD")
    for action in (service.verify_task, service.sync_task_with_epic, service.merge_task_to_epic):
        with pytest.raises(IntegrationError, match="registered Integration"):
            action(actor, task.id, key="denied")
    assert service.store.db.total_changes == before
    assert git(Path(epic.worktree_path), "rev-parse", "HEAD") == head


@pytest.mark.parametrize("location,hidden", [("task", False), ("epic", False), ("task", True)])
def test_dirty_or_hidden_work_prevents_mutation(setup, location, hidden):
    service, i, _, epic, (task, _) = setup
    approve(service, i, task)
    path = Path(task.worktree_path if location == "task" else epic.worktree_path)
    if hidden:
        git(path, "update-index", "--assume-unchanged", "base.txt")
    (path / "base.txt").write_text("dirty\n")
    with pytest.raises(IntegrationError, match="clean"):
        service.merge_task_to_epic(i, task.id, key="dirty")
    assert service.store.get_task(task.id).internal_status == TaskState.APPROVED
    assert service.store.get_operation("p", "merge_task_to_epic", "dirty") is None


def test_changed_source_and_changed_main_approval_are_rejected(setup):
    service, i, _, epic, (task, _) = setup
    approve(service, i, task)
    (Path(task.worktree_path) / "later.txt").write_text("new task commit\n")
    commit(Path(task.worktree_path))
    head = git(Path(epic.worktree_path), "rev-parse", "HEAD")
    with pytest.raises(IntegrationError, match="actual current commits"):
        service.merge_task_to_epic(i, task.id, key="stale")
    assert git(Path(epic.worktree_path), "rev-parse", "HEAD") == head


def test_failed_tests_or_stale_test_evidence_cannot_approve(setup):
    service, i, _, _, (task, _) = setup
    failing = GitIntegrationService(
        service.worktrees.settings,
        service.store,
        test_command=(sys.executable, "-c", "raise SystemExit(2)"),
    )
    result = failing.verify_task(i, task.id, key="failing")
    assert result.status == "FAILED" and result.result["exit_code"] == 2
    with pytest.raises(IntegrationError, match="actual current test"):
        failing.register_task_review(
            i,
            task.id,
            verification_key="failing",
            key="bad-review",
            approved=True,
        )
    service.verify_task(i, task.id, key="passing")
    (Path(task.worktree_path) / "later.txt").write_text("later\n")
    commit(Path(task.worktree_path))
    with pytest.raises(IntegrationError, match="actual current test"):
        service.register_task_review(
            i,
            task.id,
            verification_key="passing",
            key="stale-review",
            approved=True,
        )
    assert not service.store.get_reviews(task.id)


def test_test_command_that_changes_worktree_does_not_produce_approval_evidence(setup):
    service, i, _, _, (task, _) = setup
    mutating = GitIntegrationService(
        service.worktrees.settings,
        service.store,
        test_command=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('dirty').write_text('x')",
        ),
    )
    result = mutating.verify_task(i, task.id, key="mutating")
    assert result.status == "FAILED" and result.error_code == "STALE_TEST_EVIDENCE"


def test_operation_keys_cannot_change_task_or_decision(setup):
    service, i, _, _, (a, b) = setup
    approve(service, i, a)
    with pytest.raises(IntegrationError, match="another decision"):
        service.register_task_review(
            i,
            a.id,
            verification_key=f"verify-{a.id}",
            key=f"review-{a.id}",
            approved=False,
            feedback="change",
        )
    service.sync_task_with_epic(i, a.id, key="shared")
    with pytest.raises(IntegrationError, match="another task"):
        service.sync_task_with_epic(i, b.id, key="shared")


def test_concurrent_duplicate_requests_make_one_merge(setup):
    service, i, _, epic, (task, _) = setup
    approve(service, i, task)

    def deliver():
        with StateStore(service.worktrees.settings.sqlite_path) as store:
            parallel = GitIntegrationService(
                service.worktrees.settings, store, test_command=COMMAND
            )
            return parallel.merge_task_to_epic(i, task.id, key="same")

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: deliver(), range(2)))
    assert results[0].result["merge_commit"] == results[1].result["merge_commit"]
    assert git(Path(epic.worktree_path), "rev-list", "--count", "--merges", "HEAD") == "1"


def test_external_merge_driver_does_not_execute_or_discard_work(setup, tmp_path):
    service, i, _, _, (task, _) = setup
    approve(service, i, task)
    marker = tmp_path / "executed"
    git(Path(task.worktree_path), "config", "merge.evil.driver", f"touch {marker}")
    with pytest.raises(GitError, match="external merge or filter"):
        service.merge_task_to_epic(i, task.id, key="external")
    assert not marker.exists()
    assert service.store.get_operation("p", "merge_task_to_epic", "external").status == "PENDING"
    assert service.store.get_task(task.id).internal_status == TaskState.MERGING


def test_changed_ownership_rejects_even_fresh_approval(setup):
    service, i, _, _, (task, _) = setup
    approve(service, i, task)
    service.git.set_owner(task.branch, "foreign")
    with pytest.raises(RuntimeError, match="ownership"):
        service.merge_task_to_epic(i, task.id, key="foreign")
