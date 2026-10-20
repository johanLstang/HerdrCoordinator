"""Real Git/SQLite and exact protocol; controlled native facts, no OS attestation."""

import asyncio
import json

import pytest
from test_epic_start import setup as epic_setup  # noqa: F401

from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.epic_start_service import EpicStartError
from orchestrator.application.integration_activation_service import (
    IntegrationActivationService,
)
from orchestrator.domain.policy import Actor, Role


@pytest.fixture
def activation(epic_setup):  # noqa: F811
    parent, root, _, native = epic_setup
    parent = type(parent)(parent.settings, parent.store, native, parent.spec,
                          parent.principal, native, activate_registered=True)
    original = native.prompt
    state = dict(calls=0, loss=None)

    def prompt(name, text, *, timeout_ms):
        value = json.loads(text)
        if value.get("type") != "HERDR_INTEGRATION_ACTIVATE":
            return original(name, text, timeout_ms=timeout_ms)
        assert name in native.agents and timeout_ms == 45000
        state["calls"] += 1
        if state["loss"] != "before":
            native.turns.append(dict(id="activation-turn", status="completed", items=[
                dict(id="activation-user", type="userMessage",
                     content=[dict(type="text", text=text)]),
            ]))
        if state["loss"]:
            raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    native.prompt = prompt
    service = IntegrationActivationService(parent)
    return parent, root, native, service, state


def registered(f):
    parent, root, _, _, _ = f
    # Isolate dispatch tests from the public automatic start hookup.
    parent.activate_registered = False
    assert asyncio.run(parent.start(root, "p", "E-34", "e"))["stage"] == "REGISTERED"
    parent.activate_registered = True


def dispatch(f, actor=None):
    parent, root, _, service, _ = f
    with parent._lock():
        return service.dispatch_under_start_lock(actor or root)


def test_exact_registered_delivery_and_replay_do_not_schedule_or_duplicate(activation):
    p, _, native, service, state = activation
    registered(activation)
    result = dispatch(activation)
    assert result["status"] == "DELIVERED" and state["calls"] == native.starts == 1
    assert p.store.get_tasks("e") == [] and p.store.get_epic("e").completed_at is None
    op = p.store.get_operation("p", service.KIND, "e")
    assert op.status == "SUCCEEDED" and op.result["delivery"]["item_id"] == "activation-user"
    changes = p.store.db.total_changes
    assert dispatch(activation) == result
    assert p.store.db.total_changes == changes and state["calls"] == 1


@pytest.mark.parametrize("loss", ["before", "after"])
def test_unknown_transport_reconciles_actual_message_without_resend(activation, loss):
    p, _, native, service, state = activation
    registered(activation)
    state["loss"] = loss
    result = dispatch(activation)
    op = p.store.get_operation("p", service.KIND, "e")
    assert op.result["transport"] == "UNKNOWN" and state["calls"] == 1
    if loss == "before":
        assert result["status"] == "WAITING" and op.status == "PENDING"
        assert dispatch(activation)["status"] == "WAITING" and state["calls"] == 1
        native.turns.append(dict(id="delayed", status="completed", items=[dict(
            id="actual-delayed-user", type="userMessage",
            content=[dict(type="text", text=op.result["message"])],
        )]))
    assert dispatch(activation)["status"] == "DELIVERED"
    assert state["calls"] == native.starts == 1 and not p.store.get_tasks("e")


def test_registration_is_required_before_any_activation_intent(activation):
    p, _, _, service, state = activation
    changes = p.store.db.total_changes
    with pytest.raises(EpicStartError, match="REQUIRES_REGISTERED_ACK"):
        dispatch(activation)
    assert p.store.db.total_changes == changes and state["calls"] == 0
    assert p.store.get_operation("p", service.KIND, "e") is None


@pytest.mark.parametrize("kind", ["role", "scope", "disabled", "configuration", "generation"])
def test_wrong_role_scope_or_changed_configuration_has_no_activation_effect(activation, kind):
    p, root, _, _, state = activation
    registered(activation)
    actor = root
    if kind == "role":
        actor = p.principal
    elif kind == "scope":
        actor = Actor(actor_id="foreign", role=Role.COORDINATOR, project_id="elsewhere")
    elif kind == "disabled":
        p.activate_registered = False
    elif kind == "configuration":
        p.configuration["activation_policy_hash"] = "0" * 64
    else:
        state["loss"] = "before"
        assert dispatch(activation)["status"] == "WAITING"
        start = p.store.get_operation("p", "start_runtime", "e")
        p.store.update_operation(start.model_copy(update={
            "result": start.result | dict(generation="different-actual-generation"),
        }))
    calls, changes = state["calls"], p.store.db.total_changes
    with pytest.raises(EpicStartError):
        dispatch(activation, actor)
    assert state["calls"] == calls and p.store.db.total_changes == changes
    assert not p.store.get_tasks("e")


def test_explicit_start_automatically_dispatches_once_after_registration(activation):
    p, root, native, _, state = activation
    result = asyncio.run(p.start(root, "p", "E-34", "e"))
    assert result["stage"] == "REGISTERED" and result["activation"]["status"] == "DELIVERED"
    assert state["calls"] == native.starts == 1
    assert asyncio.run(p.start(root, "p", "E-34", "e")) == result
    assert state["calls"] == 1 and not p.store.get_tasks("e")


def test_start_keeps_claim_pending_until_actual_delivery_and_never_resends(activation):
    p, root, native, service, state = activation
    state["loss"] = "before"
    first = asyncio.run(p.start(root, "p", "E-34", "e"))
    assert first["stage"] == "ACTIVATION_PENDING" and state["calls"] == 1
    assert p.store.get_operation("p", p.KIND, "e").result["stage"] == "REGISTERED"
    assert asyncio.run(p.start(root, "p", "E-34", "e")) == first
    saved = p.store.get_operation("p", service.KIND, "e")
    native.turns.append(dict(id="actual-delayed", status="inProgress", items=[dict(
        id="delivered-message", type="userMessage",
        content=[dict(type="text", text=saved.result["message"])],
    )]))
    final = asyncio.run(p.start(root, "p", "E-34", "e"))
    assert final["stage"] == "REGISTERED" and final["activation"]["status"] == "DELIVERED"
    assert state["calls"] == native.starts == 1


def test_delayed_on_time_ack_registers_then_activates_original_session(activation, monkeypatch):
    from datetime import datetime, timedelta

    p, root, native, _, state = activation
    native.ack = False
    assert asyncio.run(p.start(root, "p", "E-34", "e"))["stage"] == "DISPATCH_REQUESTED"
    op = p.store.get_operation("p", p.KIND, "e")
    deadline = datetime.fromisoformat(op.result["deadline"])
    monkeypatch.setattr("orchestrator.application.epic_start_service.utc_now",
                        lambda: deadline + timedelta(seconds=2))
    with pytest.raises(EpicStartError, match="ACK_TIMEOUT"):
        asyncio.run(p.start(root, "p", "E-34", "e"))
    item = native.turns[0]["items"][1]
    item["text"] = json.dumps(op.result["expected_ack"])
    item["nativeObservedAt"] = (op.created_at + timedelta(seconds=1)).isoformat()
    result = asyncio.run(p.start(root, "p", "E-34", "e"))
    assert result["stage"] == "REGISTERED" and result["activation"]["status"] == "DELIVERED"
    assert state["calls"] == native.starts == 1
    assert p.store.get_operation("p", p.KIND, "e").result["deadline"] == op.result["deadline"]
    assert asyncio.run(p.start(root, "p", "E-34", "e")) == result
    assert state["calls"] == 1 and not p.store.get_tasks("e")
