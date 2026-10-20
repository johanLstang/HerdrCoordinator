"""Optional, operator-owned Linux execution profile; never an agent tool input."""

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from orchestrator.domain.worker_contracts import canonical_json


class ExecutionPolicyError(RuntimeError):
    pass


def _toml(value):
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + _toml(v) for k, v in value.items()) + "}"
    return json.dumps(value)


class CodexExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[1] = 1
    name: str = "herdr-restricted"
    workspace_access: Literal["read", "write"]
    helper: Path
    binary: Path
    binary_sha256: str

    @model_validator(mode="after")
    def bounded_operator_configuration(self):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.name) or (
            not re.fullmatch(r"[0-9a-f]{64}", self.binary_sha256)
            or any(not p.is_absolute() or len(str(p)) > 4096
                   or any(c in str(p) for c in "\x00\r\n") for p in (self.helper, self.binary))
            or self.helper.name != "codex-linux-sandbox"
            or self.helper.parent in {Path("/"), Path("/tmp"), Path("/var/tmp"),
                                      Path("/home"), Path.home()}
        ):
            raise ValueError("bounded external execution policy required")
        return self

    def verify_files(self, cwd):
        try:
            root = Path(cwd).resolve()
            if sys.platform != "linux" or any(
                p.resolve() == root or root in p.resolve().parents
                for p in (self.helper.parent, self.binary)
            ):
                raise ValueError
            if self.helper.resolve() != self.binary.resolve() or (
                not self.binary.is_file() or not os.access(self.binary, os.X_OK)
                or self.helper.parent.is_symlink()
                or {p.name for p in self.helper.parent.iterdir()} != {self.helper.name}
            ):
                raise ValueError
            for path in (self.binary, self.helper.parent):
                mode = path.stat().st_mode
                if mode & 0o022 or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ValueError
            with self.binary.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != self.binary_sha256:
                raise ValueError
        except (OSError, ValueError, RuntimeError):
            raise ExecutionPolicyError("EXECUTION_POLICY_FILES_UNVERIFIED") from None

    @property
    def fingerprint(self):
        return hashlib.sha256(canonical_json(self.model_dump(mode="json")).encode()).hexdigest()

    def cli_args(self):
        paths = {
            ":root": "deny", ":minimal": "read",
            ":workspace_roots": {".": self.workspace_access, ".git": "read",
                                 ".codex": "read", ".agents": "read", ".aws": "read"},
            str(self.helper.parent): "read", str(self.binary): "read",
        }
        # The private arg0 symlink is not mounted inside restricted bwrap. This
        # separately read-only helper directory supplies the actual pinned binary.
        path = (str(self.helper.parent)
                + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        values = {
            "default_permissions": json.dumps(self.name),
            f"permissions.{self.name}.filesystem": _toml(paths),
            f"permissions.{self.name}.network.enabled": "false",
            "features.use_legacy_landlock": "false",
            "shell_environment_policy.inherit": '"none"',
            "shell_environment_policy.set.PATH": json.dumps(path),
        }
        return [arg for key, value in values.items() for arg in ("-c", key + "=" + value)]
