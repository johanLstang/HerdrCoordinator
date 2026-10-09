"""Check a recorded F33 scenario for missing or contradictory evidence.

This offline check does not authenticate JSON or replace actual service/Git/native
verification. The owning operator exports it only after those checks succeed.
"""

import hashlib
from datetime import datetime

DEVELOPMENT_TASK = "f7a0c038-4f49-48d0-ba09-fbca7bfe0e92"


def check_board(before, after, fixture, expected_text_change=None):
    """Only exact recorded F33 development text/version may differ outside fixture."""
    excluded = set(fixture["tasks"].values())
    old = {t["id"]: t for t in before["tasks"] if t["id"] not in excluded}
    new = {t["id"]: t for t in after["tasks"] if t["id"] not in excluded}
    need(old.keys() == new.keys(), "UNRELATED_TASK_CREATED_OR_LOST")
    changes = []
    for key in old:
        if old[key] == new[key]:
            continue
        need(
            expected_text_change is not None
            and key == DEVELOPMENT_TASK
            and expected_text_change["task_id"] == key,
            "UNRELATED_TASK_CHANGED",
        )
        a, b = old[key], new[key]
        need(
            a.keys() == b.keys()
            and {k for k in a if a[k] != b[k]} == {"description", "version"}
            and a["version"] == expected_text_change["before_version"]
            and b["version"] == expected_text_change["after_version"]
            and hashlib.sha256(a["description"].encode()).hexdigest()
            == expected_text_change["before_description_sha256"]
            and hashlib.sha256(b["description"].encode()).hexdigest()
            == expected_text_change["after_description_sha256"],
            "UNEXPECTED_DEVELOPMENT_CHANGE",
        )
        changes.append(expected_text_change)
    eid = fixture["epicId"]
    need(
        {e["id"]: e for e in before["epics"] if e["id"] != eid}
        == {e["id"]: e for e in after["epics"] if e["id"] != eid},
        "UNRELATED_EPIC_CHANGED",
    )
    return changes


def public_proof(proof):
    """Relevant actual fields only; full operator history remains in private artifacts."""
    from copy import deepcopy

    p = deepcopy(proof)
    fields = (
        "id",
        "project_id",
        "epic_run_id",
        "task_id",
        "branch",
        "worktree_path",
        "codex_session_id",
        "base_commit",
        "current_commit",
        "merge_commit",
        "internal_status",
        "worker_slot",
    )
    for checkpoint in [*p["checkpoints"], p["final"], p["replay"]]:
        checkpoint["tasks"] = {
            key: {k: t[k] for k in fields} for key, t in checkpoint["tasks"].items()
        }
    p["events"] = [
        {k: e[k] for k in ("id", "project_id", "epic_run_id", "task_run_id", "created_at")}
        | {
            "request": {"actor": e["request"]["actor"], "facts": e["request"]["facts"]},
            "result": {
                k: e["result"][k]
                for k in ("id", "task_id", "internal_status", "worker_slot")
                if k in e["result"]
            },
        }
        for e in p["events"]
    ]
    # Dispatch is represented by its hash/native ACK; this export is not an input request.
    p["input"]["result"].pop("prompt", None)
    p["projection"] = "Relevant actual service/native/Git fields; full history remains private."
    need(validate_proof(p) == validate_proof(proof), "PUBLIC_PROJECTION_CHANGED_EVIDENCE")
    return p


def need(value, code):
    if not value:
        raise ValueError(code)


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def succeeded(op):
    need(op["status"] == "SUCCEEDED" and op["error_code"] is None, "OPERATION_NOT_VERIFIED")


def validate_proof(proof):
    try:
        return _validate(proof)
    except (KeyError, TypeError, IndexError, StopIteration, AttributeError):
        raise ValueError("INCOMPLETE_PROOF") from None


def _validate(p):
    final, checkpoints = p["final"], p["checkpoints"]
    need(
        set(final["tasks"]) == set(p["deliveries"]) == set(p["fixture"]["tasks"]) == set("ABC"),
        "TASK_SCOPE",
    )
    checkpoint = {c["label"]: c for c in checkpoints}
    before, waiting, resume_before, active = (
        checkpoint[k] for k in ("input-before", "input-waiting", "before-resume", "after-resume")
    )
    a = waiting["tasks"]["A"]
    ids = {k: t["id"] for k, t in final["tasks"].items()}
    need(len(set(ids.values())) == 3, "DUPLICATE_RUN")
    need(len({t["worktree_path"] for t in final["tasks"].values()}) == 3, "DUPLICATE_WORKTREE")
    need(len({t["codex_session_id"] for t in final["tasks"].values()}) == 3, "DUPLICATE_SESSION")
    identity = ("id", "branch", "worktree_path", "codex_session_id", "base_commit")
    for c in [*checkpoints, final, p["replay"]]:
        need(set(c["tasks"]).issubset(set("ABC")), "FOREIGN_TASK")
        slots = []
        for key, t in c["tasks"].items():
            need(
                t["id"] == ids[key]
                and t["task_id"] == key
                and t["project_id"] == p["project_id"]
                and t["epic_run_id"] == p["epic_run_id"],
                "CHANGED_RUN",
            )
            row = c["native"][key]
            need(
                row["taskId"] == p["fixture"]["tasks"][key]
                and row["executionOwnerKind"] == "User"
                and row["responsibleUserId"] == p["user_id"],
                "NATIVE_IDENTITY_CHANGED",
            )
            slot = t["worker_slot"]
            need(slot is None or type(slot) is int and slot in {1, 2}, "INVALID_SLOT")
            if slot is not None:
                slots.append(slot)
        need(len(slots) == len(set(slots)) <= 2, "DUPLICATE_OR_THIRD_SLOT")
    for c in (before, waiting, resume_before):
        t = c["tasks"]["A"]
        need(
            t["internal_status"] == "PARKED"
            and t["worker_slot"] is None
            and c["native"]["A"]["status"] == "NeedsInput",
            "A_NOT_WAITING_ATTENTION",
        )
        need(all(t[k] == a[k] for k in identity), "CHANGED_PARKED_RESOURCES")
    need({waiting["tasks"][k]["worker_slot"] for k in "BC"} == {1, 2}, "INPUT_NOT_FULL_CAPACITY")
    need(
        waiting["counts"]["resume_runtime"] == before["counts"]["resume_runtime"] == 0
        and waiting["counts"]["task_resume"] == 1
        and waiting["counts"]["start_runtime"] == before["counts"]["start_runtime"] == 3,
        "EARLY_OR_DUPLICATE_RESUME",
    )
    attention, park, input_op, resume = (
        p[k] for k in ("attention", "park_stop", "input", "resume")
    )
    for op in (attention, park, input_op, resume):
        succeeded(op)
        need(
            op["project_id"] == p["project_id"]
            and op["epic_run_id"] == p["epic_run_id"]
            and op["task_run_id"] == a["id"],
            "OPERATION_SCOPE_CHANGED",
        )
    ar, ir, rr = attention["result"], input_op["result"], resume["result"]
    need(
        ar["stage"] == "PARKED_AND_SYNCED"
        and ar["stop_id"] == park["id"]
        and ar["details"]["reason"].strip()
        and ar["details"]["input_required"].strip(),
        "BLOCKER_NOT_COMPLETE",
    )
    need(
        park["result"]["stage"] == "STOPPED"
        and park["result"]["inactive"] is True
        and park["result"]["process_proof"]["identities"],
        "PARK_NOT_PHYSICALLY_CONFIRMED",
    )
    need(
        ir["decision"]["blocker_id"] == attention["id"]
        and ir["decision"]["input_id"] == input_op["idempotency_key"]
        and ir["decision"]["answer"].strip()
        and ir["stage"] == "ACTIVE_AND_SYNCED"
        and ir["stop_id"] == park["id"]
        and ir["resume_id"] == resume["id"],
        "INPUT_BINDING_CHANGED",
    )
    need(
        rr["stage"] == "RESUMED"
        and rr["input_operation_id"] == input_op["id"]
        and rr["stop_operation_id"] == park["id"]
        and rr["session_id"] == ir["ack"]["session_id"] == a["codex_session_id"]
        and rr["worker_slot"] in {1, 2}
        and type(rr["worker_slot"]) is int
        and rr["processes"]
        and ir["ack"]["correlation_id"] == ir["correlation_id"],
        "RESUME_NOT_BOUND_TO_SAME_SESSION",
    )
    need(
        ir["subject"] == ar["subject"]
        and ir["ack"]["turn_id"]
        and ir["ack"]["item_id"]
        and ir["ack"]["message_hash"],
        "INPUT_PROVENANCE_MISSING",
    )
    need(
        stamp(park["updated_at"])
        <= before["at"]
        <= stamp(input_op["created_at"])
        <= waiting["at"]
        < resume_before["at"]
        <= stamp(resume["created_at"])
        <= stamp(resume["updated_at"])
        <= active["at"],
        "INVALID_RUNTIME_ORDER",
    )
    need(
        all(active["tasks"]["A"][k] == a[k] for k in identity)
        and active["tasks"]["A"]["worker_slot"] == rr["worker_slot"]
        and active["tasks"]["A"]["internal_status"] == "WORKING"
        and active["native"]["A"]["status"] == "InProgress",
        "RESUME_NOT_CONFIRMED_ACTIVE",
    )
    events, event_ids = p["events"], set()
    last_at, states, held = 0, {}, {}
    park_event = ack_event = c_claim = None
    maximum = 0
    for e in events:
        need(
            e["id"] not in event_ids and stamp(e["created_at"]) >= last_at,
            "EVENT_ORDER_OR_DUPLICATE",
        )
        event_ids.add(e["id"])
        last_at = stamp(e["created_at"])
        t = e["result"]
        if "task_id" not in t:
            continue
        k = t["task_id"]
        need(k in ids and t["id"] == ids[k] and e["task_run_id"] == ids[k], "FOREIGN_EVENT")
        state, slot = t["internal_status"], t["worker_slot"]
        need(slot is None or type(slot) is int and slot in {1, 2}, "INVALID_EVENT_SLOT")
        if state == "PARKED":
            need(
                k == "A"
                and e["id"] == "runtime-park:" + park["id"]
                and e["request"]["facts"]["inactivity_confirmed"] is True,
                "EARLY_RELEASE",
            )
            park_event = e
            # Event snapshot precedes the slot metadata release in the same F13 transaction.
            held.pop(k, None)
        elif state == "DONE":
            need(slot is None, "DONE_STILL_RESERVED")
            held.pop(k, None)
        elif slot is not None:
            held[k] = slot
        states[k] = state
        need(len(held) == len(set(held.values())) <= 2, "UNSAFE_EVENT_RESERVATION")
        maximum = max(maximum, len(held))
        if k == "C" and state == "CLAIMED":
            c_claim = e
            need(
                park_event is not None
                and stamp(park["updated_at"]) <= last_at
                and states.get("A") == "PARKED"
                and t["worker_slot"] == ar["reserved_slot"],
                "C_STARTED_BEFORE_PARK_RELEASE",
            )
        if e["id"] == "input-ack:" + input_op["id"]:
            ack_event = e
            need(
                state == "WORKING"
                and slot == rr["worker_slot"]
                and stamp(resume["updated_at"]) <= last_at
                and e["request"]["facts"]["slot_reserved"] is True
                and e["request"]["facts"]["start_confirmed"] is True,
                "ACTIVE_BEFORE_RESUME_ACK",
            )
    need(
        park_event is not None
        and c_claim is not None
        and ack_event is not None
        and states == {k: "DONE" for k in "ABC"},
        "INCOMPLETE_STATE_TIMELINE",
    )
    # Both deliveries require fresh approval, exact no-ff parents and passing aftertests.
    target = None
    for key in "BCA":
        d, t = p["deliveries"][key], final["tasks"][key]
        op, test, stop, approval, context = (
            d[k] for k in ("operation", "test", "stop", "approval", "context")
        )
        for item in (op, test, stop, approval):
            succeeded(item)
            need(
                item["task_run_id"] == t["id"]
                and item["project_id"] == p["project_id"]
                and item["epic_run_id"] == p["epic_run_id"],
                "DELIVERY_SCOPE_CHANGED",
            )
        r, pr = op["result"], approval["result"]
        source, base, merge = r["source_commit"], r["target_commit"], r["merge_commit"]
        read = d["explicit_review"]
        need(
            read["decision"] == "APPROVED"
            and read["all_files_read"] is True
            and read["all_context_read"] is True
            and read["source"] == source
            and read["target"] == base
            and read["full_patch_bytes"] == context["diff_bytes"]
            and read["full_patch_sha256"] == context["diff_sha256"],
            "FULL_CURRENT_REVIEW_NOT_RECORDED",
        )
        need(
            r["stage"] == "DONE"
            and t["merge_commit"] == merge
            and d["merge_parents"] == [base, source]
            and context["task_commit"] == pr["task_commit"] == source
            and context["epic_commit"] == pr["epic_commit"] == base
            and context["context_id"] == pr["context_id"] == r["context_id"]
            and r["approval_id"] == approval["id"]
            and pr["verification_id"] == context["test_id"],
            "STALE_OR_UNVERIFIED_REVIEW_MERGE",
        )
        need(target is None or base == target, "NON_SERIAL_DELIVERY")
        target = merge
        need(
            test["id"] == r["test_id"]
            and test["result"]["delivery_id"] == op["id"]
            and test["result"]["merge_commit"] == merge
            and test["result"]["exit_code"] == 0
            and test["result"]["source_commit"] == source
            and test["result"]["target_commit"] == base,
            "AFTERTEST_NOT_VERIFIED",
        )
        need(
            stop["id"] == r["stop_id"]
            and stop["result"]["inactive"] is True
            and stop["result"]["stage"] == "STOPPED"
            and stop["result"]["process_proof"]["identities"]
            and stamp(test["updated_at"])
            <= stamp(stop["created_at"])
            <= stamp(stop["updated_at"])
            <= stamp(op["updated_at"]),
            "DONE_BEFORE_TEST_STOP",
        )
        need(
            t["internal_status"] == "DONE"
            and t["worker_slot"] is None
            and final["native"][key]["status"] == "Done"
            and d["worktree_preserved"] is True
            and d["physically_inactive"] is True,
            "FINAL_DONE_NOT_VERIFIED",
        )
        if key in "BC":
            for label in ("before-delivery-" + key, "after-delivery-" + key):
                c = checkpoint[label]
                need(
                    c["tasks"]["A"]["internal_status"] == "PARKED"
                    and c["tasks"]["A"]["worker_slot"] is None
                    and c["native"]["A"]["status"] == "NeedsInput"
                    and c["counts"]["resume_runtime"] == 0,
                    "A_RESUMED_BEFORE_BC_DELIVERY",
                )
            need(stamp(op["updated_at"]) <= resume_before["at"], "BC_DELIVERED_AFTER_RESUME")
    good, overlap, unavailable = 0, 0, 0
    for sample in p["observations"]:
        if sample.get("error"):
            unavailable += 1
            continue
        good += 1
        need(
            type(sample["reserved"]) is int
            and 0 <= sample["reserved"] <= 2
            and len(sample["native_inventory"]) <= 2
            and not sample.get("unregistered_agents"),
            "THIRD_OR_UNKNOWN_NATIVE_WORKER",
        )
        native = [t for t in sample["tasks"] if t.get("processes")]
        processes = []
        for t in native:
            need(
                t["task_id"] in ids
                and t["task_run_id"] == ids[t["task_id"]]
                and t["session"]
                == t["native_session"]
                == final["tasks"][t["task_id"]]["codex_session_id"]
                and t["worktree"] == final["tasks"][t["task_id"]]["worktree_path"],
                "NATIVE_RESOURCES_CHANGED",
            )
            processes.extend((r["pid"], r["start_time"]) for r in t["processes"])
        need(len(processes) == len(set(processes)), "DUPLICATE_NATIVE_PROCESS")
        bc = [
            t
            for t in native
            if t["task_id"] in "BC"
            and t.get("native_status") == "working"
            and t.get("working_bracket") is True
        ]
        a_sample = next((t for t in sample["tasks"] if t["task_id"] == "A"), None)
        if (
            len(bc) == 2
            and a_sample is not None
            and a_sample["state"] == "PARKED"
            and a_sample["slot"] is None
            and not a_sample.get("processes")
        ):
            overlap += 1
    need(good > 0 and overlap > 0, "NO_NATIVE_BC_CONTINUATION_OVERLAP")
    need(
        final["counts"] == p["replay"]["counts"]
        and final["counts"]["start_runtime"] == final["counts"]["dispatch_assignment"] == 3
        and final["counts"]["task_resume"]
        == final["counts"]["resume_runtime"]
        == final["counts"]["task_attention"]
        == 1
        and final["counts"]["task_merge"] == 3
        and final["counts"]["stop_runtime"] == 4,
        "DUPLICATE_OR_MISSING_EFFECT",
    )
    active_replay = checkpoint["active-input-replay"]
    need(
        active_replay["counts"] == active["counts"]
        and p["active_input_replay"]["operation_id"] == input_op["id"]
        and p["active_input_replay"]["stage"] == "ACTIVE_AND_SYNCED"
        and p["completed_input_replay"] == "INPUT_SCOPE_DENIED",
        "INPUT_REPLAY_NOT_SAFE",
    )
    need(
        p["main_unchanged"]
        is p["old_refs_and_worktrees_preserved"]
        is p["unrelated_board_unchanged"]
        is True
        and p["native_epic_status"] == "InProgress",
        "RESOURCES_OR_EPIC_STATUS_CHANGED",
    )
    return {
        "status": "F33_NATIVE_PASS",
        "acceptance": ["F-33.A1", "F-33.A2", "F-33.A3"],
        "maximum_reserved": maximum,
        "BC_working_overlap_samples": overlap,
        "native_samples": good,
        "unavailable_samples": unavailable,
        "replay_no_effects": True,
    }
