"""Read-only TeamPlayer MCP boundary; credentials and upstream errors never become facts."""

import json
import os
import re
import subprocess
from collections.abc import Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


class TeamPlayerError(RuntimeError):
    """Fixed safe code only: no upstream messages, response bodies, URLs or headers."""


READ_TOOLS = frozenset({"get_me", "list_projects", "list_epics", "list_tasks", "get_task"})


class OperatorHeaders:
    """Explicit operator argv/env names; never serialize credential values or run a shell."""

    def __init__(self, *, command=(), bearer_env=None):
        if (
            not isinstance(command, tuple)
            or len(command) > 128
            or any(not isinstance(a, str) or not a or "\x00" in a or len(a) > 4096 for a in command)
            or (
                bearer_env is not None
                and (
                    not isinstance(bearer_env, str)
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", bearer_env)
                )
            )
            or (command and bearer_env is not None)
        ):
            raise TeamPlayerError("TEAMPLAYER_AUTH_CONFIG_INVALID")
        self._command, self._bearer_env = command, bearer_env

    def __call__(self):
        try:
            if self._bearer_env:
                return {"Authorization": "Bearer " + os.environ[self._bearer_env]}
            if self._command:
                result = subprocess.run(self._command, capture_output=True, check=True, timeout=20)
                headers = json.loads(result.stdout)
                if not isinstance(headers, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
                ):
                    raise ValueError
                return headers
            return {}
        except Exception:
            raise TeamPlayerError("TEAMPLAYER_AUTH_UNAVAILABLE") from None


@asynccontextmanager
async def teamplayer_connection(endpoint: str, headers: Callable, *, timeout_seconds=20):
    """Only an operator supplies this endpoint/provider; no implicit connection or runtime start."""
    try:
        parsed = urlsplit(endpoint)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                )
            )
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 120
        ):
            raise ValueError
    except Exception:
        raise TeamPlayerError("TEAMPLAYER_CONNECTION_CONFIG_INVALID") from None
    try:
        # No token in URL or repr/model/state/log; refresh provider runs on each new connection.
        async with httpx2.AsyncClient(headers=headers(), timeout=timeout_seconds) as http:
            async with Client(
                streamable_http_client(endpoint, http_client=http),
                mode="legacy",
                read_timeout_seconds=timeout_seconds,
            ) as client:
                adapter = TeamPlayerMCPAdapter(client)
                await adapter.verify_catalog()
                yield adapter
    except TeamPlayerError:
        raise
    except Exception:
        raise TeamPlayerError("TEAMPLAYER_CONNECTION_FAILED") from None


class TeamPlayerMCPAdapter:
    def __init__(self, client):
        self._client = client

    async def verify_catalog(self):
        names, seen, cursor = set(), set(), None
        try:
            for _ in range(100):
                page = await self._client.list_tools(cursor=cursor, cache_mode="bypass")
                for tool in page.tools:
                    if tool.name in names:
                        raise TeamPlayerError("TEAMPLAYER_CATALOG_DUPLICATE")
                    names.add(tool.name)
                    if tool.name in READ_TOOLS:
                        if not tool.annotations or tool.annotations.read_only_hint is not True:
                            raise TeamPlayerError("TEAMPLAYER_READ_CAPABILITY_CHANGED")
                        schema = tool.input_schema
                        if tool.name in {"get_me", "list_projects"}:
                            required = set()
                        else:
                            required = {"projectId"} | (
                                {"taskId"} if tool.name == "get_task" else set()
                            )
                        if (
                            schema.get("type") != "object"
                            or set(schema.get("required", [])) != required
                        ):
                            raise TeamPlayerError("TEAMPLAYER_CATALOG_CHANGED")
                        # A new cursor/page contract must be implemented explicitly, not ignored.
                        if tool.name in {"list_epics", "list_tasks"} and any(
                            k in schema.get("properties", {})
                            for k in ("cursor", "page", "pageSize", "limit")
                        ):
                            raise TeamPlayerError("TEAMPLAYER_PAGINATION_UNSUPPORTED")
                cursor = page.next_cursor
                if cursor is None:
                    break
                if cursor in seen:
                    raise TeamPlayerError("TEAMPLAYER_CATALOG_CURSOR_LOOP")
                seen.add(cursor)
            else:
                raise TeamPlayerError("TEAMPLAYER_CATALOG_PAGE_LIMIT")
            if not READ_TOOLS <= names:
                raise TeamPlayerError("TEAMPLAYER_READ_TOOLS_MISSING")
        except TeamPlayerError:
            raise
        except Exception:
            raise TeamPlayerError("TEAMPLAYER_CATALOG_UNAVAILABLE") from None

    async def read(self, name, arguments):
        if name not in READ_TOOLS:
            raise TeamPlayerError("TEAMPLAYER_READ_OPERATION_DENIED")
        try:
            result = await self._client.call_tool(name, arguments)
            texts = [block.text for block in result.content if block.type == "text"]
            if len(texts) != 1:
                raise TeamPlayerError("TEAMPLAYER_RESPONSE_INVALID")
            if len(texts[0].encode()) > 16 * 1024 * 1024:
                raise TeamPlayerError("TEAMPLAYER_RESPONSE_TOO_LARGE")

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            payload = json.loads(texts[0], object_pairs_hook=unique)
            if result.is_error or payload.get("success") is not True:
                error = payload.get("error") or {}
                code = error.get("errorCode", payload.get("code"))
                safe = {
                    "task_not_found": "TEAMPLAYER_TASK_NOT_FOUND",
                    "permission_denied": "TEAMPLAYER_PERMISSION_DENIED",
                }
                raise TeamPlayerError(safe.get(code, "TEAMPLAYER_READ_REJECTED"))
            return payload["data"]
        except TeamPlayerError:
            raise
        except Exception:
            raise TeamPlayerError("TEAMPLAYER_RESPONSE_UNAVAILABLE") from None
