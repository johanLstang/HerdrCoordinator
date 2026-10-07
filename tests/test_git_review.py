import hashlib
import io
import subprocess
from pathlib import Path

import pytest

from orchestrator.adapters.git import GitError
from orchestrator.application.git_review_service import GitReviewError, GitReviewService
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.policy import Actor, Role
from orchestrator.event_log import EventLog
from orchestrator.persistence.store import StateStore


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(path, name="change"):
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", name)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "repository with spaces"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "file.txt").write_text("base\n")
    commit(repo, "base")
    settings = Settings(
        repository=repo, worktree_root=tmp_path / "trees with spaces",
        sqlite_path=tmp_path / "state.db",
    )
    with StateStore(settings.sqlite_path) as store:
        worktrees = WorktreeService(settings, store, log=EventLog(stream=io.StringIO()))
        c = Actor(actor_id="c", role=Role.COORDINATOR, project_id="p")
        i = Actor(actor_id="i", role=Role.INTEGRATION, project_id="p", epic_run_id="epic")
        epic = worktrees.create_epic_worktree(c, epic_id="E-01", run_id="epic")
        task = worktrees.create_task_worktree(
            i, epic_run_id="epic", task_id="F-01", run_id="task"
        )
        yield GitReviewService(settings, store), c, i, epic, task


def test_known_commit_reports_pinned_diff_files_identity_and_branch_content(setup):
    service, _, i, epic, task = setup
    path = Path(task.worktree_path)
    (path / "file.txt").write_text("implemented\n")
    (path / "new file.txt").write_text("new\n")
    current = commit(path)
    changes = service.store.db.total_changes
    evidence = service.task_review(i, task.id, expected_commit=current)
    source = evidence.source
    assert source.current_commit == current
    assert source.base_commit == epic.base_commit == evidence.target_commit
    assert source.repository == str(service.worktrees.settings.repository)
    assert source.git_common_dir == str(service.git.common_dir)
    assert source.worktree_path == task.worktree_path
    assert source.branch == task.branch
    assert source.contains_base and source.stable and evidence.reviewable
    assert [(f.path, f.status, f.additions, f.deletions) for f in source.changed_files] == [
        ("file.txt", "M", 1, 1), ("new file.txt", "A", 1, 0),
    ]
    assert b"-base\n+implemented\n" in source.commit_diff.patch
    assert b"+new\n" in source.commit_diff.patch
    assert source.commit_diff.complete
    assert not source.changes and not source.staged_diff.patch and not source.unstaged_diff.patch
    assert service.git.contains_commit(task.branch, current)
    assert not service.git.contains_commit(epic.branch, current)
    assert service.store.db.total_changes == changes
    # The recovery command retrieves precisely the counted/hashed full patch.
    full = subprocess.run(
        source.commit_diff.full_command, cwd=path, env=service.git._environment(),
        capture_output=True, check=True,
    ).stdout
    assert full == source.commit_diff.patch
    assert hashlib.sha256(full).hexdigest() == source.commit_diff.sha256


def test_staged_unstaged_and_untracked_are_separate_and_block_review(setup):
    service, _, i, _, task = setup
    path = Path(task.worktree_path)
    (path / "file.txt").write_text("committed\n")
    current = commit(path)
    (path / "file.txt").write_text("staged\n")
    git(path, "add", "file.txt")
    (path / "file.txt").write_text("unstaged\n")
    (path / "untracked\nfile.txt").write_text("not part of commit")
    evidence = service.task_review(i, task.id)
    source = evidence.source
    assert source.current_commit == current and source.dirty and not evidence.reviewable
    assert b"+committed\n" in source.commit_diff.patch
    assert b"+staged\n" in source.staged_diff.patch
    assert b"+unstaged\n" in source.unstaged_diff.patch
    assert {change.path for change in source.changes} == {"file.txt", "untracked\nfile.txt"}
    assert {change.path for change in source.changes if change.index_status == "?"} == {
        "untracked\nfile.txt"
    }
    assert b"not part of commit" not in source.commit_diff.patch


def test_binary_and_unusual_paths_are_preserved(setup):
    service, _, i, _, task = setup
    path = Path(task.worktree_path)
    (path / "binary.bin").write_bytes(b"\x00\xffbinary\x00")
    (path / "tab\tnewline\nquote\".txt").write_text("text\n")
    commit(path)
    evidence = service.task_review(i, task.id)
    files = {f.path: f for f in evidence.source.changed_files}
    assert files["binary.bin"].binary
    assert files["binary.bin"].additions is None
    assert "tab\tnewline\nquote\".txt" in files
    assert b"GIT binary patch" in evidence.source.commit_diff.patch
    assert evidence.source.commit_diff.complete and evidence.reviewable


def test_staged_rename_preserves_both_paths_and_is_not_committed_diff(setup):
    service, _, i, _, task = setup
    path = Path(task.worktree_path)
    git(path, "mv", "file.txt", "renamed\tfile.txt")
    evidence = service.task_review(i, task.id)
    assert not evidence.reviewable and not evidence.source.changed_files
    change, = evidence.source.changes
    assert change.index_status == "R" and change.original_path == "file.txt"
    assert change.path == "renamed\tfile.txt"
    assert not evidence.source.commit_diff.patch and evidence.source.staged_diff.patch


def test_large_diff_explicitly_blocks_review_and_can_be_retrieved_whole(setup):
    service, _, i, _, task = setup
    path = Path(task.worktree_path)
    (path / "large.txt").write_text("line\n" * 220_000)
    current = commit(path)
    preview = service.task_review(i, task.id)
    diff = preview.source.commit_diff
    assert len(diff.patch) == 1024 * 1024 and diff.total_bytes > 1024 * 1024
    assert not diff.complete and not preview.reviewable
    full = service.task_review(i, task.id, max_diff_bytes=diff.total_bytes)
    assert full.reviewable and full.source.current_commit == current
    assert full.source.commit_diff.complete
    assert len(full.source.commit_diff.patch) == diff.total_bytes
    assert full.source.commit_diff.sha256 == diff.sha256
    assert full.source.commit_diff.full_command == diff.full_command


@pytest.mark.parametrize("value", ["0" * 40, "HEAD", "--output=/tmp/planted", "main^{commit}"])
def test_unknown_or_unsafe_commit_rejected_without_raw_input(setup, value):
    service, _, _, _, task = setup
    with pytest.raises(GitError) as error:
        service.git.snapshot(Path(task.worktree_path), task.branch, value)
    assert value not in str(error.value)


def test_non_commit_and_unknown_branch_are_rejected(setup):
    service, _, _, _, task = setup
    path = Path(task.worktree_path)
    blob = git(path, "rev-parse", "HEAD:file.txt")
    with pytest.raises(GitError, match="not a commit"):
        service.git.require_commit(blob)
    with pytest.raises(GitError, match="branch does not exist"):
        service.git.contains_commit("missing", task.base_commit)


def test_foreign_repository_and_wrong_worktree_branch_are_rejected(setup, tmp_path):
    service, _, _, _, task = setup
    foreign = tmp_path / "other repository"
    # Even an identical clone containing the same SHA/branch has a different Git identity.
    git(tmp_path, "clone", "--quiet", str(service.git.repository), str(foreign))
    git(foreign, "checkout", "-b", task.branch)
    with pytest.raises(GitError, match="not registered"):
        service.git.snapshot(foreign, task.branch, task.base_commit)
    with pytest.raises(GitError, match="not registered"):
        service.git.snapshot(Path(task.worktree_path), "main", task.base_commit)


def test_stale_expected_commit_and_unsynced_epic_cannot_pass(setup):
    service, _, i, epic, task = setup
    path = Path(task.worktree_path)
    (path / "file.txt").write_text("task\n")
    commit(path)
    with pytest.raises(GitError, match="expected review commit"):
        service.task_review(i, task.id, expected_commit=task.base_commit)
    (Path(epic.worktree_path) / "epic.txt").write_text("later epic change\n")
    commit(Path(epic.worktree_path))
    evidence = service.task_review(i, task.id)
    assert not evidence.source.contains_base and not evidence.reviewable


@pytest.mark.parametrize("role,project,epic", [
    (Role.WORKER, "p", "epic"), (Role.INTEGRATION, "other", "epic"),
    (Role.INTEGRATION, "p", "another"),
])
def test_role_and_scope_denied_before_reading_worktree(setup, monkeypatch, role, project, epic):
    service, _, _, _, task = setup
    actor = Actor(actor_id="untrusted", role=role, project_id=project, epic_run_id=epic)
    calls = []
    monkeypatch.setattr(service.git, "snapshot", lambda *a, **kw: calls.append(a))
    before = service.store.db.total_changes
    with pytest.raises(GitReviewError):
        service.task_review(actor, task.id)
    assert not calls and service.store.db.total_changes == before


def test_changed_ownership_is_rejected_before_diff(setup, monkeypatch):
    service, _, i, _, task = setup
    service.git.set_owner(task.branch, "another-operation")
    calls = []
    monkeypatch.setattr(service.git, "snapshot", lambda *a, **kw: calls.append(a))
    with pytest.raises(GitReviewError, match="ownership"):
        service.task_review(i, task.id)
    assert not calls


def test_coordinator_epic_evidence_uses_main_and_dirty_target_blocks(setup):
    service, c, i, epic, _ = setup
    path = Path(epic.worktree_path)
    (path / "epic.txt").write_text("epic\n")
    current = commit(path)
    evidence = service.epic_review(c, epic.id, expected_commit=current)
    assert evidence.reviewable and evidence.task_run_id is None
    assert evidence.target_branch == "main"
    assert evidence.target_commit == epic.base_commit
    assert evidence.source.current_commit == current
    (service.git.repository / "dirty.txt").write_text("main work")
    assert not service.epic_review(c, epic.id).reviewable
    with pytest.raises(GitReviewError, match="only Coordinator"):
        service.epic_review(i, epic.id)


def test_head_change_during_collection_marks_unstable(setup, monkeypatch):
    service, _, i, _, task = setup
    path = Path(task.worktree_path)
    original = service.git._diff
    changed = False

    def moving_diff(*args, **kwargs):
        nonlocal changed
        diff = original(*args, **kwargs)
        if not changed:
            changed = True
            (path / "late.txt").write_text("late commit\n")
            commit(path)
        return diff

    monkeypatch.setattr(service.git, "_diff", moving_diff)
    evidence = service.task_review(i, task.id)
    assert not evidence.source.stable and not evidence.reviewable


def test_target_change_during_collection_marks_unstable(setup, monkeypatch):
    service, _, i, epic, task = setup
    original = service.git.snapshot

    def moving_target(*args, **kwargs):
        source = original(*args, **kwargs)
        (Path(epic.worktree_path) / "late.txt").write_text("late epic commit\n")
        commit(Path(epic.worktree_path))
        return source

    monkeypatch.setattr(service.git, "snapshot", moving_target)
    evidence = service.task_review(i, task.id)
    assert not evidence.target_stable and not evidence.reviewable


def test_index_changes_with_same_status_still_invalidate_observation(setup, monkeypatch):
    service, _, i, _, task = setup
    path = Path(task.worktree_path)
    (path / "file.txt").write_text("first staged version\n")
    git(path, "add", "file.txt")
    original = service.git._diff
    changed = False

    def moving_index(diff_path, revisions, limit):
        nonlocal changed
        diff = original(diff_path, revisions, limit)
        if revisions and revisions[0] == "--cached" and not changed:
            changed = True
            (path / "file.txt").write_text("second staged version\n")
            git(path, "add", "file.txt")
        return diff

    monkeypatch.setattr(service.git, "_diff", moving_index)
    evidence = service.task_review(i, task.id)
    assert not evidence.source.stable and not evidence.reviewable


def test_owner_change_during_collection_is_rejected(setup, monkeypatch):
    service, _, i, _, task = setup
    original = service.git.snapshot

    def moving_owner(*args, **kwargs):
        source = original(*args, **kwargs)
        service.git.set_owner(task.branch, "changed-owner")
        return source

    monkeypatch.setattr(service.git, "snapshot", moving_owner)
    with pytest.raises(GitReviewError, match="ownership"):
        service.task_review(i, task.id)


def test_no_external_diff_textconv_environment_or_index_refresh(setup, monkeypatch, tmp_path):
    service, _, i, _, task = setup
    path = Path(task.worktree_path)
    marker = tmp_path / "executed"
    script = tmp_path / "external.sh"
    script.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    script.chmod(0o700)
    git(path, "config", "diff.external", str(script))
    git(path, "config", "diff.evil.textconv", str(script))
    (path / ".gitattributes").write_text("*.txt diff=evil\n")
    (path / "file.txt").write_text("changed\n")
    commit(path)
    index = Path(git(path, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    before = index.stat().st_mtime_ns
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(script))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nonexistent"))
    evidence = service.task_review(i, task.id)
    assert evidence.reviewable and b"+changed" in evidence.source.commit_diff.patch
    assert not marker.exists() and index.stat().st_mtime_ns == before


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, 64 * 1024 * 1024 + 1])
def test_invalid_diff_limits_rejected(setup, limit):
    service, _, i, _, task = setup
    with pytest.raises(GitError, match="positive integer"):
        service.task_review(i, task.id, max_diff_bytes=limit)
