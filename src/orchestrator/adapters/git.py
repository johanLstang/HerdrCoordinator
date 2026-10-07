import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path

from orchestrator.domain.git_facts import ChangedFile, GitDiff, GitSnapshot, WorktreeChange


class GitError(RuntimeError):
    """Safe Git error: no command output, paths or credentials."""


class GitAdapter:
    DEFAULT_DIFF_BYTES = 1024 * 1024
    MAX_DIFF_BYTES = 64 * 1024 * 1024

    def __init__(self, repository: Path):
        self.repository = repository
        if Path(self.run("rev-parse", "--show-toplevel").strip()).resolve() != repository:
            raise GitError("configured repository must be its working directory root")
        self.common_dir = Path(
            self.run("rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        ).resolve()

    @staticmethod
    def _environment() -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_OPTIONAL_LOCKS"] = "0"
        return env

    @staticmethod
    def _command(*arguments: str) -> tuple[str, ...]:
        return (
            "git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false", *arguments,
        )

    def run(self, *arguments: str, cwd: Path | None = None, missing: bool = False) -> str | None:
        try:
            result = subprocess.run(
                self._command(*arguments),
                cwd=cwd or self.repository,
                env=self._environment(),
                capture_output=True,
                encoding="utf-8",
                errors="surrogateescape",
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise GitError("Git operation failed; reconcile resources before retry") from None
        if result.returncode:
            if missing and result.returncode == 1:
                return None
            raise GitError("Git operation failed; reconcile resources before retry")
        return result.stdout

    def head(self, branch: str) -> str | None:
        ref = f"refs/heads/{branch}"
        self.run("check-ref-format", ref)
        if self.run("show-ref", "--verify", "--quiet", ref, missing=True) is None:
            return None
        return self.run("rev-parse", "--verify", f"{ref}^{{commit}}").strip()

    def owner(self, branch: str) -> str | None:
        result = self.run("config", "--local", "--get", f"branch.{branch}.herdrOwner", missing=True)
        return result.strip() if result is not None else None

    def set_owner(self, branch: str, owner: str) -> None:
        self.run("config", "--local", f"branch.{branch}.herdrOwner", owner)

    def worktrees(self) -> dict[Path, dict[str, str]]:
        trees, entry = {}, {}
        for field in self.run("worktree", "list", "--porcelain", "-z").split("\0"):
            if not field:
                if entry:
                    trees[Path(entry["worktree"]).resolve()] = entry
                    entry = {}
            else:
                key, _, value = field.partition(" ")
                entry[key] = value
        return trees

    def inspect(self, path: Path, branch: str, *, clean: bool) -> str:
        entry = self.worktrees().get(path)
        if entry is None or entry.get("branch") != f"refs/heads/{branch}" or "prunable" in entry:
            raise GitError("expected branch and worktree are not registered")
        if not path.is_dir():
            raise GitError("registered worktree is missing")
        common = Path(
            self.run("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path).strip()
        ).resolve()
        actual_branch = self.run("symbolic-ref", "--quiet", "HEAD", cwd=path).strip()
        head = self.run("rev-parse", "--verify", "HEAD^{commit}", cwd=path).strip()
        if (
            common != self.common_dir
            or actual_branch != f"refs/heads/{branch}"
            or head != self.head(branch)
        ):
            raise GitError("worktree repository, branch or HEAD changed")
        if clean and self.run("status", "--porcelain=v1", "-z", cwd=path):
            raise GitError("source or unfinished worktree must be clean")
        return head

    def descends_from(self, branch: str, commit: str) -> bool:
        return (
            self.run("merge-base", "--is-ancestor", commit, f"refs/heads/{branch}", missing=True)
            is not None
        )

    def add_worktree(self, path: Path, branch: str, base: str, *, branch_exists: bool) -> None:
        if branch_exists:
            self.run("worktree", "add", str(path), branch)
        else:
            self.run("worktree", "add", "-b", branch, str(path), base)

    def require_commit(self, commit: str) -> str:
        # Do not accept revision expressions, abbreviations or caller-controlled options.
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise GitError("commit must be a full SHA-1 object identifier")
        try:
            kind = self.run("cat-file", "-t", commit).strip()
        except GitError:
            raise GitError("commit does not exist in the configured repository") from None
        if kind != "commit":
            raise GitError("object is not a commit in the configured repository")
        return commit

    def contains_commit(self, branch: str, commit: str) -> bool:
        self.require_commit(commit)
        if self.head(branch) is None:
            raise GitError("branch does not exist in the configured repository")
        return self.descends_from(branch, commit)

    def working_changes(self, path: Path) -> tuple[WorktreeChange, ...]:
        fields = iter(self.run(
            "status", "--porcelain=v1", "-z", "--untracked-files=all",
            "--ignore-submodules=none", cwd=path,
        ).split("\0"))
        changes = []
        for field in fields:
            if not field:
                continue
            original = next(fields) if "R" in field[:2] or "C" in field[:2] else None
            changes.append(WorktreeChange(field[3:], field[0], field[1], original))
        return tuple(changes)

    def changed_files(self, base: str, current: str) -> tuple[ChangedFile, ...]:
        self.require_commit(base)
        self.require_commit(current)
        options = ("--no-ext-diff", "--no-textconv", "--no-renames")
        names = self.run("diff", *options, "--name-status", "-z", base, current, "--")
        fields = iter(names.split("\0")[:-1])
        statuses = {path: status for status, path in zip(fields, fields, strict=True)}
        stats = self.run("diff", *options, "--numstat", "-z", base, current, "--")
        files = []
        for field in stats.split("\0"):
            if not field:
                continue
            added, deleted, path = field.split("\t", 2)
            files.append(ChangedFile(
                path, statuses[path], int(added) if added != "-" else None,
                int(deleted) if deleted != "-" else None,
            ))
        return tuple(files)

    def _diff(self, path: Path, revisions: tuple[str, ...], max_bytes: int) -> GitDiff:
        if type(max_bytes) is not int or not 1 <= max_bytes <= self.MAX_DIFF_BYTES:
            raise GitError("diff byte limit must be a positive integer up to 64 MiB")
        command = self._command(
            "-c", "color.ui=false", "diff", "--no-ext-diff", "--no-textconv",
            "--no-renames", "--binary", "--full-index", "--submodule=short",
            "--no-color", "--src-prefix=a/", "--dst-prefix=b/", *revisions, "--",
        )
        # Spool to a private temporary file: a large diff never floods process memory.
        # Hash/count the whole output, then return an explicitly bounded preview.
        with tempfile.TemporaryFile() as output:
            try:
                result = subprocess.run(
                    command, cwd=path, env=self._environment(), stdout=output,
                    stderr=subprocess.DEVNULL, timeout=30, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise GitError("Git diff failed; no complete review evidence") from None
            if result.returncode:
                raise GitError("Git diff failed; no complete review evidence")
            size = output.tell()
            output.seek(0)
            digest = hashlib.file_digest(output, "sha256").hexdigest()
            output.seek(0)
            return GitDiff(output.read(max_bytes), size, digest, size <= max_bytes, command)

    def snapshot(
        self, path: Path, branch: str, base: str, *, expected_commit: str | None = None,
        max_diff_bytes: int = DEFAULT_DIFF_BYTES,
    ) -> GitSnapshot:
        """Observe a registered worktree and pin the commit diff to full object IDs."""
        self.require_commit(base)
        current = self.inspect(path, branch, clean=False)
        if expected_commit is not None and self.require_commit(expected_commit) != current:
            raise GitError("worktree HEAD differs from the expected review commit")
        before = self.working_changes(path)
        files = self.changed_files(base, current)
        committed = self._diff(path, (base, current), max_diff_bytes)
        staged = self._diff(path, ("--cached", current), max_diff_bytes)
        unstaged = self._diff(path, (), max_diff_bytes)
        contains = self.contains_commit(branch, base)
        # Concurrent changes invalidate this observation; there is no approval here.
        stable = (
            self.inspect(path, branch, clean=False) == current
            and self.working_changes(path) == before
            and self._diff(path, ("--cached", current), max_diff_bytes).sha256 == staged.sha256
            and self._diff(path, (), max_diff_bytes).sha256 == unstaged.sha256
        )
        return GitSnapshot(
            str(self.repository), str(self.common_dir), str(path), branch, base, current,
            contains, files, before, committed, staged, unstaged, stable,
        )
