"""Reject misleading proof artifacts; native observations are collected separately."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "f30_probe", Path(__file__).resolve().parents[1] / "scripts/probes/f30_native.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def evidence():
    def event(task, state, slot):
        return {"result": {"task_id": task, "internal_status": state, "worker_slot": slot}}

    events = [
        event("A", "CLAIMED", 1),
        event("B", "CLAIMED", 2),
        event("A", "DONE", None),
        event("C", "CLAIMED", 1),
        event("B", "DONE", None),
        event("C", "DONE", None),
    ]
    samples = [
        {
            "reserved": 2,
            "native_inventory": [{"name": "a"}, {"name": "b"}],
            "tasks": [
                {
                    "task_id": t,
                    "native_status": "working",
                    "working_bracket": True,
                    "worktree": "/fixture/" + t,
                    "session": t,
                    "native_session": t,
                    "processes": [{"pid": n, "start_time": "fixture"}],
                }
                for n, t in enumerate("AB", start=100)
            ],
        }
    ]
    return events, samples


def test_complete_serial_native_timeline_has_two_slots_and_a_b_overlap():
    events, samples = evidence()
    assert probe.validate_timeline(events, samples)["maximum_reserved"] == 2


@pytest.mark.parametrize(
    "dialog",
    [
        "Update available! Ask Codex to do anything",
        "Updating Codex via npm install -g @openai/codex",
        "Trust and continue. Ask Codex to do anything",
        "Would you like to approve this? Ask Codex to do anything",
        "Shell prompt only",
    ],
)
def test_assignment_transport_rejects_dialog_even_with_main_prompt(dialog):
    assert probe.startup_ui_action(dialog) == "operator-required"


def test_known_voluntary_banner_may_be_dismissed_but_is_not_ready_for_assignment():
    assert (
        probe.startup_ui_action(
            "Set up security for Daybreak mode. esc to dismiss. Ask Codex to do anything"
        )
        == "dismiss-voluntary-banner"
    )
    assert probe.startup_ui_action("Ask Codex to do anything") == "ready"


@pytest.mark.parametrize(
    "attack",
    [
        "early-c",
        "slot-three",
        "duplicate-slot",
        "bool-slot",
        "same-worktree",
        "same-process",
        "no-bracket",
        "unknown-agent",
        "third-native",
        "not-done",
        "no-native-overlap",
    ],
)
def test_incomplete_or_unsafe_timeline_cannot_claim_f30_acceptance(attack):
    events, samples = deepcopy(evidence())
    if attack == "early-c":
        events[2], events[3] = events[3], events[2]
    elif attack in {"slot-three", "duplicate-slot", "bool-slot"}:
        events[1]["result"]["worker_slot"] = {
            "slot-three": 3,
            "duplicate-slot": 1,
            "bool-slot": True,
        }[attack]
    elif attack == "same-worktree":
        samples[0]["tasks"][1]["worktree"] = samples[0]["tasks"][0]["worktree"]
    elif attack == "same-process":
        samples[0]["tasks"][1]["processes"] = samples[0]["tasks"][0]["processes"]
    elif attack == "no-bracket":
        samples[0]["tasks"][0]["working_bracket"] = False
    elif attack == "unknown-agent":
        samples[0]["unregistered_agents"] = ["outside"]
    elif attack == "third-native":
        samples[0]["native_inventory"].append({"name": "third"})
    elif attack == "not-done":
        events.pop()
    else:
        samples[0]["tasks"][1]["native_status"] = "idle"
    with pytest.raises(ValueError):
        probe.validate_timeline(events, samples)
