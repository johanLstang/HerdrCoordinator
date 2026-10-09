"""Bounded F33 native operator fixture; explicit input and separate current reviews.

Uses actual F05/F29/F31/F32/F18/F20/F21/F25 services. Never adopts old runs,
changes their resources, invents approvals, or integrates the fixture to main.
Private transport history stays below this task worktree's .herdr directory.
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

import f30_native as shared
from f33_timeline import check_board, public_proof, validate_proof

from orchestrator.adapters.git import GitAdapter
from orchestrator.application.state_service import StateService
from orchestrator.application.task_resume_service import ResumeError
from orchestrator.application.worker_report_service import ReportError
from orchestrator.config import Settings
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import LocalTaskSpec
from orchestrator.persistence.store import StateStore

BASE = Path.cwd() / ".herdr/probes/f33"
ORIGINAL_BASE = BASE
PROJECT, EPIC, RUN = "f33-probe", "f33-epic", "f33-epic-run"
COORDINATOR = Actor(actor_id="f33-coordinator", role=Role.COORDINATOR, project_id=PROJECT)
INTEGRATION = Actor(
    actor_id="f33-integration", role=Role.INTEGRATION, project_id=PROJECT, epic_run_id=RUN
)
shared.BASE, shared.PROJECT, shared.EPIC, shared.RUN = BASE, PROJECT, EPIC, RUN
shared.COORDINATOR, shared.INTEGRATION = COORDINATOR, INTEGRATION
save, load, journal = shared.save, shared.load, shared.journal


def configure_attempt(attempt):
    global BASE, PROJECT, EPIC, RUN, COORDINATOR, INTEGRATION
    suffix = "" if attempt == 1 else "-r2"
    BASE = ORIGINAL_BASE.with_name("f33" + suffix)
    PROJECT, EPIC, RUN = "f33-probe" + suffix, "f33-epic" + suffix, "f33-epic-run" + suffix
    COORDINATOR = Actor(
        actor_id="f33-coordinator" + suffix, role=Role.COORDINATOR, project_id=PROJECT
    )
    INTEGRATION = Actor(
        actor_id="f33-integration" + suffix,
        role=Role.INTEGRATION,
        project_id=PROJECT,
        epic_run_id=RUN,
    )
    shared.BASE, shared.PROJECT, shared.EPIC, shared.RUN = BASE, PROJECT, EPIC, RUN
    shared.COORDINATOR, shared.INTEGRATION = COORDINATOR, INTEGRATION


def build_specs():
    tasks = {
        "A": (
            "Report missing RETENTION_DAYS; implement only after an explicit operator answer.",
            [
                "Give the exact WORKING ACK first. RETENTION_DAYS is intentionally absent.",
                "Immediately emit strict version-1 BLOCKED with reason and concrete request "
                "for a positive integer RETENTION_DAYS; leave all files unchanged until input.",
                "After actual input implement f33_retention.retention_days() returning the "
                "decided strict positive integer; own unittest must verify type and value.",
            ],
            "f33_retention",
            [
                "Explicit blocker requests absent RETENTION_DAYS without guessing or changes.",
                "After actual input retention_days returns the decided exact positive integer.",
            ],
        ),
        "B": (
            "Implement interval intersection with strict validation and independent tests.",
            [
                "f33_intervals.intersection(left, right) accepts two tuples of exactly two "
                "strict int endpoints, each lower <= upper; other containers or bool/float "
                "endpoints raise TypeError, wrong tuple length/reversed range raise ValueError.",
                "Return closed intersection tuple (max lower, min upper), including single "
                "point overlap; disjoint ranges return None. Never mutate inputs.",
                "Cover negative values, nesting, touching/disjoint ranges and all invalid "
                "types/shapes/orders in own unittest tests. This task needs no external input.",
            ],
            "f33_intervals",
            [
                "Closed interval intersections, touching and disjoint ranges are correct.",
                "Strict type/length/order errors and input preservation are independently tested.",
            ],
        ),
        "C": (
            "Implement independent stable Unicode tag normalization and unittest tests.",
            [
                "f33_tags.unique_tags(values) accepts only a list whose items are all str; "
                "otherwise TypeError. Strip whitespace, Unicode NFC normalize, then casefold "
                "and NFC normalize each string. Empty normalized tags raise ValueError.",
                "Return a new list of normalized distinct tags in first occurrence order; "
                "input remains unchanged. Empty list returns []. No imports from A or B.",
                "Cover composed/decomposed accents, casefold such as Straße/STRASSE, stable "
                "order, duplicates, empty tags and every invalid type in own unittest tests.",
            ],
            "f33_tags",
            [
                "Unicode normalization/casefold/deduplication preserves first occurrence order.",
                "Invalid inputs raise required errors and original inputs remain unchanged.",
            ],
        ),
    }
    result = {}
    for task, (goal, requirements, module, criteria) in tasks.items():
        result[task] = LocalTaskSpec(
            version=1,
            project_id=PROJECT,
            epic_id=EPIC,
            task_id=task,
            name="[TEST] F33 " + task + " native Attention scenario",
            goal=goal,
            requirements=requirements,
            scope=[module + ".py", "tests/test_" + module + ".py"],
            out_of_scope=["Other files, network, credentials, merge, branch switching"],
            acceptance_criteria=criteria,
            sources=["AGENTS.md", "README.md"],
            verification_steps=[
                "PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v"
            ],
        ).model_dump(mode="json")
    return result


VERIFY = """import importlib, subprocess, sys
from pathlib import Path
command=[sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
assert subprocess.run(command).returncode == 0
sys.path.insert(0, str(Path.cwd()))
def rejects(fn, args, exc):
    try: fn(*args)
    except exc: return
    raise AssertionError('required validation')
if Path('f33_retention.py').exists():
    value = importlib.import_module('f33_retention').retention_days()
    assert type(value) is int and value == 7
if Path('f33_intervals.py').exists():
    fn = importlib.import_module('f33_intervals').intersection
    cases=[((1,5),(3,7),(3,5)),((1,2),(2,9),(2,2)),((-9,-3),(-7,-5),(-7,-5)),
           ((1,2),(3,4),None),((0,0),(0,0),(0,0))]
    for a,b,out in cases:
        assert fn(a,b) == out and fn(b,a) == out
    for v in [None, [1,2], '12', (True,2), (1,2.0)]:
        rejects(fn, (v,(1,2)), TypeError); rejects(fn, ((1,2),v), TypeError)
    for v in [(), (1,), (1,2,3), (4,2)]:
        rejects(fn, (v,(1,2)), ValueError); rejects(fn, ((1,2),v), ValueError)
if Path('f33_tags.py').exists():
    fn = importlib.import_module('f33_tags').unique_tags
    values=[' Straße ', 'STRASSE', 'Jose\\u0301', 'JOSÉ', 'Tokyo', 'tokyo']
    original=values.copy()
    assert fn(values) == ['strasse', 'josé', 'tokyo'] and values == original
    assert fn([]) == []
    for v in [None, ('tag',), 'tag', [1], [True], [b'tag']]: rejects(fn, (v,), TypeError)
    for v in [[''], [' \\t '], ['a', '']]: rejects(fn, (v,), ValueError)
print('F33 independent fixture acceptance PASS')
"""


def prepare(repository, server):
    if os.environ.get("HERDR_ENV") != "1" or BASE.exists() or not server.startswith("hc-f33-"):
        raise RuntimeError("Fresh owned fixture under Herdr required")
    repository = repository.resolve(strict=True)
    g = GitAdapter(repository)
    main = g.inspect(repository, "main", clean=True)
    if g.in_progress(repository) or g.unsafe_index_paths(repository):
        raise RuntimeError("Unsafe explicitly approved fixture repository")
    if PROJECT.endswith("-r2"):
        original_settings = Settings.model_validate(
            json.loads((ORIGINAL_BASE / "settings.json").read_text())
        )
        abandoned = json.loads((ORIGINAL_BASE / "abandoned-attempt.json").read_text())
        import subprocess

        original_operator = json.loads((ORIGINAL_BASE / "operator.json").read_text())
        sessions = json.loads(
            subprocess.run(
                ["herdr", "session", "list", "--json"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        )["sessions"]
        need_old = next(s for s in sessions if s["name"] == original_operator["server"])
        if (
            repository != original_settings.repository
            or need_old["running"]
            or server == original_operator["server"]
            or abandoned["acceptance_pass"]
            or not abandoned["physical_inactive"]
        ):
            raise RuntimeError("Failed attempt must remain inactive and preserved")
        old_paths = {t["worktree_path"] for t in abandoned["snapshot"]["tasks"].values()}
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                cwd = str((proc / "cwd").resolve(strict=True))
            except (OSError, RuntimeError):
                continue
            if cwd in old_paths:
                raise RuntimeError("Failed attempt still has cwd processes")
    BASE.mkdir(parents=True)
    verify = BASE / "verify.py"
    verify.write_text(VERIFY)
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
            requirements=["A parks; B/C deliver; saved input resumes the same A session"],
            acceptance_criteria=["Two slots; current reviews/merges/tests/stops; three Done"],
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
        epic = shared.WorktreeService(settings, db).create_epic_worktree(
            COORDINATOR, epic_id=EPIC, run_id=RUN
        )
        p = Path(epic.worktree_path)
        (p / "AGENTS.md").write_text(
            "# F33 harmless native fixture\n\n"
            "Only implement your assigned task in your own worktree. Never merge, switch branch, "
            "edit other worktrees, access credentials or call TeamPlayer/network. Start with the "
            "assignment's exact WORKING ACK. Then implement, test, commit and emit its strict "
            "version-1 READY_FOR_REVIEW. A alone lacks RETENTION_DAYS: immediately report BLOCKED "
            "with concrete input_required and leave files unchanged until actual operator input. "
            "B/C are independent and have all required values; do not block them for A's input. "
            "Historical greeting files are completed test data and remain unchanged. No reset, "
            "amend or permissions/settings changes. Change only assigned f33 module/test file.\n"
        )
        (p / "README.md").write_text(
            "# F33 Attention fixture\n\n"
            "A: missing RETENTION_DAYS until an actual operator decision. B: strict closed "
            "interval intersection. C: independent Unicode normalized unique tags. No task "
            "depends on another. Integration supplies input and reviews each exact current "
            "task/epic pair; input does not approve code.\n\n"
            "Test: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v\n"
        )
        g.run("add", "--", "AGENTS.md", "README.md", cwd=p)
        g.run("commit", "-m", "seed F33 independent Attention fixture", cwd=p)
        sha = g.inspect(p, epic.branch, clean=True)
        db.update_run_metadata(epic.model_copy(update={"current_commit": sha}))
        save("seed", {"commit": sha, "epic": epic.model_dump(mode="json")})
    save("specs", build_specs())
    print("F33 actual F05 fixture prepared; bind separately returned native board IDs next")


def task_snapshot(s, task_id):
    task = next(t for t in s.store.get_tasks(RUN) if t.task_id == task_id)
    return task.model_dump(mode="json")


async def snapshot(s, adapter, label):
    tasks = s.store.get_tasks(RUN)
    native = {}
    for task in tasks:
        row = await adapter.read(
            "get_task",
            {
                "projectId": shared.EXTERNAL_PROJECT,
                "taskId": load("fixture")["tasks"][task.task_id],
            },
        )
        native[task.task_id] = {
            k: row[k]
            for k in ("taskId", "status", "version", "executionOwnerKind", "responsibleUserId")
        }
    value = {
        "label": label,
        "at": time.time(),
        "tasks": {t.task_id: t.model_dump(mode="json") for t in tasks},
        "native": native,
        "counts": {
            kind: len(s.store.get_operations(RUN, kind=kind))
            for kind in (
                "start_runtime",
                "dispatch_assignment",
                "task_attention",
                "task_resume",
                "resume_runtime",
                "task_merge",
                "stop_runtime",
            )
        },
    }
    save(label, value)
    journal("checkpoints", value)
    return value


async def require_parked(s, adapter):
    a = next(t for t in s.store.get_tasks(RUN) if t.task_id == "A")
    row = await adapter.read(
        "get_task",
        {
            "projectId": shared.EXTERNAL_PROJECT,
            "taskId": load("fixture")["tasks"]["A"],
        },
    )
    if (
        a.internal_status != TaskState.PARKED
        or a.worker_slot is not None
        or row["status"] != "NeedsInput"
        or row["responsibleUserId"] != shared.USER
        or row["executionOwnerKind"] != "User"
        or not s.attention.lifecycle.confirm_task_inactive(INTEGRATION, a.id)
    ):
        raise RuntimeError("A's actual parked/Attention proof is required")
    return a


async def operate(phase, *, seconds, task_id, decision_file):
    if os.environ.get("HERDR_ENV") != "1":
        raise RuntimeError("Native operator phases require the Herdr environment")
    settings = Settings.model_validate(load("settings"))
    with StateStore(settings.sqlite_path) as db:
        async with shared.connection() as adapter:
            s = shared.composition(settings, db, adapter)
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
                    event_id="f33-epic-start",
                    actor=COORDINATOR,
                )
                assert (await s.sync.sync_epic(COORDINATOR, RUN))["status"] in {
                    "SYNCED",
                    "EXISTING",
                }
                print("F33 fixture bound; Coordinator started actual epic")
                return
            if phase == "input":
                a = await require_parked(s, adapter)
                before = await snapshot(s, adapter, "input-before")
                bc = [t for t in db.get_tasks(RUN) if t.task_id in {"B", "C"}]
                assert len(bc) == 2 and {t.worker_slot for t in bc} == {1, 2}
                decision = json.loads(decision_file.read_text())
                journal("operator-input-intent", {"at": time.time(), "decision": decision})
                result = await s.resume.resume(INTEGRATION, a.id, decision)
                assert result["stage"] == "WAITING_RESUME"
                after = await snapshot(s, adapter, "input-waiting")
                assert after["tasks"]["A"]["internal_status"] == "PARKED"
                assert before["counts"]["resume_runtime"] == after["counts"]["resume_runtime"] == 0
                assert after["counts"]["start_runtime"] == 3 and after["counts"]["task_resume"] == 1
                save("input-result", result)
                save("decision", decision)
                print(json.dumps(result))
                return
            if phase in {"review", "deliver"}:
                if task_id in {"B", "C"}:
                    await require_parked(s, adapter)
                task = next(t for t in db.get_tasks(RUN) if t.task_id == task_id)
                if phase == "review":
                    if task.internal_status == TaskState.WORKING:
                        deadline = time.monotonic() + seconds
                        while True:
                            try:
                                s.reports.collect(
                                    INTEGRATION, task.id, expected_status="READY_FOR_REVIEW"
                                )
                                break
                            except ReportError as error:
                                if str(error) not in {
                                    "REPORT_NOT_AVAILABLE",
                                    "REPORT_TURN_NOT_FINISHED",
                                }:
                                    raise
                                if time.monotonic() >= deadline:
                                    save("review-waiting-" + task_id, {"code": str(error)})
                                    print(json.dumps({"review": task_id, "waiting": str(error)}))
                                    return
                                await asyncio.sleep(1)
                    epic = db.get_epic(RUN)
                    key = "f33-review:" + task.id + ":" + s.selection.git.head(epic.branch)
                    package = s.review.request(INTEGRATION, task.id, key=key)
                    context = package["context"]
                    assert context["reviewable"]
                    save("review-" + task_id, context)
                    (BASE / ("review-" + task_id + ".diff")).write_bytes(
                        base64.b64decode(context["diff"]["patch_base64"], validate=True)
                    )
                    print(
                        json.dumps(
                            {
                                "review": task_id,
                                "context_id": context["context_id"],
                                "task_commit": context["task_commit"],
                                "epic_commit": context["epic_commit"],
                            }
                        )
                    )
                    return
                context = load("review-" + task_id)
                decision = json.loads(decision_file.read_text())
                assert decision["context_id"] == context["context_id"]
                await snapshot(s, adapter, "before-delivery-" + task_id)
                approved = s.approval.approve(
                    INTEGRATION,
                    task.id,
                    decision,
                    key="f33-explicit-review:" + context["context_id"],
                )
                save("approved-" + task_id, approved)
                result = s.merge.merge(
                    INTEGRATION,
                    task.id,
                    key="f33-delivery:" + context["context_id"],
                    verification_key="initial",
                )
                save("delivered-" + task_id, result)
                assert result["status"] in {"DONE", "EXISTING"}
                assert (await s.sync.sync_task(INTEGRATION, task.id))["status"] in {
                    "SYNCED",
                    "EXISTING",
                }
                if task_id in {"B", "C"}:
                    await require_parked(s, adapter)
                await snapshot(s, adapter, "after-delivery-" + task_id)
                print(json.dumps({"delivered": task_id, "result": result}))
                return
            if phase == "export":
                await export(s, adapter)
                return
            assert phase in {"drive", "resume"}
            if phase == "resume":
                # A's decision must already be saved by the full-capacity phase.
                await require_parked(s, adapter)
                assert all(
                    t.internal_status == TaskState.DONE
                    for t in db.get_tasks(RUN)
                    if t.task_id in {"B", "C"}
                )
                pending = db.get_operations(RUN, kind="task_resume")
                assert len(pending) == 1 and pending[0].result["decision"] == load("decision")
                await snapshot(s, adapter, "before-resume")
            with shared.Sampler(settings, s.start.herdr):
                deadline = time.monotonic() + seconds
                # Observe pending original F15 deadlines within this explicit operator
                # call; never return between dispatch and ACK merely because a slow
                # native/MCP step used the requested polling duration. No deadline,
                # task identity, startup intent or transport is reset or extended.
                hard_deadline = deadline + 3 * 75
                while True:
                    result = await s.tick(INTEGRATION, RUN)
                    save("latest-tick", result)
                    journal("ticks", {"at": time.time(), "result": result})
                    print(json.dumps(result), flush=True)
                    if any(r["phase"] == "PAUSED" for r in result["tasks"]):
                        raise RuntimeError("Native pipeline paused; actual journals preserved")
                    tasks = {t.task_id: t for t in db.get_tasks(RUN)}
                    if phase == "resume" and tasks["A"].internal_status == TaskState.WORKING:
                        first = await snapshot(s, adapter, "after-resume")
                        replay = await s.resume.resume(INTEGRATION, tasks["A"].id, load("decision"))
                        assert replay["stage"] == "ACTIVE_AND_SYNCED"
                        second = await snapshot(s, adapter, "active-input-replay")
                        assert first["counts"] == second["counts"]
                        save("active-input-replay-result", replay)
                        return
                    if (
                        phase == "drive"
                        and len(tasks) == 3
                        and tasks["A"].internal_status == TaskState.PARKED
                        and all(
                            tasks[k].internal_status not in {TaskState.CLAIMED, TaskState.STARTING}
                            for k in "BC"
                        )
                    ):
                        await snapshot(s, adapter, "parked-with-BC")
                        return
                    unconfirmed = any(
                        t.internal_status in {TaskState.CLAIMED, TaskState.STARTING}
                        for t in tasks.values()
                    ) or (
                        phase == "resume"
                        and any(
                            o.status == "PENDING"
                            for o in db.get_operations(RUN, kind="task_resume")
                        )
                    )
                    if time.monotonic() >= hard_deadline:
                        raise RuntimeError(
                            "Bounded startup observation exhausted; preserve journals"
                        )
                    if time.monotonic() >= deadline and not unconfirmed:
                        return
                    await asyncio.sleep(0.5)


async def export(s, adapter):
    final = await snapshot(s, adapter, "final")
    db, g = s.store, s.selection.git
    tasks = db.get_tasks(RUN)
    assert len(tasks) == 3 and all(t.internal_status == TaskState.DONE for t in tasks)
    assert all(s.attention.lifecycle.confirm_task_inactive(INTEGRATION, t.id) for t in tasks)
    before = load("before-git")
    assert g.head("main") == before["main"]
    assert all(
        g.run("rev-parse", "--verify", ref).strip() == sha for ref, sha in before["refs"].items()
    )
    assert all(Path(p) in g.worktrees() for p in before["worktrees"])
    deliveries = {}
    for t in tasks:
        delivery = next(
            o for o in db.get_operations(RUN, kind="task_merge") if o.task_run_id == t.id
        )
        test = next(
            o
            for o in db.get_operations(RUN, kind="task_delivery_test")
            if o.id == delivery.result["test_id"]
        )
        stop = next(
            o
            for o in db.get_operations(RUN, kind="stop_runtime")
            if o.id == delivery.result["stop_id"]
        )
        context = load("review-" + t.task_id)
        approved = next(
            o for o in db.get_operations(RUN, kind="task_approve") if o.task_run_id == t.id
        )
        assert g.inspect(Path(t.worktree_path), t.branch, clean=True) == context["task_commit"]
        assert s.start.herdr.get_agent(t.worker_agent_id) is None
        deliveries[t.task_id] = {
            "operation": delivery.model_dump(mode="json"),
            "test": test.model_dump(mode="json"),
            "stop": stop.model_dump(mode="json"),
            "approval": approved.model_dump(mode="json"),
            "merge_parents": list(g.parents(t.merge_commit)),
            "context": {k: context[k] for k in ("context_id", "task_commit", "epic_commit")}
            | {
                "test_id": context["tests"]["operation_id"],
                "diff_bytes": context["diff"]["bytes"],
                "diff_sha256": context["diff"]["sha256"],
            },
            "explicit_review": load("explicit-review-" + t.task_id),
            "worktree_preserved": True,
            "physically_inactive": True,
        }
    counts = final["counts"]
    try:
        await s.resume.resume(INTEGRATION, final["tasks"]["A"]["id"], load("decision"))
    except ResumeError as error:
        assert str(error) == "INPUT_SCOPE_DENIED"
        closed_replay = str(error)
    else:
        raise RuntimeError("Completed task unexpectedly accepted resume")
    replay_tick = await s.tick(INTEGRATION, RUN)
    assert all(row["phase"] == "DONE" for row in replay_tick["tasks"])
    replay = await snapshot(s, adapter, "replay")
    assert replay["counts"] == counts
    board_object = await s.selection.reader.read_project()
    board = board_object.model_dump(mode="json")
    baseline = load("before-board")
    expected = BASE / "expected-development-text-change.json"
    changes = check_board(
        baseline,
        board,
        load("fixture"),
        json.loads(expected.read_text()) if expected.exists() else None,
    )
    eid = load("fixture")["epicId"]
    proof = {
        "fixture": load("fixture"),
        "user_id": shared.USER,
        "project_id": PROJECT,
        "epic_run_id": RUN,
        "events": [
            json.loads(row[0])
            for row in db.db.execute(
                "SELECT payload FROM transition_events "
                "WHERE project_id=? AND epic_run_id=? ORDER BY rowid",
                (PROJECT, RUN),
            )
        ],
        "deliveries": deliveries,
        "attention": db.get_operations(RUN, kind="task_attention")[0].model_dump(mode="json"),
        "input": db.get_operations(RUN, kind="task_resume")[0].model_dump(mode="json"),
        "resume": db.get_operations(RUN, kind="resume_runtime")[0].model_dump(mode="json"),
        "park_stop": next(
            o
            for o in db.get_operations(RUN, kind="stop_runtime")
            if o.id == db.get_operations(RUN, kind="task_attention")[0].result["stop_id"]
        ).model_dump(mode="json"),
        "checkpoints": [
            json.loads(line) for line in (BASE / "checkpoints.jsonl").read_text().splitlines()
        ],
        "observations": [
            json.loads(line) for line in (BASE / "observations.jsonl").read_text().splitlines()
        ],
        "final": final,
        "replay": replay,
        "active_input_replay": load("active-input-replay-result"),
        "completed_input_replay": closed_replay,
        "main_unchanged": True,
        "old_refs_and_worktrees_preserved": True,
        "unrelated_board_unchanged": True,
        "expected_development_text_changes": changes,
        "native_epic_status": next(e["status"] for e in board["epics"] if e["id"] == eid),
        "limitation": "Bounded explicit operator input/reviews; "
        "no fixture main merge or autonomous loop.",
    }
    # F27 rechecks actual approvals, merge markers/ancestry, aftertests and stops.
    for identity in load("fixture")["tasks"].values():
        s.selection._task_run(board_object.task(identity), board_object, PROJECT)
    save("proof-candidate", proof)
    proof["validation"] = validate_proof(proof)
    save("proof", proof)
    save("public-proof", public_proof(proof))
    print(json.dumps(proof["validation"]))


def main():
    logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=("prepare", "bind", "drive", "input", "review", "deliver", "resume", "export"),
    )
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--server")
    parser.add_argument("--seconds", type=int, choices=range(1, 61), default=45)
    parser.add_argument("--task", choices=("A", "B", "C"))
    parser.add_argument("--decision-file", type=Path)
    parser.add_argument("--attempt", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    configure_attempt(args.attempt)
    if args.phase == "prepare":
        if args.repository is None or args.server is None:
            parser.error("prepare requires explicit approved repository and own server")
        prepare(args.repository, args.server)
    else:
        if args.phase in {"review", "deliver"} and args.task is None:
            parser.error("review/deliver requires task")
        if args.phase in {"input", "deliver"} and args.decision_file is None:
            parser.error("input/deliver requires an explicit operator decision file")
        asyncio.run(
            operate(
                args.phase,
                seconds=args.seconds,
                task_id=args.task,
                decision_file=args.decision_file,
            )
        )


if __name__ == "__main__":
    main()
