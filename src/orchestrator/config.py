"""Read-only configuration validation. No directories or processes are created here."""

import os
import re
import tomllib
from pathlib import Path
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from orchestrator.domain.review_contracts import EpicReviewSpec

_METADATA = {".git", ".codex", ".agents", ".aws"}
_SYSTEM_ROOTS = tuple(Path(p) for p in ("/etc", "/usr", "/bin", "/sbin", "/proc", "/sys", "/dev"))
_SHARED_ROOTS = {Path(p) for p in ("/tmp", "/var", "/var/tmp", "/home", "/opt", "/srv", "/run")}


class ConfigurationError(ValueError):
    """Safe error text; never contains raw TOML, input values or OS exceptions."""


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _writable_parent(path: Path) -> bool:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    repository: Path
    worktree_root: Path
    sqlite_path: Path
    max_workers: int = Field(default=2, ge=1, le=2, strict=True)
    credential_env: tuple[str, ...] = ()
    worker_test_command: tuple[str, ...] = ()
    worker_test_timeout: int = Field(default=300, ge=1, le=3600, strict=True)
    review_context: EpicReviewSpec | None = None

    @field_validator("worker_test_command")
    @classmethod
    def test_arguments(cls, command):
        if len(command) > 128 or any(
            not arg or "\x00" in arg or len(arg) > 4096 for arg in command
        ):
            raise ValueError("worker test command must be bounded argv")
        return command

    @field_validator("repository", "worktree_root", "sqlite_path", mode="before")
    @classmethod
    def resolve_path(cls, value: object, info: ValidationInfo) -> Path:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError("path must be a non-empty string")
        path = Path(value).expanduser()
        base = (info.context or {}).get("base_dir", Path.cwd())
        try:
            return (base / path).resolve()
        except (OSError, RuntimeError, ValueError):
            raise ValueError("path cannot be resolved") from None

    @field_validator("credential_env")
    @classmethod
    def environment_names(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names):
            raise ValueError("credential_env must contain environment variable names")
        return names

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        repo, root, db = self.repository, self.worktree_root, self.sqlite_path
        if not repo.is_dir() or not (repo / ".git").exists():
            raise ValueError("repository must be an existing Git working directory")
        if _inside(repo, root) or root == Path.home().resolve() or root in _SHARED_ROOTS:
            raise ValueError("worktree_root must not be the repository or one of its ancestors")
        for path in (root, db):
            if any(part in _METADATA for part in path.parts):
                raise ValueError("runtime paths must not use protected metadata directories")
            if any(_inside(path, system_root) for system_root in _SYSTEM_ROOTS):
                raise ValueError("runtime paths must not use system directories")
        if _inside(root, repo) and not _inside(root, repo / ".worktrees"):
            raise ValueError("worktree_root inside the repository must be under .worktrees")
        if root.exists() and not root.is_dir():
            raise ValueError("worktree_root must be a directory")
        if db == repo or _inside(db, root) or _inside(root, db):
            raise ValueError("sqlite_path must be separate from worktrees and the repository root")
        if db.exists() and (not db.is_file() or not os.access(db, os.W_OK)):
            raise ValueError("sqlite_path must be a writable file path")
        if not _writable_parent(root) or not _writable_parent(db.parent):
            raise ValueError("runtime paths must have writable directory ancestors")
        return self

    def public_config(self) -> dict[str, object]:
        # Explicit allowlist: credentials and future private fields stay out of logs.
        return {
            "repository": str(self.repository),
            "worktree_root": str(self.worktree_root),
            "sqlite_path": str(self.sqlite_path),
            "max_workers": self.max_workers,
        }


def load_settings(config_file: Path) -> Settings:
    try:
        with config_file.open("rb") as source:
            data = tomllib.load(source)
    except (OSError, ValueError):
        raise ConfigurationError("configuration file cannot be read as valid TOML") from None
    try:
        return Settings.model_validate(data, context={"base_dir": config_file.resolve().parent})
    except ValidationError as exc:
        details = []
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            location = error["loc"][0] if error["loc"] else "configuration"
            field = location if location in Settings.model_fields else "configuration"
            details.append(f"{field}: {error['msg']}")
        raise ConfigurationError("; ".join(details)) from None
    except (OSError, ValueError, RuntimeError):
        raise ConfigurationError("configuration paths cannot be inspected") from None
