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


@pytest.mark.parametrize("record_timeout", [False, True])
def test_delayed_observer_reconciles_dated_on_time_ack_without_resend(
    setup, monkeypatch, record_timeout
):
    s, i, h, history = setup
    h.prompt = lambda *a, **k: h.sent.append(a[1])
    assert s.dispatch(i, "t1", "fixture")["status"] == "WAITING"
    delivery_time = int(utc_now().timestamp())
    monkeypatch.setattr(
        "orchestrator.application.runtime_assignment_service.utc_now",
        lambda: utc_now() + timedelta(seconds=60),
    )
    if record_timeout:
        with pytest.raises(AssignmentError, match="ACK_TIMEOUT"):
            s.observe(i, "t1")
    h.session_id = SID
    history.reply(h.sent[0])
    history.turns[-1]["completedAt"] = delivery_time
    result = s.dispatch(i, "t1", "fixture")
    assert result["status"] == "CONFIRMED"
    assert result["ack"]["timely_completion_verified"] is True
    assert s.store.get_task("t1").internal_status == TaskState.WORKING
    assert len(h.sent) == 1
    with StateStore(s.settings.sqlite_path) as db:
        recovered = RuntimeAssignmentService(s.settings, db, h, history)
        assert recovered.dispatch(i, "t1", "fixture") == result
    assert len(h.sent) == 1


@pytest.mark.parametrize("timing", ["late", "missing", "string", "boolean", "boundary"])
def test_timeout_does_not_accept_unproven_or_late_completion(setup, monkeypatch, timing):
    s, i, h, history = setup
    h.prompt = lambda *a, **k: h.sent.append(a[1])
    s.dispatch(i, "t1", "fixture")
    from datetime import datetime

    op = s.store.get_operation("p", s.KIND, "t1")
    deadline = datetime.fromisoformat(op.result["deadline"]).timestamp()
    monkeypatch.setattr(
        "orchestrator.application.runtime_assignment_service.utc_now",
        lambda: utc_now() + timedelta(seconds=60),
    )
    with pytest.raises(AssignmentError, match="ACK_TIMEOUT"):
        s.observe(i, "t1")
    h.session_id = SID
    history.reply(h.sent[0])
    timestamps = {
        "late": int(deadline) + 1,
        "string": str(int(deadline) - 10),
        "boolean": True,
        "boundary": int(deadline),
    }
    if timing != "missing":
        history.turns[-1]["completedAt"] = timestamps[timing]
    with pytest.raises(AssignmentError, match="ACK_TIMEOUT"):
        s.dispatch(i, "t1", "fixture")
    assert s.store.get_task("t1").internal_status == TaskState.STARTING
    assert len(h.sent) == 1


@pytest.mark.parametrize("timely", [True, False])
def test_exact_native_ack_time_is_independent_of_long_turn_completion(setup, monkeypatch, timely):
    s, i, h, history = setup
    h.prompt = lambda *a, **k: h.sent.append(a[1])
    s.dispatch(i, "t1", "fixture")
    delivered = utc_now()
    monkeypatch.setattr(
        "orchestrator.application.runtime_assignment_service.utc_now",
        lambda: utc_now() + timedelta(seconds=60),
    )
    with pytest.raises(AssignmentError, match="ACK_TIMEOUT"):
        s.observe(i, "t1")
    h.session_id = SID
    history.reply(h.sent[0])
    turn = history.turns[-1]
    turn["completedAt"] = int((delivered + timedelta(seconds=90)).timestamp())
    turn["items"][-1]["nativeObservedAt"] = (
        delivered + timedelta(seconds=0 if timely else 60)
    ).isoformat()
    if timely:
        result = s.dispatch(i, "t1", "fixture")
        assert result["status"] == "CONFIRMED"
        assert result["ack"]["native_observed_at"] == delivered.isoformat()
    else:
        with pytest.raises(AssignmentError, match="ACK_TIMEOUT"):
            s.dispatch(i, "t1", "fixture")
    assert len(h.sent) == 1


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


def test_readiness_loss_before_send_has_separate_lease_and_first_ack_clock(setup, monkeypatch):
    from datetime import datetime

    s, actor, h, _ = setup
    original = s._runtime
    calls = []

    def readiness(*args):
        result = original(*args)
        calls.append(result)
        return result | {"ready": False} if len(calls) == 2 else result

    s._runtime = readiness
    first = s.dispatch(actor, "t1", "exact fixture")
    assert first["status"] == "WAITING" and first["dispatch_stage"] == "PREPARED"
    prepared = s.store.get_operation("p", s.KIND, "t1")
    assert prepared.result["deadline"] is None and h.sent == []
    with pytest.raises(AssignmentError, match="NOT_DISPATCHED"):
        s.observe(actor, "t1")
    # The controller may finish another startup before revisiting this known
    # unsent task: the original preparation lease survives, no ACK time existed.
    now = datetime.fromisoformat(prepared.result["preparation_deadline"]) - timedelta(seconds=240)
    monkeypatch.setattr("orchestrator.application.runtime_assignment_service.utc_now", lambda: now)
    result = s.dispatch(actor, "t1", "exact fixture")
    assert result["status"] == "CONFIRMED" and len(h.sent) == 1
    final = s.store.get_operation("p", s.KIND, "t1")
    assert final.id == prepared.id
    for key in ["correlation_id", "prompt_hash", "instruction_hash", "baseline_turns",
                "preparation_deadline"]:
        assert final.result[key] == prepared.result[key]
    assert datetime.fromisoformat(final.result["deadline"]) == now + timedelta(seconds=45)
    assert s.dispatch(actor, "t1", "exact fixture") == result and len(h.sent) == 1
    assert s.store.get_operation("p", s.KIND, "t1").result["deadline"] == final.result["deadline"]


@pytest.mark.parametrize("legacy", [False, True])
def test_unsent_preparation_never_extends_expired_original_lease(setup, monkeypatch, legacy):
    from datetime import datetime

    s, actor, h, _ = setup
    original = s._runtime
    count = []

    def readiness(*args):
        facts = original(*args)
        count.append(1)
        return facts | {"ready": False} if len(count) == 2 else facts

    s._runtime = readiness
    assert s.dispatch(actor, "t1", "exact fixture")["dispatch_stage"] == "PREPARED"
    prepared = s.store.get_operation("p", s.KIND, "t1")
    if legacy:
        data = {k: v for k, v in prepared.result.items()
                if k not in {"dispatch_protocol", "preparation_deadline"}}
        data["deadline"] = (utc_now() + timedelta(seconds=45)).isoformat()
        prepared = prepared.model_copy(update={"result": data})
        s.store.update_operation(prepared)
    key = "deadline" if legacy else "preparation_deadline"
    deadline = prepared.result[key]
    monkeypatch.setattr("orchestrator.application.runtime_assignment_service.utc_now",
                        lambda: datetime.fromisoformat(deadline) + timedelta(seconds=1))
    error = "ACK_TIMEOUT" if legacy else "PREPARATION_TIMEOUT"
    with pytest.raises(AssignmentError, match=error):
        s.dispatch(actor, "t1", "exact fixture")
    expired = s.store.get_operation("p", s.KIND, "t1")
    assert expired.status == "TIMED_OUT" and expired.result[key] == deadline
    assert expired.result["stage"] == "PREPARED" and h.sent == []
    assert expired.result["deadline"] == prepared.result["deadline"]
    with pytest.raises(AssignmentError, match=error):
        s.observe(actor, "t1")
    assert s.store.get_task("t1").internal_status == TaskState.STARTING


def test_legacy_dispatched_unknown_outcome_is_not_reinterpreted_as_prepared(setup):
    s, actor, h, _ = setup

    def crash(*args, **kwargs):
        raise RuntimeError("after dispatch marker; transport outcome unknown")

    h.prompt = crash
    with pytest.raises(RuntimeError):
        s.dispatch(actor, "t1", "exact fixture")
    op = s.store.get_operation("p", s.KIND, "t1")
    data = {k: v for k, v in op.result.items()
            if k not in {"dispatch_protocol", "preparation_deadline"}}
    assert data["stage"] == "DISPATCH_REQUESTED" and data["deadline"]
    s.store.update_operation(op.model_copy(update={"result": data}))
    h.prompt = lambda *a, **k: pytest.fail("legacy unknown send must never be repeated")
    result = s.dispatch(actor, "t1", "exact fixture")
    assert result["status"] == "WAITING" and h.sent == []
    assert s.store.get_operation("p", s.KIND, "t1").result == data
