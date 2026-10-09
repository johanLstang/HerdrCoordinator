"""Bounded operator contract probe; this is not the product TeamPlayer adapter.

Only ``fixture`` writes, and only inside the pinned, explicitly named test epic.
Run that phase as Integration test operator, never from a Worker runtime.
Credentials are obtained from the operator's local Codex configuration in memory.
Private responses stay under ignored .herdr; export contains selected fixture facts.
"""

import argparse
import asyncio
import json
import logging
import os
import shlex
import subprocess
import sys
import tomllib
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

PROJECT = "d2ee4c75-7b80-465f-83ac-1750854a8e80"
USER = "105f26a7-0648-438d-94fd-3260ac3af4ee"
EPIC = "8051e9de-f4dc-4277-8f80-62b1c36c4690"
PREFIX = "[TEST] E06 TeamPlayer MCP-kontrakt"
BASE = Path(".herdr/probes/f23")
ACCEPTANCE = [
    "Återge Unicode: åäö, e\u0301 och 東京.",
    "Bevara kriteriernas ordning.\nAndra raden.",
]
DESCRIPTION = "[TEST] Avgränsat MCP-kontraktsprov, ingen produktleverans.\nBehåll historik och ID."
EVENT = "[herdr-event:f23-contract-20261008]"


def save(name, data):
    BASE.mkdir(parents=True, exist_ok=True)
    (BASE / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


@asynccontextmanager
async def connect(config):
    entry = tomllib.loads(config.read_text())["mcp_servers"]["teamplayer"]
    url = urlsplit(entry["url"])
    if (
        url.username
        or url.password
        or url.query
        or url.fragment
        or (
            url.scheme != "https"
            and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"})
        )
    ):
        raise ValueError("Operator endpoint must be HTTPS or loopback without URL credentials")
    headers = dict(entry.get("http_headers", {}))
    for name, env in entry.get("env_http_headers", {}).items():
        headers[name] = os.environ[env]
    if token_env := entry.get("bearer_token_env_var"):
        headers["Authorization"] = "Bearer " + os.environ[token_env]
    if helper := entry.get("http_headers_helper"):
        # Operator configuration, argument execution only; never shell or log output.
        result = subprocess.run(shlex.split(helper), capture_output=True, check=True, timeout=20)
        headers.update(json.loads(result.stdout))
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise ValueError("Invalid operator headers")
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
        async with Client(
            streamable_http_client(entry["url"], http_client=http),
            mode="legacy",
            read_timeout_seconds=20,
        ) as client:
            yield client


async def call(client, name, arguments, *, failure=False):
    result = await client.call_tool(name, arguments)
    blocks = [block.text for block in result.content if block.type == "text"]
    if len(blocks) != 1:
        raise ValueError("Unexpected result envelope")
    payload = json.loads(blocks[0])
    if failure:
        if payload.get("success") is not False:
            raise ValueError("Expected contract rejection")
        return payload
    if result.is_error or payload.get("success") is not True:
        # No raw server messages/arguments in exceptions, stdout or public proof.
        raise ValueError("External operation rejected; reconcile before retry")
    return payload["data"]


async def identity(client):
    me = await call(client, "get_me", {})
    projects = await call(client, "list_projects", {})
    project = next(p for p in projects if p["projectId"] == PROJECT)
    if me["userId"] != USER or me["email"] != "blitterbot@gmail.com":
        raise ValueError("Authenticated identity mismatch")
    if project["name"] != "HerdrCoordinator" or project["access"] != "Write":
        raise ValueError("Project identity or grant mismatch")
    epics = await call(client, "list_epics", {"projectId": PROJECT})
    epic = next(e for e in epics if e["id"] == EPIC)
    if not epic["name"].startswith(PREFIX):
        raise ValueError("Pinned test epic identity mismatch")
    return epic


async def catalog(client):
    tools, cursors, cursor = [], set(), None
    for _ in range(100):
        page = await client.list_tools(cursor=cursor, cache_mode="bypass")
        tools.extend(t.model_dump(by_alias=True, exclude_none=True) for t in page.tools)
        cursor = page.next_cursor
        if cursor is None:
            break
        if cursor in cursors:
            raise ValueError("Repeated catalog cursor")
        cursors.add(cursor)
    else:
        raise ValueError("Catalog page limit")
    if len({t["name"] for t in tools}) != len(tools):
        raise ValueError("Duplicate catalog tool")
    save("catalog", {"protocol": client.protocol_version, "sdk": version("mcp"), "tools": tools})
    return tools


async def task(client, task_id):
    value = await call(client, "get_task", {"projectId": PROJECT, "taskId": task_id})
    if (
        value["projectId"] != PROJECT
        or value["epicId"] != EPIC
        or value["executionOwnerKind"] != "User"
        or value["responsibleUserId"] != USER
        or not value["name"].startswith("[TEST] F23")
    ):
        raise ValueError("Fixture task identity/owner mismatch")
    return value


async def fixture(client):
    first = None
    for index in range(2):
        dependencies = [] if first is None else [first["taskId"]]
        request = {
            "projectId": PROJECT,
            "epicId": EPIC,
            "name": f"[TEST] F23 kontrakt {index + 1}",
            "description": DESCRIPTION,
            "taskType": "Task",
            "priority": "High",
            "acceptanceCriteria": ACCEPTANCE,
            "executionOwnerKind": "User",
            "responsibleUserId": USER,
            "validationOwnerKind": "User",
            "validationUserId": USER,
            "dependsOnTaskIds": dependencies,
            "traceabilityArtifactIds": [],
        }
        args = {
            "request": request,
            "idempotencyKey": f"herdr-f23-contract-20261008-task-{index + 1}",
        }
        created = await call(client, "create_task", args)
        save(f"create-{index + 1}", created)
        repeated = await call(client, "create_task", args)
        if repeated["taskId"] != created["taskId"]:
            raise ValueError("Create retry changed task identity")
        current = await task(client, created["taskId"])
        if (
            current["acceptanceCriteria"] != ACCEPTANCE
            or current["dependencyTaskIds"] != dependencies
        ):
            raise ValueError("Fixture acceptance/dependencies changed")
        if index == 0:
            first = current
    # One explicit description event; readback makes an unknown response reconcilable.
    first = await task(client, first["taskId"])
    expected = (
        DESCRIPTION + "\n\n" + EVENT + "\nVerifierad historik: saknat fristående kommentarverktyg."
    )
    if first["description"] != expected:
        if first["description"] != DESCRIPTION:
            raise ValueError("Unexpected fixture description; preserve manual edits")
        await call(
            client,
            "update_task_details",
            {
                "request": {
                    "projectId": PROJECT,
                    "taskId": first["taskId"],
                    "version": first["version"],
                    "description": expected,
                    "changeReason": "F23 explicit test-event, no product completion",
                }
            },
        )
    current = await task(client, first["taskId"])
    if current["description"] != expected or current["description"].count(EVENT) != 1:
        raise ValueError("Description event readback mismatch")
    # Never re-run completed lifecycle trials; readbacks and journal are retained.
    journal = BASE / "status-trial.json"
    states = json.loads(journal.read_text()) if journal.exists() else []
    sequence = ["InProgress", "NeedsInput", "InProgress", "Pending"]
    for index, status in enumerate(sequence):
        if index < len(states) and states[index].get("staleRejected"):
            continue
        current = await task(client, first["taskId"])
        if index == len(states):
            states.append({"status": status, "beforeVersion": current["version"]})
            save("status-trial", states)  # Persist intention before the external write.
        before = states[index]["beforeVersion"]
        if current["version"] == before:
            await call(
                client,
                "update_task_status",
                {
                    "request": {
                        "projectId": PROJECT,
                        "taskId": first["taskId"],
                        "version": before,
                        "status": status,
                        "statusReason": "F23 declared transport fixture only; no product run/Done",
                    }
                },
            )
            current = await task(client, first["taskId"])
        # Unknown response: observe the pinned effect; never resend with a fresh version.
        if current["status"] != status or current["version"] != before + 1:
            raise ValueError("Status trial diverged; operator reconciliation required")
        rejected = await call(
            client,
            "update_task_status",
            {
                "request": {
                    "projectId": PROJECT,
                    "taskId": first["taskId"],
                    "version": before,
                    "status": "Testing",
                    "statusReason": "F23 stale-version negative contract trial",
                }
            },
            failure=True,
        )
        unchanged = await task(client, first["taskId"])
        if unchanged != current:
            raise ValueError("Rejected stale write changed task")
        states[index].update(
            {"version": current["version"], "staleRejected": True, "error": rejected.get("error")}
        )
        save("status-trial", states)


async def verify(client, tools):
    first = await call(client, "list_tasks", {"projectId": PROJECT})
    second = await call(client, "list_tasks", {"projectId": PROJECT})
    selected = sorted([t for t in first["tasks"] if t["epicId"] == EPIC], key=lambda t: t["name"])
    if len(selected) != 2 or first != second:
        raise ValueError("Incomplete or changing fixture/list snapshot")
    for t in selected:
        if t != await task(client, t["taskId"]):
            raise ValueError("List/get disagree")
    if selected[1]["dependencyTaskIds"] != [selected[0]["taskId"]]:
        raise ValueError("Fixture dependency lost")
    schema = {t["name"]: t["inputSchema"] for t in tools}
    evidence = {
        "projectId": PROJECT,
        "projectName": "HerdrCoordinator",
        "access": "Write",
        "authenticatedUserId": USER,
        "email": "blitterbot@gmail.com",
        "epicId": EPIC,
        "protocol": client.protocol_version,
        "sdk": version("mcp"),
        "tools": sorted(schema),
        "catalogPagination": "walked to null next_cursor",
        "dataPagination": {
            n: sorted(schema[n]["properties"]) for n in ["list_tasks", "list_epics"]
        },
        "listTaskEnvelopeKeys": sorted(first),
        "repeatReadIdentical": True,
        "tasks": [
            {
                k: t[k]
                for k in [
                    "taskId",
                    "epicId",
                    "projectId",
                    "name",
                    "status",
                    "version",
                    "taskType",
                    "priority",
                    "acceptanceCriteria",
                    "dependencyTaskIds",
                    "executionOwnerKind",
                    "responsibleUserId",
                    "traceabilityArtifactIds",
                ]
            }
            for t in selected
        ],
        "descriptionEventCount": selected[0]["description"].count(EVENT),
        "limitations": [
            "No standalone comment/delete/reopen/dependency-edit tool in this catalog",
            "API account Write grant is not a product Worker/Integration role boundary",
            "No product runtime, merge or Done established by these fixture status trials",
        ],
    }
    save("proof", evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


async def main(args):
    async with connect(args.config) as client:
        await identity(client)
        tools = await catalog(client)
        if args.phase == "fixture":
            await fixture(client)
        if args.phase in {"fixture", "verify"}:
            await verify(client, tools)
        else:
            print(
                json.dumps(
                    {"protocol": client.protocol_version, "tools": sorted(t["name"] for t in tools)}
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["catalog", "fixture", "verify"])
    parser.add_argument("--config", type=Path, default=Path.home() / ".codex/config.toml")
    logging.disable(logging.CRITICAL)  # HTTP/SDK logs must not emit credentials or payloads.
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception:
        print(
            "F23_PROBE_FAILED: reconcile external fixture before retry; raw errors withheld",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
