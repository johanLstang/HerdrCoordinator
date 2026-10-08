import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from mcp import Client
from mcp.server import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from orchestrator.adapters.teamplayer_mcp import (
    OperatorHeaders,
    TeamPlayerError,
    TeamPlayerMCPAdapter,
    teamplayer_connection,
)
from orchestrator.application.teamplayer_reader import TeamPlayerReader
from orchestrator.domain.states import Kanban
from orchestrator.domain.teamplayer import LocalBoardBinding, Priority

P, U, E, T1, T2, T3, OTHER = (str(UUID(int=i)) for i in range(1, 8))
SECRET = "harmless-test-token-not-a-real-secret"
CRITERIA = ["Åäö, e\u0301, 東京", "One\nTwo"]


def epic():
    return {
        "id": E,
        "projectId": P,
        "name": "Fixture",
        "description": "rules",
        "status": "Pending",
        "version": 1,
    }


def task(identity=T1, **changes):
    value = {
        "taskId": identity,
        "projectId": P,
        "epicId": E,
        "name": "Task",
        "description": "Scope\nRefs",
        "taskType": "Task",
        "status": "Pending",
        "priority": 2,
        "version": 1,
        "acceptanceCriteria": CRITERIA,
        "dependencyTaskIds": [],
        "traceabilityArtifactIds": [OTHER],
        "executionOwnerKind": "User",
        "responsibleUserId": U,
        "responsibleAgentId": OTHER,
    }
    value.update(changes)
    return value


class FakeBoard:
    def __init__(self, tasks=None, epics=None):
        self.tasks = tasks if tasks is not None else [task()]
        self.epics = epics if epics is not None else [epic()]
        self.calls, self.snapshots = [], []
        self.me = {"userId": U}
        self.projects = [{"projectId": P, "name": "Fixture project", "access": "Read"}]
        self.extra = {}

    async def read(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "get_me":
            return copy.deepcopy(self.me)
        if name == "list_projects":
            return copy.deepcopy(self.projects)
        assert args["projectId"] == P
        if name == "list_epics":
            return copy.deepcopy(self.epics)
        if name == "list_tasks":
            rows = self.snapshots.pop(0) if self.snapshots else self.tasks
            return copy.deepcopy({"projectId": P, "tasks": rows, **self.extra})
        if name == "get_task":
            found = next((t for t in self.tasks if t["taskId"] == args["taskId"]), None)
            if found is None:
                raise TeamPlayerError("TEAMPLAYER_TASK_NOT_FOUND")
            return copy.deepcopy(found)
        raise AssertionError("Read triggered an unexpected operation")


def reader(board, **kwargs):
    return TeamPlayerReader(
        board, project_id=P, user_id=U, project_name="Fixture project", **kwargs
    )


def read(board, **kwargs):
    return asyncio.run(reader(board, **kwargs).read_project())


def codes(snapshot, identity):
    return {i.code for i in snapshot.issues if i.task_id == identity}


def test_lossless_fields_explicit_bindings_no_runtime_or_writes():
    board = FakeBoard([task(status="Done"), task(T2, dependencyTaskIds=[T1], priority=3)])
    binding = LocalBoardBinding(local_id="F-02", sources=("README.md", "design.md"))
    snapshot = read(board, bindings={T2: binding, E: LocalBoardBinding(local_id="E-01")})
    assert snapshot.epic(E).kanban == Kanban.PLANNED
    assert snapshot.task(T2).priority == Priority.CRITICAL
    assert snapshot.task(T2).acceptance_criteria == tuple(CRITERIA)
    assert snapshot.task(T2).description == "Scope\nRefs"
    assert snapshot.task(T2).dependencies == (T1,)
    assert snapshot.task(T2).traceability_artifact_ids == (OTHER,)
    assert snapshot.task(T2).binding == binding
    assert snapshot.candidate(T2) and not snapshot.candidate(T1)
    assert {name for name, _ in board.calls} == {
        "get_me",
        "list_projects",
        "list_epics",
        "list_tasks",
    }
    assert all(args.get("projectId", P) == P for _, args in board.calls)


def test_repeat_and_reordered_complete_lists_have_exactly_one_of_each_task():
    board = FakeBoard([task(), task(T2)])
    board.snapshots = [[task(T2), task()], [task(), task(T2)]]
    first = read(board)
    second = asyncio.run(reader(board).read_project(previous=first))
    assert first == second and [t.id for t in first.tasks] == [T1, T2]


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("acceptanceCriteria", [], "TASK_ACCEPTANCE_MISSING"),
        ("acceptanceCriteria", ["  "], "TASK_ACCEPTANCE_MISSING"),
        ("description", "", "TASK_INSTRUCTION_MISSING"),
        ("responsibleUserId", OTHER, "TASK_EXECUTION_OWNER_MISMATCH"),
        ("executionOwnerKind", "Agent", "TASK_EXECUTION_OWNER_MISMATCH"),
        ("epicId", None, "TASK_EPIC_MISSING"),
        ("epicId", OTHER, "TASK_EPIC_MISSING"),
        ("taskType", "Epic", "LEGACY_EPIC_NOT_EXECUTABLE"),
        ("status", "Cancelled", "TASK_CANCELLED_NOT_VERIFIED_DONE"),
        ("dependencyTaskIds", [OTHER], "DEPENDENCY_MISSING"),
        ("dependencyTaskIds", [T1], "DEPENDENCY_CYCLE"),
        ("dependencyTaskIds", [OTHER, OTHER], "DUPLICATE_DEPENDENCY"),
    ],
)
def test_invalid_task_is_flagged_and_cannot_be_candidate(field, value, code):
    snapshot = read(FakeBoard([task(**{field: value})]))
    assert code in codes(snapshot, T1) and not snapshot.candidate(T1)


def test_cycle_and_bad_done_dependency_invalidate_descendants_but_independent_task_survives():
    board = FakeBoard(
        [
            task(status="Done", dependencyTaskIds=[T2]),
            task(T2, status="Done", dependencyTaskIds=[T1]),
            task(T3, dependencyTaskIds=[T1]),
            task(OTHER),
        ]
    )
    snapshot = read(board)
    assert "DEPENDENCY_CYCLE" in codes(snapshot, T1) & codes(snapshot, T2)
    assert "DEPENDENCY_INVALID" in codes(snapshot, T3)
    assert not snapshot.candidate(T3) and snapshot.candidate(OTHER)


@pytest.mark.parametrize("status", ["Pending", "Cancelled", "Blocked", "Testing"])
def test_dependency_requires_exact_done_not_board_done_column(status):
    snapshot = read(FakeBoard([task(status=status), task(T2, dependencyTaskIds=[T1])]))
    assert "DEPENDENCY_NOT_DONE" in codes(snapshot, T2) and not snapshot.candidate(T2)


@pytest.mark.parametrize(
    "field,value",
    [
        ("projectId", OTHER),
        ("priority", True),
        ("priority", 99),
        ("priority", "High"),
        ("version", True),
        ("status", "FutureStatus"),
        ("dependencyTaskIds", None),
    ],
)
def test_invalid_schema_or_scope_never_returns_a_partial_board(field, value):
    with pytest.raises(TeamPlayerError):
        read(FakeBoard([task(**{field: value})]))


def test_duplicate_task_and_kind_collision_rejected():
    with pytest.raises(TeamPlayerError, match="DUPLICATE_ID"):
        read(FakeBoard([task(), task()]))
    with pytest.raises(TeamPlayerError, match="KIND_COLLISION"):
        read(FakeBoard([task(E)]))


@pytest.mark.parametrize(
    "change,code",
    [
        ("user", "IDENTITY_MISMATCH"),
        ("project", "PROJECT_UNAVAILABLE"),
        ("name", "PROJECT_MISMATCH"),
    ],
)
def test_identity_checked_before_board_reads(change, code):
    board = FakeBoard()
    if change == "user":
        board.me["userId"] = OTHER
    if change == "project":
        board.projects[0]["projectId"] = OTHER
    if change == "name":
        board.projects[0]["name"] = "Wrong"
    with pytest.raises(TeamPlayerError, match=code):
        read(board)
    assert not any(n in {"list_epics", "list_tasks"} for n, _ in board.calls)


def test_deleted_task_is_flagged_from_previous_and_explicit_binding_and_get_rejects():
    board = FakeBoard()
    first = read(board)
    board.tasks = []
    second = asyncio.run(reader(board).read_project(previous=first))
    assert "MISSING_BOUND_OR_PREVIOUS_ITEM" in codes(second, T1) and not second.candidate(T1)
    assert "MISSING_BOUND_OR_PREVIOUS_ITEM" in codes(
        read(board, bindings={T1: LocalBoardBinding(local_id="F1")}), T1
    )
    with pytest.raises(TeamPlayerError, match="TASK_NOT_FOUND"):
        asyncio.run(reader(board).get_task(T1))


def test_changing_snapshot_retries_bounded_and_deleted_during_read_is_flagged():
    board = FakeBoard([])
    board.snapshots = [[task()], []]
    assert "MISSING_BOUND_OR_PREVIOUS_ITEM" in codes(read(board), T1)
    board = FakeBoard()
    board.snapshots = [[task(version=i)] for i in range(1, 7)]
    with pytest.raises(TeamPlayerError, match="SNAPSHOT_CHANGED"):
        read(board)
    assert sum(n == "list_tasks" for n, _ in board.calls) == 6


@pytest.mark.parametrize(
    "key,value", [("nextCursor", "page2"), ("hasMore", True), ("continuationToken", "more")]
)
def test_unverified_data_pagination_never_silently_loses_tasks(key, value):
    board = FakeBoard()
    board.extra[key] = value
    with pytest.raises(TeamPlayerError, match="PAGINATION_UNSUPPORTED"):
        read(board)


def test_local_binding_collision_and_previous_wrong_project_rejected():
    binding = LocalBoardBinding(local_id="F1")
    with pytest.raises(TeamPlayerError, match="BINDING_INVALID"):
        reader(FakeBoard(), bindings={T1: binding, T2: binding})
    previous = read(FakeBoard()).model_copy(update={"project_id": OTHER})
    with pytest.raises(TeamPlayerError, match="PREVIOUS_SCOPE_MISMATCH"):
        asyncio.run(reader(FakeBoard()).read_project(previous=previous))


def envelope(value, **kwargs):
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(value))], **kwargs)


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"success": False, "code": "permission_denied", "message": SECRET}, "PERMISSION_DENIED"),
        (
            {"success": False, "error": {"errorCode": "task_not_found", "message": SECRET}},
            "TASK_NOT_FOUND",
        ),
        ({"success": True, "data": SECRET}, "READ_REJECTED"),
    ],
)
def test_upstream_errors_and_mcp_iserror_are_sanitized(payload, expected, capsys):
    client = Mock()
    client.call_tool = AsyncMock(return_value=envelope(payload, is_error=True))
    with pytest.raises(TeamPlayerError, match=expected) as e:
        asyncio.run(TeamPlayerMCPAdapter(client).read("get_task", {}))
    assert SECRET not in str(e.value) and SECRET not in repr(e.value)
    assert not any(capsys.readouterr())


def test_read_adapter_rejects_write_before_network_and_duplicate_json_keys():
    client = Mock()
    client.call_tool = AsyncMock()
    with pytest.raises(TeamPlayerError, match="OPERATION_DENIED"):
        asyncio.run(TeamPlayerMCPAdapter(client).read("update_task_status", {}))
    client.call_tool.assert_not_called()
    client.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text='{"success":true,"success":false,"data":{}}')]
    )
    with pytest.raises(TeamPlayerError, match="UNAVAILABLE"):
        asyncio.run(TeamPlayerMCPAdapter(client).read("get_me", {}))


def native_tools():
    value = json.loads(
        (Path(__file__).parents[1] / "docs/teamplayer/F-23-katalog.json").read_text()
    )
    return [Tool.model_validate(t) for t in value["tools"]]


def test_actual_mcp_transport_with_catalog_pages_preserves_complete_fixture():
    board = FakeBoard([task(status="Done"), task(T2, dependencyTaskIds=[T1])])
    tools = native_tools()
    cursors = []

    async def listing(ctx, params):
        cursor = params.cursor if params is not None else None
        cursors.append(cursor)
        if cursor is None:
            return ListToolsResult(tools=tools[:8], next_cursor="next")
        assert cursor == "next"
        return ListToolsResult(tools=tools[8:])

    async def calling(ctx, params):
        return envelope(
            {"success": True, "data": await board.read(params.name, params.arguments or {})}
        )

    server = Server("f24-contract-fixture", on_list_tools=listing, on_call_tool=calling)

    async def exercise():
        async with Client(server, mode="legacy") as client:
            adapter = TeamPlayerMCPAdapter(client)
            await adapter.verify_catalog()
            snapshot = await reader(adapter).read_project()
            assert snapshot.candidate(T2) and len(snapshot.epic_tasks(E)) == 2
            assert await reader(adapter).get_task(T2) == snapshot.task(T2)

    asyncio.run(exercise())
    assert cursors == [None, "next"]


@pytest.mark.parametrize(
    "case,code",
    [
        ("loop", "CURSOR_LOOP"),
        ("duplicate", "CATALOG_DUPLICATE"),
        ("missing", "READ_TOOLS_MISSING"),
        ("write", "READ_CAPABILITY_CHANGED"),
        ("pagination", "PAGINATION_UNSUPPORTED"),
    ],
)
def test_changed_or_incomplete_catalog_rejected(case, code):
    tools = native_tools()
    if case == "missing":
        tools = [t for t in tools if t.name != "get_task"]
    if case == "write":
        next(t for t in tools if t.name == "get_me").annotations.read_only_hint = False
    if case == "pagination":
        next(t for t in tools if t.name == "list_tasks").input_schema["properties"]["cursor"] = {
            "type": "string"
        }
    page = SimpleNamespace(
        tools=tools, next_cursor="same" if case in {"loop", "duplicate"} else None
    )
    client = Mock()
    client.list_tools = AsyncMock(return_value=page)
    if case == "loop":
        client.list_tools.side_effect = [page, SimpleNamespace(tools=[], next_cursor="same")]
    with pytest.raises(TeamPlayerError, match=code):
        asyncio.run(TeamPlayerMCPAdapter(client).verify_catalog())


def test_operator_credentials_stay_in_memory_repr_and_safe_errors(monkeypatch, capsys):
    monkeypatch.setenv("F24_TEST_TOKEN", SECRET)
    provider = OperatorHeaders(bearer_env="F24_TEST_TOKEN")
    assert provider() == {"Authorization": "Bearer " + SECRET} and SECRET not in repr(provider)
    monkeypatch.delenv("F24_TEST_TOKEN")
    with pytest.raises(TeamPlayerError, match="AUTH_UNAVAILABLE") as e:
        provider()
    assert SECRET not in str(e.value) and not any(capsys.readouterr())


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://external.invalid/mcp",
        "https://name:password@host/mcp",
        "https://host/mcp?token=x",
        "https://host/mcp#token",
    ],
)
def test_unsafe_endpoint_rejected_before_credentials_or_connection(endpoint):
    provider = Mock()

    async def exercise():
        async with teamplayer_connection(endpoint, provider):
            pass

    with pytest.raises(TeamPlayerError, match="CONFIG_INVALID"):
        asyncio.run(exercise())
    provider.assert_not_called()


def test_helper_uses_argv_no_shell_and_hides_stderr(monkeypatch):
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps({"Authorization": SECRET}).encode()))
    monkeypatch.setattr("orchestrator.adapters.teamplayer_mcp.subprocess.run", run)
    provider = OperatorHeaders(command=("/operator/helper", "headers"))
    assert provider() == {"Authorization": SECRET}
    assert run.call_args.args[0] == ("/operator/helper", "headers")
    assert "shell" not in run.call_args.kwargs and run.call_args.kwargs["capture_output"]
    run.side_effect = RuntimeError(SECRET)
    with pytest.raises(TeamPlayerError) as e:
        provider()
    assert SECRET not in str(e.value)


@pytest.mark.parametrize(
    "key,value,code",
    [
        ("status", "Pending", "LIST_FILTERED"),
        ("responsibleAgentId", OTHER, "LIST_FILTERED"),
        ("futurePages", 2, "CONTRACT_CHANGED"),
    ],
)
def test_filtered_or_unknown_list_contract_cannot_be_a_complete_snapshot(key, value, code):
    board = FakeBoard()
    board.extra[key] = value
    with pytest.raises(TeamPlayerError, match=code):
        read(board)


def test_single_get_checks_identity_before_task_lookup():
    board = FakeBoard()
    board.me["userId"] = OTHER
    with pytest.raises(TeamPlayerError, match="IDENTITY_MISMATCH"):
        asyncio.run(reader(board).get_task(T1))
    assert not any(name == "get_task" for name, _ in board.calls)


def test_done_epic_with_unfinished_task_requires_reopening_decision_not_candidate():
    e = epic()
    e["status"] = "Done"
    snapshot = read(FakeBoard([task()], epics=[e]))
    assert "UNFINISHED_TASK_IN_DONE_EPIC" in codes(snapshot, T1)
    assert not snapshot.candidate(T1)
