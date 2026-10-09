"""Actual temporary Git/SQLite delivery; injected board and runtime, no native-runtime claim."""

import asyncio
import io
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from mcp import Client
from test_task_merge_service import (  # noqa: F401
    changes_setup,
    deliver,
    report_setup,
    review_setup,
    start_setup,
)
from test_task_merge_service import setup as delivery_setup  # noqa: F401
from test_teamplayer_reader import T1, T2, T3, E, FakeBoard, reader
from test_teamplayer_reader import epic as board_epic
from test_teamplayer_reader import task as board_task
from test_teamplayer_sync import finish_epic

from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.state_service import StateService
from orchestrator.application.task_selection_service import TaskSelectionError, TaskSelectionService
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.domain.policy import Role
from orchestrator.domain.states import EpicState
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


def run(awaitable):
    return asyncio.run(awaitable)


def selection(settings, store, actor, spec, *, history=None, processes=None):
    specs = {
        identity: spec | {"task_id": local} for identity, local in [(T1, "T"), (T2, "B"), (T3, "C")]
    }
    specs[T3]["dependencies"] = ["T"]
    board = FakeBoard(
        tasks=[
            board_task(
                identity,
                name=v["name"],
                acceptanceCriteria=v["acceptance_criteria"],
                dependencyTaskIds=[T1] if identity == T3 else [],
            )
            for identity, v in specs.items()
        ],
        epics=[board_epic() | {"status": "InProgress"}],
    )
    bindings = {E: LocalBoardBinding(local_id="E")}
    bindings.update({k: LocalBoardBinding(local_id=v["task_id"]) for k, v in specs.items()})
    r = reader(board, bindings=bindings)
    service = TaskSelectionService(
        settings,
        store,
        r,
        specs=specs,
        task_order=(T2, T1, T3),
        epic_prerequisites={"E": ()},
        codex=history,
        processes=processes,
    )
    return service, board


@pytest.fixture
def setup(start_setup):  # noqa: F811
    start, actor, spec, _, history = start_setup
    service, board = selection(start.settings, start.store, actor, spec, history=history)
    return service, board, actor


@pytest.fixture
def ready(delivery_setup):  # noqa: F811
    s, actor, worker, h, history, processes = delivery_setup
    spec = s.store.get_operation("p", "task_start", "T").result["spec"]
    service, board = selection(
        s.settings, s.store, actor, spec, history=history, processes=processes
    )
    return service, board, actor, s, worker, h, history, processes


def row(result, identity=T3):
    return next(t for t in result["tasks"] if t["task_id"] == identity)


def codes(result, identity=T3):
    return {b["code"] for b in row(result, identity)["blockers"]}


def test_two_independent_candidates_stable_order_all_blockers_and_no_effects(setup):
    s, b, a = setup
    before = s.store.db.total_changes, s.git.worktrees(), s.git.head("main")
    result = run(s.get_next(a, a.epic_run_id))
    assert result["candidates"] == [T2, T1] and result["next_task_id"] == T2
    assert codes(result) == {"DEPENDENCY_NOT_DONE"}
    assert row(result)["blockers"] == [{"code": "DEPENDENCY_NOT_DONE", "related_id": T1}]
    assert result["reservation"] is False
    b.tasks.reverse()
    assert run(s.get_next(a, a.epic_run_id)) == result
    assert (s.store.db.total_changes, s.git.worktrees(), s.git.head("main")) == before
    assert not s.store.get_tasks(a.epic_run_id)
    with StateStore(s.settings.sqlite_path) as db:
        reopened = TaskSelectionService(
            s.settings,
            db,
            s.reader,
            specs=s.specs,
            task_order=s.order,
            epic_prerequisites=s.epic_prerequisites,
        )
        assert run(reopened.get_next(a, a.epic_run_id)) == result


def test_native_priority_precedes_operator_tie_order(setup):
    s, b, a = setup
    b.tasks[0]["priority"] = 3
    result = run(s.get_next(a, a.epic_run_id))
    assert result["candidates"] == [T1, T2]


@pytest.mark.parametrize(
    "status",
    [
        "NeedsInput",
        "NeedsReview",
        "Blocked",
        "InProgress",
        "Testing",
        "Paused",
        "Cancelled",
        "Done",
    ],
)
def test_only_pending_is_a_candidate_and_attention_dependency_blocks(setup, status):
    s, b, a = setup
    b.tasks[0]["status"] = status
    result = run(s.get_next(a, a.epic_run_id))
    assert result["candidates"] == [T2]
    assert "TASK_NOT_PLANNED" in codes(result, T1)
    assert not row(result)["runnable"]


@pytest.mark.parametrize(
    "attack,expected",
    [
        ("cycle", "DEPENDENCY_CYCLE"),
        ("unknown", "DEPENDENCY_MISSING"),
        ("binding", "DEPENDENCY_BINDING_MISSING"),
        ("owner", "TASK_EXECUTION_OWNER_MISMATCH"),
        ("criteria", "TASK_SPEC_BOARD_MISMATCH"),
        ("spec", "TASK_SPEC_MISSING"),
        ("incomplete", "TASK_SPEC_INVALID"),
        ("deps", "TASK_SPEC_BOARD_MISMATCH"),
        ("gate", "EPIC_PREREQUISITES_UNSPECIFIED"),
        ("dirty", "EPIC_OR_MAIN_WORKTREE_UNSAFE"),
        ("prerequisite", "TASK_PREREQUISITE_UNVERIFIED"),
    ],
)
def test_invalid_spec_graph_owner_external_gate_or_git_explains_and_never_starts(
    setup, attack, expected
):
    s, b, a = setup
    target = T1
    if attack == "cycle":
        b.tasks[0]["dependencyTaskIds"] = [T3]
        s.specs[T1]["dependencies"] = ["C"]
    elif attack == "unknown":
        b.tasks[0]["dependencyTaskIds"] = [str(UUID(int=99))]
    elif attack == "binding":
        s.reader.bindings.pop(T1)
        target = T3
    elif attack == "owner":
        b.tasks[0]["responsibleUserId"] = str(UUID(int=99))
    elif attack == "criteria":
        b.tasks[0]["acceptanceCriteria"] = ["changed"]
    elif attack == "spec":
        s.specs.pop(T1)
    elif attack == "incomplete":
        s.specs[T1].pop("requirements")
    elif attack == "deps":
        s.specs[T1]["dependencies"] = ["Unknown local"]
    elif attack == "gate":
        s.epic_prerequisites.clear()
    elif attack == "dirty":
        e = s.store.get_epic(a.epic_run_id)
        (Path(e.worktree_path) / "uncommitted").write_text("preserve")
    else:
        s.specs[T1]["external_prerequisites"] = ["Unavailable test service"]
    before = s.store.db.total_changes, s.git.worktrees(), s.git.head("main")
    result = run(s.get_next(a, a.epic_run_id))
    assert expected in codes(result, target) and not row(result, target)["runnable"]
    assert (s.store.db.total_changes, s.git.worktrees(), s.git.head("main")) == before
    assert not s.store.get_tasks(a.epic_run_id)


def test_external_probe_requires_exact_true_and_does_not_leak_exception(setup):
    s, _, a = setup
    s.specs[T1]["external_prerequisites"] = ["External fact"]
    s.prerequisite_probe = lambda _: "yes"
    assert "TASK_PREREQUISITE_UNVERIFIED" in codes(run(s.get_next(a, a.epic_run_id)), T1)
    s.prerequisite_probe = lambda _: (_ for _ in ()).throw(RuntimeError("private credential"))
    result = run(s.get_next(a, a.epic_run_id))
    assert "private credential" not in str(result)
    s.prerequisite_probe = lambda _: True
    assert T1 in run(s.get_next(a, a.epic_run_id))["candidates"]


def test_approved_is_not_done_then_actual_delivery_opens_dependency(ready):
    s, b, a, delivery, w, h, _, _ = ready
    b.tasks[0]["status"] = "Done"  # deliberately divergent board
    result = run(s.get_next(a, a.epic_run_id))
    assert "DEPENDENCY_RUNTIME_NOT_DONE" in codes(result) and T3 not in result["candidates"]
    assert deliver(delivery, a, w)["status"] == "DONE"
    before = s.store.db.total_changes, h.starts, h.exits, len(h.sent)
    result = run(s.get_next(a, a.epic_run_id))
    assert result["candidates"] == [T2, T3] and not codes(result)
    proof = row(result)["dependencies"][T1]
    task = s.store.get_task(w.task_run_id)
    assert proof["merge_commit"] == task.merge_commit and proof["test_id"] and proof["stop_id"]
    assert (s.store.db.total_changes, h.starts, h.exits, len(h.sent)) == before


def test_ready_for_review_report_does_not_unlock_dependency(review_setup, report_setup):  # noqa: F811
    reviews, a, w, _ = review_setup
    _, _, _, _, history, _ = report_setup
    spec = reviews.store.get_operation("p", "task_start", "T").result["spec"]
    s, b = selection(reviews.settings, reviews.store, a, spec, history=history)
    b.tasks[0]["status"] = "Done"
    result = run(s.get_next(a, a.epic_run_id))
    assert "DEPENDENCY_RUNTIME_NOT_DONE" in codes(result) and T3 not in result["candidates"]


@pytest.mark.parametrize(
    "attack", ["test", "merge", "approval", "context", "stop", "alive", "event", "task-sha"]
)
def test_board_done_without_current_complete_git_delivery_cannot_unlock(ready, attack):
    s, b, a, delivery, w, _, _, processes = ready
    deliver(delivery, a, w)
    b.tasks[0]["status"] = "Done"
    if attack in {"test", "merge", "approval", "context", "stop"}:
        kind = {
            "test": "task_delivery_test",
            "merge": "merge_task_to_epic",
            "approval": "task_approve",
            "context": "task_review_request",
            "stop": "stop_runtime",
        }[attack]
        op = s.store.get_operations(a.epic_run_id, kind=kind)[0]
        s.store.update_operation(op.model_copy(update={"status": "PENDING"}))
    elif attack == "alive":
        processes.alive = True
    elif attack == "event":
        s.store.db.execute("DELETE FROM transition_events WHERE event_id LIKE 'delivery-done:%'")
    else:
        t = s.store.get_task(w.task_run_id)
        p = Path(t.worktree_path)
        (p / "changed").write_text("Changed")
        s.git.run("add", ".", cwd=p)
        s.git.run("commit", "-m", "Changed", cwd=p)
    before = s.store.db.total_changes
    result = run(s.get_next(a, a.epic_run_id))
    assert "DEPENDENCY_DELIVERY_UNVERIFIED" in codes(result) and T3 not in result["candidates"]
    assert s.store.db.total_changes == before


@pytest.mark.parametrize("attack", ["worker", "coordinator", "project", "epic"])
def test_scope_denied_before_board_read(setup, attack):
    s, b, a = setup
    bad = {
        "worker": a.model_copy(update={"role": Role.WORKER}),
        "coordinator": a.model_copy(update={"role": Role.COORDINATOR}),
        "project": a.model_copy(update={"project_id": "foreign"}),
        "epic": a.model_copy(update={"epic_run_id": "foreign"}),
    }[attack]
    with pytest.raises(TaskSelectionError, match="SCOPE_DENIED"):
        run(s.get_next(bad, a.epic_run_id))
    assert not b.calls


def test_actual_mcp_advertises_read_only_and_caller_cannot_inject_status_spec_or_role(setup):
    s, b, a = setup

    async def exercise(actor):
        runtime = RuntimeService(s.store, actor, EventLog(stream=io.StringIO()), task_selection=s)
        async with Client(create_server(runtime)) as client:
            catalog = await client.list_tools()
            tool = next(t for t in catalog.tools if t.name == "task_get_next")
            assert tool.annotations.read_only_hint and not tool.annotations.destructive_hint
            args = {"project_id": "p", "epic_run_id": a.epic_run_id}
            good = await client.call_tool("task_get_next", args)
            assert good.structured_content["code"] == ("OK" if actor == a else "FORBIDDEN")
            if actor == a:
                assert good.structured_content["data"]["candidates"] == [T2, T1]
            injected = await client.call_tool(
                "task_get_next", args | {"role": "Integration", "status": "Done"}
            )
            assert injected.structured_content["code"] in {"FORBIDDEN", "INVALID_ARGUMENT"}
            foreign = await client.call_tool("task_get_next", args | {"project_id": "foreign"})
            assert foreign.structured_content["code"] == "FORBIDDEN"

    run(exercise(a.model_copy(update={"role": Role.WORKER})))
    assert not b.calls
    run(exercise(a))
    assert not s.store.get_tasks(a.epic_run_id)


def previous_epic(ready, *, complete=True, final=True, stale=False):
    s, b, a, delivery, w, _, history, processes = ready
    c = a.model_copy(update={"role": Role.COORDINATOR})
    next_coordinator = c.model_copy(update={"epic_run_id": "next-run"})
    worktrees = WorktreeService(s.settings, s.store)
    epic = (
        worktrees.create_epic_worktree(next_coordinator, epic_id="Next", run_id="next-run")
        if stale
        else None
    )
    if complete:
        finish_epic(None, delivery, a, w, c, final=final)
    else:
        deliver(delivery, a, w)
    b.tasks = b.tasks[:1]
    b.tasks[0]["status"] = "Done"
    b.epics[0]["status"] = "Done"
    new_id = str(UUID(int=33))
    new_task = str(UUID(int=34))
    if epic is None:
        epic = worktrees.create_epic_worktree(next_coordinator, epic_id="Next", run_id="next-run")
    StateService(s.store).transition_epic(
        epic.id,
        EpicState.ACTIVE,
        expected=EpicState.PLANNED,
        event_id="next-active",
        actor=c.model_copy(update={"epic_run_id": "next-run"}),
    )
    b.epics.append(board_epic() | {"id": new_id, "status": "InProgress"})
    spec = deepcopy(s.specs[T2])
    spec.update(epic_id="Next", task_id="N", dependencies=["T"])
    s.specs[new_task] = spec
    b.tasks.append(
        board_task(
            new_task,
            epicId=new_id,
            name=spec["name"],
            acceptanceCriteria=spec["acceptance_criteria"],
            dependencyTaskIds=[T1],
        )
    )
    s.reader.bindings.update(
        {new_id: LocalBoardBinding(local_id="Next"), new_task: LocalBoardBinding(local_id="N")}
    )
    s.epic_prerequisites["Next"] = (E,)
    s.order = (*s.order, new_task)
    s.delivery_contexts[a.epic_run_id] = s.settings
    s.scopes[a.epic_run_id] = ("T",)
    return s, b, a.model_copy(update={"epic_run_id": epic.id}), new_task


def test_previous_epic_requires_actual_main_merge_and_final_acceptance(ready):
    s, _, a, t = previous_epic(ready)
    result = run(s.get_next(a, a.epic_run_id))
    assert result["candidates"] == [t] and result["epic_dependencies"][E]["final_test_id"]
    assert s.git.contains_commit(
        s.store.get_epic(a.epic_run_id).branch, result["epic_dependencies"][E]["merge_commit"]
    )


@pytest.mark.parametrize(
    "attack", ["no-main", "no-final", "no-context", "no-scope", "base-before-main"]
)
def test_previous_epic_board_done_is_insufficient(ready, attack):
    s, b, a, t = previous_epic(
        ready,
        complete=attack != "no-main",
        final=attack != "no-final",
        stale=attack == "base-before-main",
    )
    if attack == "no-context":
        s.delivery_contexts.clear()
    elif attack == "no-scope":
        s.scopes.clear()
    before = s.store.db.total_changes
    result = run(s.get_next(a, a.epic_run_id))
    assert not result["candidates"] and any("EPIC_" in c for c in codes(result, t))
    assert s.store.db.total_changes == before


def test_done_board_without_registered_run_is_precise(setup):
    s, b, a = setup
    b.tasks[0]["status"] = "Done"
    result = run(s.get_next(a, a.epic_run_id))
    assert "DEPENDENCY_RUN_MISSING_OR_AMBIGUOUS" in codes(result)
    assert result["candidates"] == [T2]


def test_existing_planned_run_is_not_adopted(setup):
    s, _, a = setup
    WorktreeService(s.settings, s.store).create_task_worktree(
        a, epic_run_id=a.epic_run_id, task_id="T", run_id="independent"
    )
    result = run(s.get_next(a, a.epic_run_id))
    assert "TASK_ALREADY_OWNED" in codes(result, T1) and result["candidates"] == [T2]
    assert s.store.get_task("independent").worker_slot is None


def test_unregistered_task_branch_is_not_an_executable_candidate(setup):
    s, _, a = setup
    s.git.run("branch", "task/e-t", s.git.head("main"))
    result = run(s.get_next(a, a.epic_run_id))
    assert "TASK_GIT_RESOURCES_OCCUPIED" in codes(result, T1)
    assert result["candidates"] == [T2] and not s.store.get_tasks(a.epic_run_id)


def test_spec_order_must_be_complete_and_unique(setup):
    s, _, _ = setup
    for order in [(T1, T1, T2), (T1,)]:
        with pytest.raises(TaskSelectionError, match="ORDER_INVALID"):
            TaskSelectionService(
                s.settings,
                s.store,
                s.reader,
                specs=s.specs,
                task_order=order,
                epic_prerequisites=s.epic_prerequisites,
            )


def test_transitive_dependency_with_board_done_but_no_run_is_verified(ready):
    s, b, a, delivery, w, _, _, _ = ready
    deliver(delivery, a, w)
    b.tasks[0]["status"] = "Done"
    b.tasks[1]["status"] = "Done"
    b.tasks[0]["dependencyTaskIds"] = [T2]
    s.specs[T1]["dependencies"] = ["B"]
    result = run(s.get_next(a, a.epic_run_id))
    assert not result["candidates"]
    assert {"code": "DEPENDENCY_RUN_MISSING_OR_AMBIGUOUS", "related_id": T2} in row(result)[
        "blockers"
    ]


def test_cross_epic_task_always_requires_main_even_without_declared_epic_gate(ready):
    s, _, a, t = previous_epic(ready, complete=False)
    s.epic_prerequisites["Next"] = ()
    result = run(s.get_next(a, a.epic_run_id))
    assert "EPIC_DEPENDENCY_PROOF_MISSING" in codes(result, t) and not result["candidates"]


def test_invalid_owned_worktree_returns_safe_reconcile_code(setup):
    s, _, a = setup
    e = s.store.get_epic(a.epic_run_id)
    s.git.run("config", "--local", f"branch.{e.branch}.herdrOwner", "unexpected")
    with pytest.raises(TaskSelectionError, match="WORKTREE_UNVERIFIED"):
        run(s.get_next(a, a.epic_run_id))


def test_native_auth_failure_and_unregistered_mcp_do_not_return_candidates(setup):
    from orchestrator.adapters.teamplayer_mcp import TeamPlayerError

    s, b, a = setup
    b.me["userId"] = str(UUID(int=99))
    with pytest.raises(TeamPlayerError, match="IDENTITY_MISMATCH"):
        run(s.get_next(a, a.epic_run_id))
    runtime = RuntimeService(s.store, None, EventLog(stream=io.StringIO()), task_selection=s)
    result = run(
        runtime.call_async("task_get_next", {"project_id": "p", "epic_run_id": a.epic_run_id})
    )
    assert result.code == "UNAUTHENTICATED" and not result.data
