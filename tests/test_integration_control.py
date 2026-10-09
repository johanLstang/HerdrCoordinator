"""F35 controlled native proposals, actual Git/SQLite/F34 registration and MCP SDK."""

import asyncio
import io
import json
import sys
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from mcp import Client
from test_epic_start import setup as epic_setup  # noqa: F401
from test_task_scheduler import Board, Workers, approve
from test_teamplayer_reader import T1, T2, T3, E, P, U, reader

from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.integration_control_service import IntegrationControlService
from orchestrator.application.task_scheduler_service import TaskSchedulerService
from orchestrator.application.task_selection_service import TaskSelectionService
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.states import TaskState
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server


class Sessions(Workers):
    def resume_agent(self, name, pane, cwd, sid):
        self.starts += 1
        self.agents[name] = self.workspaces[pane] | {
            "name": name,
            "session": sid,
            "sid": sid,
            "pid": 100 + self.starts,
        }
        assert self.threads[sid]["cwd"] == cwd

    def prompt(self, name, text, *, timeout_ms):
        packet = json.loads(text)
        if packet.get("type") != "HERDR_EPIC_ASSIGNMENT":
            return super().prompt(name, text, timeout_ms=timeout_ms)
        a = self.agents[name]
        a["session"] = a["sid"]
        self.threads[a["sid"]]["turns"].append(
            dict(
                id="epic-receipt",
                status="completed",
                items=[
                    dict(
                        id="epic-user", type="userMessage", content=[dict(type="text", text=text)]
                    ),
                    dict(
                        id="epic-ack", type="agentMessage", text=json.dumps(packet["confirmation"])
                    ),
                ],
            )
        )
        self.sent.append(text)


@pytest.fixture
def setup(epic_setup):  # noqa: F811
    initial, coordinator, integration, _ = epic_setup
    data = initial.spec.model_dump(mode="json")
    tasks = {T1: data["tasks"][0]["task"], T2: data["tasks"][1]["task"]}
    tasks[T2]["dependencies"] = []
    tasks[T3] = deepcopy(tasks[T1]) | {"task_id": "F-03", "dependencies": ["F-01"]}
    data["tasks"] = [dict(version=1, priority="P0", task=t) for t in tasks.values()]
    data["dependencies"] = []
    settings = initial.settings.model_copy(
        update={
            "worker_test_command": (
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('README.md').is_file(); "
                "assert all(p.read_text()=='verified\\n' for p in Path('.').glob('*.result'))",
            ),
            "review_context": EpicReviewSpec(
                version=1,
                project_id="p",
                epic_id="E-34",
                requirements=["Fixture integration"],
                acceptance_criteria=["Three verified results"],
                sources=["README.md"],
            ),
        }
    )
    sessions = Sessions()
    epic = EpicStartService(settings, initial.store, sessions, data, integration, codex=sessions)
    assert asyncio.run(epic.start(coordinator, "p", "E-34", "e"))["stage"] == "REGISTERED"
    board = Board(tasks)
    bindings = {E: LocalBoardBinding(local_id="E-34")} | {
        k: LocalBoardBinding(local_id=v["task_id"]) for k, v in tasks.items()
    }
    selection = TaskSelectionService(
        settings,
        initial.store,
        reader(board, bindings=bindings),
        specs=tasks,
        task_order=(T1, T2, T3),
        epic_prerequisites={"E-34": ()},
        codex=sessions,
        processes=sessions,
    )
    sync = TeamPlayerSyncService(
        settings,
        initial.store,
        board,
        project_id="p",
        external_project_id=P,
        user_id=U,
        scopes={"e": ("F-01", "F-02", "F-03")},
        codex=sessions,
        processes=sessions,
    )
    asyncio.run(sync.bind_epic(coordinator, "e", E))
    scheduler = TaskSchedulerService(
        settings,
        initial.store,
        selection,
        sync,
        sessions,
        sessions,
        processes=sessions,
        review_provider=None,
        timeout_seconds=45,
    )
    log = EventLog(stream=io.StringIO())
    control = IntegrationControlService(epic, scheduler, log)
    yield control, control.runtime(log), integration, board, sessions


def task(control, local_id):
    return next(t for t in control.store.get_tasks("e") if t.task_id == local_id)


def finish(control, sessions, local_id):
    t = task(control, local_id)
    path = Path(t.worktree_path)
    filename = local_id + ".result"
    (path / filename).write_text("verified\n")
    g = control.scheduler.selection.git
    if g.run("ls-files", "--", filename, cwd=path).strip():
        readme = path / "README.md"
        readme.write_text(readme.read_text() + "Fixture correction documented\n")
        g.run("add", "--", "README.md", cwd=path)
    g.run("add", "--", filename, cwd=path)
    g.run("commit", "-m", "fixture Worker result", cwd=path)
    corrected = (
        bool(g.run("ls-files", "--", "README.md", cwd=path).strip())
        and "Fixture correction documented" in (path / "README.md").read_text()
    )
    report = dict(
        version=1,
        status="READY_FOR_REVIEW",
        project_id="p",
        epic_id="E-34",
        epic_run_id=t.epic_run_id,
        task_id=t.task_id,
        task_run_id=t.id,
        branch=t.branch,
        commit=g.head(t.branch),
        summary="Actual fixture Git result",
        test_summary="Fixture command",
        tests=[
            dict(command=list(control.settings.worker_test_command), exit_code=0, summary="PASS")
        ],
        files_changed=([filename, "README.md"] if corrected else [filename]),
        limitations=[],
    )
    turns = sessions.threads[t.codex_session_id]["turns"]
    number = len(turns)
    turns.append(
        dict(
            id=f"worker-report-{number}",
            status="completed",
            items=[
                dict(
                    id=f"worker-report-item-{number}",
                    type="agentMessage",
                    phase="final_answer",
                    text=json.dumps(report),
                )
            ],
        )
    )


def test_sdk_two_explicit_choices_dependency_gate_review_merge_and_refill(setup):
    control, runtime, _, board, sessions = setup

    async def exercise():
        async with Client(create_server(runtime)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"integration_overview", "task_start", "task_get_next", "task_merge"} <= names
            assert (
                not {"task_report_ready", "task_report_blocked", "set_epic_status", "epic_start"}
                & names
            )
            args = dict(project_id="p", epic_run_id="e")
            denied = await client.call_tool(
                "task_start", args | {"task": control.scheduler.selection.specs[T3]}
            )
            assert not denied.structured_content["ok"]
            assert control.store.get_tasks("e") == [] and sessions.starts == 1
            for identity in (T2, T1):
                r = await client.call_tool(
                    "task_start", args | {"task": control.scheduler.selection.specs[identity]}
                )
                assert r.structured_content["ok"], r.structured_content
                assert r.structured_content["data"]["overview"]["status"] == "FRESH"
            assert {t.worker_slot for t in control.store.get_tasks("e")} == {1, 2}
            assert sessions.starts == 3  # one Integration plus two Workers
            finish(control, sessions, "F-01")
            await client.call_tool("task_schedule", args)
            current = task(control, "F-01")
            assert current.internal_status == TaskState.REVIEWING
            package = control.store.get_operations("e", kind="task_review_request")[-1].result[
                "context"
            ]
            approved = await client.call_tool(
                "task_approve",
                dict(
                    project_id="p",
                    task_run_id=current.id,
                    request_key="agent-approve-a",
                    decision=approve(package),
                ),
            )
            assert approved.structured_content["ok"], approved.structured_content
            merged = await client.call_tool(
                "task_merge",
                dict(
                    project_id="p",
                    task_run_id=current.id,
                    request_key="agent-merge-a",
                    verification_key="agent-test-a",
                ),
            )
            assert merged.structured_content["ok"], merged.structured_content
            assert task(control, "F-01").internal_status == TaskState.DONE
            assert task(control, "F-01").worker_slot is None
            assert next(t for t in board.tasks if t["taskId"] == T1)["status"] == "Done"
            start_c = await client.call_tool(
                "task_start", args | {"task": control.scheduler.selection.specs[T3]}
            )
            assert start_c.structured_content["ok"], start_c.structured_content
            assert task(control, "F-03").internal_status == TaskState.WORKING
            assert task(control, "F-03").worker_slot == 2

    asyncio.run(asyncio.wait_for(exercise(), timeout=90))


@pytest.mark.parametrize("change", ["scope", "role", "spec", "session"])
def test_invalid_proposal_has_no_task_or_native_effect(setup, change):
    control, runtime, actor, _, sessions = setup
    args = dict(
        project_id="p", epic_run_id="e", task=deepcopy(control.scheduler.selection.specs[T1])
    )
    if change == "scope":
        args["epic_run_id"] = "foreign"
    if change == "role":
        args["role"] = "Coordinator"
    if change == "spec":
        args["task"]["goal"] = "Different work"
    if change == "session":
        epic = control.store.get_epic("e")
        sessions.agents[epic.integration_agent_id]["session"] = (
            "00000000-0000-4000-8000-000000000999"
        )
    before = len(sessions.sent), sessions.starts
    result = asyncio.run(runtime.call_async("task_start", args))
    assert not result.ok
    assert control.store.get_tasks("e") == [] and (len(sessions.sent), sessions.starts) == before


def test_sync_api_cannot_bypass_control_guard(setup):
    control, runtime, _, _, sessions = setup
    r = runtime.call(
        "task_start",
        dict(project_id="p", epic_run_id="e", task=control.scheduler.selection.specs[T1]),
    )
    assert r.code == "ASYNC_CONTROL_REQUIRED" and sessions.starts == 1


def test_correction_returns_to_same_worker_and_new_current_review(setup):
    control, runtime, _, _, sessions = setup
    target = dict(project_id="p", epic_run_id="e")

    async def exercise():
        await runtime.call_async(
            "task_start", target | {"task": control.scheduler.selection.specs[T1]}
        )
        finish(control, sessions, "F-01")
        await runtime.call_async("task_schedule", target)
        first = task(control, "F-01")
        packages = control.store.get_operations("e", kind="task_review_request")
        context = next(p.result["context"] for p in packages if p.task_run_id == first.id)
        decision = dict(
            version=1,
            result="CHANGES_REQUESTED",
            context_id=context["context_id"],
            issues=[
                dict(
                    number=1,
                    problem="Fixture explanation missing",
                    requested_change="Document the fixture in README",
                    acceptance_criteria=["Verified"],
                )
            ],
        )
        result = await runtime.call_async(
            "task_request_changes",
            dict(project_id="p", task_run_id=first.id, request_key="agent-fix", decision=decision),
        )
        assert result.ok, result
        after = task(control, "F-01")
        assert after.internal_status == TaskState.WORKING
        assert (
            after.codex_session_id,
            after.worker_agent_id,
            after.branch,
            after.worktree_path,
        ) == (first.codex_session_id, first.worker_agent_id, first.branch, first.worktree_path)
        assert sessions.starts == 3  # Integration, A, and B started by bounded tick
        count = len(sessions.sent)
        replay = await runtime.call_async(
            "task_request_changes",
            dict(project_id="p", task_run_id=first.id, request_key="agent-fix", decision=decision),
        )
        assert replay.ok and len(sessions.sent) == count
        finish(control, sessions, "F-01")
        await runtime.call_async("task_schedule", target)
        current = task(control, "F-01")
        assert current.internal_status == TaskState.REVIEWING
        new = [
            p.result["context"]
            for p in control.store.get_operations("e", kind="task_review_request")
            if p.task_run_id == first.id
        ][-1]
        assert (
            new["context_id"] != context["context_id"]
            and new["task_commit"] != context["task_commit"]
        )
        stale = await runtime.call_async(
            "task_approve",
            dict(
                project_id="p",
                task_run_id=first.id,
                request_key="agent-stale",
                decision=approve(context),
            ),
        )
        assert not stale.ok and task(control, "F-01").internal_status == TaskState.REVIEWING

    asyncio.run(asyncio.wait_for(exercise(), timeout=90))


def test_lost_overview_does_not_hide_or_replay_successful_start(setup, monkeypatch):
    control, runtime, _, _, sessions = setup

    async def unavailable(actor):
        raise RuntimeError("fixture read failure")

    monkeypatch.setattr(control, "overview", unavailable)
    args = dict(project_id="p", epic_run_id="e", task=control.scheduler.selection.specs[T1])
    first = asyncio.run(runtime.call_async("task_start", args))
    assert first.ok and first.data["task"]["id"] == task(control, "F-01").id
    assert first.data["overview"]["status"] == "UNAVAILABLE"
    before = sessions.starts, len(sessions.sent)
    replay = asyncio.run(runtime.call_async("task_start", args))
    assert replay.ok and replay.data["task"]["id"] == first.data["task"]["id"]
    assert (sessions.starts, len(sessions.sent)) == before


def test_review_block_park_and_explicit_same_session_resume(setup):
    control, runtime, _, board, sessions = setup
    target = dict(project_id="p", epic_run_id="e")

    async def exercise():
        await runtime.call_async(
            "task_start", target | {"task": control.scheduler.selection.specs[T1]}
        )
        finish(control, sessions, "F-01")
        await runtime.call_async("task_schedule", target)
        first = task(control, "F-01")
        context = next(
            p.result["context"]
            for p in control.store.get_operations("e", kind="task_review_request")
            if p.task_run_id == first.id
        )
        parked = await runtime.call_async(
            "task_block_review",
            dict(
                project_id="p",
                task_run_id=first.id,
                decision=dict(
                    version=1,
                    result="NEEDS_INPUT",
                    context_id=context["context_id"],
                    reason="Fixture decision missing",
                    input_required="Confirm fixture explanation",
                    responsible_role="User",
                ),
            ),
        )
        assert parked.ok, parked
        current = task(control, "F-01")
        assert current.internal_status == TaskState.PARKED and current.worker_slot is None
        assert current.worker_agent_id not in sessions.agents
        assert next(t for t in board.tasks if t["taskId"] == T1)["status"] == "NeedsInput"
        invalid = await runtime.call_async(
            "resume_task", dict(project_id="p", task_run_id=first.id)
        )
        assert not invalid.ok and task(control, "F-01").worker_slot is None
        decision = dict(
            version=1,
            input_id=str(uuid4()),
            blocker_id=parked.data["blocker_id"],
            answer="Fixture explanation confirmed",
        )
        args = dict(project_id="p", task_run_id=first.id, decision=decision)
        resumed = await runtime.call_async("resume_task", args)
        assert resumed.ok, resumed
        after = task(control, "F-01")
        assert after.internal_status == TaskState.REVIEWING and after.worker_slot is not None
        assert (after.codex_session_id, after.branch, after.worktree_path) == (
            first.codex_session_id,
            first.branch,
            first.worktree_path,
        )
        count = sessions.starts, len(sessions.sent)
        replay = await runtime.call_async("resume_task", args)
        assert replay.ok and (sessions.starts, len(sessions.sent)) == count

    asyncio.run(asyncio.wait_for(exercise(), timeout=90))


def test_board_owner_change_and_unregistered_runtime_block_all_mutation_paths(setup):
    control, runtime, _, board, sessions = setup
    args = dict(project_id="p", epic_run_id="e", task=control.scheduler.selection.specs[T1])
    row = next(t for t in board.tasks if t["taskId"] == T1)
    row["responsibleUserId"] = "00000000-0000-4000-8000-000000000999"
    denied = asyncio.run(runtime.call_async("task_start", args))
    assert not denied.ok and not control.store.get_tasks("e") and sessions.starts == 1
    for operation in ("task_report_ready", "task_report_blocked", "set_epic_status", "epic_start"):
        assert asyncio.run(runtime.call_async(operation, args)).code == "UNKNOWN_OPERATION"
        assert runtime.call(operation, args).code == "UNKNOWN_OPERATION"
    epic = control.store.get_epic("e")
    sessions.exit_agent(epic.integration_agent_id)
    assert not asyncio.run(
        runtime.call_async("task_schedule", dict(project_id="p", epic_run_id="e"))
    ).ok
    assert not control.store.get_tasks("e") and sessions.starts == 1


def test_delayed_correction_ack_reuses_recorded_request_without_redispatch(setup, monkeypatch):
    control, runtime, _, _, sessions = setup
    target = dict(project_id="p", epic_run_id="e")

    async def exercise():
        await runtime.call_async(
            "task_start", target | {"task": control.scheduler.selection.specs[T1]}
        )
        finish(control, sessions, "F-01")
        await runtime.call_async("task_schedule", target)
        first = task(control, "F-01")
        context = next(
            p.result["context"]
            for p in control.store.get_operations("e", kind="task_review_request")
            if p.task_run_id == first.id
        )
        decision = dict(
            version=1,
            result="CHANGES_REQUESTED",
            context_id=context["context_id"],
            issues=[
                dict(
                    number=1,
                    problem="Fixture explanation missing",
                    requested_change="Document the fixture",
                    acceptance_criteria=["Verified"],
                )
            ],
        )
        original = sessions.prompt
        delayed = []

        def delayed_prompt(name, text, *, timeout_ms):
            original(name, text, timeout_ms=timeout_ms)
            delayed.append(sessions.threads[first.codex_session_id]["turns"].pop())

        monkeypatch.setattr(sessions, "prompt", delayed_prompt)
        result = await runtime.call_async(
            "task_request_changes",
            dict(
                project_id="p",
                task_run_id=first.id,
                request_key="agent-delayed-fix",
                decision=decision,
            ),
        )
        assert result.ok and task(control, "F-01").internal_status == TaskState.CHANGES_REQUESTED
        count = len(sessions.sent)
        sessions.threads[first.codex_session_id]["turns"].extend(delayed)
        await runtime.call_async("task_schedule", target)
        assert task(control, "F-01").internal_status == TaskState.WORKING
        assert len(sessions.sent) == count
        changes = [
            o
            for o in control.store.get_operations("e", kind="task_request_changes")
            if o.task_run_id == first.id
        ]
        assert len(changes) == 1 and changes[0].idempotency_key == "agent-delayed-fix"

    asyncio.run(asyncio.wait_for(exercise(), timeout=90))


def test_known_correction_result_survives_failed_scheduler_checkpoint(setup, monkeypatch):
    control, runtime, _, _, sessions = setup
    target = dict(project_id="p", epic_run_id="e")

    async def exercise():
        await runtime.call_async(
            "task_start", target | {"task": control.scheduler.selection.specs[T1]}
        )
        finish(control, sessions, "F-01")
        await runtime.call_async("task_schedule", target)
        first = task(control, "F-01")
        context = next(
            p.result["context"]
            for p in control.store.get_operations("e", kind="task_review_request")
            if p.task_run_id == first.id
        )
        decision = dict(
            version=1,
            result="CHANGES_REQUESTED",
            context_id=context["context_id"],
            issues=[
                dict(
                    number=1,
                    problem="Fixture explanation missing",
                    requested_change="Document the fixture",
                    acceptance_criteria=["Verified"],
                )
            ],
        )
        original = control.scheduler._record

        def unavailable(*a, **k):
            raise RuntimeError("fixture checkpoint failure")

        monkeypatch.setattr(control.scheduler, "_record", unavailable)
        args = dict(
            project_id="p",
            task_run_id=first.id,
            request_key="agent-checkpoint-fix",
            decision=decision,
        )
        result = await runtime.call_async("task_request_changes", args)
        assert result.ok and result.data["status"] == "CONFIRMED"
        assert result.data["control_refresh"] == "PENDING"
        assert task(control, "F-01").internal_status == TaskState.WORKING
        count = len(sessions.sent)
        monkeypatch.setattr(control.scheduler, "_record", original)
        retry = await runtime.call_async("task_request_changes", args)
        assert retry.ok and retry.data["status"] == "EXISTING" and len(sessions.sent) == count

    asyncio.run(asyncio.wait_for(exercise(), timeout=90))
