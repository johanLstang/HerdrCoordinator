import json
from datetime import timedelta

import pytest
from test_runtime_start import setup as startup_setup  # noqa: F401

from orchestrator.adapters.herdr import HerdrAdapter, HerdrError
from orchestrator.application.runtime_assignment_service import (
    AssignmentError,
    RuntimeAssignmentService,
)
from orchestrator.domain.models import utc_now
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import TaskState
from orchestrator.persistence.store import StateStore

SID = "00000000-0000-4000-8000-000000000001"


class History:
    def __init__(self):
        self.turns = []

    def read_thread(self, sid, cwd):
        assert sid == SID
        return {"id": sid, "sessionId": sid, "cwd": cwd, "turns": self.turns}

    def reply(self, prompt, *, ack=None, turn_id="new", item_type="agentMessage"):
        packet = json.loads(prompt)
        self.turns.append(
            {
                "id": turn_id,
                "status": "completed",
                "items": [
                    {
                        "id": "user",
                        "type": "userMessage",
                        "content": [{"type": "text", "text": prompt}],
                    },
                    {
                        "id": "answer",
                        "type": item_type,
                        "text": json.dumps(ack if ack is not None else packet["assignment"]),
                    },
                ],
            }
        )


@pytest.fixture
def setup(startup_setup):  # noqa: F811
    start, _, actor, h = startup_setup
    start.start_task(actor, "t1")
    history = History()
    original = h.verify_agent
    h.sent, h.session_id, h.status = [], None, "idle"

    def verify(*args):
        return original(*args) | {
            "session_id": h.session_id,
            "status": h.status,
            "ready": h.status in {"idle", "done"},
        }

    owned_name = start.store.get_task("t1").worker_agent_id

    def prompt(name, text, *, timeout_ms):
        assert name == owned_name
        h.sent.append(text)
        h.session_id = SID
        history.reply(text)

    h.verify_agent, h.prompt = verify, prompt
    yield RuntimeAssignmentService(start.settings, start.store, h, history), actor, h, history


def test_exact_delivery_and_native_ack_persist_once_after_reopen(setup):
    s, i, h, history = setup
    a = s.dispatch(i, "t1", "Harmless no-tools fixture")
    assert a["status"] == "CONFIRMED"
    assert a["ack"]["session_id"] == SID and a["ack"]["turn_id"] == "new"
    t = s.store.get_task("t1")
    assert t.internal_status == TaskState.WORKING and t.codex_session_id == SID
    assert t.worker_slot == 1 and s.store.get_event("p", a["event_id"]) is not None
    changes = s.store.db.total_changes
    assert s.dispatch(i, "t1", "Harmless no-tools fixture") == a
    with StateStore(s.settings.sqlite_path) as db:
        assert (
            RuntimeAssignmentService(s.settings, db, h, history).dispatch(
                i, "t1", "Harmless no-tools fixture"
            )
            == a
        )
    assert len(h.sent) == 1 and s.store.db.total_changes == changes


def test_lost_transport_ack_is_reconciled_without_resend(setup):
    s, i, h, _ = setup
    original = h.prompt

    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    h.prompt = lost
    assert s.dispatch(i, "t1", "fixture")["status"] == "CONFIRMED"
    assert len(h.sent) == 1
    assert s.store.get_operation("p", s.KIND, "t1").result["transport"] == "UNKNOWN"


def test_timeout_stays_starting_keeps_slot_and_rejects_late_ack(setup, monkeypatch):
    s, i, h, history = setup
    h.prompt = lambda *a, **k: h.sent.append(a[1])
    assert s.dispatch(i, "t1", "fixture")["status"] == "WAITING"
    monkeypatch.setattr(
        "orchestrator.application.runtime_assignment_service.utc_now",
        lambda: utc_now() + timedelta(seconds=60),
    )
    with pytest.raises(AssignmentError, match="ACK_TIMEOUT"):
        s.observe(i, "t1")
    h.session_id = SID
    history.reply(h.sent[0])
    with pytest.raises(AssignmentError, match="ACK_TIMEOUT"):
        s.dispatch(i, "t1", "fixture")
    t = s.store.get_task("t1")
    assert t.internal_status == TaskState.STARTING and t.worker_slot == 1
    assert t.codex_session_id is None and len(h.sent) == 1


@pytest.mark.parametrize(
    "attack",
    [
        "foreign-run",
        "foreign-project",
        "foreign-correlation",
        "old-turn",
        "user-echo",
        "wrong-prompt",
        "failed-turn",
        "malformed",
        "generic-ready",
        "foreign-session",
    ],
)
def test_foreign_old_and_non_agent_signals_never_confirm_working(setup, attack):
    s, i, h, history = setup
    h.session_id = SID
    history.turns = [{"id": "old", "status": "completed", "items": []}]

    def prompt(name, text, *, timeout_ms):
        h.sent.append(text)
        ack = json.loads(text)["assignment"]
        if attack == "foreign-run":
            ack["task_run_id"] = "t2"
        if attack == "foreign-project":
            ack["project_id"] = "foreign"
        if attack == "foreign-correlation":
            ack["correlation_id"] = "old-nonce"
        history.reply(
            text,
            ack=ack,
            turn_id="old" if attack == "old-turn" else "new",
            item_type="userMessage" if attack == "user-echo" else "agentMessage",
        )
        turn = history.turns[-1]
        if attack == "wrong-prompt":
            turn["items"][0]["content"][0]["text"] = "foreign prompt"
        if attack == "failed-turn":
            turn["status"] = "failed"
        if attack == "malformed":
            turn["items"][-1]["text"] = "secret unstructured response"
        if attack == "generic-ready":
            turn["items"][-1]["text"] = '{"status":"READY_FOR_REVIEW"}'
        if attack == "foreign-session":
            h.session_id = "00000000-0000-4000-8000-000000000002"

    h.prompt = prompt
    if attack == "foreign-session":
        with pytest.raises(AssignmentError, match="SESSION_CHANGED"):
            s.dispatch(i, "t1", "fixture")
    else:
        assert s.dispatch(i, "t1", "fixture")["status"] == "WAITING"
    assert s.store.get_task("t1").internal_status == TaskState.STARTING
    assert (
        s.store.get_event("p", "assignment-ack:" + s.store.get_operation("p", s.KIND, "t1").id)
        is None
    )


def test_crash_after_dispatch_intent_only_observes_on_retry(setup):
    s, i, h, history = setup

    def crash(*args, **kwargs):
        raise RuntimeError("simulated crash before send")

    h.prompt = crash
    with pytest.raises(RuntimeError):
        s.dispatch(i, "t1", "fixture")
    h.prompt = lambda *a, **k: pytest.fail("must not blindly resend")
    with StateStore(s.settings.sqlite_path) as db:
        result = RuntimeAssignmentService(s.settings, db, h, history).dispatch(i, "t1", "fixture")
    assert result["status"] == "WAITING" and not h.sent


def test_delayed_matching_ack_after_unknown_delivery_is_accepted_once(setup):
    s, i, h, history = setup

    def lost(name, text, **kwargs):
        h.sent.append(text)
        raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    h.prompt = lost
    assert s.dispatch(i, "t1", "fixture")["status"] == "WAITING"
    h.session_id = SID
    history.reply(h.sent[0])
    a = s.observe(i, "t1")
    assert s.observe(i, "t1") == a and len(h.sent) == 1


@pytest.mark.parametrize(
    "role,project,epic",
    [
        (Role.WORKER, "p", "e"),
        (Role.COORDINATOR, "p", None),
        (Role.INTEGRATION, "other", "e"),
        (Role.INTEGRATION, "p", "other"),
    ],
)
def test_unauthorized_dispatch_has_no_write_or_prompt(setup, role, project, epic):
    s, _, h, _ = setup
    a = Actor(actor_id="foreign", role=role, project_id=project, epic_run_id=epic, task_run_id="t1")
    count = s.store.db.total_changes
    with pytest.raises(AssignmentError):
        s.dispatch(a, "t1", "fixture")
    assert s.store.db.total_changes == count and not h.sent


def test_repeated_intent_with_changed_instruction_is_rejected(setup):
    s, i, h, _ = setup
    s.dispatch(i, "t1", "fixture")
    with pytest.raises(AssignmentError, match="INTENT_MISMATCH"):
        s.dispatch(i, "t1", "changed instruction")
    assert len(h.sent) == 1


@pytest.mark.parametrize("changed", ["branch", "pane", "process", "blocked"])
def test_changed_resource_or_nonidle_runtime_cannot_receive_assignment(setup, changed):
    s, i, h, _ = setup
    t = s.store.get_task("t1")
    if changed == "branch":
        from pathlib import Path

        s.start.worktrees.git.run(
            "symbolic-ref", "HEAD", "refs/heads/main", cwd=Path(t.worktree_path)
        )
    elif changed == "pane":
        h.workspaces[t.herdr_pane_id]["cwd"] = "foreign"
    elif changed == "process":
        original = h.verify_agent
        h.verify_agent = lambda *a: original(*a) | {"processes": []}
    else:
        h.status = "blocked"
    with pytest.raises(AssignmentError):
        s.dispatch(i, "t1", "fixture")
    assert not h.sent and s.store.get_operation("p", s.KIND, "t1") is None


def test_prompt_adapter_uses_explicit_name_timeout_and_literal_argument(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    h = HerdrAdapter("isolated")
    seen = []
    monkeypatch.setattr(h, "call", lambda *a, **k: seen.append((a, k)))
    text = '{"instruction":"$(touch /tmp/should-not-run)"}'
    h.prompt("owned", text, timeout_ms=45000)
    assert seen == [
        (
            (
                "agent",
                "prompt",
                "owned",
                text,
                "--wait",
                "--until",
                "working",
                "--timeout",
                "45000",
            ),
            {"timeout": 50},
        )
    ]


def test_native_working_without_correlated_ack_is_only_physical_status(setup):
    s, i, h, history = setup

    def prompt(name, text, **kwargs):
        h.sent.append(text)
        h.session_id, h.status = SID, "working"

    h.prompt = prompt
    result = s.dispatch(i, "t1", "fixture")
    assert result["status"] == "WAITING" and result["runtime_status"] == "working"
    t = s.store.get_task("t1")
    assert t.internal_status == TaskState.STARTING and t.codex_session_id == SID
    assert not history.turns


def test_concurrent_dispatch_sends_only_once_and_keeps_one_ack_event(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    s, i, h, history = setup
    entered, release = Event(), Event()
    original = h.prompt

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(h, "prompt", paused)

    def dispatch():
        with StateStore(s.settings.sqlite_path) as db:
            return RuntimeAssignmentService(s.settings, db, h, history).dispatch(i, "t1", "fixture")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch)
        assert entered.wait(5)
        try:
            assert s.dispatch(i, "t1", "fixture")["status"] == "WAITING"
        finally:
            release.set()
        result = future.result(timeout=10)
    assert s.observe(i, "t1") == result and len(h.sent) == 1
    assert s.store.get_event("p", result["event_id"]) is not None


@pytest.mark.parametrize(
    "runtime_status,expected", [("working", "CONFIRMED"), ("idle", "WAITING"), ("done", "WAITING")]
)
def test_unloaded_interrupted_projection_needs_verified_working_runtime(
    setup, runtime_status, expected
):
    s, i, h, history = setup
    original = h.prompt

    def prompt(*args, **kwargs):
        original(*args, **kwargs)
        h.status = runtime_status
        history.turns[-1]["status"] = "interrupted"

    h.prompt = prompt
    result = s.dispatch(i, "t1", "fixture")
    assert result["status"] == expected
    assert s.store.get_task("t1").internal_status == (
        TaskState.WORKING if expected == "CONFIRMED" else TaskState.STARTING
    )
    assert len(h.sent) == 1


def test_live_interrupted_projection_does_not_accept_foreign_ack(setup):
    s, i, h, history = setup
    original = h.prompt

    def prompt(*args, **kwargs):
        original(*args, **kwargs)
        h.status = "working"
        history.turns[-1]["status"] = "interrupted"
        history.turns[-1]["items"][-1]["text"] = json.dumps(
            {"status": "WORKING", "task_run_id": "foreign"}
        )

    h.prompt = prompt
    assert s.dispatch(i, "t1", "fixture")["status"] == "WAITING"
    assert s.store.get_task("t1").internal_status == TaskState.STARTING
