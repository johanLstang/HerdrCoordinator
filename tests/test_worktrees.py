import io
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from orchestrator.adapters.git import GitError
from orchestrator.application.worktree_service import WorktreeError, WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.policy import Actor, Role
from orchestrator.event_log import EventLog
from orchestrator.persistence.store import StateStore


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "repository with spaces"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "file.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "base")
    settings = Settings(
        repository=repo,
        worktree_root=tmp_path / "trees with spaces",
        sqlite_path=tmp_path / "state.db",
    )
    with StateStore(settings.sqlite_path) as store:
        service = WorktreeService(settings, store, log=EventLog(stream=io.StringIO()))
        coordinator = Actor(actor_id="c", role=Role.COORDINATOR, project_id="p")
        integration = Actor(actor_id="i", role=Role.INTEGRATION, project_id="p", epic_run_id="epic")
        yield service, coordinator, integration


def create_epic(service, coordinator):
    return service.create_epic_worktree(coordinator, epic_id="E-01", run_id="epic")


def test_epic_and_two_tasks_use_current_source_heads_without_touching_main(setup):
    service, c, i = setup
    base = git(service.settings.repository, "rev-parse", "main")
    epic = create_epic(service, c)
    epic_path = Path(epic.worktree_path)
    (epic_path / "epic.txt").write_text("epic change\n")
    git(epic_path, "add", ".")
    git(epic_path, "commit", "--quiet", "-m", "epic change")
    current_epic = git(epic_path, "rev-parse", "HEAD")
    tasks = [
        service.create_task_worktree(i, epic_run_id="epic", task_id=f"F-0{n}", run_id=f"task{n}")
        for n in (1, 2)
    ]
    assert epic.branch == "feature/epic-e01"
    assert {task.branch for task in tasks} == {"task/e01-f01", "task/e01-f02"}
    assert {task.base_commit for task in tasks} == {current_epic}
    assert epic.base_commit == base
    assert len({epic.worktree_path, *(task.worktree_path for task in tasks)}) == 3
    for task in tasks:
        assert git(Path(task.worktree_path), "rev-parse", "HEAD") == current_epic
        assert (Path(task.worktree_path) / "epic.txt").read_text() == "epic change\n"
    assert git(service.settings.repository, "rev-parse", "main") == base
    assert git(service.settings.repository, "status", "--porcelain") == ""


def test_retry_after_reopen_returns_same_resources_even_with_worker_changes(setup):
    service, c, i = setup
    epic = create_epic(service, c)
    task = service.create_task_worktree(i, epic_run_id="epic", task_id="F-01", run_id="task")
    task_path = Path(task.worktree_path)
    (task_path / "file.txt").write_text("worker commit\n")
    git(task_path, "add", ".")
    git(task_path, "commit", "--quiet", "-m", "worker commit")
    (task_path / "uncommitted.txt").write_text("preserve me")
    changes = service.store.db.total_changes
    assert create_epic(service, c) == epic
    assert service.store.db.total_changes == changes
    with StateStore(service.settings.sqlite_path) as reopened:
        new_service = WorktreeService(service.settings, reopened)
        assert (
            new_service.create_task_worktree(i, epic_run_id="epic", task_id="F-01", run_id="task")
            == task
        )
        assert len(new_service.git.worktrees()) == 3
        assert reopened.get_operation("p", "create_task_worktree", "task").status == "SUCCEEDED"
    assert (task_path / "uncommitted.txt").read_text() == "preserve me"


@pytest.mark.parametrize("identity", ["../escape", "--force", "E-01;touch planted", "bad\nname"])
def test_invalid_identity_has_no_database_or_git_effects(setup, identity):
    service, c, _ = setup
    changes = service.store.db.total_changes
    with pytest.raises(WorktreeError):
        service.create_epic_worktree(c, epic_id=identity, run_id="epic")
    assert service.store.db.total_changes == changes
    assert len(service.git.worktrees()) == 1


def test_paths_outside_root_traversal_and_symlink_are_rejected(setup, tmp_path):
    service, c, _ = setup
    changes = service.store.db.total_changes
    paths = [tmp_path / "outside", service.settings.worktree_root / "p/../../escape"]
    root = service.settings.worktree_root
    root.mkdir()
    (root / "p").symlink_to(tmp_path, target_is_directory=True)
    paths.append(root / "p/epic-e01")
    for path in paths:
        with pytest.raises(WorktreeError):
            service.create_epic_worktree(c, epic_id="E-01", run_id="epic", path=path)
    assert service.store.db.total_changes == changes
    assert len(service.git.worktrees()) == 1


def test_occupied_branch_and_foreign_worktree_are_not_adopted(setup, tmp_path):
    service, c, _ = setup
    git(service.settings.repository, "branch", "feature/epic-e01")
    existing = tmp_path / "foreign tree"
    git(service.settings.repository, "worktree", "add", str(existing), "feature/epic-e01")
    (existing / "uncommitted").write_text("preserve")
    changes = service.store.db.total_changes
    with pytest.raises(WorktreeError, match="occupied"):
        create_epic(service, c)
    assert service.store.db.total_changes == changes
    assert (existing / "uncommitted").read_text() == "preserve"
    assert service.git.owner("feature/epic-e01") is None


def test_wrong_role_epic_project_and_reused_run_identity_are_rejected(setup):
    service, c, i = setup
    epic = create_epic(service, c)
    changes = service.store.db.total_changes
    worker = Actor(actor_id="w", role=Role.WORKER, project_id="p", epic_run_id="epic")
    foreign = Actor(actor_id="i", role=Role.INTEGRATION, project_id="other", epic_run_id="epic")
    calls = [
        lambda: service.create_epic_worktree(i, epic_id="E-02", run_id="another"),
        lambda: service.create_task_worktree(
            worker, epic_run_id="epic", task_id="F-01", run_id="t"
        ),
        lambda: service.create_task_worktree(i, epic_run_id="wrong", task_id="F-01", run_id="t"),
        lambda: service.create_task_worktree(
            foreign, epic_run_id="epic", task_id="F-01", run_id="t"
        ),
        lambda: service.create_epic_worktree(c, epic_id="E-02", run_id="epic"),
    ]
    for call in calls:
        with pytest.raises(WorktreeError):
            call()
    assert service.store.db.total_changes == changes
    assert service.store.get_epic("epic") == epic
    assert len(service.git.worktrees()) == 2


@pytest.mark.parametrize("failure", ["before_git", "after_branch", "after_worktree", "database"])
@pytest.mark.parametrize("resource", ["epic", "task"])
def test_retry_reconciles_partial_side_effects_without_duplicate_resources(
    setup, monkeypatch, failure, resource
):
    service, c, i = setup
    if resource == "task":
        create_epic(service, c)

    def create(manager):
        return (
            create_epic(manager, c)
            if resource == "epic"
            else manager.create_task_worktree(i, epic_run_id="epic", task_id="F-01", run_id="task")
        )

    kind = f"create_{resource}_worktree"
    real_add = service.git.add_worktree
    real_complete = service.store.complete_worktree_creation

    def interrupted_add(path, branch, base, *, branch_exists):
        if failure == "after_branch":
            git(service.settings.repository, "branch", branch, base)
        elif failure == "after_worktree":
            real_add(path, branch, base, branch_exists=branch_exists)
        raise RuntimeError("injected interruption")

    def interrupted_complete(record, operation):
        real_complete(record, operation)
        raise RuntimeError("injected after database writes")

    if failure == "database":
        monkeypatch.setattr(service.store, "complete_worktree_creation", interrupted_complete)
    else:
        monkeypatch.setattr(service.git, "add_worktree", interrupted_add)
    with pytest.raises(RuntimeError, match="injected"):
        create(service)
    intent = service.store.get_operation("p", kind, resource)
    assert intent.status == "PENDING"
    pending = (
        service.store.get_epic(resource) if resource == "epic" else service.store.get_task(resource)
    )
    assert pending.current_commit is None
    monkeypatch.setattr(service.git, "add_worktree", real_add)
    monkeypatch.setattr(service.store, "complete_worktree_creation", real_complete)
    with StateStore(service.settings.sqlite_path) as reopened:
        recovery = WorktreeService(service.settings, reopened)
        result = create(recovery)
        assert len(recovery.git.worktrees()) == (2 if resource == "epic" else 3)
        assert recovery.git.owner(result.branch) == intent.id
        assert reopened.get_operation("p", kind, resource).status == "SUCCEEDED"


def test_missing_or_tampered_completed_resource_is_not_recreated(setup):
    service, c, _ = setup
    epic = create_epic(service, c)
    service.git.set_owner(epic.branch, "foreign-owner")
    with pytest.raises(WorktreeError, match="ownership"):
        create_epic(service, c)
    assert len(service.git.worktrees()) == 2
    operation = service.store.get_operation("p", "create_epic_worktree", "epic")
    service.git.set_owner(epic.branch, operation.id)
    git(service.settings.repository, "worktree", "remove", epic.worktree_path)
    with pytest.raises(WorktreeError, match="missing"):
        create_epic(service, c)
    assert len(service.git.worktrees()) == 1


def test_git_environment_and_checkout_hook_cannot_redirect_creation(setup, tmp_path, monkeypatch):
    service, c, _ = setup
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "planted-secret-directory"))
    hook = service.git.common_dir / "hooks/post-checkout"
    planted = tmp_path / "hook-ran"
    hook.write_text(f"#!/bin/sh\ntouch '{planted}'\n")
    hook.chmod(0o755)
    result = create_epic(service, c)
    assert Path(result.worktree_path).is_dir()
    assert not planted.exists()


def test_dirty_or_wrong_source_branch_is_rejected_before_intent(setup):
    service, c, _ = setup
    repo = service.settings.repository
    (repo / "untracked").write_text("preserve")
    with pytest.raises(GitError, match="clean"):
        create_epic(service, c)
    assert service.store.get_epic("epic") is None
    (repo / "untracked").unlink()
    git(repo, "checkout", "-b", "other")
    with pytest.raises(GitError, match="branch"):
        create_epic(service, c)
    assert service.store.get_epic("epic") is None


def test_concurrent_identical_creation_has_one_run_branch_worktree_and_operation(setup):
    service, c, _ = setup
    barrier = Barrier(2)

    def create():
        with StateStore(service.settings.sqlite_path) as store:
            concurrent = WorktreeService(service.settings, store)
            barrier.wait(timeout=5)
            return create_epic(concurrent, c)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        records = [future.result(timeout=15) for future in futures]
    assert records[0] == records[1]
    assert len(service.git.worktrees()) == 2
    assert service.store.db.execute("SELECT count(*) FROM epic_runs").fetchone()[0] == 1
    assert service.store.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


def test_pending_creation_does_not_silently_follow_changed_main(setup, monkeypatch):
    service, c, _ = setup
    original = service.git.add_worktree

    def interrupt(*args, **kwargs):
        raise RuntimeError("interruption")

    monkeypatch.setattr(service.git, "add_worktree", interrupt)
    with pytest.raises(RuntimeError):
        create_epic(service, c)
    base = service.store.get_epic("epic").base_commit
    repo = service.settings.repository
    (repo / "new-main.txt").write_text("new main")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "new main")
    monkeypatch.setattr(service.git, "add_worktree", original)
    with pytest.raises(WorktreeError, match="source base changed"):
        create_epic(service, c)
    assert service.store.get_epic("epic").base_commit == base
    assert service.store.get_operation("p", "create_epic_worktree", "epic").status == "PENDING"
    assert len(service.git.worktrees()) == 1


def test_existing_destination_files_are_preserved(setup):
    service, c, _ = setup
    path = service.settings.worktree_root / "p/epic-e01"
    path.mkdir(parents=True)
    (path / "precious.txt").write_text("preserve")
    with pytest.raises(WorktreeError, match="occupied"):
        create_epic(service, c)
    assert (path / "precious.txt").read_text() == "preserve"
    assert service.store.get_epic("epic") is None


def test_new_tasks_cannot_change_epic_scope_during_final_review(setup):
    service, c, i = setup
    epic = create_epic(service, c)
    # A reviewed epic is an explicit fixture; no final approval or merge is fabricated.
    reviewed = type(epic).model_validate(epic.model_dump() | {"status": "REVIEWING"})
    service.store.db.execute(
        "UPDATE epic_runs SET status=?, payload=? WHERE id=?",
        (reviewed.status, reviewed.model_dump_json(), epic.id),
    )
    changes = service.store.db.total_changes
    with pytest.raises(WorktreeError, match="source epic"):
        service.create_task_worktree(i, epic_run_id="epic", task_id="F-01", run_id="task")
    assert service.store.db.total_changes == changes
    assert service.store.get_task("task") is None
    assert len(service.git.worktrees()) == 2
