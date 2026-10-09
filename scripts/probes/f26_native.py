"""Bounded sequential test operator. One native Worker; new Git/SQLite/board fixture.

Each phase preserves durable product operations. No fake ownership/ACK/merge/test
facts. Manual input and final Done recording are explicit test-operator steps;
automated input/completion policies and the long-lived Coordinator arrive later.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from uuid import uuid4

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.git import GitAdapter
from orchestrator.adapters.herdr import HerdrAdapter
from orchestrator.adapters.teamplayer_mcp import (
    OperatorHeaders,
    TeamPlayerError,
    teamplayer_connection,
)
from orchestrator.application.epic_integration_service import EpicIntegrationService
from orchestrator.application.runtime_lifecycle_service import RuntimeLifecycleService
from orchestrator.application.state_service import StateService
from orchestrator.application.task_approval_service import TaskApprovalService
from orchestrator.application.task_merge_service import TaskMergeService
from orchestrator.application.task_review_service import TaskReviewService
from orchestrator.application.task_start_service import TaskStartService
from orchestrator.application.teamplayer_reader import TeamPlayerReader
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.application.worker_report_service import ReportError, WorkerReportService
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Actor, Role, VerifiedFacts
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import LocalTaskSpec, canonical_json
from orchestrator.persistence.store import StateStore

BASE = Path.cwd() / ".herdr/probes/f26"
ORIGINAL_BASE, ATTEMPT = BASE, 1
PROJECT, EPIC, RUN, TASK = "f26-probe", "f26-epic", "f26-epic-run", "f26-input"
EXTERNAL_PROJECT = "d2ee4c75-7b80-465f-83ac-1750854a8e80"
USER = "105f26a7-0648-438d-94fd-3260ac3af4ee"
COORDINATOR = Actor(
    actor_id="f26-coordinator", role=Role.COORDINATOR, project_id=PROJECT, epic_run_id=RUN
)
INTEGRATION = COORDINATOR.model_copy(
    update={"actor_id": "f26-integration", "role": Role.INTEGRATION}
)


def save(name, data):
    (BASE / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load(name):
    return json.loads((BASE / (name + ".json")).read_text())


def ops(db):
    kinds = [
        r[0]
        for r in db.db.execute(
            "SELECT DISTINCT kind FROM operations WHERE epic_run_id=? ORDER BY kind", (RUN,)
        )
    ]
    return [o for kind in kinds for o in db.get_operations(RUN, kind=kind)]


def only_task(db):
    tasks = db.get_tasks(RUN)
    assert len(tasks) == 1 and tasks[0].task_id == TASK
    return tasks[0]


class StartupPreflight(HerdrAdapter):
    def start_agent(self, name, pane_id, cwd, *, resume_session_id=None):
        super().start_agent(name, pane_id, cwd, resume_session_id=resume_session_id)
        time.sleep(3)
        command = [
            "herdr",
            "--session",
            self.server_session,
            "agent",
            "read",
            name,
            "--source",
            "visible",
            "--lines",
            "80",
        ]
        visible = subprocess.run(command, check=True, capture_output=True, text=True).stdout
        suffix = str(uuid4())
        (BASE / (name + "-" + suffix + "-startup.txt")).write_text(visible)
        # Known voluntary banner only. Actual approval/trust UI requires operator input.
        if "Set up security for Daybreak mode" in visible and "esc to dismiss" in visible:
            self.call("agent", "send-keys", name, "esc")
            time.sleep(3)
            visible = subprocess.run(command, check=True, capture_output=True, text=True).stdout
            (BASE / (name + "-" + suffix + "-after-banner.txt")).write_text(visible)
        if "esc to dismiss" in visible or "trust this" in visible.lower():
            raise RuntimeError("Startup UI requires inspection; do not automatically approve")


def prepare(server):
    if ATTEMPT == 2:
        return prepare_retry(server)
    if BASE.exists() or not server.startswith("hc-f26-"):
        raise RuntimeError("Fresh F26 history and explicitly owned named test server required")
    BASE.mkdir(parents=True)
    repo = BASE / "repo"
    repo.mkdir()
    for args in (
        ["init", "-b", "main"],
        ["config", "user.name", "F26 Fixture"],
        ["config", "user.email", "f26@example.invalid"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "README.md").write_text(
        "Harmless isolated F26 runtime and TeamPlayer fixture.\n\n"
        "Run tests: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v\n"
    )
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n")
    (repo / "AGENTS.md").write_text(
        "# F26 harmless test fixture\n\n"
        "Worker writes only assigned task worktree, tests and commits its own bounded changes. "
        "Never merge, edit main/epic/other worktrees, access credentials, TeamPlayer or network. "
        "Start with ACK and then emit the assignment's strict version-1 final report. "
        "Required greeting prefix initially comes from an unavailable operator decision: "
        "do not guess it; report BLOCKED with concrete input_required and no code change. "
        "After explicit input in this same session, implement original acceptance.\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "seed harmless F26 fixture"],
        check=True,
        capture_output=True,
    )
    verifier = BASE / "verify.py"
    verifier.write_text("""import subprocess, sys
from pathlib import Path
result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
if result.returncode:
    raise SystemExit(result.returncode)
sys.path.insert(0, str(Path.cwd()))
from greeting import greet
assert greet('  Åsa  ') == 'Hej, Åsa!'
assert greet('\\t東京\\n') == 'Hej, 東京!'
assert greet('Jose\\u0301') == 'Hej, Jose\\u0301!'
for value in (None, 42, b'Name'):
    try: greet(value)
    except TypeError: pass
    else: raise AssertionError('non-string accepted')
for value in ('', '   ', '\\t\\n'):
    try: greet(value)
    except ValueError: pass
    else: raise AssertionError('empty name accepted')
print('F26 independent Unicode/type/empty acceptance PASS')
""")
    settings = Settings(
        repository=repo,
        worktree_root=BASE / "trees",
        sqlite_path=BASE / "state.sqlite",
        max_workers=1,
        worker_test_command=(sys.executable, str(verifier)),
        worker_test_timeout=45,
        review_context=EpicReviewSpec(
            version=1,
            project_id=PROJECT,
            epic_id=EPIC,
            requirements=[
                "One Worker; explicit input; same session; protected delivery and TeamPlayer mirror"
            ],
            acceptance_criteria=[
                "Greeting conforms to the supplied prefix and Unicode/type/empty cases.",
                "Actual review, merge tests, stop and final main verification precede Done.",
            ],
            sources=["AGENTS.md", "README.md"],
        ),
    )
    save("settings", settings.model_dump(mode="json"))
    save("operator", {"server": server})
    with StateStore(settings.sqlite_path) as db:
        epic = WorktreeService(settings, db).create_epic_worktree(
            COORDINATOR, epic_id=EPIC, run_id=RUN
        )
        save("before-git", {"main": GitAdapter(repo).head("main"), "epic": epic.base_commit})
    spec = LocalTaskSpec(
        version=1,
        project_id=PROJECT,
        epic_id=EPIC,
        task_id=TASK,
        name="F26 supply greeting input and resume same Worker",
        goal="Implement greeting.greet, tests and README. Prefix is an operator decision "
        "initially unavailable. After ACK, report strict BLOCKED JSON with input_required "
        "asking for the prefix; do not guess or implement. Wait for explicit same-session input. "
        "Then meet original criteria, run tests, commit and report READY_FOR_REVIEW.",
        requirements=[
            "Operator supplies prefix after blocker report; never infer it from files.",
            "Return '<prefix>, <trimmed name>!' preserving underlying Unicode and combining marks.",
            "Raise TypeError for non-str and ValueError for empty or all-whitespace name.",
        ],
        scope=["greeting.py", "tests/test_greeting.py", "README.md"],
        out_of_scope=[
            "Merges, other worktrees, credentials, TeamPlayer, network and operator journals"
        ],
        acceptance_criteria=[
            "Explicitly supplied prefix is used in '<prefix>, <trimmed name>!'.",
            "Whitespace, Unicode and combining marks have independent regression coverage.",
            "Non-string and empty/all-whitespace inputs raise specified errors with tests.",
            "Own task changes committed, worktree clean, main/epic unchanged before integration.",
        ],
        sources=["AGENTS.md", "README.md"],
        dependencies=[],
        external_prerequisites=[],
        verification_steps=[
            "PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v",
            "git status --short: clean after own commit",
        ],
    )
    save("spec", spec.model_dump(mode="json"))
    print(
        json.dumps(
            {
                "spec": spec.model_dump(mode="json"),
                "epicName": "[TEST] F26 live TeamPlayer lifecycle (2026-10-08)",
            },
            ensure_ascii=False,
        )
    )


def prepare_retry(server):
    """New actual F05 fixture; never rewrite the lost terminal's ownership journal."""
    if BASE.exists() or server != "hc-f26-20261008":
        raise RuntimeError("Fresh retry history and exact owned test server required")
    original = json.loads((ORIGINAL_BASE / "settings.json").read_text())
    settings = Settings.model_validate(original)
    git = GitAdapter(settings.repository)
    git.inspect(settings.repository, "main", clean=True)
    assert git.head("main") == json.loads((ORIGINAL_BASE / "before-git.json").read_text())["main"]
    with StateStore(settings.sqlite_path) as old:
        tasks = old.get_tasks("f26-epic-run")
        assert len(tasks) == 1
        abandoned = tasks[0]
        assert (
            abandoned.internal_status == TaskState.STARTING and abandoned.codex_session_id is None
        )
        assert not old.get_operations("f26-epic-run", kind="dispatch_assignment")
        start = old.get_operation("f26-probe", "start_runtime", abandoned.id)
        assert start.status == "PENDING" and start.error_code == "RUNTIME_PANE_CHANGED"
    herdr = HerdrAdapter(server, sandbox="workspace-write")
    assert not herdr.call("agent", "list")["agents"]
    pane = herdr.pane(abandoned.herdr_pane_id)
    info = herdr.process_info(abandoned.herdr_pane_id)
    assert pane["terminal_id"] != abandoned.herdr_terminal_id
    assert pane["cwd"] == abandoned.worktree_path
    assert len(info["foreground_processes"]) == 1
    assert info["foreground_processes"][0]["pid"] == info["shell_pid"]
    old_cwd_processes = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if str((process / "cwd").resolve(strict=True)) == abandoned.worktree_path:
                old_cwd_processes.append(int(process.name))
        except (OSError, RuntimeError):
            continue
    assert old_cwd_processes == [info["shell_pid"]], "Original cwd has unaccounted processes"
    BASE.mkdir(parents=True)
    for name in ("fixture", "before-board"):
        save(name, json.loads((ORIGINAL_BASE / (name + ".json")).read_text()))
    save(
        "abandoned-start",
        {
            "run": abandoned.model_dump(mode="json"),
            "operation": start.model_dump(mode="json"),
            "observedPane": pane,
            "shellOnly": True,
            "oldCwdProcesses": old_cwd_processes,
            "reason": "Server stopped before assignment; recreated terminal rejected by F11",
        },
    )
    (BASE / "verify.py").write_text((ORIGINAL_BASE / "verify.py").read_text())
    settings = Settings.model_validate(
        settings.model_dump()
        | {
            "sqlite_path": BASE / "state.sqlite",
            "worktree_root": BASE / "trees",
            "worker_test_command": (sys.executable, str(BASE / "verify.py")),
            "review_context": settings.review_context.model_copy(
                update={"project_id": PROJECT, "epic_id": EPIC}
            ),
        }
    )
    save("settings", settings.model_dump(mode="json"))
    save("operator", {"server": server})
    spec = LocalTaskSpec.model_validate(json.loads((ORIGINAL_BASE / "spec.json").read_text()))
    spec = spec.model_copy(update={"project_id": PROJECT, "epic_id": EPIC})
    save("spec", spec.model_dump(mode="json"))
    with StateStore(settings.sqlite_path) as db:
        epic = WorktreeService(settings, db).create_epic_worktree(
            COORDINATOR, epic_id=EPIC, run_id=RUN
        )
        save("before-git", {"main": git.head("main"), "epic": epic.base_commit})
    print(
        "Fresh actual F05 retry fixture; original lost-start evidence and approved repo preserved"
    )


def connection():
    config = tomllib.loads((Path.home() / ".codex/config.toml").read_text())["mcp_servers"][
        "teamplayer"
    ]
    return teamplayer_connection(
        config["url"],
        OperatorHeaders(
            command=tuple(shlex.split(config.get("http_headers_helper", ""))),
            bearer_env=config.get("bearer_token_env_var"),
        ),
        writable=True,
    )


def service(settings, db, adapter):
    fixture = load("fixture")

    class FixtureWriter:
        read = adapter.read
        redact_text = adapter.redact_text

        async def write(self, name, arguments):
            request = arguments if name == "update_epic_status" else arguments.get("request", {})
            target_key = "epicId" if name == "update_epic_status" else "taskId"
            expected = fixture["epicId"] if target_key == "epicId" else fixture["taskId"]
            if (
                name not in {"update_epic_status", "update_task_status", "update_task_details"}
                or request.get("projectId") != EXTERNAL_PROJECT
                or request.get(target_key) != expected
            ):
                raise TeamPlayerError("F26_FIXTURE_WRITE_SCOPE_DENIED")
            intent = {
                "tool": name,
                "projectId": EXTERNAL_PROJECT,
                "targetId": expected,
                "version": request.get("version"),
                "status": request.get("status"),
            }
            with (BASE / "native-write-intents.jsonl").open("a") as journal:
                journal.write(json.dumps(intent) + "\n")
            return await adapter.write(name, arguments)

    return TeamPlayerSyncService(
        settings,
        db,
        FixtureWriter(),
        project_id=PROJECT,
        external_project_id=EXTERNAL_PROJECT,
        user_id=USER,
        scopes={RUN: (TASK,)},
    )


async def snapshot(adapter):
    return (
        await TeamPlayerReader(
            adapter, project_id=EXTERNAL_PROJECT, user_id=USER, project_name="HerdrCoordinator"
        ).read_project()
    ).model_dump(mode="json", by_alias=True)


async def mirror(phase, settings, db):
    fixture = load("fixture")
    assert fixture["projectId"] == EXTERNAL_PROJECT and fixture["userId"] == USER
    async with connection() as adapter:
        sync = service(settings, db, adapter)
        if phase == "bind-planned":
            await sync.bind_epic(COORDINATOR, RUN, fixture["epicId"])
            if ATTEMPT == 2:
                print("Retry fixture bound; original Planned baseline retained; no epic rollback")
                return
            assert (await sync.sync_epic(COORDINATOR, RUN))["status"] == "SYNCED"
            save("before-board", await snapshot(adapter))
            print("Planned epic mirrored; full board baseline saved")
        elif phase == "sync-epic-active":
            result = await sync.sync_epic(COORDINATOR, RUN)
            assert result["status"] in {"SYNCED", "EXISTING"}, result
            save(phase, result)
            print("Started fixture epic mirrored; task remains awaiting native ACK")
        else:
            task = only_task(db)
            await sync.bind_task(INTEGRATION, task.id, fixture["taskId"])
            a = await sync.sync_task(INTEGRATION, task.id)
            assert a["status"] in {"SYNCED", "EXISTING"}, a
            e = await sync.sync_epic(COORDINATOR, RUN)
            assert e["status"] in {"SYNCED", "EXISTING"}, e
            current = await adapter.read(
                "get_task", {"projectId": EXTERNAL_PROJECT, "taskId": fixture["taskId"]}
            )
            epics = await adapter.read("list_epics", {"projectId": EXTERNAL_PROJECT})
            native_epic = next(v for v in epics if v["id"] == fixture["epicId"])
            save(
                phase,
                {"taskSync": a, "epicSync": e, "nativeTask": current, "nativeEpic": native_epic},
            )
            print(
                json.dumps(
                    {"phase": phase, "taskStatus": current["status"], "version": current["version"]}
                )
            )


async def disconnected_done(settings, db):
    # A genuinely closed MCP client, not a fabricated server response or synthetic TimeoutError.
    async with connection() as closed:
        await closed.read("get_me", {})
    async with connection() as live:

        class DisconnectedWriter:
            read = live.read
            redact_text = live.redact_text

            async def write(self, name, arguments):
                return await closed.write(name, arguments)

        task = only_task(db)
        assert task.internal_status == TaskState.DONE
        before = [o.model_dump(mode="json") for o in ops(db) if o.kind != "teamplayer_sync"]
        pending = await service(settings, db, DisconnectedWriter()).sync_task(INTEGRATION, task.id)
        assert (
            pending["status"] == "PENDING"
            and pending["reason"] == "TEAMPLAYER_WRITE_OUTCOME_UNKNOWN"
        )
        save("disconnected", pending)
        assert before == [o.model_dump(mode="json") for o in ops(db) if o.kind != "teamplayer_sync"]
        print(json.dumps(pending))


async def export(settings, db, herdr):
    fixture, task, epic = load("fixture"), only_task(db), db.get_epic(RUN)
    assert task.internal_status == TaskState.DONE and epic.status == EpicState.DONE
    assert task.worker_slot is None
    assert RuntimeLifecycleService(settings, db, herdr).confirm_task_inactive(INTEGRATION, task.id)
    original = load("start")["task"]
    assert all(
        getattr(task, field) == original[field]
        for field in ("id", "codex_session_id", "branch", "worktree_path", "worker_agent_id")
    )
    async with connection() as adapter:
        before, after = load("before-board"), await snapshot(adapter)
        other = {}
        tracking_task = "1626d7a9-387d-47cd-b20b-86cb2a9f0613"
        for kind, key, excluded in (
            ("epics", "id", fixture["epicId"]),
            ("tasks", "taskId", fixture["taskId"]),
        ):
            first = {i[key]: i for i in before[kind] if i[key] != excluded}
            last = {i[key]: i for i in after[kind] if i[key] != excluded}
            if kind == "tasks":
                # Development workflow records this probe's real trust blocker and progress.
                # The product fixture writer never writes the parent tracking task.
                old_tracking, new_tracking = first.pop(tracking_task), last.pop(tracking_task)
                mutable = {"status", "version", "description"}
                assert {k: v for k, v in old_tracking.items() if k not in mutable} == {
                    k: v for k, v in new_tracking.items() if k not in mutable
                }, "Parent tracking task changed outside documented workflow fields"
            assert first == last, "An unrelated board item changed"
            other[kind] = first
        native_task = next(t for t in after["tasks"] if t["taskId"] == fixture["taskId"])
        native_epic = next(e for e in after["epics"] if e["id"] == fixture["epicId"])
        assert native_task["status"] == native_epic["status"] == "Done"
        initial = next(t["status"] for t in before["tasks"] if t["taskId"] == fixture["taskId"])
        sequence = [initial] + [
            load(p)["nativeTask"]["status"]
            for p in ("sync-active", "sync-attention", "sync-resumed", "sync-done")
        ]
        assert sequence == ["Pending", "InProgress", "NeedsInput", "InProgress", "Done"]
        epic_sequence = [
            next(e["status"] for e in before["epics"] if e["id"] == fixture["epicId"]),
            load("sync-active")["nativeEpic"]["status"],
            native_epic["status"],
        ]
        assert epic_sequence == ["Pending", "InProgress", "Done"]
        syncs = [o for o in ops(db) if o.kind == "teamplayer_sync" and o.task_run_id == task.id]
        assert len(syncs) == 4 and all(o.status == "SUCCEEDED" for o in syncs)
        for op in syncs:
            assert native_task["description"].count("<!-- herdr-event:" + op.id + ";") == 1
        assert len(db.get_operations(RUN, kind="merge_task_to_epic")) == 1
        assert len(db.get_operations(RUN, kind="task_merge")) == 1
        assert len(db.get_operations(RUN, kind="resume_runtime")) == 1
        assert len(db.get_operations(RUN, kind="merge_epic_to_main")) == 1
        assert (
            db.get_operation(PROJECT, "test_operator_input", "f26-prefix-input").status
            == "SUCCEEDED"
        )
        writes = [
            json.loads(line)
            for line in (BASE / "native-write-intents.jsonl").read_text().splitlines()
        ]
        assert writes and all(
            item["projectId"] == EXTERNAL_PROJECT
            and item["targetId"] in {fixture["epicId"], fixture["taskId"]}
            for item in writes
        )
        git = GitAdapter(settings.repository)
        review = db.get_operation(PROJECT, "epic_review", "f26-epic-review")
        assert git.parents(epic.merge_commit) == (
            review.result["target_commit"],
            review.result["source_commit"],
        )
        proof = {
            "projectId": EXTERNAL_PROJECT,
            "userId": USER,
            "epicId": fixture["epicId"],
            "taskId": fixture["taskId"],
            "localRunId": task.id,
            "sessionId": task.codex_session_id,
            "taskSequence": sequence,
            "epicSequence": epic_sequence,
            "physicalStopBeforeSlotRelease": True,
            "sameSessionBranchWorktree": True,
            "taskCommit": task.current_commit,
            "taskMergeCommit": task.merge_commit,
            "epicMergeCommit": epic.merge_commit,
            "mainCommit": git.head("main"),
            "taskParents": list(git.parents(task.merge_commit)),
            "epicParents": list(git.parents(epic.merge_commit)),
            "oneTaskMerge": True,
            "oneResume": True,
            "fourUniqueHistoryEvents": True,
            "closedTransportPending": load("disconnected"),
            "reconnectSucceeded": True,
            "unrelatedBoardItemsUnchanged": True,
            "trackingWorkflowException": {
                "taskId": tracking_task,
                "fields": ["status", "version", "description"],
                "reason": "Parent development task records actual approval blocker and progress",
                "allOtherFieldsUnchanged": True,
            },
            "productFixtureWritesOnlyBoundIds": True,
            "fixtureWriteAttempts": len(writes),
            "attempt": ATTEMPT,
            "abandonedStartPreserved": ATTEMPT == 2,
            "otherBoardDigest": hashlib.sha256(canonical_json(other).encode()).hexdigest(),
            "nativeTaskVersion": native_task["version"],
            "nativeEpicVersion": native_epic["version"],
            "manualOperatorSteps": [
                "prefix input notification",
                "current task/epic review",
                "actual final-test Done record",
            ],
            "limitations": "Bounded fixture; agent loop and F38 OS-autonomy remain future work",
        }
        save("proof", proof)
        save(
            "full-evidence",
            {
                "task": task.model_dump(mode="json"),
                "epic": epic.model_dump(mode="json"),
                "operations": [o.model_dump(mode="json") for o in ops(db)],
            },
        )
        print(json.dumps(proof, ensure_ascii=False, indent=2))


def input_prompt(task):
    return (
        "F26 test operator input; same registered task/run/session/branch.\n"
        "Blocking decision resolved: prefix is exactly Hej.\n"
        "Implement original criteria. Do not merge or edit other worktrees. "
        "Run local unittest tests, commit assigned scope, then emit a fresh strict version-1 "
        "READY_FOR_REVIEW JSON with exact original task/run/branch identifiers and new commit.\n"
        "Correlation: f26-prefix-input; run: " + task.id
    )


def notify_input(settings, db, herdr):
    task = only_task(db)
    assert task.internal_status == TaskState.WORKING and task.worker_slot == 1
    text = input_prompt(task)
    key, kind = "f26-prefix-input", "test_operator_input"
    old = db.get_operation(PROJECT, kind, key)
    if old:
        # Reconcile native delivery, never repeat an uncertain prompt.
        thread = CodexAdapter().read_thread(task.codex_session_id, task.worktree_path)
        found = [
            (t["id"], i["id"])
            for t in thread["turns"]
            for i in t["items"]
            if i.get("type") == "userMessage"
            and any(p.get("text") == text for p in i.get("content", []))
        ]
        assert len(found) == 1, "Unknown/manual input outcome requires inspection"
        db.update_operation(
            old.model_copy(
                update={
                    "status": "SUCCEEDED",
                    "updated_at": utc_now(),
                    "result": old.result | {"native_item": list(found[0])},
                }
            )
        )
        print("Existing single native operator input verified")
        return
    op = Operation(
        project_id=PROJECT,
        epic_run_id=RUN,
        task_run_id=task.id,
        kind=kind,
        idempotency_key=key,
        result={
            "session_id": task.codex_session_id,
            "branch": task.branch,
            "text_hash": hashlib.sha256(text.encode()).hexdigest(),
            "stage": "SEND_INTENT",
        },
    )
    db.add_operation(op)
    herdr.prompt(task.worker_agent_id, text, timeout_ms=10000)
    print("Input submitted; rerun input phase to verify the one native user message")


def execute(phase):
    settings = Settings.model_validate(load("settings"))
    herdr = StartupPreflight(load("operator")["server"], sandbox="workspace-write")
    with StateStore(settings.sqlite_path) as db:
        if phase in {
            "bind-planned",
            "sync-epic-active",
            "sync-active",
            "sync-attention",
            "sync-resumed",
            "sync-done",
            "sync-epic-done",
        }:
            asyncio.run(mirror(phase, settings, db))
        elif phase == "activate":
            epic = db.get_epic(RUN)
            if epic.status == EpicState.PLANNED:
                StateService(db).transition_epic(
                    RUN,
                    EpicState.ACTIVE,
                    expected=EpicState.PLANNED,
                    event_id="f26-activate",
                    actor=COORDINATOR,
                )
            print("Local epic active")
        elif phase == "start":
            result = TaskStartService(settings, db, herdr).start(INTEGRATION, RUN, load("spec"))
            task = only_task(db)
            save("start", {"result": result, "task": task.model_dump(mode="json")})
            print(
                json.dumps(
                    {
                        "status": result["status"],
                        "runId": task.id,
                        "sessionId": task.codex_session_id,
                    }
                )
            )
        elif phase in {"collect-blocked", "collect-ready"}:
            task = only_task(db)
            expected = "BLOCKED" if phase == "collect-blocked" else "READY_FOR_REVIEW"
            try:
                result = WorkerReportService(settings, db, herdr).collect(
                    INTEGRATION, task.id, expected_status=expected
                )
            except ReportError as error:
                print("REPORT_WAIT", str(error))
                return
            save(phase, result)
            print(json.dumps({"reportStatus": result["status"], "runId": task.id}))
        elif phase == "park":
            task = only_task(db)
            assert task.internal_status in {TaskState.BLOCKED, TaskState.PARKED}
            op = RuntimeLifecycleService(settings, db, herdr).stop_task(
                INTEGRATION,
                task.id,
                key="f26-input-park",
                reason="Missing operator greeting prefix; preserve session/branch.",
            )
            save("park", op.model_dump(mode="json"))
            assert db.get_task(task.id).worker_slot is None
            print(json.dumps({"stop": op.status, "state": db.get_task(task.id).internal_status}))
        elif phase == "resume":
            task = only_task(db)
            original = load("start")["task"]
            op = RuntimeLifecycleService(settings, db, herdr).resume_task(
                INTEGRATION, task.id, key="f26-resume"
            )
            current = db.get_task(task.id)
            assert (
                current.codex_session_id == original["codex_session_id"]
                and current.branch == original["branch"]
            )
            assert current.worker_slot == 1 and current.internal_status == TaskState.WORKING
            save("resume", op.model_dump(mode="json"))
            print(json.dumps({"resume": op.status, "sessionId": current.codex_session_id}))
        elif phase == "input":
            notify_input(settings, db, herdr)
        elif phase == "review":
            task = only_task(db)
            result = TaskReviewService(settings, db).request(INTEGRATION, task.id, key="f26-review")
            save("review", result)
            print(
                json.dumps(
                    {
                        "contextId": result["context"]["context_id"],
                        "taskCommit": result["context"]["task_commit"],
                    }
                )
            )
        elif phase == "approve":
            task, context = only_task(db), load("review")["context"]
            # Run only after the operator inspects the complete stored current context/diff/tests.
            decision = dict(
                version=1,
                result="APPROVED",
                context_id=context["context_id"],
                summary="Full current diff, independent tests, sources and criteria reviewed.",
                verified_criteria=context["acceptance_criteria"],
            )
            result = TaskApprovalService(settings, db).approve(
                INTEGRATION, task.id, decision, key="f26-approved"
            )
            save("approval", result)
            print(json.dumps({"approval": result["status"]}))
        elif phase == "deliver":
            result = TaskMergeService(settings, db, herdr).merge(
                INTEGRATION, only_task(db).id, key="f26-deliver", verification_key="f26-first"
            )
            save("delivery-replay" if result["status"] == "EXISTING" else "delivery", result)
            print(json.dumps(result))
        elif phase == "sync-disconnected":
            asyncio.run(disconnected_done(settings, db))
        elif phase == "export":
            asyncio.run(export(settings, db, herdr))
        elif phase in {"epic-verify", "epic-approve", "epic-merge", "epic-final", "epic-done"}:
            manager = EpicIntegrationService(
                settings, db, expected_task_ids=(TASK,), test_command=settings.worker_test_command
            )
            if phase == "epic-verify":
                op = manager.verify_epic(COORDINATOR, RUN, key="f26-aggregate")
            elif phase == "epic-approve":
                op = manager.register_epic_review(
                    COORDINATOR,
                    RUN,
                    verification_key="f26-aggregate",
                    key="f26-epic-review",
                    approved=True,
                    verified_criteria=settings.review_context.acceptance_criteria,
                )
            elif phase == "epic-merge":
                op = manager.merge_epic_to_main(COORDINATOR, RUN, key="f26-main")
            elif phase == "epic-final":
                op = manager.verify_main_merge(
                    COORDINATOR, RUN, merge_key="f26-main", key="f26-final"
                )
            else:
                final = db.get_operation(PROJECT, "verify_main_merge", "f26-final")
                merge = db.get_operation(PROJECT, "merge_epic_to_main", "f26-main")
                assert (
                    final.status == merge.status == "SUCCEEDED" and final.result["exit_code"] == 0
                )
                # Trusted sequential test operator records actual complete evidence;
                # this is not the future autonomous F40 completion policy.
                done = StateService(db).transition_epic(
                    RUN,
                    EpicState.DONE,
                    expected=EpicState.MERGING,
                    event_id="f26-operator-done",
                    actor=COORDINATOR,
                    facts=VerifiedFacts(
                        source_commit=merge.result["source_commit"],
                        target_commit=merge.result["target_commit"],
                        merge_commit=merge.result["merge_commit"],
                        verification_commit=final.result["merge_commit"],
                        tests_passed=True,
                        scope_complete=True,
                        review_approved=True,
                    ),
                )
                save(phase, done.model_dump(mode="json"))
                print("Fixture local epic Done from actual merge/final test")
                return
            assert op.status == "SUCCEEDED"
            save(phase, op.model_dump(mode="json"))
            print(json.dumps({"phase": phase, "operationId": op.id, "status": op.status}))
        else:
            raise RuntimeError("Unsupported phase")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser()
    parser.add_argument("phase")
    parser.add_argument("--server")
    parser.add_argument("--attempt", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    ATTEMPT = args.attempt
    if ATTEMPT == 2:
        BASE = ORIGINAL_BASE.with_name("f26-r2")
        PROJECT, EPIC, RUN = "f26-probe-r2", "f26-epic-r2", "f26-epic-run-r2"
        COORDINATOR = Actor(
            actor_id="f26-coordinator", role=Role.COORDINATOR, project_id=PROJECT, epic_run_id=RUN
        )
        INTEGRATION = COORDINATOR.model_copy(
            update={"actor_id": "f26-integration", "role": Role.INTEGRATION}
        )
    try:
        if args.phase == "prepare":
            prepare(args.server or "")
        else:
            execute(args.phase)
    except Exception:
        print(
            "F26_PROBE_FAILED: inspect fixed phase/run/role/UI; raw errors withheld",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
