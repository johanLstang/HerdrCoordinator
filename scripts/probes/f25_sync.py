"""Sequential test operator: mirror a backup of actual F22 journals to NEW F25 fixtures.

Never mutate the original database, run Git/test commands or start/stop/resume runtime.
Creation of labeled external fixture objects is an explicit operator step using MCP.
"""

import asyncio
import hashlib
import json
import logging
import shlex
import sqlite3
import sys
import tomllib
from pathlib import Path

from orchestrator.adapters.git import GitAdapter
from orchestrator.adapters.teamplayer_mcp import (
    OperatorHeaders,
    TeamPlayerError,
    teamplayer_connection,
)
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.config import Settings
from orchestrator.domain.policy import Actor, Role
from orchestrator.persistence.store import StateStore

PROJECT = "d2ee4c75-7b80-465f-83ac-1750854a8e80"
USER = "105f26a7-0648-438d-94fd-3260ac3af4ee"
BASE = Path(".herdr/probes/f25")
SOURCE = Path("../task-e05-f22/.herdr/probes/f22")
LOCAL_PROJECT, EPIC_RUN = "f22-probe", "f22-epic-run"
EXPECTED = {
    "f22-display-name": "b57c9cc5-9ad5-45ac-82eb-41678f1a0bda",
    "f22-blocked-test": "14faff65-6d6e-4278-b94c-adcf69b46389",
}


def save(name, value):
    (BASE / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load(name):
    return json.loads((BASE / (name + ".json")).read_text())


def prepare():
    if BASE.exists():
        raise RuntimeError("Refuse to replace any existing probe history")
    source = Settings.model_validate(json.loads((SOURCE / "settings.json").read_text()))
    BASE.mkdir(parents=True)
    # Consistent SQLite snapshot: source readonly, retain all actual IDs/digests.
    with sqlite3.connect(source.sqlite_path.as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(BASE / "state.sqlite") as backup:
            original.backup(backup)
    settings = source.model_copy(update={"sqlite_path": (BASE / "state.sqlite").resolve()})
    save("settings", settings.model_dump(mode="json"))
    with StateStore(settings.sqlite_path) as db:
        tasks = db.get_tasks(EPIC_RUN)
        assert {t.task_id: t.id for t in tasks} == EXPECTED
        assert {t.internal_status.value for t in tasks} == {"DONE", "PARKED"}
        intent = {
            "epicName": "[TEST] F25 status/history mirror from actual F22 evidence (2026-10-08)",
            "tasks": [
                {
                    "runId": t.id,
                    "spec": db.get_operation(LOCAL_PROJECT, "task_start", t.task_id).result["spec"],
                }
                for t in tasks
            ],
        }
    save("fixture-intent", intent)
    print(json.dumps(intent, ensure_ascii=False))


class LoseOneReply:
    """Real native write, then a declared client-side lost reply; durable one-shot injection."""

    def __init__(self, adapter):
        self.adapter = adapter

    async def read(self, name, arguments):
        return await self.adapter.read(name, arguments)

    def redact_text(self, value):
        return self.adapter.redact_text(value)

    async def write(self, name, arguments):
        receipt = await self.adapter.write(name, arguments)
        marker = BASE / "reply-loss-injected"
        if (
            name == "update_task_status"
            and arguments["request"]["status"] == "Done"
            and not marker.exists()
        ):
            marker.write_text("Declared injection after successful native Done write\n")
            raise TeamPlayerError("TEAMPLAYER_WRITE_OUTCOME_UNKNOWN")
        return receipt


async def exercise(verify_only):
    settings, fixture = Settings.model_validate(load("settings")), load("fixture")
    assert fixture["projectId"] == PROJECT and fixture["userId"] == USER
    assert fixture["epicId"] != "8051e9de-f4dc-4277-8f80-62b1c36c4690"
    assert set(fixture["tasks"]) == set(EXPECTED)
    config = tomllib.loads((Path.home() / ".codex/config.toml").read_text())["mcp_servers"][
        "teamplayer"
    ]
    provider = OperatorHeaders(
        command=tuple(shlex.split(config.get("http_headers_helper", ""))),
        bearer_env=config.get("bearer_token_env_var"),
    )
    git = GitAdapter(settings.repository)
    async with teamplayer_connection(config["url"], provider, writable=True) as native:
        adapter = native if verify_only else LoseOneReply(native)
        with StateStore(settings.sqlite_path) as db:
            epic = db.get_epic(EPIC_RUN)
            before = {
                "main": git.head("main"),
                "epic": git.head(epic.branch),
                "operations": [
                    o.model_dump(mode="json")
                    for kind in (
                        "task_start",
                        "start_runtime",
                        "resume_runtime",
                        "task_merge",
                        "merge_task_to_epic",
                        "task_delivery_test",
                        "stop_runtime",
                    )
                    for o in db.get_operations(EPIC_RUN, kind=kind)
                ],
            }
            coordinator = Actor(
                actor_id="f25-test-coordinator",
                role=Role.COORDINATOR,
                project_id=LOCAL_PROJECT,
                epic_run_id=EPIC_RUN,
            )
            integration = coordinator.model_copy(
                update={"actor_id": "f25-test-integration", "role": Role.INTEGRATION}
            )
            sync = TeamPlayerSyncService(
                settings,
                db,
                adapter,
                project_id=LOCAL_PROJECT,
                external_project_id=PROJECT,
                user_id=USER,
            )
            if not verify_only:
                await sync.bind_epic(coordinator, EPIC_RUN, fixture["epicId"])
                for task_id, run_id in EXPECTED.items():
                    await sync.bind_task(integration, run_id, fixture["tasks"][task_id])
            results = []
            for task_id, run_id in EXPECTED.items():
                first = await sync.sync_task(integration, run_id)
                if first["status"] == "PENDING":
                    assert (
                        task_id == "f22-display-name"
                        and first["reason"] == "TEAMPLAYER_WRITE_OUTCOME_UNKNOWN"
                    )
                    pending = db.get_operations(EPIC_RUN, kind=sync.KIND)[-1]
                    assert pending.status == "PENDING"
                    results.append({"lostReply": first})
                answer = await sync.sync_task(integration, run_id)
                assert answer["status"] in {"SYNCED", "EXISTING"}
                assert (await sync.sync_task(integration, run_id))["status"] == "EXISTING"
                current = await native.read(
                    "get_task", {"projectId": PROJECT, "taskId": fixture["tasks"][task_id]}
                )
                assert current["status"] == (
                    "Done" if task_id == "f22-display-name" else "NeedsInput"
                )
                assert current["description"].count("<!-- herdr-event:") == 1
                results.append(
                    {
                        "taskId": current["taskId"],
                        "runId": run_id,
                        "status": current["status"],
                        "version": current["version"],
                        "operationId": answer["operation_id"],
                        "exactlyOneHistoryEvent": True,
                    }
                )
            assert (await sync.sync_epic(coordinator, EPIC_RUN))["status"] in {"SYNCED", "EXISTING"}
            with_worker = integration.model_copy(
                update={"role": Role.WORKER, "task_run_id": EXPECTED["f22-display-name"]}
            )
            try:
                await sync.sync_task(with_worker, with_worker.task_run_id)
            except TeamPlayerError as error:
                assert str(error) == "TEAMPLAYER_SYNC_ROLE_DENIED"
            else:
                raise AssertionError("Worker allowed native sync")
            after = {
                "main": git.head("main"),
                "epic": git.head(epic.branch),
                "operations": [
                    o.model_dump(mode="json")
                    for kind in (
                        "task_start",
                        "start_runtime",
                        "resume_runtime",
                        "task_merge",
                        "merge_task_to_epic",
                        "task_delivery_test",
                        "stop_runtime",
                    )
                    for o in db.get_operations(EPIC_RUN, kind=kind)
                ],
            }
            assert before == after
            listed = await native.read("list_epics", {"projectId": PROJECT})
            board_epic = next(e for e in listed if e["id"] == fixture["epicId"])
            assert (
                board_epic["name"].startswith("[TEST] F25 status/history mirror")
                and board_epic["status"] == "InProgress"
            )
            proof = {
                "projectId": PROJECT,
                "userId": USER,
                "epicId": fixture["epicId"],
                "epicStatus": board_epic["status"],
                "epicVersion": board_epic["version"],
                "source": "Actual inactive F22 backup; original ownership/runtime/Git preserved",
                "taskResults": results,
                "runtimeGitJournalUnchanged": True,
                "journalDigest": hashlib.sha256(
                    json.dumps(before, sort_keys=True).encode()
                ).hexdigest(),
                "mainCommit": before["main"],
                "fixtureEpicCommit": before["epic"],
                "workerDeniedBeforeNativeCall": True,
                "injection": "One-shot injected reply loss after successful native Done write",
            }
            save("verify-proof" if verify_only else "proof", proof)
            print(json.dumps(proof, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        phase = sys.argv[1]
        if phase == "prepare":
            prepare()
        elif phase in {"sync", "verify"}:
            asyncio.run(exercise(phase == "verify"))
        else:
            raise RuntimeError("Unknown phase")
    except Exception:
        print(
            "F25_PROBE_FAILED: inspect fixed identity/proof/phase; raw errors withheld",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
