import os
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Safe Git error: no command output, paths or credentials."""


class GitAdapter:
    def __init__(self, repository: Path):
        self.repository = repository
        if Path(self.run("rev-parse", "--show-toplevel").strip()).resolve() != repository:
            raise GitError("configured repository must be its working directory root")
        self.common_dir = Path(
            self.run("rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        ).resolve()

    def run(self, *arguments: str, cwd: Path | None = None, missing: bool = False) -> str | None:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            result = subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *arguments],
                cwd=cwd or self.repository,
                env=env,
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
