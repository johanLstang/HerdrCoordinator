"""Reject misleading native acceptance artifacts; these checks do not run Herdr."""

import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "f33_timeline", ROOT / "scripts/probes/f33_timeline.py"
)
timeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timeline)


def evidence():
    return json.loads((ROOT / "docs/attention/F-33-prover.json").read_text())


def test_complete_recorded_native_scenario_has_full_capacity_and_same_session_resume():
    result = timeline.validate_proof(evidence())
    assert result["status"] == "F33_NATIVE_PASS"
    assert result["maximum_reserved"] == 2
    assert result["BC_working_overlap_samples"] > 0
    assert result["acceptance"] == ["F-33.A1", "F-33.A2", "F-33.A3"]


def test_exact_known_development_text_change_does_not_hide_another_mutation():
    row = {
        "id": timeline.DEVELOPMENT_TASK,
        "description": "before",
        "version": 3,
        "status": "InProgress",
        "owner": "verified-user",
    }
    before = {"tasks": [row], "epics": [{"id": "other", "status": "InProgress"}]}
    after = deepcopy(before)
    after["tasks"][0].update(description="after", version=4)
    fixture = {"tasks": {"A": "fixture-A"}, "epicId": "fixture-epic"}
    expected = {
        "task_id": row["id"],
        "before_version": 3,
        "after_version": 4,
        "before_description_sha256": hashlib.sha256(b"before").hexdigest(),
        "after_description_sha256": hashlib.sha256(b"after").hexdigest(),
    }
    assert timeline.check_board(before, after, fixture, expected) == [expected]
    for change in (
        {"owner": "foreign"},
        {"status": "Done"},
        {"version": 5},
        {"description": "different"},
        {"unexpected": "new field"},
    ):
        invalid = deepcopy(after)
        invalid["tasks"][0].update(change)
        with pytest.raises(ValueError):
            timeline.check_board(before, invalid, fixture, expected)
    with pytest.raises(ValueError):
        timeline.check_board(before, after, fixture)
    after["epics"][0]["status"] = "Done"
    with pytest.raises(ValueError):
        timeline.check_board(before, after, fixture, expected)


def test_unrelated_task_creation_or_removal_is_not_an_expected_text_change():
    before = {"tasks": [{"id": "other"}], "epics": []}
    fixture = {"tasks": {"A": "fixture-A"}, "epicId": "fixture-epic"}
    for rows in ([], [{"id": "other"}, {"id": "new-task"}]):
        with pytest.raises(ValueError):
            timeline.check_board(before, {"tasks": rows, "epics": []}, fixture)


@pytest.mark.parametrize(
    "attack",
    [
        "unconfirmed-park",
        "missing-park-event",
        "early-c",
        "early-resume",
        "not-full-capacity",
        "third-slot",
        "bool-slot",
        "duplicate-slot",
        "new-session",
        "changed-worktree",
        "changed-input",
        "missing-ack",
        "stale-review",
        "wrong-parent",
        "failed-aftertest",
        "wrong-aftertest-sha",
        "stop-unconfirmed",
        "native-not-done",
        "native-owner-changed",
        "missing-worktree",
        "BC-delivery-after-resume",
        "third-native",
        "unknown-native",
        "same-native-process",
        "no-BC-overlap",
        "duplicate-start",
        "replay-extra-effect",
        "missing-final-task",
        "epic-done-too-early",
        "old-resources-lost",
    ],
)
def test_incomplete_or_contradictory_native_proof_cannot_claim_acceptance(attack):
    p = deepcopy(evidence())
    waiting = next(c for c in p["checkpoints"] if c["label"] == "input-waiting")
    d = p["deliveries"]["C"]
    if attack == "unconfirmed-park":
        p["park_stop"]["result"]["inactive"] = False
    elif attack == "missing-park-event":
        p["events"] = [e for e in p["events"] if not e["id"].startswith("runtime-park:")]
    elif attack == "early-c":
        park = next(i for i, e in enumerate(p["events"]) if e["id"].startswith("runtime-park:"))
        claim = next(
            i
            for i, e in enumerate(p["events"])
            if e["result"].get("task_id") == "C" and e["result"].get("internal_status") == "CLAIMED"
        )
        p["events"].insert(park, p["events"].pop(claim))
    elif attack == "early-resume":
        p["resume"]["created_at"] = "2026-01-01T00:00:00Z"
    elif attack == "not-full-capacity":
        waiting["tasks"]["C"]["worker_slot"] = None
    elif attack in {"third-slot", "bool-slot", "duplicate-slot"}:
        waiting["tasks"]["C"]["worker_slot"] = {
            "third-slot": 3,
            "bool-slot": True,
            "duplicate-slot": waiting["tasks"]["B"]["worker_slot"],
        }[attack]
    elif attack == "new-session":
        p["resume"]["result"]["session_id"] = "replacement-session"
    elif attack == "changed-worktree":
        waiting["tasks"]["A"]["worktree_path"] = "/replacement"
    elif attack == "changed-input":
        p["input"]["result"]["decision"]["blocker_id"] = "another-blocker"
    elif attack == "missing-ack":
        del p["input"]["result"]["ack"]
    elif attack == "stale-review":
        d["approval"]["result"]["epic_commit"] = "stale-base"
    elif attack == "wrong-parent":
        d["merge_parents"].reverse()
    elif attack == "failed-aftertest":
        d["test"]["result"]["exit_code"] = 1
    elif attack == "wrong-aftertest-sha":
        d["test"]["result"]["merge_commit"] = "different-merge"
    elif attack == "stop-unconfirmed":
        d["stop"]["result"]["inactive"] = False
    elif attack == "native-not-done":
        p["final"]["native"]["C"]["status"] = "InProgress"
    elif attack == "native-owner-changed":
        p["final"]["native"]["C"]["responsibleUserId"] = "another-user"
    elif attack == "missing-worktree":
        d["worktree_preserved"] = False
    elif attack == "BC-delivery-after-resume":
        checkpoint = next(c for c in p["checkpoints"] if c["label"] == "after-delivery-C")
        checkpoint["tasks"]["A"]["internal_status"] = "WORKING"
    elif attack in {"third-native", "unknown-native", "same-native-process"}:
        sample = next(
            s for s in p["observations"] if len([t for t in s["tasks"] if t.get("processes")]) == 2
        )
        if attack == "third-native":
            sample["native_inventory"].append({"name": "third"})
        elif attack == "unknown-native":
            sample["unregistered_agents"] = ["foreign"]
        else:
            native = [t for t in sample["tasks"] if t.get("processes")]
            native[1]["processes"] = native[0]["processes"]
    elif attack == "no-BC-overlap":
        for sample in p["observations"]:
            for t in sample["tasks"]:
                t["working_bracket"] = False
    elif attack == "duplicate-start":
        p["final"]["counts"]["start_runtime"] += 1
        p["replay"]["counts"]["start_runtime"] += 1
    elif attack == "replay-extra-effect":
        p["replay"]["counts"]["task_merge"] += 1
    elif attack == "missing-final-task":
        del p["final"]["tasks"]["C"]
    elif attack == "epic-done-too-early":
        p["native_epic_status"] = "Done"
    else:
        p["old_refs_and_worktrees_preserved"] = False
    with pytest.raises(ValueError):
        timeline.validate_proof(p)
