"""Immutable observations, not approval or merge evidence."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GitDiff:
    patch: bytes
    total_bytes: int
    sha256: str
    complete: bool
    # Exact argument vector for retrieving the full patch at these pinned commits.
    full_command: tuple[str, ...]


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str
    additions: int | None
    deletions: int | None

    @property
    def binary(self) -> bool:
        return self.additions is None or self.deletions is None


@dataclass(frozen=True)
class WorktreeChange:
    path: str
    index_status: str
    worktree_status: str
    original_path: str | None = None


@dataclass(frozen=True)
class GitSnapshot:
    repository: str
    git_common_dir: str
    worktree_path: str
    branch: str
    base_commit: str
    current_commit: str
    contains_base: bool
    changed_files: tuple[ChangedFile, ...]
    changes: tuple[WorktreeChange, ...]
    unsafe_index_paths: tuple[str, ...]
    commit_diff: GitDiff
    staged_diff: GitDiff
    unstaged_diff: GitDiff
    stable: bool

    @property
    def dirty(self) -> bool:
        return bool(self.changes)

    @property
    def reviewable(self) -> bool:
        return (
            self.stable
            and self.contains_base
            and not self.dirty
            and not self.unsafe_index_paths
            and self.commit_diff.complete
            and self.staged_diff.complete
            and self.unstaged_diff.complete
        )
