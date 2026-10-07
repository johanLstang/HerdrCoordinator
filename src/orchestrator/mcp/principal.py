import json
import os
import stat
from pathlib import Path

from pydantic import ValidationError

from orchestrator.config import Settings
from orchestrator.domain.policy import Actor, Role


class PrincipalError(RuntimeError):
    """Safe operator-configuration error."""


def load_principal(path: Path | None, settings: Settings) -> Actor | None:
    """Trusted launcher input. Tool arguments and client metadata never enter this path.

    The MCP host must own launch configuration; Workers must not control it or write
    the manifest/database. OS/sandbox isolation is independently verified in F-10/F-17.
    """
    if path is None:
        return None
    try:
        resolved = path.resolve()
        if resolved == settings.worktree_root or settings.worktree_root in resolved.parents:
            raise PrincipalError("principal file must be outside worktrees and repositories")
        if any(
            (parent / ".git").is_file() or (parent / ".git/HEAD").is_file()
            for parent in resolved.parents
        ):
            raise PrincipalError("principal file must be outside worktrees and repositories")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "r") as source:
            metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise PrincipalError("principal file must be owner-only and owned by the operator")
            if resolved.parent.stat().st_mode & 0o022:
                raise PrincipalError("principal directory must not be writable by other users")
            actor = Actor.model_validate(json.load(source))
        if actor.role in {Role.WORKER, Role.INTEGRATION} and actor.epic_run_id is None:
            raise PrincipalError("principal must bind an epic run")
        if actor.role == Role.WORKER and actor.task_run_id is None:
            raise PrincipalError("Worker principal must bind a task run")
        return actor
    except (OSError, ValueError, ValidationError):
        raise PrincipalError("principal file is unreadable or invalid") from None
