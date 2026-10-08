"""Read one recorded Codex thread through a private stdio app-server child."""

import json
import os
import selectors
import subprocess
import time
from pathlib import Path


class CodexError(RuntimeError):
    """No raw stdout, credentials or upstream errors escape this adapter."""


class CodexAdapter:
    def read_session(self, session_id: str, cwd: str) -> dict:
        thread = self.read_thread(session_id, cwd, include_turns=False)
        return {"id": thread["id"], "session_id": thread["sessionId"], "cwd": thread["cwd"]}

    def read_thread(self, session_id: str, cwd: str, *, include_turns: bool = True) -> dict:
        try:
            p = subprocess.Popen(
                ["codex", "app-server", "--listen", "stdio://"],
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except OSError:
            raise CodexError("CODEX_METADATA_UNAVAILABLE") from None
        pending = b""
        selector = selectors.DefaultSelector()
        selector.register(p.stdout, selectors.EVENT_READ)

        def write(value):
            p.stdin.write(json.dumps(value).encode() + b"\n")
            p.stdin.flush()

        def request(number, method, params):
            nonlocal pending
            write({"id": number, "method": method, "params": params})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    value = json.loads(line)
                    if value.get("id") == number:
                        if "error" in value:
                            raise CodexError("CODEX_METADATA_REJECTED")
                        return value["result"]
                if not selector.select(max(0, deadline - time.monotonic())):
                    break
                chunk = os.read(p.stdout.fileno(), 65536)
                if not chunk:
                    break
                pending += chunk
                if len(pending) > 1024 * 1024:
                    raise CodexError("CODEX_METADATA_TOO_LARGE")
            raise CodexError("CODEX_METADATA_UNKNOWN")

        try:
            request(
                1, "initialize", {"clientInfo": {"name": "herdr_coordinator", "version": "0.1"}}
            )
            write({"method": "initialized"})
            thread = request(
                2, "thread/read", {"threadId": session_id, "includeTurns": include_turns}
            )["thread"]
            if (
                thread["id"] != session_id
                or thread["sessionId"] != session_id
                or Path(thread["cwd"]) != Path(cwd)
            ):
                raise CodexError("CODEX_SESSION_MISMATCH")
            if include_turns:
                if not isinstance(thread["turns"], list):
                    raise ValueError
                for turn in thread["turns"]:
                    if (
                        not isinstance(turn["id"], str)
                        or not isinstance(turn["status"], str)
                        or not isinstance(turn["items"], list)
                    ):
                        raise ValueError
                    for item in turn["items"]:
                        if not isinstance(item["id"], str) or not isinstance(item["type"], str):
                            raise ValueError
                        if item["type"] == "agentMessage" and not isinstance(item["text"], str):
                            raise ValueError
                        if item["type"] == "userMessage" and not isinstance(item["content"], list):
                            raise ValueError
            return thread
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise CodexError("CODEX_METADATA_UNVERIFIED") from None
        finally:
            selector.close()
            try:
                p.stdin.close()
            except OSError:
                pass
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=3)
            p.stdout.close()
