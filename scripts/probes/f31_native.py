"""Explicit F31 operator fixture; actual F05/F15/F16/F25/F13, no bootstrap adoption.

prepare requires the already approved harmless repo and a fresh named server.
Create native board fixtures separately from spec.json; write returned IDs to fixture.json.
start sends only the actual F15 assignment. park/export never supply Worker report facts.
Artifacts and live named shell/server remain for the separate F32 same-session test.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import f30_native as shared

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.git import GitAdapter
from orchestrator.application.state_service import StateService
from orchestrator.application.task_attention_service import TaskAttentionService
from orchestrator.application.task_start_service import TaskStartError, TaskStartService
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.application.worker_report_service import ReportError, WorkerReportService
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import LocalTaskSpec
from orchestrator.persistence.store import StateStore

BASE = Path.cwd() / ".herdr/probes/f31"
PROJECT, EPIC, RUN = "f31-probe", "f31-epic", "f31-epic-run"
EXTERNAL_PROJECT, USER = shared.EXTERNAL_PROJECT, shared.USER
COORDINATOR = Actor(actor_id="f31-coordinator", role=Role.COORDINATOR, project_id=PROJECT)
INTEGRATION = Actor(
    actor_id="f31-integration", role=Role.INTEGRATION, project_id=PROJECT, epic_run_id=RUN
)
shared.BASE = BASE


def save(name, value):
    (BASE / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load(name):
    return json.loads((BASE / (name + ".json")).read_text())


def prepare(repository, server):
    if os.environ.get("HERDR_ENV") != "1" or BASE.exists() or not server.startswith("hc-f31-"):
        raise RuntimeError("Fresh owned native fixture under Herdr is required")
    repository = repository.resolve(strict=True)
    g = GitAdapter(repository)
    main = g.inspect(repository, "main", clean=True)
    if g.in_progress(repository) or g.unsafe_index_paths(repository):
        raise RuntimeError("Unsafe approved fixture")
    BASE.mkdir(parents=True)
    settings = Settings(
        repository=repository,
        worktree_root=BASE / "trees",
        sqlite_path=BASE / "state.sqlite",
        max_workers=2,
        worker_test_command=(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"),
        review_context=EpicReviewSpec(
            version=1,
            project_id=PROJECT,
            epic_id=EPIC,
            requirements=["Do not guess missing retention policy"],
            acceptance_criteria=["Blocked Worker physically parked with same resources"],
            sources=["AGENTS.md", "README.md"],
        ),
    )
    save("settings", settings.model_dump(mode="json"))
    save("operator", {"server": server, "approved_repository": str(repository)})
    save(
        "before-git",
        {
            "main": main,
            "worktrees": [str(p) for p in g.worktrees()],
            "refs": dict(
                line.split()
                for line in g.run("for-each-ref", "--format=%(refname) %(objectname)").splitlines()
            ),
        },
    )
    with StateStore(settings.sqlite_path) as db:
        epic = WorktreeService(settings, db).create_epic_worktree(
            COORDINATOR, epic_id=EPIC, run_id=RUN
        )
        path = Path(epic.worktree_path)
        (path / "AGENTS.md").write_text(
            "# F31 harmless native fixture\n\n"
            "Only act on the assigned task in your own worktree. Never merge, switch branch, "
            "modify other worktrees, access credentials or call TeamPlayer/network. "
            "Give the assignment's exact WORKING ACK first. For a missing required value, "
            "emit its strict version-1 BLOCKED report with reason and concrete input_required. "
            "The F31 task has intentionally missing RETENTION_DAYS. Do not invent or derive "
            "this value from historical greeting files. Do not implement until an operator "
            "provides that decision. No tests, commits or file changes are needed before input.\n"
        )
        (path / "README.md").write_text(
            "# F31 blocker fixture\n\n"
            "Task A needs an operator decision: RETENTION_DAYS is absent. "
            "Before implementation emit BLOCKED; leave every file unchanged. "
            "After actual input implement f31_retention.retention_days() returning exactly "
            "the decided strict positive integer and own unittest tests. "
            "Historical greeting files/tests are unchanged data.\n\n"
            "Test: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v\n"
        )
        g.run("add", "--", "AGENTS.md", "README.md", cwd=path)
        g.run("commit", "-m", "seed F31 explicit missing-input fixture", cwd=path)
        sha = g.inspect(path, epic.branch, clean=True)
        db.update_run_metadata(epic.model_copy(update={"current_commit": sha}))
        save("seed", {"commit": sha, "epic_run_id": epic.id})
    spec = LocalTaskSpec(
        version=1,
        project_id=PROJECT,
        epic_id=EPIC,
        task_id="A",
        name="[TEST] F31 A explicit missing retention input"
        + (" r2" if PROJECT.endswith("r2") else ""),
        goal="Give the exact WORKING ACK then report BLOCKED for missing RETENTION_DAYS. "
        "Do not implement, test, commit or modify any file until the actual operator decision.",
        requirements=[
            "RETENTION_DAYS is intentionally absent; never guess it or reuse a historical value.",
            "Before input emit strict version-1 BLOCKED with clear reason and request a positive "
            "integer RETENTION_DAYS; keep branch/worktree/session and leave files unchanged.",
            "After input implement only f31_retention.retention_days() returning the decided "
            "strict positive integer, with own unittest tests.",
        ],
        scope=["f31_retention.py", "tests/test_f31_retention.py"],
        out_of_scope=["Other modules, repository controls, credentials, network, merge"],
        acceptance_criteria=[
            "Explicit blocker requests absent RETENTION_DAYS without guessing or file changes.",
            "After actual input own retention module/tests match the decision and pass.",
        ],
        sources=["AGENTS.md", "README.md"],
        verification_steps=["PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v"],
    )
    save("spec", spec.model_dump(mode="json"))
    print("F31 actual F05 fixture prepared; native board binding required next")


def composition(settings, db, adapter):
    fixture = load("fixture")
    herdr = shared.StartupPreflight(load("operator")["server"], sandbox="workspace-write")
    codex = CodexAdapter()
    writer = shared.FixtureWriter(adapter, fixture)
    sync = TeamPlayerSyncService(
        settings,
        db,
        writer,
        project_id=PROJECT,
        external_project_id=EXTERNAL_PROJECT,
        user_id=USER,
        scopes={RUN: ("A",)},
        codex=codex,
    )
    return sync, herdr, codex


async def operate(phase):
    settings = Settings.model_validate(load("settings"))
    with StateStore(settings.sqlite_path) as db:
        async with shared.connection() as adapter:
            sync, herdr, codex = composition(settings, db, adapter)
            start = TaskStartService(settings, db, herdr, codex)
            if phase == "start":
                await sync.bind_epic(COORDINATOR, RUN, load("fixture")["epicId"])
                epic = db.get_epic(RUN)
                if epic.status == EpicState.PLANNED:
                    StateService(db).transition_epic(
                        RUN,
                        EpicState.ACTIVE,
                        expected=EpicState.PLANNED,
                        event_id="f31-start-epic",
                        actor=COORDINATOR,
                    )
                assert (await sync.sync_epic(COORDINATOR, RUN))["status"] in {"SYNCED", "EXISTING"}
                existing = db.get_operation(PROJECT, "task_start", "A")
                task = db.get_task(existing.task_run_id) if existing else None
                if task is None or task.internal_status == TaskState.CLAIMED:
                    task = start.prepare_git(INTEGRATION, RUN, load("spec"))
                await sync.bind_task(INTEGRATION, task.id, load("fixture")["tasks"]["A"])
                assert (await sync.sync_task(INTEGRATION, task.id))["status"] in {
                    "SYNCED",
                    "EXISTING",
                }
                # F15 dispatch returns WAITING; keep observing the original bounded
                # intent now. Never repeat Git preparation or assignment transport.
                try:
                    result = start.start(INTEGRATION, RUN, load("spec"))
                    while result["status"] == "WAITING":
                        await asyncio.sleep(0.5)
                        result = start.start(INTEGRATION, RUN, load("spec"))
                except TaskStartError as error:
                    op = db.get_operation(PROJECT, "task_start", "A")
                    save("start-result", {"code": str(error), "stage": op.result["stage"]})
                    raise
                task = db.get_task(task.id)
                assert (await sync.sync_task(INTEGRATION, task.id))["status"] in {
                    "SYNCED",
                    "EXISTING",
                }
                save("working", task.model_dump(mode="json"))
                print(
                    json.dumps(
                        {
                            "state": task.internal_status,
                            "task_run_id": task.id,
                            "session_id": task.codex_session_id,
                            "slot": task.worker_slot,
                        }
                    )
                )
                return
            tasks = db.get_tasks(RUN)
            assert len(tasks) == 1
            task = tasks[0]
            attention = TaskAttentionService(settings, db, sync, herdr, codex)
            if phase == "park":
                reports = WorkerReportService(settings, db, herdr, codex)
                if task.internal_status == TaskState.WORKING:
                    deadline = time.monotonic() + 45
                    while True:
                        try:
                            reports.collect(INTEGRATION, task.id, expected_status="BLOCKED")
                            break
                        except ReportError as error:
                            if str(error) not in {
                                "REPORT_NOT_AVAILABLE",
                                "REPORT_TURN_NOT_FINISHED",
                            }:
                                raise
                            if time.monotonic() >= deadline:
                                raise
                            await asyncio.sleep(1)
                before = db.get_task(task.id)
                save("blocked-before-park", before.model_dump(mode="json"))
                result = await attention.park_blocked(INTEGRATION, task.id)
                save("park-result", result)
                save("parked", db.get_task(task.id).model_dump(mode="json"))
                print(json.dumps(result))
                return
            assert phase == "export"
            first = load("park-result")
            before = db.get_task(task.id)
            counts = {
                kind: len(db.get_operations(RUN, kind=kind))
                for kind in (
                    "start_runtime",
                    "dispatch_assignment",
                    "stop_runtime",
                    "task_attention",
                )
            }
            replay = await attention.park_blocked(INTEGRATION, task.id)
            assert replay == first and before.internal_status == TaskState.PARKED
            assert before.worker_slot is None and attention.lifecycle.confirm_task_inactive(
                INTEGRATION, task.id
            )
            assert counts == {k: len(db.get_operations(RUN, kind=k)) for k in counts}
            original = load("working")
            assert all(
                getattr(before, k) == original[k]
                for k in (
                    "id",
                    "branch",
                    "worktree_path",
                    "codex_session_id",
                    "base_commit",
                    "current_commit",
                )
            )
            actual = await adapter.read(
                "get_task",
                {
                    "projectId": EXTERNAL_PROJECT,
                    "taskId": load("fixture")["tasks"]["A"],
                },
            )
            assert actual["status"] == "NeedsInput" and actual["responsibleUserId"] == USER
            assert (
                actual["executionOwnerKind"] == "User"
                and "Responsible role: User" in actual["description"]
            )
            assert herdr.get_agent(before.worker_agent_id) is None
            g = GitAdapter(settings.repository)
            baseline = load("before-git")
            assert g.head("main") == baseline["main"]
            assert all(
                g.run("rev-parse", "--verify", r).strip() == sha
                for r, sha in baseline["refs"].items()
            )
            assert all(Path(p) in g.worktrees() for p in baseline["worktrees"])
            assert (
                g.inspect(Path(before.worktree_path), before.branch, clean=True)
                == before.base_commit
            )
            proof = {
                "acceptance_pass": True,
                "fixture": load("fixture"),
                "project_id": PROJECT,
                "epic_run_id": RUN,
                "task": before.model_dump(mode="json"),
                "replay": replay,
                "operation_counts": counts,
                "blocker": db.get_operations(RUN, kind="task_attention")[0].model_dump(mode="json"),
                "physical_stop": db.get_operations(RUN, kind="stop_runtime")[0].model_dump(
                    mode="json"
                ),
                "same_resources": True,
                "slot_released_after_stop": True,
                "old_refs_and_worktrees_preserved": True,
                "actual_native_status": actual["status"],
                "native_version": actual["version"],
                "next": "F32 same-session input/resume; owned server and pane preserved",
            }
            save("proof", proof)
            print(
                json.dumps(
                    {
                        "F31_native": "PASS",
                        "session": before.codex_session_id,
                        "stop": first["stop_id"],
                        "stage": replay["stage"],
                    }
                )
            )


def main():
    logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "start", "park", "export"))
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--server")
    parser.add_argument("--attempt", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    if args.attempt == 2:
        global BASE, PROJECT, EPIC, RUN, COORDINATOR, INTEGRATION
        BASE = BASE.with_name("f31-r2")
        PROJECT, EPIC, RUN = "f31-probe-r2", "f31-epic-r2", "f31-epic-run-r2"
        COORDINATOR = Actor(
            actor_id="f31-coordinator-r2", role=Role.COORDINATOR, project_id=PROJECT
        )
        INTEGRATION = Actor(
            actor_id="f31-integration-r2",
            role=Role.INTEGRATION,
            project_id=PROJECT,
            epic_run_id=RUN,
        )
        shared.BASE = BASE
    if args.phase == "prepare":
        prepare(args.repository, args.server)
    else:
        asyncio.run(operate(args.phase))


if __name__ == "__main__":
    main()
