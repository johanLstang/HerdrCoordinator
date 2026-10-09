"""F30 operator harness: native three-task/two-Worker proof, no implicit approval.

Run only in the owning F30 worktree. Private artifacts stay in .herdr/probes/f30.
prepare requires an explicitly approved harmless repository and a fresh named
server. Native board creation is a separate operator step using specs.json.
"""

import argparse
import asyncio
import json
import logging
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.git import GitAdapter
from orchestrator.adapters.herdr import HerdrAdapter, HerdrError
from orchestrator.adapters.teamplayer_mcp import (
    OperatorHeaders,
    TeamPlayerError,
    teamplayer_connection,
)
from orchestrator.application.state_service import StateService
from orchestrator.application.task_scheduler_service import TaskSchedulerService
from orchestrator.application.task_selection_service import TaskSelectionService
from orchestrator.application.teamplayer_reader import TeamPlayerReader
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.models import TaskRun
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.domain.worker_contracts import LocalTaskSpec, canonical_json
from orchestrator.persistence.store import StateStore

BASE = Path.cwd() / ".herdr/probes/f30"
ORIGINAL_BASE = BASE
PROJECT, EPIC, RUN = "f30-probe", "f30-epic", "f30-epic-run"
EXTERNAL_PROJECT = "d2ee4c75-7b80-465f-83ac-1750854a8e80"
USER = "105f26a7-0648-438d-94fd-3260ac3af4ee"
COORDINATOR = Actor(actor_id="f30-coordinator", role=Role.COORDINATOR, project_id=PROJECT)
INTEGRATION = Actor(
    actor_id="f30-integration", role=Role.INTEGRATION, project_id=PROJECT, epic_run_id=RUN
)


def configure_attempt(attempt):
    global BASE, PROJECT, EPIC, RUN, COORDINATOR, INTEGRATION
    suffix = "" if attempt == 1 else "-r2"
    BASE = ORIGINAL_BASE.with_name("f30" + suffix)
    PROJECT, EPIC, RUN = "f30-probe" + suffix, "f30-epic" + suffix, "f30-epic-run" + suffix
    COORDINATOR = Actor(
        actor_id="f30-coordinator" + suffix, role=Role.COORDINATOR, project_id=PROJECT
    )
    INTEGRATION = Actor(
        actor_id="f30-integration" + suffix,
        role=Role.INTEGRATION,
        project_id=PROJECT,
        epic_run_id=RUN,
    )


def startup_ui_action(visible):
    """Never paste assignment text into an update, trust or account settings dialog."""
    if "Set up security for Daybreak mode" in visible and "esc to dismiss" in visible:
        return "dismiss-voluntary-banner"
    lower = visible.lower()
    if any(
        s in lower
        for s in (
            "update available",
            "updating codex",
            "trust this",
            "trust and continue",
            "set up security",
            "approve this",
            "would you like",
        )
    ):
        return "operator-required"
    return "ready" if "Ask Codex to do anything" in visible else "operator-required"


class StartupPreflight(HerdrAdapter):
    """Native fixture transport guard; no permission choice or automatic update."""

    def visible(self, name):
        result = subprocess.run(
            [
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
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode:
            raise HerdrError("F30_STARTUP_UI_UNVERIFIED")
        return result.stdout

    def prompt(self, name, text, *, timeout_ms):
        visible = self.visible(name)
        action = startup_ui_action(visible)
        journal("startup-ui", {"agent": name, "action": action, "at": time.time()})
        if action == "dismiss-voluntary-banner":
            self.call("agent", "send-keys", name, "esc")
            time.sleep(1)
            action = startup_ui_action(self.visible(name))
        if action != "ready":
            raise HerdrError("F30_STARTUP_UI_REQUIRES_OPERATOR")
        super().prompt(name, text, timeout_ms=timeout_ms)


def save(name, value):
    (BASE / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load(name):
    return json.loads((BASE / (name + ".json")).read_text())


def journal(name, value):
    with (BASE / (name + ".jsonl")).open("a") as f:
        f.write(canonical_json(value) + "\n")


def prepare(repository, server):
    if BASE.exists() or not server.startswith("hc-f30-"):
        raise RuntimeError("Fresh F30 history and owned named server required")
    repository = repository.resolve(strict=True)
    g = GitAdapter(repository)
    main = g.inspect(repository, "main", clean=True)
    if PROJECT.endswith("-r2"):
        old_settings = Settings.model_validate(
            json.loads((ORIGINAL_BASE / "settings.json").read_text())
        )
        sessions = json.loads(
            subprocess.run(
                ["herdr", "session", "list", "--json"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        )["sessions"]
        old_operator = json.loads((ORIGINAL_BASE / "operator.json").read_text())
        old_session = next(s for s in sessions if s["name"] == old_operator["server"])
        if (
            repository != old_settings.repository
            or old_session["running"]
            or server == old_operator["server"]
            or main != json.loads((ORIGINAL_BASE / "before-git.json").read_text())["main"]
        ):
            raise RuntimeError("Original failed attempt must remain preserved and inactive")
        with StateStore(old_settings.sqlite_path) as old:
            old_tasks = old.get_tasks("f30-epic-run")
            a = next(t for t in old_tasks if t.task_id == "A")
            b = next(t for t in old_tasks if t.task_id == "B")
            stop = old.get_operation("f30-probe", "stop_runtime", "f30-first-aborted-B-stop")
            if (
                a.internal_status != TaskState.STARTING
                or a.codex_session_id is not None
                or b.internal_status != TaskState.PARKED
                or b.worker_slot is not None
                or stop is None
                or stop.status != "SUCCEEDED"
            ):
                raise RuntimeError("Actual first attempt differs from recorded failed startup")
        # Physical old cwd processes must also be absent after the owned server stop.
        old_paths = {t.worktree_path for t in old_tasks}
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                cwd = str((proc / "cwd").resolve(strict=True))
            except (OSError, RuntimeError):
                continue
            if cwd in old_paths:
                raise RuntimeError("Original attempt still has cwd processes")
    if g.in_progress(repository) or g.unsafe_index_paths(repository):
        raise RuntimeError("Unsafe fixture repository")
    BASE.mkdir(parents=True)
    if PROJECT.endswith("-r2"):
        save("fixture", json.loads((ORIGINAL_BASE / "fixture.json").read_text()))
        save(
            "abandoned-attempt",
            {
                "directory": str(ORIGINAL_BASE),
                "project": "f30-probe",
                "acceptance_pass": False,
                "original_resources_preserved": True,
                "original_server_running": False,
                "old_cwd_processes": [],
                "A_reservation_preserved": a.worker_slot,
                "B_stop_id": stop.id,
            },
        )
    verify = BASE / "verify.py"
    verify.write_text("""import importlib, subprocess, sys
from pathlib import Path
r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
assert r.returncode == 0
sys.path.insert(0, str(Path.cwd()))
if Path('f30_names.py').exists():
    clean = importlib.import_module('f30_names').clean_name
    assert clean('  Åsa  ') == 'Åsa'
    assert clean('Jose\\u0301') == 'José'
    assert clean('\\t東京\\n') == '東京'
    for v in (None, 3, True, b'name'):
        try: clean(v)
        except TypeError: pass
        else: raise AssertionError('name type')
    for v in ('', '  ', '\\t\\n'):
        try: clean(v)
        except ValueError: pass
        else: raise AssertionError('empty name')
if Path('f30_numbers.py').exists():
    even = importlib.import_module('f30_numbers').sum_even
    assert even([]) == 0 and even([1, 2, 3, 4]) == 6
    assert even([-4, -3, -2, 0, 2]) == -4
    data = [2, 4]; assert even(data) == 6 and data == [2, 4]
    for v in (None, '123', 3, [True], [1.0], [2, '4']):
        try: even(v)
        except TypeError: pass
        else: raise AssertionError('number type')
if Path('f30_labels.py').exists():
    label = importlib.import_module('f30_labels').make_label
    assert label('  Åsa  ') == '[Åsa]' and label('Jose\\u0301') == '[José]'
    for v, exc in [(None, TypeError), (' ', ValueError)]:
        try: label(v)
        except exc: pass
        else: raise AssertionError('label validation')
print('F30 independent acceptance PASS')
""")
    settings = Settings(
        repository=repository,
        worktree_root=BASE / "trees",
        sqlite_path=BASE / "state.sqlite",
        max_workers=2,
        worker_test_command=(sys.executable, str(verify)),
        worker_test_timeout=60,
        review_context=EpicReviewSpec(
            version=1,
            project_id=PROJECT,
            epic_id=EPIC,
            requirements=["Two isolated native Workers and serial verified task delivery"],
            acceptance_criteria=[
                "A/B overlap; C follows A; at most two reservations; current reviews"
            ],
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
        p = Path(epic.worktree_path)
        (p / "AGENTS.md").write_text(
            "# F30 harmless native fixture\n\n"
            "Implement only your assigned task in your own worktree. Never merge, switch branch, "
            "edit main/epic/other worktrees, read credentials or call TeamPlayer/network. "
            "Start with the assignment's exact WORKING ACK; then implement, test, commit and emit "
            "its strict version-1 READY_FOR_REVIEW report. Existing greeting files/tests are "
            "completed historical fixture data; no prefix input is missing in F30. "
            "Only change your assigned f30 module and tests file. Do not amend/reset commits.\n"
        )
        (p / "README.md").write_text(
            "# F30 three-task native fixture\n\n"
            "A: Unicode clean_name; B: validated sum_even; C: label using integrated A. "
            "A/B independent; C waits for A's reviewed merge and tests.\n\n"
            "Run tests: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v\n"
            "Historical greeting remains unchanged. Worker changes only assigned f30 files.\n"
        )
        g.run("add", "--", "AGENTS.md", "README.md", cwd=p)
        g.run("commit", "-m", "seed F30 bounded parallel fixture rules", cwd=p)
        seed = g.inspect(p, epic.branch, clean=True)
        epic = epic.model_copy(update={"current_commit": seed})
        db.update_run_metadata(epic)
        save("seed", {"commit": seed, "epic": epic.model_dump(mode="json")})
    save("specs", build_specs())
    print("F30 prepared actual F05 epic/seed; native fixture creation and operator binding next")


def build_specs():
    """Pure bounded assignment construction, also used after a known setup checkpoint loss."""
    requirements = {
        "A": [
            "Implement f30_names.clean_name(str): strip whitespace, normalize NFC, "
            "preserve Unicode.",
            "Raise TypeError for every non-str and ValueError for empty/all-whitespace input.",
        ],
        "B": [
            "Implement f30_numbers.sum_even(list[int]): sum even integers including negatives; "
            "[] gives 0.",
            "Require a list and strict int elements, reject bool/non-int; never mutate the input.",
        ],
        "C": [
            "Implement f30_labels.make_label(str): import and use f30_names.clean_name "
            "and return '[<clean name>]'.",
            "Propagate A's TypeError/ValueError; preserve NFC/Unicode; never copy or modify A.",
        ],
    }
    modules = {"A": "names", "B": "numbers", "C": "labels"}
    specs = {}
    for identity in "ABC":
        module = "f30_" + modules[identity]
        spec = LocalTaskSpec(
            version=1,
            project_id=PROJECT,
            epic_id=EPIC,
            task_id=identity,
            name="[TEST] F30 " + identity + " " + module,
            goal="Implement only the assigned small module and thorough unittest tests. "
            "Give the exact WORKING ACK first, then work independently. "
            "Existing historical greeting is already complete; no input is missing.",
            requirements=requirements[identity],
            scope=[module + ".py", "tests/test_" + module + ".py"],
            out_of_scope=[
                "Other modules/tests, README/AGENTS, repository controls, credentials, "
                "network, merge"
            ],
            acceptance_criteria=requirements[identity]
            + ["Own meaningful unittest tests pass; commit only assigned files."],
            sources=["AGENTS.md", "README.md"],
            dependencies=["A"] if identity == "C" else [],
            verification_steps=[
                "PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v",
                "Confirm clean own worktree and report full commit and own changed files",
            ],
        )
        specs[identity] = spec.model_dump(mode="json")
    return specs


def finish_prepare(expected_seed):
    """Operator reconciliation of this known seed commit, never an adoption or new commit."""
    settings = Settings.model_validate(load("settings"))
    before = load("before-git")
    g = GitAdapter(settings.repository)
    with StateStore(settings.sqlite_path) as db:
        epic = db.get_epic(RUN)
        if (
            epic is None
            or epic.status != EpicState.PLANNED
            or epic.project_id != PROJECT
            or epic.epic_id != EPIC
            or db.get_tasks(RUN)
            or epic.base_commit != before["main"]
            or epic.current_commit not in (epic.base_commit, expected_seed)
            or g.head("main") != before["main"]
        ):
            raise RuntimeError("Setup checkpoint no longer matches the known preparation")
        actual = WorktreeService(settings, db).verify_owned_worktree(epic)
        snapshot = g.snapshot(
            Path(epic.worktree_path),
            epic.branch,
            epic.base_commit,
            expected_commit=expected_seed,
        )
        if (
            actual != expected_seed
            or g.parents(actual) != (epic.base_commit,)
            or not snapshot.reviewable
            or {f.path for f in snapshot.changed_files} != {"AGENTS.md", "README.md"}
            or g.in_progress(Path(epic.worktree_path))
        ):
            raise RuntimeError("Actual seed ownership, parent or complete clean diff differs")
        for ref, commit in before["refs"].items():
            if g.run("rev-parse", "--verify", ref).strip() != commit:
                raise RuntimeError("Historical fixture ref changed")
        for path in before["worktrees"]:
            if Path(path) not in g.worktrees():
                raise RuntimeError("Historical worktree missing")
        epic = epic.model_copy(update={"current_commit": actual})
        db.update_run_metadata(epic)
        save("seed", {"commit": actual, "epic": epic.model_dump(mode="json")})
        save("specs", build_specs())
        save(
            "setup-reconciliation",
            {
                "kind": "known_seed_metadata_checkpoint",
                "commit": actual,
                "base": epic.base_commit,
                "diff_sha256": snapshot.commit_diff.sha256,
                "new_git_operations": 0,
                "new_runs": 0,
            },
        )
    print("F30 known seed checkpoint reconciled; no Git commit or runtime created")


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


class FixtureWriter:
    def __init__(self, adapter, fixture):
        self.adapter, self.fixture = adapter, fixture
        self.read, self.redact_text = adapter.read, adapter.redact_text

    async def write(self, name, arguments):
        request = arguments if name == "update_epic_status" else arguments.get("request", {})
        allowed = {"update_epic_status", "update_task_status", "update_task_details"}
        target = request.get("epicId") if name == "update_epic_status" else request.get("taskId")
        expected = (
            {self.fixture["epicId"]}
            if name == "update_epic_status"
            else set(self.fixture["tasks"].values())
        )
        if (
            name not in allowed
            or request.get("projectId") != EXTERNAL_PROJECT
            or target not in expected
        ):
            raise TeamPlayerError("F30_FIXTURE_WRITE_SCOPE_DENIED")
        journal(
            "native-write-intents",
            {
                "tool": name,
                "targetId": target,
                "version": request.get("version"),
                "status": request.get("status"),
            },
        )
        return await self.adapter.write(name, arguments)


def composition(settings, db, adapter):
    fixture = load("fixture")
    h = StartupPreflight(load("operator")["server"], sandbox="workspace-write")
    c = CodexAdapter()
    writer = FixtureWriter(adapter, fixture)
    specs = {fixture["tasks"][k]: v for k, v in load("specs").items()}
    bindings = {fixture["epicId"]: LocalBoardBinding(local_id=EPIC)} | {
        i: LocalBoardBinding(local_id=s["task_id"]) for i, s in specs.items()
    }
    reader = TeamPlayerReader(
        writer,
        project_id=EXTERNAL_PROJECT,
        user_id=USER,
        project_name="HerdrCoordinator",
        bindings=bindings,
    )
    selection = TaskSelectionService(
        settings,
        db,
        reader,
        specs=specs,
        task_order=tuple(fixture["tasks"][k] for k in "ABC"),
        epic_prerequisites={EPIC: ()},
        codex=c,
    )
    sync = TeamPlayerSyncService(
        settings,
        db,
        writer,
        project_id=PROJECT,
        external_project_id=EXTERNAL_PROJECT,
        user_id=USER,
        scopes={RUN: ("A", "B", "C")},
        codex=c,
    )
    return TaskSchedulerService(settings, db, selection, sync, h, c, review_provider=None)


class Sampler:
    """Separate readonly SQLite connection; never share the services' connection."""

    def __init__(self, settings, h):
        self.settings, self.h = settings, h
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        while not self.stop.is_set():
            result = {"at": time.time(), "tasks": []}
            try:
                with sqlite3.connect(
                    f"file:{self.settings.sqlite_path}?mode=ro", uri=True, timeout=1
                ) as db:
                    tasks = [
                        TaskRun.model_validate_json(r[0])
                        for r in db.execute(
                            "SELECT payload FROM task_runs WHERE project_id=? AND epic_run_id=?",
                            (PROJECT, RUN),
                        )
                    ]
                result["reserved"] = sum(t.worker_slot is not None for t in tasks)
                inventory = self.h.call("agent", "list")["agents"]
                result["native_inventory"] = [
                    {"name": a["name"], "status": a.get("agent_status")} for a in inventory
                ]
                known = {t.worker_agent_id for t in tasks}
                result["unregistered_agents"] = [
                    a["name"] for a in inventory if a["name"] not in known
                ]
                before = {a["name"]: a.get("agent_status") for a in inventory}
                for t in tasks:
                    item = {
                        "task_id": t.task_id,
                        "task_run_id": t.id,
                        "slot": t.worker_slot,
                        "state": t.internal_status,
                        "worktree": t.worktree_path,
                        "session": t.codex_session_id,
                    }
                    if (
                        t.worker_agent_id
                        and t.codex_session_id
                        and t.internal_status != TaskState.DONE
                    ):
                        agent = self.h.get_agent(t.worker_agent_id)
                        if agent is not None:
                            binding = {
                                k: getattr(t, "herdr_" + k)
                                for k in ("workspace_id", "tab_id", "pane_id", "terminal_id")
                            }
                            actual = self.h.verify_agent(
                                agent, binding, t.worktree_path, t.worker_agent_id
                            )
                            item.update(
                                native_status=actual["status"],
                                native_session=actual["session_id"],
                                processes=actual["processes"],
                            )
                    result["tasks"].append(item)
                after = {
                    a["name"]: a.get("agent_status") for a in self.h.call("agent", "list")["agents"]
                }
                for item in result["tasks"]:
                    t = next(t for t in tasks if t.id == item["task_run_id"])
                    item["working_bracket"] = (
                        before.get(t.worker_agent_id) == after.get(t.worker_agent_id) == "working"
                    )
            except Exception:
                result["error"] = "OBSERVATION_UNAVAILABLE"
            journal("observations", result)
            self.stop.wait(1)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=40)
        if self.thread.is_alive():
            raise RuntimeError("Sampler did not settle")


def export_reviews(s, result):
    for row in result["tasks"]:
        if row["phase"] == "AWAITING_REVIEW":
            task = s.store.get_task(row["task_run_id"])
            package = s.review.request(INTEGRATION, task.id, key=row["request_key"])
            context = package["context"]
            save("review-" + task.task_id, context)
            print(
                json.dumps(
                    {
                        "review": task.task_id,
                        "context_id": context["context_id"],
                        "task_commit": context["task_commit"],
                        "epic_commit": context["epic_commit"],
                        "file": str(BASE / ("review-" + task.task_id + ".json")),
                    }
                )
            )


def validate_timeline(events, observations):
    slots, states, maximum, overlaps = {}, {}, 0, []
    for event in events:
        row = event["result"]
        if "task_id" not in row:
            continue
        identity = row["task_id"]
        slot = row.get("worker_slot")
        if slot is not None and (type(slot) is not int or slot not in {1, 2}):
            raise ValueError("Invalid reservation")
        slots[identity], states[identity] = slot, row["internal_status"]
        occupied = [s for s in slots.values() if s is not None]
        if len(occupied) > 2 or len(set(occupied)) != len(occupied):
            raise ValueError("Worker reservation invariant failed")
        maximum = max(maximum, len(occupied))
        if identity == "C" and row["internal_status"] == "CLAIMED" and states.get("A") != "DONE":
            raise ValueError("C claimed before verified A-Done")
    for o in observations:
        if o.get("reserved", 0) > 2:
            raise ValueError("Observed too many reservations")
        if o.get("unregistered_agents") or len(o.get("native_inventory", [])) > 2:
            raise ValueError("Observed unknown or excessive native agents")
        active = [t for t in o["tasks"] if t.get("native_status") == "working"]
        if len(active) > 2:
            raise ValueError("Observed too many native Workers")
        if {t["task_id"] for t in active} == {"A", "B"} and all(
            t.get("working_bracket") for t in active
        ):
            if (
                len({t["worktree"] for t in active}) != 2
                or len({t["native_session"] for t in active}) != 2
            ):
                raise ValueError("Native Workers are not isolated")
            if not all(t.get("processes") and t["native_session"] == t["session"] for t in active):
                raise ValueError("Native overlap lacks physical identity")
            if {p["pid"] for p in active[0]["processes"]} & {
                p["pid"] for p in active[1]["processes"]
            }:
                raise ValueError("Native Workers share physical process")
            overlaps.append(o)
    if (
        not overlaps
        or maximum != 2
        or set(states) != {"A", "B", "C"}
        or set(states.values()) != {"DONE"}
    ):
        raise ValueError("Incomplete native timeline")
    return {
        "maximum_reserved": maximum,
        "overlap_samples": len(overlaps),
        "first_overlap": overlaps[0],
    }


async def operate(phase, *, seconds=45, task=None, decision_file=None):
    settings = Settings.model_validate(load("settings"))
    with StateStore(settings.sqlite_path) as db:
        async with connection() as adapter:
            s = composition(settings, db, adapter)
            if phase == "bind":
                await s.sync.bind_epic(COORDINATOR, RUN, load("fixture")["epicId"])
                save(
                    "before-board",
                    (await s.selection.reader.read_project()).model_dump(mode="json"),
                )
                StateService(db).transition_epic(
                    RUN,
                    EpicState.ACTIVE,
                    expected=EpicState.PLANNED,
                    event_id="f30-epic-start",
                    actor=COORDINATOR,
                )
                assert (await s.sync.sync_epic(COORDINATOR, RUN))["status"] in {
                    "SYNCED",
                    "EXISTING",
                }
                print("Actual F30 fixture bound/Active; scheduler chooses all task starts")
                return
            if phase == "approve":
                context = load("review-" + task)
                decision = json.loads(decision_file.read_text())
                assert decision["context_id"] == context["context_id"]
                result = s.approval.approve(
                    INTEGRATION,
                    context["task_run_id"],
                    decision,
                    key="f30-operator-review:" + context["context_id"],
                )
                save("approved-" + task + "-" + context["context_id"], result)
                print(json.dumps({"approved": task, "context_id": context["context_id"]}))
                return
            if phase == "export":
                board = await s.selection.reader.read_project()
                events = [
                    json.loads(r[0])
                    for r in db.db.execute(
                        "SELECT payload FROM transition_events "
                        "WHERE project_id=? AND epic_run_id=? ORDER BY rowid",
                        (PROJECT, RUN),
                    )
                ]
                observations = [
                    json.loads(line)
                    for line in (BASE / "observations.jsonl").read_text().splitlines()
                ]
                proof = validate_timeline(events, observations)
                tasks = db.get_tasks(RUN)
                assert {t.task_id for t in tasks} == {"A", "B", "C"}
                assert all(
                    t.internal_status == TaskState.DONE and t.worker_slot is None for t in tasks
                )
                assert all(
                    board.task(i).status == "Done" for i in load("fixture")["tasks"].values()
                )
                assert board.epic(load("fixture")["epicId"]).status == "InProgress"
                delivered = {
                    k: s.selection._task_run(board.task(i), board, PROJECT)[1]
                    for k, i in load("fixture")["tasks"].items()
                }
                g = GitAdapter(settings.repository)
                assert g.head("main") == load("before-git")["main"]
                refs = dict(
                    line.split()
                    for line in g.run(
                        "for-each-ref", "--format=%(refname) %(objectname)"
                    ).splitlines()
                )
                assert all(refs.get(k) == v for k, v in load("before-git")["refs"].items())
                assert set(load("before-git")["worktrees"]).issubset(
                    {str(p) for p in g.worktrees()}
                )
                baseline = load("before-board")
                known = set(load("fixture")["tasks"].values())
                after = board.model_dump(mode="json")
                assert [t for t in baseline["tasks"] if t["id"] not in known] == [
                    t for t in after["tasks"] if t["id"] not in known
                ]
                eid = load("fixture")["epicId"]
                assert [e for e in baseline["epics"] if e["id"] != eid] == [
                    e for e in after["epics"] if e["id"] != eid
                ]
                proof.update(
                    fixture=load("fixture"),
                    source_main_unchanged=g.head("main"),
                    deliveries=delivered,
                    tasks=[t.model_dump(mode="json") for t in tasks],
                    events=events,
                    native_epic_status="InProgress",
                    unrelated_board_unchanged=True,
                )
                save("proof", proof)
                print(
                    json.dumps(
                        {
                            "status": "F30_NATIVE_PASS",
                            "overlap_samples": proof["overlap_samples"],
                            "max_reserved": proof["maximum_reserved"],
                        }
                    )
                )
                return
            with Sampler(settings, s.start.herdr):
                deadline = time.monotonic() + seconds
                while True:
                    result = await s.tick(INTEGRATION, RUN)
                    save("latest-tick", result)
                    journal("ticks", {"at": time.time(), "result": result})
                    export_reviews(s, result)
                    print(json.dumps(result), flush=True)
                    unconfirmed = any(
                        t.internal_status == TaskState.STARTING
                        and db.get_operation(PROJECT, s.start.KIND, t.task_id).error_code is None
                        for t in db.get_tasks(RUN)
                    )
                    if not unconfirmed and any(
                        r["phase"] in {"PAUSED", "AWAITING_REVIEW", "WAITING_INPUT"}
                        for r in result["tasks"]
                    ):
                        return
                    if len(result["tasks"]) == 3 and all(
                        r["phase"] == "DONE" for r in result["tasks"]
                    ):
                        return
                    if phase == "tick" or time.monotonic() >= deadline:
                        return
                    await asyncio.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=("prepare", "finish-prepare", "bind", "tick", "drive", "approve", "export")
    )
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--server")
    parser.add_argument("--expected-seed")
    parser.add_argument("--attempt", type=int, choices=(1, 2), default=1)
    parser.add_argument("--seconds", type=int, default=45)
    parser.add_argument("--task", choices=tuple("ABC"))
    parser.add_argument("--decision-file", type=Path)
    args = parser.parse_args()
    configure_attempt(args.attempt)
    if not 1 <= args.seconds <= 55:
        parser.error("seconds must be 1–55")
    logging.disable(logging.CRITICAL)
    try:
        if args.phase == "prepare":
            if args.repository is None or args.server is None:
                parser.error("prepare requires approved repository and owned named server")
            prepare(args.repository, args.server)
        elif args.phase == "finish-prepare":
            if args.expected_seed is None:
                parser.error("finish-prepare requires the actually reviewed full seed SHA")
            finish_prepare(args.expected_seed)
        else:
            if args.phase == "approve" and (args.task is None or args.decision_file is None):
                parser.error("approve requires actual reviewed task/context decision-file")
            asyncio.run(
                operate(
                    args.phase,
                    seconds=args.seconds,
                    task=args.task,
                    decision_file=args.decision_file,
                )
            )
    except (HerdrError, TeamPlayerError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print(
            "F30_PHASE_UNVERIFIED: inspect preserved scoped journal; raw errors withheld",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
