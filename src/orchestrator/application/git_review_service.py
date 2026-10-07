"""Role-bound, read-only Git evidence. Review decisions remain a separate feature."""

from dataclasses import dataclass
from pathlib import Path

from orchestrator.adapters.git import GitAdapter
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.worktree_service import WorktreeError, WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.git_facts import GitSnapshot, WorktreeChange
from orchestrator.domain.policy import Actor, Role
from orchestrator.persistence.store import StateStore


class GitReviewError(RuntimeError):
    """Safe scope/ownership rejection, without diff contents or credentials."""


@dataclass(frozen=True)
class ReviewEvidence:
    project_id: str
    epic_run_id: str
    task_run_id: str | None
    source: GitSnapshot
    target_branch: str
    target_worktree_path: str
    target_commit: str
    target_changes: tuple[WorktreeChange, ...]
    target_unsafe_index_paths: tuple[str, ...]
    target_stable: bool

    @property
    def reviewable(self) -> bool:
        return (
            self.source.reviewable and self.target_stable and not self.target_changes
            and not self.target_unsafe_index_paths
        )


class GitReviewService:
    def __init__(self, settings: Settings, store: StateStore):
        self.store = store
        self.worktrees = WorktreeService(settings, store)
        self.git = self.worktrees.git

    def task_review(
        self, actor: Actor, task_run_id: str, *, expected_commit: str | None = None,
        max_diff_bytes: int = GitAdapter.DEFAULT_DIFF_BYTES,
    ) -> ReviewEvidence:
        task = self.store.get_task(task_run_id)
        if task is None or actor.role not in {Role.INTEGRATION, Role.COORDINATOR}:
            raise GitReviewError("task review scope is not authorized")
        self._authorize(actor, task)
        epic = self.store.get_epic(task.epic_run_id)
        if epic is None:
            raise GitReviewError("epic run does not exist")
        self._authorize(actor, epic)
        self._owned(task)
        target = self._owned(epic)
        evidence = self._evidence(
            epic.project_id, epic.id, task.id, Path(task.worktree_path), task.branch,
            Path(epic.worktree_path), epic.branch, target, expected_commit, max_diff_bytes,
        )
        self._owned(task)
        self._owned(epic)
        return evidence

    def epic_review(
        self, actor: Actor, epic_run_id: str, *, expected_commit: str | None = None,
        max_diff_bytes: int = GitAdapter.DEFAULT_DIFF_BYTES,
    ) -> ReviewEvidence:
        epic = self.store.get_epic(epic_run_id)
        if epic is None or actor.role != Role.COORDINATOR:
            raise GitReviewError("only Coordinator may review an epic against main")
        self._authorize(actor, epic)
        self._owned(epic)
        target_path = self.worktrees.settings.repository
        target = self.git.inspect(target_path, "main", clean=False)
        evidence = self._evidence(
            epic.project_id, epic.id, None, Path(epic.worktree_path), epic.branch,
            target_path, "main", target, expected_commit, max_diff_bytes,
        )
        self._owned(epic)
        return evidence

    @staticmethod
    def _authorize(actor, record):
        try:
            StateService.authorize_scope(actor, record)
        except StateError:
            raise GitReviewError("review scope is not authorized") from None

    def _owned(self, record):
        try:
            return self.worktrees.verify_owned_worktree(record)
        except WorktreeError:
            raise GitReviewError("review worktree ownership is not verified") from None

    def _evidence(
        self, project_id, epic_run_id, task_run_id, source_path, source_branch,
        target_path, target_branch, target, expected_commit, limit,
    ):
        changes = self.git.working_changes(target_path)
        unsafe = self.git.unsafe_index_paths(target_path)
        source = self.git.snapshot(
            source_path, source_branch, target, expected_commit=expected_commit,
            max_diff_bytes=limit,
        )
        stable = (
            self.git.inspect(target_path, target_branch, clean=False) == target
            and self.git.working_changes(target_path) == changes
            and self.git.unsafe_index_paths(target_path) == unsafe
        )
        return ReviewEvidence(
            project_id, epic_run_id, task_run_id, source, target_branch, str(target_path),
            target, changes, unsafe, stable,
        )
