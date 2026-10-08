"""Bounded Herdr CLI calls; no implicit server selection or prompt submission."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

from orchestrator.adapters.codex import CodexAdapter, CodexError


class HerdrError(RuntimeError):
    def __init__(self, code: str = "RUNTIME_UNAVAILABLE"):
        self.code = code
        super().__init__(code)


def identity(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
        raise HerdrError("INVALID_RUNTIME_RESPONSE")
    return value


def process_stamp(pid: int, argv: list[str], cwd: str) -> str:
    try:
        proc = Path(f"/proc/{pid}")
        actual = proc.joinpath("cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        if [os.fsdecode(a) for a in actual] != argv or str(proc.joinpath("cwd").resolve()) != cwd:
            raise HerdrError("PROCESS_UNVERIFIED")
        # comm can contain spaces/parentheses. Field 22 follows the final ')'.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        raise HerdrError("PROCESS_UNVERIFIED") from None


class HerdrAdapter:
    def __init__(self, server_session: str, *, sandbox: str = "read-only"):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", server_session):
            raise HerdrError("INVALID_RUNTIME_CONFIGURATION")
        if sandbox not in {"read-only", "workspace-write"}:
            raise HerdrError("INVALID_RUNTIME_CONFIGURATION")
        if os.environ.get("HERDR_ENV") != "1":
            raise HerdrError("HERDR_CONTEXT_REQUIRED")
        self.server_session, self.sandbox = server_session, sandbox

    def call(self, *args: str, timeout: int = 15) -> dict:
        try:
            p = subprocess.run(
                ["herdr", "--session", self.server_session, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            data = json.loads(p.stdout if p.returncode == 0 else p.stderr)
            if not isinstance(data, dict):
                raise ValueError
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise HerdrError("UNKNOWN_RUNTIME_OUTCOME") from None
        error = data.get("error")
        if error is not None:
            known = {
                "agent_not_found",
                "agent_not_ready",
                "agent_blocked",
                "pane_not_found",
                "workspace_not_found",
                "timeout",
            }
            code = error.get("code") if isinstance(error, dict) else None
            raise HerdrError(code.upper() if code in known else "RUNTIME_REJECTED")
        if p.returncode or not isinstance(data.get("result"), dict):
            raise HerdrError("INVALID_RUNTIME_RESPONSE")
        return data["result"]

    def create_workspace(self, cwd: str, label: str) -> dict:
        result = self.call("workspace", "create", "--cwd", cwd, "--label", label, "--no-focus")
        try:
            workspace, tab, pane = result["workspace"], result["tab"], result["root_pane"]
            binding = {
                "workspace_id": identity(workspace["workspace_id"]),
                "tab_id": identity(tab["tab_id"]),
                "pane_id": identity(pane["pane_id"]),
                "terminal_id": identity(pane["terminal_id"]),
            }
            if (
                tab["workspace_id"] != binding["workspace_id"]
                or pane["workspace_id"] != binding["workspace_id"]
                or pane["tab_id"] != binding["tab_id"]
            ):
                raise ValueError
            return binding
        except (KeyError, TypeError, ValueError):
            raise HerdrError("INVALID_RUNTIME_RESPONSE") from None

    def get_agent(self, name: str) -> dict | None:
        try:
            value = self.call("agent", "get", name)["agent"]
            if not isinstance(value, dict):
                raise HerdrError("INVALID_RUNTIME_RESPONSE")
            return value
        except HerdrError as e:
            if e.code == "AGENT_NOT_FOUND":
                return None
            raise
        except (KeyError, TypeError):
            raise HerdrError("INVALID_RUNTIME_RESPONSE") from None

    def pane(self, pane_id: str) -> dict:
        try:
            value = self.call("pane", "get", pane_id)["pane"]
            if not isinstance(value, dict):
                raise HerdrError("INVALID_RUNTIME_RESPONSE")
            return value
        except (KeyError, TypeError):
            raise HerdrError("INVALID_RUNTIME_RESPONSE") from None

    def start_agent(self, name: str, pane_id: str, cwd: str) -> None:
        self.call(
            "agent",
            "start",
            name,
            "--kind",
            "codex",
            "--pane",
            pane_id,
            "--timeout",
            "30000",
            "--",
            "--no-daemon",
            "--sandbox",
            self.sandbox,
            "--ask-for-approval",
            "on-request",
            "--cd",
            cwd,
            timeout=35,
        )

    def process_info(self, pane_id: str) -> dict:
        try:
            value = self.call("pane", "process-info", "--pane", pane_id)["process_info"]
            if not isinstance(value, dict):
                raise HerdrError("INVALID_RUNTIME_RESPONSE")
            return value
        except (KeyError, TypeError):
            raise HerdrError("INVALID_RUNTIME_RESPONSE") from None

    def verify_agent(self, agent: dict, binding: dict, cwd: str, name: str) -> dict:
        try:
            if (
                agent["name"] != name
                or agent["agent"] != "codex"
                or agent["cwd"] != cwd
                or agent.get("foreground_cwd") != cwd
                or any(agent[key] != binding[key] for key in binding)
            ):
                raise ValueError
            info = self.process_info(binding["pane_id"])
            required = [
                "--no-daemon",
                "--sandbox",
                self.sandbox,
                "--ask-for-approval",
                "on-request",
                "--cd",
                cwd,
            ]
            cli = shutil.which("codex")
            if cli is None:
                raise HerdrError("CODEX_EXECUTABLE_UNVERIFIED")
            installed = Path(cli).resolve()
            processes = []
            for process in info["foreground_processes"]:
                args = process["argv"]
                executable = Path(shutil.which(args[0]) or args[0]).resolve()
                launcher = (
                    len(args) > 1
                    and Path(args[0]).name == "node"
                    and (Path(args[1]).resolve() == installed)
                )
                native = executable == installed or (
                    installed.suffix == ".js"
                    and installed.parent.parent in executable.parents
                    and executable.name == "codex"
                )
                if (launcher or native) and any(
                    args[i : i + len(required)] == required
                    for i in range(len(args) - len(required) + 1)
                ):
                    if process["cwd"] != cwd:
                        raise ValueError
                    pid = process["pid"]
                    if type(pid) is not int or pid <= 0:
                        raise ValueError
                    processes.append({"pid": pid, "start_time": process_stamp(pid, args, cwd)})
            if info["pane_id"] != binding["pane_id"] or not processes:
                raise ValueError
            session = agent.get("agent_session")
            session_id = None
            if session is not None:
                if session.get("source") != "herdr:codex" or session.get("kind") != "id":
                    raise ValueError
                session_id = str(UUID(session["value"]))
                try:
                    metadata = CodexAdapter().read_session(session_id, cwd)
                except CodexError:
                    raise HerdrError("CODEX_SESSION_UNVERIFIED") from None
                if metadata["session_id"] != session_id:
                    raise ValueError
            return {
                "ready": agent.get("interactive_ready") is True
                and not agent.get("launch_pending", False)
                and agent.get("agent_status") in {"idle", "done"},
                "status": agent.get("agent_status")
                if agent.get("agent_status") in {"idle", "working", "blocked", "done", "unknown"}
                else "unknown",
                "session_id": session_id,
                "processes": processes,
            }
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise HerdrError("RUNTIME_IDENTITY_MISMATCH") from None
