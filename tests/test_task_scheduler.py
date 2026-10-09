"""Registered product runs, real Git/SQLite, controlled per-Worker runtime and board."""

import asyncio
import copy
import io
import json
import sys
from pathlib import Path
from uuid import UUID

import pytest
from mcp import Client
from test_task_start import setup as start_setup  # noqa: F401
from test_teamplayer_reader import T1, T2, T3, E, FakeBoard, P, U, epic, reader, task

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.application.git_integration_service import IntegrationError
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_scheduler_service import SchedulerError, TaskSchedulerService
from orchestrator.application.task_selection_service import TaskSelectionService
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.states import TaskState
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


def run(awaitable):
    return asyncio.run(awaitable)


class Board(FakeBoard):
    def __init__(self, specs):
        super().__init__(
            tasks=[
                task(
                    i,
                    name=s["name"],
                    acceptanceCriteria=s["acceptance_criteria"],
                    dependencyTaskIds=[T1] if i == T3 else [],
                )
                for i, s in specs.items()
            ],
            epics=[epic() | {"status": "InProgress"}],
        )
        self.projects[0]["access"] = "Write"
        self.applied = []
        self.failure = None

    async def write(self, name, args):
        arg = args.get("request", args)
        assert arg["projectId"] == P
        node = next(t for t in self.tasks if t["taskId"] == arg["taskId"])
        if arg["version"] != node["version"]:
            raise TeamPlayerError("TEAMPLAYER_VERSION_CONFLICT", current_version=node["version"])
        if self.failure == (name, "before"):
            self.failure = None
            raise TeamPlayerError("TEAMPLAYER_WRITE_OUTCOME_UNKNOWN")
        node.update({k: v for k, v in arg.items() if k in {"status", "description"}})
        node["version"] += 1
        self.applied.append((name, copy.deepcopy(arg)))
        if self.failure == (name, "after"):
            self.failure = None
            raise TeamPlayerError("TEAMPLAYER_WRITE_OUTCOME_UNKNOWN")
        return copy.deepcopy(node)


class Workers:
    """Distinct simulated PID/groups/session/turn history; no claim of native isolation."""

    server_session = "scheduler-fixture"
    sandbox = "read-only"

    def __init__(self):
        self.workspaces = {}
        self.agents = {}
        self.threads = {}
        self.starts = 0
        self.exits = 0
        self.sent = []

    def create_workspace(self, cwd, label):
        n = len(self.workspaces) + 1
        binding = {
            "workspace_id": f"w{n}",
            "tab_id": f"w{n}:t1",
            "pane_id": f"w{n}:p1",
            "terminal_id": f"term{n}",
        }
        self.workspaces[binding["pane_id"]] = binding | {"cwd": cwd}
        return binding

    def pane(self, pane_id):
        return self.workspaces[pane_id]

    def process_info(self, pane_id):
        return {"pane_id": pane_id, "shell_pid": 10, "foreground_processes": [{"pid": 10}]}

    def get_agent(self, name):
        return self.agents.get(name)

    def start_agent(self, name, pane_id, cwd):
        self.starts += 1
        sid = str(UUID(int=100 + self.starts))
        self.agents[name] = self.workspaces[pane_id] | {
            "name": name,
            "session": None,
            "pid": 100 + self.starts,
            "sid": sid,
        }
        self.threads[sid] = {"id": sid, "sessionId": sid, "cwd": cwd, "turns": []}

    def verify_agent(self, agent, binding, cwd, name):
        assert agent["name"] == name and agent["cwd"] == cwd
        assert all(agent[k] == v for k, v in binding.items())
        return {
            "ready": True,
            "status": "idle",
            "session_id": agent["session"],
            "processes": [{"pid": agent["pid"], "start_time": "fixture"}],
        }

    def prompt(self, name, text, *, timeout_ms):
        a = self.agents[name]
        a["session"] = a["sid"]
        packet = json.loads(text)
        t = self.threads[a["sid"]]
        n = len(t["turns"])
        t["turns"].append(
            {
                "id": f"turn{n}",
                "status": "completed",
                "items": [
                    {
                        "id": f"user{n}",
                        "type": "userMessage",
                        "content": [{"type": "text", "text": text}],
                    },
                    {
                        "id": f"ack{n}",
                        "type": "agentMessage",
                        "text": json.dumps(packet["assignment"]),
                    },
                ],
            }
        )
        self.sent.append(text)

    def read_thread(self, sid, cwd):
        t = self.threads[sid]
        assert t["cwd"] == cwd
        return copy.deepcopy(t)

    def read_session(self, sid, cwd):
        t = self.read_thread(sid, cwd)
        return {"id": sid, "session_id": sid, "cwd": t["cwd"]}

    def capture(self, roots, shell_pid):
        assert shell_pid == 10
        return {"identities": copy.deepcopy(roots), "groups": [p["pid"] for p in roots]}

    def combine(self, a, b):
        return {"identities": a["identities"], "groups": a["groups"]}

    def inactive(self, proof):
        return not any(a["pid"] in proof["groups"] for a in self.agents.values())

    def exit_agent(self, name):
        del self.agents[name]
        self.exits += 1


def approve(context):
    return dict(
        version=1,
        result="APPROVED",
        context_id=context["context_id"],
        summary="Fixture reviewer checked actual full diff and independent tests",
        verified_criteria=context["acceptance_criteria"],
    )


@pytest.fixture
def setup(start_setup):  # noqa: F811
    initial, a, spec, _, _ = start_setup
    rules = EpicReviewSpec(
        version=1,
        project_id="p",
        epic_id="E",
        requirements=["Deliver isolated task results"],
        acceptance_criteria=["Results are independently verified"],
        sources=["README.md"],
    )
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; assert Path('README.md').is_file(); "
        "assert all(p.read_text() == 'verified\\n' for p in Path('.').glob('*.result'))",
    )
    settings = initial.settings.model_copy(
        update={"max_workers": 2, "review_context": rules, "worker_test_command": command}
    )
    specs = {
        i: spec | {"task_id": local, "name": local}
        for i, local in [(T1, "A"), (T2, "B"), (T3, "C")]
    }
    specs[T3]["dependencies"] = ["A"]
    b = Board(specs)
    w = Workers()
    bindings = {E: LocalBoardBinding(local_id="E")} | {
        i: LocalBoardBinding(local_id=s["task_id"]) for i, s in specs.items()
    }
    selection = TaskSelectionService(
        settings,
        initial.store,
        reader(b, bindings=bindings),
        specs=specs,
        task_order=(T1, T2, T3),
        epic_prerequisites={"E": ()},
        codex=w,
        processes=w,
    )
    sync = TeamPlayerSyncService(
        settings,
        initial.store,
        b,
        project_id="p",
        external_project_id=P,
        user_id=U,
        scopes={a.epic_run_id: ("A", "B", "C")},
        codex=w,
        processes=w,
    )
    # Separately registered trusted Coordinator context; Integration is never promoted.
    coordinator = Actor(actor_id="fixture-coordinator", role=Role.COORDINATOR, project_id="p")
    run(sync.bind_epic(coordinator, a.epic_run_id, E))
    scheduler = TaskSchedulerService(
        settings,
        initial.store,
        selection,
        sync,
        w,
        w,
        processes=w,
        review_provider=approve,
        timeout_seconds=1,
    )
    yield scheduler, a, b, w


def local(s, identity):
    return next(t for t in s.store.get_tasks("epic-run") if t.task_id == identity)


def finish(s, w, identity):
    t = local(s, identity)
    path = Path(t.worktree_path)
    (path / (identity + ".result")).write_text("verified\n")
    g = s.selection.git
    g.run("add", "--", identity + ".result", cwd=path)
    g.run("commit", "-m", "worker result", cwd=path)
    sha = g.head(t.branch)
    report = dict(
        version=1,
        status="READY_FOR_REVIEW",
        project_id="p",
        epic_id="E",
        epic_run_id=t.epic_run_id,
        task_id=t.task_id,
        task_run_id=t.id,
        branch=t.branch,
        commit=sha,
        summary="Verified result",
        test_summary="Passing independent fixture command",
        tests=[
            {"command": list(s.settings.worker_test_command), "exit_code": 0, "summary": "PASS"}
        ],
        files_changed=[identity + ".result"],
        limitations=[],
    )
    turns = w.threads[t.codex_session_id]["turns"]
    n = len(turns)
    turns.append(
        {
            "id": f"report{n}",
            "status": "completed",
            "items": [
                {
                    "id": f"report-item{n}",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": json.dumps(report),
                }
            ],
        }
    )
    return sha


def test_two_workers_then_automatic_refill_and_serial_current_reviews(setup):
    s, a, b, w = setup
    first = run(s.tick(a, a.epic_run_id))
    assert len(first["started"]) == 2 and {t.task_id for t in s.store.get_tasks(a.epic_run_id)} == {
        "A",
        "B",
    }
    assert sorted(t.worker_slot for t in s.store.get_tasks(a.epic_run_id)) == [1, 2]
    assert w.starts == 2 and all(
        t.internal_status == TaskState.WORKING for t in s.store.get_tasks(a.epic_run_id)
    )
    a_sha = finish(s, w, "A")
    finish(s, w, "B")
    next_result = run(s.tick(a, a.epic_run_id))
    assert len(next_result["started"]) == 1 and local(s, "C").internal_status == TaskState.WORKING
    assert local(s, "A").internal_status == local(s, "B").internal_status == TaskState.DONE
    assert w.starts == 3 and w.exits == 2 and local(s, "C").worker_slot == 1
    merged_a = local(s, "A").merge_commit
    merged_b = local(s, "B").merge_commit
    assert s.selection.git.parents(merged_a)[1] == a_sha
    assert s.selection.git.parents(merged_b)[0] == merged_a
    review_b = s.store.get_reviews(local(s, "B").id)[-1]
    assert review_b.epic_commit == merged_a
    assert s.selection.git.contains_commit(local(s, "C").branch, merged_a)
    finish(s, w, "C")
    run(s.tick(a, a.epic_run_id))
    assert all(
        t.internal_status == TaskState.DONE and t.worker_slot is None
        for t in s.store.get_tasks(a.epic_run_id)
    )
    assert all(t["status"] == "Done" for t in b.tasks)
    before = (
        w.starts,
        w.exits,
        len(w.sent),
        len(s.store.get_operations(a.epic_run_id, kind="task_review_request")),
    )
    run(s.tick(a, a.epic_run_id))
    assert (
        w.starts,
        w.exits,
        len(w.sent),
        len(s.store.get_operations(a.epic_run_id, kind="task_review_request")),
    ) == before


def test_repeat_reopen_busy_and_other_principal_cannot_duplicate_or_adopt(setup):
    s, a, _, w = setup
    run(s.tick(a, a.epic_run_id))
    before = w.starts, len(w.sent), s.store.db.total_changes
    run(s.tick(a, a.epic_run_id))
    assert (w.starts, len(w.sent), s.store.db.total_changes) == before
    with s._lock("p"), pytest.raises(SchedulerError, match="BUSY"):
        run(s.tick(a, a.epic_run_id))
    with pytest.raises(SchedulerError, match="OWNER_CHANGED"):
        run(s.tick(a.model_copy(update={"actor_id": "another-integration"}), a.epic_run_id))
    with StateStore(s.settings.sqlite_path) as db:
        selected = TaskSelectionService(
            s.settings,
            db,
            s.selection.reader,
            specs=s.selection.specs,
            task_order=s.selection.order,
            epic_prerequisites=s.selection.epic_prerequisites,
            codex=w,
            processes=w,
        )
        sync = TeamPlayerSyncService(
            s.settings,
            db,
            s.sync.adapter,
            project_id="p",
            external_project_id=P,
            user_id=U,
            scopes=s.sync.evidence.scopes,
            codex=w,
            processes=w,
        )
        opened = TaskSchedulerService(
            s.settings,
            db,
            selected,
            sync,
            w,
            w,
            processes=w,
            review_provider=approve,
            timeout_seconds=1,
        )
        run(opened.tick(a, a.epic_run_id))
    assert (w.starts, len(w.sent)) == before[:2]


def test_no_implicit_approval_and_actual_mcp_scope(setup):
    s, a, _, w = setup
    s.review_provider = None
    run(s.tick(a, a.epic_run_id))
    finish(s, w, "A")
    waiting = run(s.tick(a, a.epic_run_id))
    assert local(s, "A").internal_status == TaskState.REVIEWING and w.exits == 0
    queued = next(t for t in waiting["tasks"] if t["task_run_id"] == local(s, "A").id)
    assert queued["phase"] == "AWAITING_REVIEW" and queued["context_id"]
    handoff = next(
        o
        for o in s.store.get_operations(a.epic_run_id, kind="worker_report")
        if o.task_run_id == local(s, "A").id and o.status == "SUCCEEDED"
    )
    verification = next(
        o
        for o in s.store.get_operations(a.epic_run_id, kind="verify_task")
        if o.id == handoff.result["verification_id"]
    )
    assert (
        verification.result["purpose"] == "worker_report"
        and "target_commit" not in verification.result
    )
    with pytest.raises(IntegrationError, match="current test evidence"):
        s.approval.integration.register_task_review(
            a,
            local(s, "A").id,
            verification_key=verification.idempotency_key,
            key="cannot-approve-worker-test",
            approved=True,
        )
    service = RuntimeService(s.store, a, EventLog(stream=io.StringIO()), task_scheduler=s)

    async def probe():
        async with Client(create_server(service)) as client:
            catalog = await client.list_tools()
            tool = next(t for t in catalog.tools if t.name == "task_schedule")
            assert tool.annotations.read_only_hint is False
            args = {"project_id": "p", "epic_run_id": a.epic_run_id}
            assert (await client.call_tool("task_schedule", args)).structured_content["ok"]
            assert (
                await client.call_tool("task_schedule", args | {"role": "Coordinator"})
            ).structured_content["code"] == "INVALID_ARGUMENT"
            service.actor = a.model_copy(
                update={"role": Role.WORKER, "task_run_id": local(s, "A").id}
            )
            calls = len(s.sync.adapter.calls)
            assert (await client.call_tool("task_schedule", args)).structured_content[
                "code"
            ] == "FORBIDDEN"
            assert len(s.sync.adapter.calls) == calls

    run(probe())


def test_changed_owner_after_advice_denies_git_runtime_and_replay(setup, monkeypatch):
    s, a, b, w = setup
    original = s.selection.evaluate
    changed = False

    def change(*args):
        nonlocal changed
        result = original(*args)
        if not changed:
            changed = True
            b.tasks[0]["responsibleUserId"] = str(UUID(int=999))
        return result

    monkeypatch.setattr(s.selection, "evaluate", change)
    run(s.tick(a, a.epic_run_id))
    failed = local(s, "A")
    assert failed.worker_slot is None and not Path(failed.worktree_path).exists()
    assert s.selection.git.head(failed.branch) is None and w.starts == 1
    assert s.store.get_operation("p", s.KIND, failed.id).result["phase"] == "PAUSED"
    b.tasks[0]["responsibleUserId"] = U
    run(s.tick(a, a.epic_run_id))
    assert w.starts == 1 and local(s, "A").id == failed.id and local(s, "A").worker_slot is None


def test_stale_approval_requeues_unchanged_task_requires_new_actual_review(setup):
    s, a, _, w = setup
    s.review_provider = None
    run(s.tick(a, a.epic_run_id))
    finish(s, w, "A")
    finish(s, w, "B")
    for name in ("A", "B"):
        t = local(s, name)
        s.reports.collect(a, t.id)
        context = s.review.request(a, t.id, key="external-review-" + name)["context"]
        s.approval.approve(a, t.id, approve(context), key="external-approval-" + name)
    before_b = local(s, "B")
    old_review = s.store.get_reviews(before_b.id)[-1]
    run(s.tick(a, a.epic_run_id))
    current = local(s, "B")
    assert local(s, "A").internal_status == TaskState.DONE
    assert current.internal_status == TaskState.REVIEWING and current.approved_source_commit is None
    assert (
        current.branch,
        current.worktree_path,
        current.codex_session_id,
        current.worker_slot,
    ) == (before_b.branch, before_b.worktree_path, before_b.codex_session_id, before_b.worker_slot)
    assert s.store.get_reviews(current.id) == [old_review] and w.exits == 1
    op = s.store.get_operation("p", s.KIND, current.id)
    assert op.result["epic_commit"] == local(s, "A").merge_commit
    s.review_provider = approve
    run(s.tick(a, a.epic_run_id))
    assert local(s, "B").internal_status == TaskState.DONE
    assert len(s.store.get_reviews(current.id)) == 2
    assert s.store.get_reviews(current.id)[-1].epic_commit == local(s, "A").merge_commit


def test_done_sync_failure_retries_only_mirror_not_merge_test_or_worker(setup, monkeypatch):
    s, a, b, w = setup
    run(s.tick(a, a.epic_run_id))
    finish(s, w, "A")
    exit_agent = w.exit_agent

    def exit_and_lose_reply(name):
        exit_agent(name)
        if name == local(s, "A").worker_agent_id:
            b.failure = ("update_task_status", "after")

    monkeypatch.setattr(w, "exit_agent", exit_and_lose_reply)
    run(s.tick(a, a.epic_run_id))
    done = local(s, "A")
    op = s.store.get_operation("p", s.KIND, done.id)
    assert done.internal_status == TaskState.DONE and done.worker_slot is None
    assert op.result["phase"] == "SYNC_PENDING"
    before = (
        done.merge_commit,
        len(s.store.get_operations(a.epic_run_id, kind="task_delivery_test")),
        w.exits,
    )
    run(s.tick(a, a.epic_run_id))
    assert (
        local(s, "A").merge_commit,
        len(s.store.get_operations(a.epic_run_id, kind="task_delivery_test")),
        w.exits,
    ) == before
    assert s.store.get_operation("p", s.KIND, done.id).result["phase"] == "DONE"
    assert next(t for t in b.tasks if t["taskId"] == T1)["status"] == "Done" and w.starts == 3


def test_failed_worker_test_pauses_without_retry_and_independent_task_delivers(setup):
    s, a, _, w = setup
    run(s.tick(a, a.epic_run_id))
    finish(s, w, "A")
    t = local(s, "A")
    p = Path(t.worktree_path)
    (p / "A.result").write_text("incorrect\n")
    s.selection.git.run("add", "A.result", cwd=p)
    s.selection.git.run("commit", "-m", "incorrect result", cwd=p)
    report = json.loads(w.threads[t.codex_session_id]["turns"][-1]["items"][0]["text"])
    report["commit"] = s.selection.git.head(t.branch)
    w.threads[t.codex_session_id]["turns"][-1]["items"][0]["text"] = json.dumps(report)
    run(s.tick(a, a.epic_run_id))
    failed = s.store.get_operation("p", s.KIND, t.id)
    assert failed.result["phase"] == "PAUSED" and failed.result["reason"] == "REPORT_TEST_FAILED"
    before = len(
        [
            o
            for o in s.store.get_operations(a.epic_run_id, kind="verify_task")
            if o.task_run_id == t.id
        ]
    )
    finish(s, w, "B")
    run(s.tick(a, a.epic_run_id))
    run(s.tick(a, a.epic_run_id))
    assert local(s, "A").worker_slot == 1 and local(s, "B").internal_status == TaskState.DONE
    assert (
        len(
            [
                o
                for o in s.store.get_operations(a.epic_run_id, kind="verify_task")
                if o.task_run_id == t.id
            ]
        )
        == before
    )
    assert w.starts == 2 and w.exits == 1


def test_unknown_stop_retains_known_merge_and_slot_without_replaying_delivery(setup, monkeypatch):
    s, a, _, w = setup
    run(s.tick(a, a.epic_run_id))
    finish(s, w, "A")
    monkeypatch.setattr(w, "inactive", lambda proof: False)
    run(s.tick(a, a.epic_run_id))
    t = local(s, "A")
    assert t.internal_status == TaskState.MERGING and t.merge_commit and t.worker_slot == 1
    journal = s.store.get_operation("p", s.KIND, t.id)
    assert journal.result["phase"] == "PAUSED" and journal.result["delivery_key"]
    before = (
        s.selection.git.head(s.store.get_epic(a.epic_run_id).branch),
        len(s.store.get_operations(a.epic_run_id, kind="task_delivery_test")),
        w.exits,
    )
    run(s.tick(a, a.epic_run_id))
    assert (
        s.selection.git.head(s.store.get_epic(a.epic_run_id).branch),
        len(s.store.get_operations(a.epic_run_id, kind="task_delivery_test")),
        w.exits,
    ) == before
    assert w.starts == 2 and not any(t.task_id == "C" for t in s.store.get_tasks(a.epic_run_id))


def test_changed_task_after_approval_is_paused_without_requeue_or_merge(setup):
    s, a, _, w = setup
    s.review_provider = None
    run(s.tick(a, a.epic_run_id))
    finish(s, w, "A")
    t = local(s, "A")
    s.reports.collect(a, t.id)
    context = s.review.request(a, t.id, key="before-change")["context"]
    s.approval.approve(a, t.id, approve(context), key="approved-before-change")
    p = Path(t.worktree_path)
    (p / "unreviewed.txt").write_text("not approved\n")
    s.selection.git.run("add", "unreviewed.txt", cwd=p)
    s.selection.git.run("commit", "-m", "unreviewed change", cwd=p)
    epic = s.store.get_epic(a.epic_run_id)
    head = s.selection.git.head(epic.branch)
    run(s.tick(a, a.epic_run_id))
    run(s.tick(a, a.epic_run_id))
    op = s.store.get_operation("p", s.KIND, t.id)
    assert op.result["phase"] == "PAUSED" and op.result["reason"] == "APPROVAL_TASK_CHANGED"
    assert local(s, "A").internal_status == TaskState.APPROVED and local(s, "A").worker_slot == 1
    assert s.selection.git.head(epic.branch) == head and w.exits == 0
    assert len(s.store.get_reviews(t.id)) == 1


def blocked(s, workers, identity):
    t = local(s, identity)
    turns = workers.threads[t.codex_session_id]["turns"]
    turns.append(
        {
            "id": "blocked-final",
            "status": "completed",
            "items": [
                {
                    "id": "blocked-item",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": json.dumps(
                        dict(
                            version=1,
                            status="BLOCKED",
                            project_id="p",
                            epic_id="E",
                            epic_run_id=t.epic_run_id,
                            task_id=t.task_id,
                            task_run_id=t.id,
                            branch=t.branch,
                            summary="Missing retention input",
                            reason="Policy not specified",
                            input_required="How many days?",
                        )
                    ),
                }
            ],
        }
    )


def test_worker_block_parks_and_other_tasks_continue_without_extra_runtime(setup):
    s, a, b, w = setup
    run(s.tick(a, a.epic_run_id))
    before = local(s, "B")
    blocked(s, w, "B")
    result = run(s.tick(a, a.epic_run_id))
    current = local(s, "B")
    assert current.internal_status == TaskState.PARKED and current.worker_slot is None
    assert current.codex_session_id == before.codex_session_id and w.starts == 2 and w.exits == 1
    row = next(r for r in result["tasks"] if r["task_run_id"] == current.id)
    assert row["phase"] == "WAITING_INPUT"
    finish(s, w, "A")
    refill = run(s.tick(a, a.epic_run_id))
    assert local(s, "A").internal_status == TaskState.DONE and len(refill["started"]) == 1
    assert local(s, "C").internal_status == TaskState.WORKING and w.starts == 3
    assert local(s, "B").internal_status == TaskState.PARKED
    finish(s, w, "C")
    run(s.tick(a, a.epic_run_id))
    assert local(s, "C").internal_status == TaskState.DONE and w.exits == 3
    assert next(t for t in b.tasks if t["taskId"] == T2)["status"] == "NeedsInput"
    run(s.tick(a, a.epic_run_id))
    assert w.starts == 3 and w.exits == 3


def test_external_review_input_parks_without_approval_changes_or_merge(setup):
    s, a, _, w = setup
    run(s.tick(a, a.epic_run_id))
    finish(s, w, "B")
    s.review_provider = lambda context: dict(
        result="NEEDS_INPUT",
        context_id=context["context_id"],
        reason="Retention policy unspecified",
        input_required="How many days?",
        responsible_role="User",
    )
    result = run(s.tick(a, a.epic_run_id))
    task = local(s, "B")
    assert task.internal_status == TaskState.PARKED and task.resume_state == TaskState.REVIEWING
    assert task.worker_slot is None and w.exits == 1
    assert (
        next(r for r in result["tasks"] if r["task_run_id"] == task.id)["phase"] == "WAITING_INPUT"
    )
    assert not s.store.get_reviews(task.id)
    assert not s.store.get_operations(task.epic_run_id, kind="task_merge")
    run(s.tick(a, a.epic_run_id))
    assert w.exits == 1 and w.starts == 2


def test_already_owned_worker_block_parks_even_when_board_is_offline(setup):
    s, a, b, w = setup
    run(s.tick(a, a.epic_run_id))
    blocked(s, w, "B")
    read = b.read

    async def offline(*args):
        raise TeamPlayerError("TEAMPLAYER_TRANSPORT_UNAVAILABLE")

    b.read = offline
    with pytest.raises(TeamPlayerError):
        run(s.tick(a, a.epic_run_id))
    task = local(s, "B")
    assert task.internal_status == TaskState.PARKED and task.worker_slot is None
    assert w.exits == 1 and w.starts == 2
    assert not s.store.get_operations(a.epic_run_id, kind="task_merge")
    b.read = read
    run(s.tick(a, a.epic_run_id))
    row = s.store.get_operation("p", s.KIND, task.id)
    assert row.result["phase"] == "WAITING_INPUT" and row.result["reason"] is None
    assert w.exits == 1 and w.starts == 2
