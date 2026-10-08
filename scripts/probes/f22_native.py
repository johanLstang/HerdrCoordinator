"""Operator-driven, bounded native F22 proof. Never use the development repository as fixture.

Run from the F22 task worktree with uv run --locked python. Full runtime evidence
stays in ignored .herdr/probes/f22; only deliberately selected evidence is published.
The named Herdr server must already be started by its operator.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from orchestrator.adapters.herdr import HerdrAdapter
from orchestrator.application.runtime_lifecycle_service import RuntimeLifecycleService
from orchestrator.application.state_service import StateService
from orchestrator.application.task_approval_service import TaskApprovalService
from orchestrator.application.task_changes_service import TaskChangesService
from orchestrator.application.task_merge_service import TaskMergeService
from orchestrator.application.task_review_service import TaskReviewService
from orchestrator.application.task_start_service import TaskStartService
from orchestrator.application.worker_report_service import ReportError, WorkerReportService
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.persistence.store import StateStore

BASE = Path.cwd() / ".herdr/probes/f22"
PROJECT, EPIC, RUN = "f22-probe", "f22-epic", "f22-epic-run"
ACTOR = Actor(
    actor_id="f22-integration", role=Role.INTEGRATION, project_id=PROJECT, epic_run_id=RUN
)


def save(name, data):
    (BASE / (name + ".json")).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load(name):
    return json.loads((BASE / (name + ".json")).read_text())


def operations(db):
    """Read this probe's journal; the store requires an explicit operation kind."""
    kinds = [
        row[0]
        for row in db.db.execute(
            "SELECT DISTINCT kind FROM operations WHERE epic_run_id=? ORDER BY kind", (RUN,)
        )
    ]
    return [op for kind in kinds for op in db.get_operations(RUN, kind=kind)]


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
        (BASE / (name + "-startup.txt")).write_text(visible)
        # Exact voluntary setup banner only. Never answer a trust/permission dialog.
        if "Set up security for Daybreak mode" in visible and "esc to dismiss" in visible:
            self.call("agent", "send-keys", name, "esc")
            time.sleep(3)
            visible = subprocess.run(command, check=True, capture_output=True, text=True).stdout
            (BASE / (name + "-after-banner.txt")).write_text(visible)
        if "esc to dismiss" in visible or "trust this" in visible.lower():
            raise RuntimeError("Operator must inspect startup UI; no automatic approval")


def prepare(repo, server):
    if BASE.exists() or repo == Path.cwd() or not repo.is_dir():
        raise RuntimeError("Fresh probe directory and separate existing trusted fixture required")
    if (
        not (repo / "README.md").is_file()
        or not (repo / "README.md").read_text().startswith("Harmless isolated F-10 runtime probe.")
        or (repo / "pyproject.toml").exists()
    ):
        raise RuntimeError("This bounded harness requires the known harmless fixture")
    BASE.mkdir(parents=True)
    save("operator", {"server": server})
    verifier = BASE / "verify.py"
    verifier.write_text("""import subprocess, sys
from pathlib import Path
result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
if result.returncode:
    raise SystemExit(result.returncode)
# Deliberate, declared operator prerequisite for the separate blocked-task proof.
if Path.cwd().name == 'task-f22-epic-f22-blocked-test':
    print('F22 independent external test prerequisite unavailable', file=sys.stderr)
    raise SystemExit(23)
""")
    settings = Settings(
        repository=repo,
        worktree_root=repo / ".worktrees/f22",
        sqlite_path=BASE / "state.sqlite",
        max_workers=1,
        worker_test_command=(sys.executable, str(verifier)),
        worker_test_timeout=45,
        review_context=EpicReviewSpec(
            version=1,
            project_id=PROJECT,
            epic_id=EPIC,
            requirements=["Use the same registered Worker for correction; no fixture main merge"],
            acceptance_criteria=[
                "Current review, actual delivery tests and physical stop precede Done"
            ],
            sources=["AGENTS.md", "README.md"],
        ),
    )
    save("settings", settings.model_dump(mode="json"))
    coordinator = Actor(actor_id="f22-coordinator", role=Role.COORDINATOR, project_id=PROJECT)
    with StateStore(settings.sqlite_path) as db:
        w = WorktreeService(settings, db)
        assert w.git.inspect(repo, "main", clean=True) == w.git.head("main")
        main = w.git.head("main")
        epic = w.create_epic_worktree(coordinator, epic_id=EPIC, run_id=RUN)
        path = Path(epic.worktree_path)
        (path / "AGENTS.md").write_text("""# Harmless F22 fixture

Follow only the assigned task and authenticated correction envelope. The parent
HerdrCoordinator implementation backlog is not this fixture's scope. Work only
in your assigned task branch/worktree; do not switch branches, merge, update
TeamPlayer, delegate, use external MCP services, network, credentials or other
worktrees. Report real approval blockers without bypassing them.

Test command: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v.
Commit only your assigned files. READY_FOR_REVIEW means ready to inspect, not
that every acceptance criterion is met. Describe the controlled initial draft
defect truthfully in limitations. Current tests array lists only tests on the
delivered commit; earlier red TDD checks belong honestly in test_summary.
""")
        # Prevent native tests creating irrelevant dirty/ignored bytecode artifacts.
        (path / ".gitignore").write_text(
            (path / ".gitignore").read_text() + "__pycache__/\n*.py[cod]\n"
        )
        w.git.run("add", "AGENTS.md", ".gitignore", cwd=path)
        w.git.run("commit", "-m", "prepare isolated F22 native review fixture", cwd=path)
        db.update_run_metadata(epic.model_copy(update={"current_commit": w.git.head(epic.branch)}))
        epic = StateService(db).transition_epic(
            RUN,
            EpicState.ACTIVE,
            expected=EpicState.PLANNED,
            event_id="f22-active",
            actor=coordinator,
        )
        save(
            "before",
            {
                "main": main,
                "epic": w.git.head(epic.branch),
                "epic_run": epic.model_dump(mode="json"),
            },
        )
    spec = dict(
        version=1,
        project_id=PROJECT,
        epic_id=EPIC,
        task_id="f22-display-name",
        name="Normalize display names with controlled initial review defect",
        goal="Implement display_names.normalize_display_name(value), tests and documentation. "
        "This is a controlled review exercise: first commit the bounded draft that uses "
        "isinstance(value, str) and ' '.join(value.split()). Disclose that overridable "
        "split is a known limitation; do not claim full acceptance. Wait for authenticated "
        "review correction in this same session before fixing that deliberate draft defect.",
        requirements=[
            "Final implementation collapses standard str whitespace to one ASCII space and "
            "preserves actual Unicode text, including subclasses overriding split or __str__.",
            "Raise ValueError for empty/all-whitespace strings and TypeError for non-strings.",
            "Initial draft has passing ordinary tests and an honest documented subclass limitation "
            "and a committed reviewable result. Correct it only after the review envelope.",
        ],
        scope=["display_names.py", "tests/test_display_names.py", "README.md"],
        out_of_scope=[
            "Other fixtures, TeamPlayer, network, credentials, merges and other worktrees"
        ],
        acceptance_criteria=[
            "ASCII spaces, tabs and line breaks collapse correctly.",
            "Underlying Unicode letters and combining marks are preserved even for a str subclass "
            "that overrides split and __str__; regression tests independently demonstrate this.",
            "Empty/all-whitespace strings and non-string inputs have passing edge-case tests.",
            "Own changes are committed on the assigned branch; "
            "main and epic stay unchanged before delivery.",
        ],
        sources=["AGENTS.md", "README.md"],
        dependencies=[],
        external_prerequisites=[],
        verification_steps=[
            "PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v",
            "git status --short: clean after own commit",
        ],
    )
    save("spec", spec)
    blocked = dict(
        spec,
        task_id="f22-blocked-test",
        name="Exercise independent blocked test",
        goal="Append one short README paragraph documenting the delivered normalization helper; "
        "run existing local unittest tests, commit only README.md and report honestly. "
        "Operator verification has a deliberately unavailable external prerequisite for "
        "this task, outside your scope; local passing tests do not claim that prerequisite.",
        scope=["README.md"],
        requirements=["Document existing helper without changing code or tests."],
        acceptance_criteria=[
            "README accurately describes the existing helper and local tests pass."
        ],
        dependencies=["f22-display-name"],
    )
    save("blocked-spec", blocked)
    print(json.dumps({"prepared": True, "main": main, "server": server}))


def task(db, blocked=False):
    identity = "f22-blocked-test" if blocked else "f22-display-name"
    return next(t for t in db.get_tasks(RUN) if t.task_id == identity)


def independent_regression(path):
    code = """from display_names import normalize_display_name
class Name(str):
    def split(self, *args, **kwargs): return ['replacement']
    def __str__(self): return 'replacement'
value = Name('  Jose\\u0301\\u2003李  ')
actual = normalize_display_name(value)
assert actual == 'Jose\\u0301 李', repr(actual)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=path, capture_output=True, text=True
    )
    return {
        "command": [sys.executable, "-B", "-c", code],
        "exit_code": result.returncode,
        "output": result.stdout + result.stderr,
    }


def execute(phase, blocked):
    settings = Settings.model_validate(load("settings"))
    h = StartupPreflight(load("operator")["server"], sandbox="workspace-write")
    with StateStore(settings.sqlite_path) as db:
        if phase == "start":
            spec = load("blocked-spec" if blocked else "spec")
            answer = TaskStartService(settings, db, h).start(ACTOR, RUN, spec)
            t = task(db, blocked)
            parent = db.get_operation(PROJECT, "task_start", t.task_id)
            save(
                ("blocked-" if blocked else "") + "start",
                {
                    "answer": answer,
                    "parent": parent.model_dump(mode="json"),
                    "task": t.model_dump(mode="json"),
                },
            )
            print(json.dumps({"result": answer, "parent": parent.status, "run": t.id}))
            return
        t = task(db, blocked)
        assert db.get_operation(PROJECT, "task_start", t.task_id).status == "SUCCEEDED"
        prefix = "blocked-" if blocked else ""
        if phase in {"initial", "corrected", "collect"}:
            try:
                handoff = WorkerReportService(settings, db, h).collect(ACTOR, t.id)
            except ReportError as e:
                save(
                    prefix + phase + "-error",
                    {
                        "reason": str(e),
                        "task": db.get_task(t.id).model_dump(mode="json"),
                        "operations": [
                            o.model_dump(mode="json")
                            for o in operations(db)
                            if o.task_run_id == t.id
                        ],
                    },
                )
                print("REPORT_WAIT", str(e))
                return
            save(prefix + phase + "-handoff", handoff)
            context = TaskReviewService(settings, db).request(ACTOR, t.id, key="f22-" + phase)
            save(prefix + phase + "-context", context)
            regression = independent_regression(t.worktree_path)
            save(prefix + phase + "-regression", regression)
            print(
                json.dumps(
                    {
                        "handoff": handoff["status"],
                        "context_id": context["context"]["context_id"],
                        "regression_exit": regression["exit_code"],
                    }
                )
            )
        elif phase == "changes":
            context = load("initial-context")["context"]
            assert load("initial-regression")["exit_code"] != 0
            decision = dict(
                version=1,
                result="CHANGES_REQUESTED",
                context_id=context["context_id"],
                issues=[
                    dict(
                        number=1,
                        problem="The draft calls overridable value.split(): our str subclass "
                        "with combining marks and Unicode whitespace returns replacement, "
                        "losing its underlying text.",
                        requested_change="Normalize the underlying str with standard str behavior "
                        "even when split and __str__ are overridden. Add a subclass regression "
                        "with input '  Jose\\u0301\\u2003李  ' and expected 'Jose\\u0301 李'. "
                        "Preserve other edge cases, run tests, commit and report a fresh "
                        "version-1 final JSON in this same session. The initial draft "
                        "exercise is finished; fully meet the original final acceptance now.",
                        acceptance_criteria=[context["acceptance_criteria"][1]],
                    )
                ],
            )
            save("changes-decision", decision)
            answer = TaskChangesService(settings, db, h).request(
                ACTOR, t.id, decision, key="f22-subclass-fix"
            )
            save("changes", {"answer": answer, "task": db.get_task(t.id).model_dump(mode="json")})
            print(json.dumps(answer))
        elif phase == "approve":
            assert load("corrected-regression")["exit_code"] == 0
            context = load("corrected-context")["context"]
            decision = dict(
                version=1,
                result="APPROVED",
                context_id=context["context_id"],
                summary="Operator reviewed full current diff, sources, tests and all criteria; "
                "independent subclass regression passes on the corrected commit.",
                verified_criteria=context["acceptance_criteria"],
            )
            save("approval-decision", decision)
            answer = TaskApprovalService(settings, db).approve(
                ACTOR, t.id, decision, key="f22-approved"
            )
            save("approval", answer)
            print(json.dumps(answer))
        elif phase == "deliver":
            answer = TaskMergeService(settings, db, h).merge(
                ACTOR, t.id, key="f22-deliver", verification_key="first"
            )
            save("delivery-duplicate" if answer["status"] == "EXISTING" else "delivery", answer)
            print(json.dumps(answer))
        elif phase == "stop-blocked":
            assert blocked and t.internal_status != TaskState.DONE and t.merge_commit is None
            failed = [
                o
                for o in db.get_operations(RUN, kind="verify_task")
                if o.task_run_id == t.id
                and o.status == "FAILED"
                and o.result.get("exit_code") == 23
            ]
            assert failed, "Real independent blocking test exit23 required before parking"
            answer = RuntimeLifecycleService(settings, db, h).stop_task(
                ACTOR,
                t.id,
                key="f22-blocked-stop",
                reason="Independent verification test exit23: "
                "declared unavailable external prerequisite; no READY/merge/Done. "
                "Preserve fixture evidence.",
            )
            save("blocked-stop", answer.model_dump(mode="json"))
            print(
                json.dumps(
                    {
                        "stop": answer.status,
                        "state": db.get_task(t.id).internal_status,
                        "slot": db.get_task(t.id).worker_slot,
                    }
                )
            )
        elif phase == "export":
            g = WorktreeService(settings, db).git
            before = load("before")
            assert g.head("main") == before["main"]
            delivered, blocked_task = task(db), task(db, True)
            assert delivered.internal_status == TaskState.DONE and delivered.worker_slot is None
            assert (
                blocked_task.internal_status == TaskState.PARKED
                and blocked_task.worker_slot is None
            )
            assert blocked_task.merge_commit is None
            lifecycle = RuntimeLifecycleService(settings, db, h)
            assert lifecycle.confirm_task_inactive(ACTOR, delivered.id)
            assert lifecycle.confirm_task_inactive(ACTOR, blocked_task.id)
            initial, corrected = (
                load("initial-context")["context"],
                load("corrected-context")["context"],
            )
            assert initial["context_id"] != corrected["context_id"]
            assert initial["task_commit"] != corrected["task_commit"] == delivered.current_commit
            assert initial["epic_commit"] == corrected["epic_commit"] == before["epic"]
            start = load("start")["task"]
            for field in ("id", "codex_session_id", "worker_agent_id", "branch", "worktree_path"):
                assert getattr(delivered, field) == start[field], field
            changes = db.get_operation(PROJECT, "task_request_changes", "f22-subclass-fix")
            assert changes.status == "SUCCEEDED"
            assert changes.result["ack"]["session_id"] == delivered.codex_session_id
            reviews = sorted(db.get_reviews(delivered.id), key=lambda r: r.review_number)
            assert [(r.review_number, r.review_result, r.review_commit) for r in reviews] == [
                (1, "CHANGES_REQUESTED", initial["task_commit"]),
                (2, "APPROVED", corrected["task_commit"]),
            ]
            assert g.parents(delivered.merge_commit) == (before["epic"], corrected["task_commit"])
            assert g.head(db.get_epic(RUN).branch) == delivered.merge_commit
            delivery = db.get_operation(PROJECT, "task_merge", "f22-deliver")
            assert delivery.status == "SUCCEEDED"
            posttest = next(
                o
                for o in db.get_operations(RUN, kind="task_delivery_test")
                if o.id == delivery.result["test_id"]
            )
            assert posttest.status == "SUCCEEDED" and posttest.result["exit_code"] == 0
            assert posttest.result["merge_commit"] == delivered.merge_commit
            assert (
                len(
                    [
                        o
                        for o in db.get_operations(RUN, kind="merge_task_to_epic")
                        if o.task_run_id == delivered.id
                    ]
                )
                == 1
            )
            assert not db.get_reviews(blocked_task.id)
            assert not any(
                o.task_run_id == blocked_task.id
                for o in db.get_operations(RUN, kind="merge_task_to_epic")
            )
            save(
                "full-evidence",
                {
                    "main": g.head("main"),
                    "before": before,
                    "runs": [r.model_dump(mode="json") for r in db.get_runs()],
                    "reviews": [
                        r.model_dump(mode="json")
                        for x in db.get_tasks(RUN)
                        for r in db.get_reviews(x.id)
                    ],
                    "operations": [o.model_dump(mode="json") for o in operations(db)],
                },
            )
            print(
                json.dumps(
                    {
                        "main_unchanged": True,
                        "delivered": delivered.id,
                        "blocked": blocked_task.id,
                        "merge": delivered.merge_commit,
                        "physical_inactive": True,
                    }
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=[
            "prepare",
            "start",
            "initial",
            "changes",
            "corrected",
            "approve",
            "deliver",
            "collect",
            "stop-blocked",
            "export",
        ],
    )
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--server", default="hc-f22-20261008")
    parser.add_argument("--blocked", action="store_true")
    args = parser.parse_args()
    if args.phase == "prepare":
        if args.repo is None:
            parser.error("--repo must be the operator-trusted harmless fixture")
        prepare(args.repo.resolve(), args.server)
    else:
        execute(args.phase, args.blocked)
