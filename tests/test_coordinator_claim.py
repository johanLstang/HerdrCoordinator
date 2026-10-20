"""Actual SQLite/Git/F34 services; simulated runtime/security/board stated explicitly."""

import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import UUID

import pytest
from mcp import Client
from test_coordinator_runtime import plan
from test_coordinator_runtime import setup as runtime_setup  # noqa: F401
from test_epic_start import Native
from test_epic_start import setup as epic_setup  # noqa: F401
from test_teamplayer_reader import OTHER, T1, T2, E, FakeBoard, P, epic, reader, task

from orchestrator.application.coordinator_claim_service import CoordinatorClaimService
from orchestrator.application.coordinator_control_service import CoordinatorControlService
from orchestrator.application.coordinator_runtime_service import (
    CoordinatorError,
    CoordinatorRuntimeService,
)
from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.domain.models import EpicRun
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import EpicState
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


def run(awaitable):
    return asyncio.run(awaitable)


def gate(runtime, main):
    # Controlled positive fixture, NEVER a real environment/security attestation.
    return dict(
        version=1, configuration_hash=runtime.configuration_hash, main_commit=main,
        probe_source="controlled-test-adapter", outside_read_denied=True,
        protected_state_denied=True, git_role_boundaries_verified=True,
        verification_process_isolated=True, input_readiness_verified=True,
    )


class Sync:
    """Controlled board transport; F34 still uses actual Git and persistent intents."""

    def __init__(self, settings, store, board):
        self.settings, self.store, self.board = settings, store, board
        self.calls = []

    async def bind_epic(self, actor, local, native):
        self.calls.append((local, native))

    async def sync_epic(self, actor, run_id):
        local = self.store.get_epic(run_id).epic_id
        for row in self.board.epics:
            if row["description"] == local:
                row["status"] = "InProgress"
                row["version"] += 1
        return dict(status="SYNCED")


@pytest.fixture
def setup(runtime_setup, request):  # noqa: F811
    original, actor, herdr, processes = runtime_setup
    config = plan() | {"native_project_id": P}
    config["epics"][0].update(native_epic_id=E, native_task_ids=[T1, T2])
    if hasattr(request, "param"):
        first = config["epics"][0]
        first["priority"] = "P1"
        second = deepcopy(first)
        second["native_epic_id"] = str(UUID(int=1000))
        second["native_task_ids"] = [str(UUID(int=1001)), str(UUID(int=1002))]
        second["priority"] = "P0" if request.param != "tie" else "P1"
        second["spec"].update(epic_id="E-35", title="Second fixture", dependencies=[])
        second["spec"]["tasks"][0]["task"].update(
            epic_id="E-35", task_id="F-03", dependencies=[],
        )
        second["spec"]["tasks"][1]["task"].update(
            epic_id="E-35", task_id="F-04", dependencies=["F-03"],
        )
        if request.param in {"dependency", "done-without-proof"}:
            second["spec"]["dependencies"] = ["E-34"]
        config["epics"].append(second)
    runtime = CoordinatorRuntimeService(
        original.settings, original.store, herdr, config, actor, original.run_id,
        codex=herdr, processes=processes,
    )
    runtime.start(actor)
    rows, epics, bindings = [], [], {}
    for configured in config["epics"]:
        epics.append(epic() | dict(
            id=configured["native_epic_id"], name=configured["spec"]["title"],
            description=configured["spec"]["epic_id"],
        ))
        bindings[configured["native_epic_id"]] = LocalBoardBinding(
            local_id=configured["spec"]["epic_id"]
        )
        for index, (identity, entry) in enumerate(zip(
            configured["native_task_ids"], configured["spec"]["tasks"], strict=True,
        )):
            rows.append(task(
                identity, name=entry["task"]["name"], epicId=configured["native_epic_id"],
                priority={"P0": 3, "P1": 2, "P2": 1, "P3": 0}[entry["priority"]],
                acceptanceCriteria=entry["task"]["acceptance_criteria"],
                dependencyTaskIds=[configured["native_task_ids"][0]] if index else [],
            ))
            bindings[identity] = LocalBoardBinding(local_id=entry["task"]["task_id"])
    board = FakeBoard(tasks=rows, epics=epics)
    board.projects[0]["access"] = "Write"
    r = reader(board, bindings=bindings)
    natives, services = {}, []

    def factory(configured, run_id, *, store=None):
        db = store or runtime.store
        if run_id not in natives:
            h = Native()
            original_prompt = h.prompt

            def prompt(*args, **kwargs):
                original_prompt(*args, **kwargs)
                h.sid = str(UUID(int=900))

            h.prompt = prompt
            natives[run_id] = h
        h = natives[run_id]
        principal = Actor(actor_id="integration-" + run_id, role=Role.INTEGRATION,
                          project_id="p", epic_run_id=run_id)
        service = EpicStartService(
            runtime.settings, db, h, configured.spec, principal, codex=h,
            sync=Sync(runtime.settings, db, board), external_epic_id=configured.native_epic_id,
        )
        services.append(service)
        return service

    claims = CoordinatorClaimService(runtime, r, factory, runtime_gate=gate)
    yield claims, actor, board, factory, natives, services


def test_claim_registers_one_integration_from_current_main_and_retries_reopen(setup):
    claims, actor, board, factory, natives, _ = setup
    main = claims.runtime.main()
    result = run(claims.claim(actor, "p", "claim-1"))
    assert result["status"] == "REGISTERED" and result["epic_id"] == "E-34", result
    assert result["main_commit"] == main
    epic_run = claims.store.get_epic(result["epic_run_id"])
    assert epic_run.base_commit == main
    assert epic_run.integration_agent_id is not None and claims.store.get_tasks(epic_run.id) == []
    assert len(natives) == 1
    assert run(claims.claim(actor, "p", "claim-1")) == result
    assert run(claims.claim(actor, "p", "another-key"))["status"] == "EXISTING"
    with StateStore(claims.settings.sqlite_path) as db:
        rt = CoordinatorRuntimeService(
            claims.settings, db, claims.runtime.herdr, claims.spec, actor, claims.runtime.run_id,
            codex=claims.runtime.codex, processes=claims.runtime.processes,
        )
        reopened = CoordinatorClaimService(
            rt, claims.reader, lambda c, r: factory(c, r, store=db), runtime_gate=gate,
        )
        assert run(reopened.claim(actor, "p", "claim-1")) == result
    h = next(iter(natives.values()))
    assert h.starts == len(h.workspaces) == len(h.prompts) == 1
    assert board.epics[0]["status"] == "InProgress"


def test_first_claim_uses_current_main_after_coordinator_registration(setup):
    claims, actor, _, _, _, _ = setup
    initial = claims.store.get_coordinator(claims.runtime.run_id).initial_commit
    readme = claims.settings.repository / "README.md"
    readme.write_text(readme.read_text() + "Current main before first claim\n")
    claims.runtime.git.run("add", "README.md")
    claims.runtime.git.run("commit", "-m", "new current main")
    current = claims.runtime.main()
    assert current != initial
    result = run(claims.claim(actor, "p", "current-main"))
    assert result["main_commit"] == current
    assert claims.store.get_epic(result["epic_run_id"]).base_commit == current


def test_default_runtime_gate_waits_without_any_epic_effect(setup):
    claims, actor, _, _, natives, _ = setup
    claims.gate = None
    result = run(claims.claim(actor, "p", "blocked"))
    assert result == dict(status="WAIT", reason="RUNTIME_SECURITY_GATE_UNVERIFIED", blockers=[])
    assert not natives and claims.store.get_runs() == []
    assert claims.store.project_operations(claims.runtime.run_id, kind=claims.KIND) == []


@pytest.mark.parametrize("field,value", [
    ("outside_read_denied", False), ("protected_state_denied", False),
    ("git_role_boundaries_verified", False), ("verification_process_isolated", False),
    ("input_readiness_verified", False), ("main_commit", "a" * 40),
    ("configuration_hash", "b" * 64), ("outside_read_denied", "true"),
])
def test_gate_requires_all_current_strict_probe_facts(setup, field, value):
    claims, actor, _, _, natives, _ = setup
    claims.gate = lambda r, m: gate(r, m) | {field: value}
    assert run(claims.claim(actor, "p", "blocked"))["status"] == "WAIT"
    assert not natives and not claims.store.get_runs()


@pytest.mark.parametrize("field,value,code", [
    ("executionOwnerKind", "Agent", "TASK_OWNER_CHANGED"),
    ("responsibleUserId", OTHER, "TASK_OWNER_CHANGED"),
    ("name", "Changed scope", "TASK_SPEC_BOARD_MISMATCH"),
    ("acceptanceCriteria", ["Changed criterion"], "TASK_SPEC_BOARD_MISMATCH"),
    ("status", "Testing", "EPIC_TASKS_NOT_PLANNED"),
    ("priority", 1, "TASK_PRIORITY_CHANGED"),
])
def test_changed_board_prevents_reservation_and_start(setup, field, value, code):
    claims, actor, board, _, natives, _ = setup
    board.tasks[0][field] = value
    result = run(claims.claim(actor, "p", "changed"))
    assert result["status"] == "WAIT"
    assert result["blockers"] == [{"epic_id": "E-34", "code": code}]
    assert not natives and not claims.store.get_runs()


@pytest.mark.parametrize("setup,expected", [("priority", "E-35"), ("tie", "E-34")],
                         indirect=["setup"])
def test_operator_priority_and_order_are_independent_of_board_order(setup, expected):
    claims, actor, board, _, _, _ = setup
    board.epics.reverse()
    board.tasks.reverse()
    result = run(claims.claim(actor, "p", "ordered"))
    assert result["epic_id"] == expected and result["status"] == "REGISTERED"
    overview = run(claims.overview(actor, "p"))
    assert overview["active_epics"] == [expected] and overview["candidates"] == []


@pytest.mark.parametrize("setup", ["dependency"], indirect=True)
def test_unfulfilled_epic_dependency_selects_independent_epic_with_recorded_reason(setup):
    claims, actor, _, _, _, _ = setup
    result = run(claims.claim(actor, "p", "dependency"))
    assert result["epic_id"] == "E-34"
    assert result["selection_blockers"] == [
        {"epic_id": "E-35", "code": "EPIC_DEPENDENCY_NOT_DONE"},
    ]


@pytest.mark.parametrize("setup", ["done-without-proof"], indirect=True)
def test_board_done_alone_does_not_satisfy_main_dependency(setup):
    claims, actor, board, _, natives, _ = setup
    board.epics[0]["status"] = "Done"
    for row in board.tasks:
        if row["epicId"] == E:
            row["status"] = "Done"
    result = run(claims.claim(actor, "p", "fake-done"))
    assert result["status"] == "WAIT" and result["reason"] == "NO_RUNNABLE_EPIC"
    assert result["blockers"] == [
        {"epic_id": "E-35", "code": "EPIC_DEPENDENCY_RUN_UNVERIFIED"},
    ]
    assert not natives and not claims.store.get_runs()


def test_empty_candidate_list_waits_without_artificial_done(setup):
    claims, actor, board, _, natives, _ = setup
    board.epics[0]["status"] = "Done"
    for row in board.tasks:
        row["status"] = "Done"
    result = run(claims.claim(actor, "p", "empty"))
    assert result == dict(status="WAIT", reason="NO_RUNNABLE_EPIC", blockers=[])
    assert not natives and not claims.store.get_runs()


def test_unlisted_board_fixtures_are_never_selected(setup):
    claims, actor, board, _, _, _ = setup
    board.epics.insert(0, epic() | {"id": OTHER, "name": "Legacy high-priority fixture"})
    result = run(claims.claim(actor, "p", "whitelist"))
    assert result["native_epic_id"] == E


def test_registered_claim_rechecks_owner_but_allows_tasks_to_progress(setup):
    claims, actor, board, _, natives, _ = setup
    first = run(claims.claim(actor, "p", "owned"))
    board.tasks[0]["status"] = "InProgress"
    assert run(claims.claim(actor, "p", "owned")) == first
    board.tasks[0]["responsibleUserId"] = OTHER
    with pytest.raises(CoordinatorError):
        run(claims.claim(actor, "p", "owned"))
    with pytest.raises(CoordinatorError, match="OWNER_CHANGED"):
        run(claims.claim(actor, "p", "new-key"))
    assert len(natives) == 1


def test_fresh_write_and_identity_required_before_reservation(setup):
    claims, actor, board, _, natives, _ = setup
    board.projects[0]["access"] = "Read"
    with pytest.raises(CoordinatorError, match="WRITE_DENIED"):
        run(claims.claim(actor, "p", "read-only"))
    board.projects[0]["access"] = "Write"
    board.me = {"userId": OTHER}
    with pytest.raises(Exception, match="IDENTITY_MISMATCH"):
        run(claims.claim(actor, "p", "foreign-user"))
    assert not natives and not claims.store.get_runs()


def test_unknown_child_start_keeps_reservation_and_same_run_without_duplicate(setup):
    claims, actor, _, factory, natives, _ = setup
    calls = 0

    def failing_factory(configured, run_id):
        nonlocal calls
        service = factory(configured, run_id)
        calls += 1
        original = service.start

        async def lost_result(*args):
            result = await original(*args)
            if calls == 1:
                raise RuntimeError("injected lost parent result after actual child registration")
            return result

        service.start = lost_result
        return service

    claims.factory = failing_factory
    with pytest.raises(CoordinatorError, match="START_UNVERIFIED"):
        run(claims.claim(actor, "p", "partial"))
    pending = claims.store.get_project_operation("p", claims.KIND, "partial")
    assert pending.status == "PENDING" and pending.result["stage"] == "RESERVED"
    repeated = run(claims.claim(actor, "p", "different"))
    assert repeated["epic_run_id"] == pending.result["epic_run_id"]
    result = run(claims.claim(actor, "p", "partial"))
    assert result["status"] == "REGISTERED"
    assert result["epic_run_id"] == pending.result["epic_run_id"]
    assert next(iter(natives.values())).starts == 1


def test_concurrent_claims_across_connections_start_at_most_one(setup):
    claims, actor, _, factory, natives, _ = setup
    barrier = Barrier(2)

    def claim(key):
        with StateStore(claims.settings.sqlite_path) as db:
            runtime = CoordinatorRuntimeService(
                claims.settings, db, claims.runtime.herdr, claims.spec, actor,
                claims.runtime.run_id, codex=claims.runtime.codex,
                processes=claims.runtime.processes,
            )
            scoped = CoordinatorClaimService(
                runtime, claims.reader, lambda c, r: factory(c, r, store=db), runtime_gate=gate,
            )
            barrier.wait(timeout=10)
            try:
                return run(scoped.claim(actor, "p", key))
            except CoordinatorError as error:
                assert str(error) == "COORDINATOR_BUSY"
                return {"status": "BUSY"}

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [f.result(timeout=30) for f in [
            executor.submit(claim, "a"), executor.submit(claim, "b"),
        ]]
    assert sum(r["status"] == "REGISTERED" for r in results) == 1
    assert len(claims.store.project_operations(claims.runtime.run_id, kind=claims.KIND)) == 1
    assert len(natives) == 1 and next(iter(natives.values())).starts == 1


def test_mcp_only_project_operations_no_role_spec_command_or_fallthrough(setup):
    claims, actor, _, _, natives, _ = setup
    service = RuntimeService(
        claims.store, actor, EventLog(io.StringIO()),
        coordinator_control=CoordinatorControlService(claims),
    )

    async def exercise():
        async with Client(create_server(service)) as client:
            listed = (await client.list_tools()).tools
            assert {t.name for t in listed} == {"coordinator_overview", "epic_claim_next"}
            result = await client.call_tool("coordinator_overview", {"project_id": "p"})
            assert result.structured_content["data"]["candidates"] == ["E-34"]
            for extra in ({"role": "Coordinator"}, {"spec": {}}, {"command": ["git"]}):
                rejected = await client.call_tool("epic_claim_next", dict(
                    project_id="p", request_key="invalid", **extra,
                ))
                assert rejected.is_error
            for name in ("epic_start", "task_start", "set_task_status", "task_merge"):
                denied = await service.call_async(name, {})
                assert denied.code == "UNKNOWN_OPERATION"
            denied = await client.call_tool("epic_claim_next", {
                "project_id": "foreign", "request_key": "denied",
            })
            assert denied.is_error

    run(exercise())
    assert not natives and not claims.store.get_runs()


@pytest.mark.parametrize("role", [Role.INTEGRATION, Role.WORKER, None])
def test_foreign_or_unregistered_connections_cannot_use_project_control(setup, role):
    claims, actor, _, _, natives, _ = setup
    principal = actor.model_copy(update={"role": role}) if role else None
    runtime = RuntimeService(
        claims.store, principal, EventLog(io.StringIO()),
        coordinator_control=CoordinatorControlService(claims),
    )
    response = run(runtime.call_async("epic_claim_next", {
        "project_id": "p", "request_key": "foreign-principal",
    }))
    assert not response.ok and response.code == "COORDINATOR_SCOPE_DENIED"
    assert not natives and not claims.store.get_runs()


@pytest.mark.parametrize("setup", ["priority"], indirect=True)
def test_foreign_active_board_epic_prevents_a_second_start(setup):
    claims, actor, board, _, natives, _ = setup
    board.epics[0]["status"] = "InProgress"
    result = run(claims.claim(actor, "p", "foreign-active"))
    assert result["status"] == "WAIT", result
    assert not natives and claims.store.get_runs() == []
    assert run(claims.overview(actor, "p"))["candidates"] == []
    assert claims.store.project_operations(claims.runtime.run_id, kind=claims.KIND) == []


@pytest.mark.parametrize("state", [EpicState.PLANNED, EpicState.ACTIVE, EpicState.DONE])
def test_unreserved_registered_epic_prevents_another_start(setup, state):
    claims, actor, _, _, natives, _ = setup
    existing = EpicRun(
        id="foreign-epic-run", project_id="p", epic_id="unowned-epic",
        branch="feature/epic-unowned", worktree_path="/tmp/unowned",
        status=state,
    )
    claims.store.add_epic(existing)
    result = run(claims.claim(actor, "p", "foreign-runtime"))
    assert result["status"] == "WAIT", result
    assert not natives and claims.store.get_runs() == [existing]
    assert run(claims.overview(actor, "p"))["candidates"] == []


@pytest.mark.parametrize("setup", ["priority"], indirect=True)
def test_owned_retry_does_not_bypass_a_second_unowned_active_epic(setup):
    claims, actor, board, _, natives, _ = setup
    first = run(claims.claim(actor, "p", "first-owned"))
    assert first["epic_id"] == "E-35" and first["status"] == "REGISTERED"
    original = claims.store.get_project_operation("p", claims.KIND, "first-owned")
    board.epics[0]["status"] = "InProgress"
    for key in ("first-owned", "different-key"):
        assert run(claims.claim(actor, "p", key))["status"] == "WAIT"
    assert len(natives) == 1 and next(iter(natives.values())).starts == 1
    assert claims.store.get_project_operation("p", claims.KIND, "first-owned") == original
    assert len(claims.store.project_operations(claims.runtime.run_id, kind=claims.KIND)) == 1


@pytest.mark.parametrize("setup", ["priority"], indirect=True)
@pytest.mark.parametrize("status", ["InProgress", "Testing", "Paused", "NeedsReview", "Done"])
def test_pending_epic_with_unowned_started_task_prevents_second_start(setup, status):
    claims, actor, board, _, natives, _ = setup
    board.tasks[0]["status"] = status
    result = run(claims.claim(actor, "p", "unowned-task"))
    assert result["status"] == "WAIT", result
    assert not natives and claims.store.get_runs() == []
    assert run(claims.overview(actor, "p"))["candidates"] == []
