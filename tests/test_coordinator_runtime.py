"""Real Git/SQLite, controlled Herdr/Codex/process adapter; not native isolation proof."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from test_epic_start import SID, spec
from test_epic_start import setup as epic_setup  # noqa: F401
from test_runtime_lifecycle import FakeProcesses

from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.coordinator_runtime_service import (
    CoordinatorError,
    CoordinatorRuntimeService,
)
from orchestrator.domain.models import utc_now
from orchestrator.domain.policy import Role
from orchestrator.domain.worker_contracts import canonical_json
from orchestrator.persistence.store import StateStore

PROJECT = "00000000-0000-4000-8000-000000000038"
EPIC = "00000000-0000-4000-8000-000000000138"
TASK1 = "00000000-0000-4000-8000-000000000238"
TASK2 = "00000000-0000-4000-8000-000000000338"


def plan():
    return dict(
        version=1, project_id="p", native_project_id=PROJECT, title="Fixture project",
        sources=["README.md"], epics=[dict(
            version=1, native_epic_id=EPIC, native_task_ids=[TASK1, TASK2], priority="P0",
            spec=spec() | {"dependencies": [], "external_prerequisites": []},
        )],
    )


@pytest.fixture
def setup(epic_setup):  # noqa: F811
    epic, coordinator, _, herdr = epic_setup
    processes = FakeProcesses()
    herdr.missing_ack, herdr.late_ack = False, False
    herdr.interrupts, herdr.exits = 0, 0

    def prompt(name, text, *, timeout_ms):
        assert timeout_ms == 45000 and name in herdr.agents
        herdr.prompts.append(text)
        herdr.sid = SID
        confirmation = json.loads(text)["confirmation"]
        observed = utc_now() + timedelta(seconds=100 if herdr.late_ack else 0)
        herdr.turns.append(dict(id="turn-1", status="completed", items=[
            dict(id="u1", type="userMessage", content=[dict(type="text", text=text)]),
            dict(id="a1", type="agentMessage", nativeObservedAt=observed.isoformat(),
                 text=canonical_json(confirmation if not herdr.missing_ack else {"wrong": True})),
        ]))
        if herdr.unknown:
            raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    def interrupt(name):
        assert name in herdr.agents
        herdr.interrupts += 1

    def exit_agent(name):
        del herdr.agents[name]
        processes.alive = False
        herdr.exits += 1

    herdr.prompt, herdr.interrupt, herdr.exit_agent = prompt, interrupt, exit_agent
    runtime = CoordinatorRuntimeService(
        epic.settings, epic.store, herdr, plan(), coordinator, "coordinator-r1",
        codex=herdr, processes=processes,
    )
    yield runtime, coordinator, herdr, processes


def test_actual_project_registration_no_epic_or_slot_and_reopen(setup):
    service, actor, herdr, _ = setup
    main = service.main()
    run = service.start(actor)
    assert run.status == "ACTIVE" and run.codex_session_id == SID
    assert run.initial_commit == main and run.worktree_path == str(service.settings.repository)
    assert service.store.get_runs() == []
    assert service.start(actor) == run
    assert service.connection_principal(actor) == actor
    with StateStore(service.settings.sqlite_path) as db:
        reopened = CoordinatorRuntimeService(
            service.settings, db, herdr, plan(), actor, run.id,
            codex=herdr, processes=service.processes,
        )
        assert reopened.start(actor) == run
    assert herdr.starts == len(herdr.workspaces) == len(herdr.prompts) == 1


@pytest.mark.parametrize("changes", [
    {"actor_id": "foreign"}, {"role": Role.INTEGRATION}, {"project_id": "foreign"},
    {"epic_run_id": "invented"}, {"task_run_id": "invented"},
])
def test_wrong_connection_rejected_before_effects(setup, changes):
    service, actor, herdr, _ = setup
    with pytest.raises(CoordinatorError, match="SCOPE_DENIED"):
        service.start(actor.model_copy(update=changes))
    assert herdr.starts == 0 and service.store.project_coordinators("p") == []


def test_lost_prompt_response_reconciles_native_ack_without_resend(setup):
    service, actor, herdr, _ = setup
    herdr.unknown = True
    first = service.start(actor)
    assert first.status == "ACTIVE"
    assert service.start(actor) == first
    assert len(herdr.prompts) == 1


@pytest.mark.parametrize("flag", ["missing_ack", "late_ack"])
def test_unverified_or_late_ack_stays_pending_without_prompt_replay(setup, flag):
    service, actor, herdr, _ = setup
    setattr(herdr, flag, True)
    for _ in range(2):
        with pytest.raises(CoordinatorError, match="ACK_UNVERIFIED"):
            service.start(actor)
    assert service.store.get_coordinator(service.run_id).status == "STARTING"
    assert len(herdr.prompts) == 1
    with pytest.raises(CoordinatorError, match="NOT_REGISTERED"):
        service.connection_principal(actor)


def test_unknown_workspace_is_not_recreated_or_adopted(setup):
    service, actor, herdr, _ = setup
    herdr.fail_create = True
    with pytest.raises(HerdrError):
        service.start(actor)
    herdr.fail_create = False
    with pytest.raises(CoordinatorError, match="WORKSPACE_OUTCOME_UNKNOWN"):
        service.start(actor)
    assert len(herdr.workspaces) == 1 and herdr.starts == 0


def test_unknown_agent_start_is_reconciled_using_original_identity(setup):
    service, actor, herdr, _ = setup
    herdr.start_failure = "UNKNOWN_RUNTIME_OUTCOME"
    with pytest.raises(HerdrError):
        service.start(actor)
    herdr.start_failure = None
    assert service.start(actor).status == "ACTIVE"
    assert herdr.starts == 1


def test_changed_process_or_native_ack_revokes_connection(setup):
    service, actor, herdr, _ = setup
    service.start(actor)
    herdr.process_changed = True
    with pytest.raises(CoordinatorError, match="PROCESS_CHANGED"):
        service.connection_principal(actor)
    herdr.process_changed = False
    herdr.turns[0]["items"][1]["text"] = "{}"
    with pytest.raises(CoordinatorError, match="ACK_UNVERIFIED"):
        service.connection_principal(actor)


def test_stop_requires_real_inactivity_preserves_session_and_never_exits_twice(setup):
    service, actor, herdr, processes = setup
    initial = service.start(actor)
    processes.lingering = True
    with pytest.raises(CoordinatorError, match="STOP_UNCONFIRMED"):
        service.stop(actor, "stop")
    assert service.store.get_coordinator(service.run_id).status == "ACTIVE"
    processes.lingering = False
    stopped = service.stop(actor, "stop")
    assert stopped.status == "STOPPED" and stopped.codex_session_id == initial.codex_session_id
    assert service.stop(actor, "stop") == stopped
    assert herdr.exits == herdr.interrupts == 1
    with pytest.raises(CoordinatorError, match="START_INTENT_MISMATCH"):
        service.start(actor)


def test_duplicate_native_ack_is_not_registration(setup):
    service, actor, herdr, _ = setup
    service.start(actor)
    herdr.turns[0]["items"].append(deepcopy(herdr.turns[0]["items"][1]))
    with pytest.raises(CoordinatorError, match="ACK_UNVERIFIED"):
        service.connection_principal(actor)


def test_boolean_contract_version_and_mutated_operator_plan_are_rejected(setup):
    service, actor, herdr, _ = setup
    service.start(actor)
    ack = json.loads(herdr.turns[0]["items"][1]["text"])
    ack["version"] = True
    herdr.turns[0]["items"][1]["text"] = canonical_json(ack)
    with pytest.raises(CoordinatorError, match="ACK_UNVERIFIED"):
        service.connection_principal(actor)
    ack["version"] = 1
    herdr.turns[0]["items"][1]["text"] = canonical_json(ack)
    service.spec.epics[0].spec.dependencies.append("invented")
    with pytest.raises(CoordinatorError, match="CONFIGURATION_CHANGED"):
        service.connection_principal(actor)


def test_hidden_main_index_changes_reject_start_before_native_effects(setup):
    service, actor, herdr, _ = setup
    service.git.run("update-index", "--assume-unchanged", "README.md")
    with pytest.raises(CoordinatorError, match="MAIN_GIT_UNVERIFIED"):
        service.start(actor)
    assert not herdr.starts and not herdr.workspaces
