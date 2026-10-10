"""Private native timestamps must agree with independently reconstructed thread items."""

import json
from copy import deepcopy

import pytest

from orchestrator.adapters.codex import CodexAdapter


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    root = tmp_path / "sessions"
    root.mkdir()
    path = root / "rollout-example-owned-sid.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": "owned-sid", "cwd": "/owned/task"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "owned-turn"}},
        {
            "type": "response_item",
            "timestamp": "2026-10-10T15:00:00.123Z",
            "payload": {
                "type": "message",
                "role": "assistant",
                "id": "owned-item",
                "content": [{"type": "output_text", "text": '{"status":"WORKING"}'}],
            },
        },
    ]
    thread = {
        "path": str(path),
        "sessionId": "owned-sid",
        "cwd": "/owned/task",
        "turns": [
            {
                "id": "owned-turn",
                "items": [
                    {"id": "owned-item", "type": "agentMessage", "text": '{"status":"WORKING"}'}
                ],
            }
        ],
    }
    return path, rows, thread


def write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_native_timing_attaches_only_to_exact_owned_message(journal):
    path, rows, thread = journal
    write(path, rows)
    CodexAdapter._attach_message_times(thread)
    assert thread["turns"][0]["items"][0]["nativeObservedAt"] == "2026-10-10T15:00:00.123000+00:00"


@pytest.mark.parametrize(
    "change", ["session", "cwd", "turn", "item", "text", "user", "duplicate", "naive"]
)
def test_foreign_ambiguous_or_untrusted_records_supply_no_time(journal, change):
    path, rows, thread = journal
    if change == "session":
        rows[0]["payload"]["id"] = "foreign"
    elif change == "cwd":
        rows[0]["payload"]["cwd"] = "/foreign"
    elif change == "turn":
        rows[1]["payload"]["turn_id"] = "foreign"
    elif change == "item":
        rows[2]["payload"]["id"] = "foreign"
    elif change == "text":
        rows[2]["payload"]["content"][0]["text"] = "not the ACK"
    elif change == "user":
        rows[2]["payload"]["role"] = "user"
    elif change == "duplicate":
        rows.append(deepcopy(rows[2]))
    elif change == "naive":
        rows[2]["timestamp"] = "2026-10-10T15:00:00"
    write(path, rows)
    CodexAdapter._attach_message_times(thread)
    assert "nativeObservedAt" not in thread["turns"][0]["items"][0]


@pytest.mark.parametrize("unsafe", ["symlink", "outside", "partial"])
def test_unsafe_or_partial_journal_is_not_evidence(journal, unsafe):
    path, rows, thread = journal
    if unsafe == "partial":
        write(path, rows)
        with path.open("a") as f:
            f.write('{"type":')
    else:
        outside = path.parent.parent / path.name
        write(outside, rows)
        if unsafe == "symlink":
            path.symlink_to(outside)
        else:
            thread["path"] = str(outside)
    CodexAdapter._attach_message_times(thread)
    assert "nativeObservedAt" not in thread["turns"][0]["items"][0]


def test_preexisting_unverified_timestamp_is_removed_when_journal_is_missing(journal):
    _, _, thread = journal
    item = thread["turns"][0]["items"][0]
    item["nativeObservedAt"] = "2026-10-10T15:00:00Z"
    CodexAdapter._attach_message_times(thread)
    assert "nativeObservedAt" not in item
