import asyncio
import io
import json
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import Client
from test_task_review_service import report_setup, start_setup  # noqa: F401
from test_task_review_service import setup as review_setup  # noqa: F401

from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.review_prompt import render_review_prompt
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_changes_service import TaskChangesError, TaskChangesService
from orchestrator.application.task_review_service import TaskReviewService
from orchestrator.application.worker_report_service import ReportError, WorkerReportService
from orchestrator.domain.models import utc_now
from orchestrator.domain.review_contracts import ChangesDecision
from orchestrator.domain.states import Kanban, TaskState
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


@pytest.fixture
def setup(review_setup, report_setup):  # noqa: F811
    r, actor, worker, report = review_setup
    _, _, _, h, history, _ = report_setup
    context = r.request(actor, worker.task_run_id, key="review-before-fix")["context"]
    decision = dict(
        version=1,
        result="CHANGES_REQUESTED",
        context_id=context["context_id"],
        issues=[
            {
                "number": 1,
                "problem": "Missing explicit persistence regression documentation",
                "requested_change": "Add fix.txt documenting the checked persistence result",
                "acceptance_criteria": ["Result survives reopening"],
            }
        ],
    )

    owned_name = r.store.get_task(worker.task_run_id).worker_agent_id

    def prompt(name, text, *, timeout_ms):
        assert name == owned_name
        h.sent.append(text)
        history.reply(text, turn_id="correction-ack")

    h.prompt = prompt
    yield (
        TaskChangesService(r.settings, r.store, h, history),
        actor,
        worker,
        decision,
        h,
        history,
        report,
    )


def test_negative_review_persists_number_commits_criteria_and_same_session_ack(setup):
    s, a, w, decision, h, _history, report = setup
    before = s.store.get_task(w.task_run_id)
    e = s.store.get_epic(a.epic_run_id)
    main, epic = s.integration.git.head("main"), s.integration.git.head(e.branch)
    answer = s.request(a, before.id, decision, key="fix-one")
    assert answer["status"] == "CONFIRMED"
    reviews = s.store.get_reviews(before.id)
    assert len(reviews) == 1 and reviews[0].review_number == 1
    assert reviews[0].review_commit == report["commit"] and reviews[0].epic_commit == epic
    assert json.loads(reviews[0].feedback) == decision
    assert reviews[0].review_result == "CHANGES_REQUESTED"
    after = s.store.get_task(before.id)
    assert after.internal_status == TaskState.WORKING and after.kanban_status == Kanban.ACTIVE
    for field in ("codex_session_id", "branch", "worktree_path", "worker_agent_id", "worker_slot"):
        assert getattr(before, field) == getattr(after, field)
    assert after.approved_source_commit is None and after.merge_commit is None
    assert s.integration.git.head("main") == main and s.integration.git.head(e.branch) == epic
    packet = json.loads(h.sent[-1])
    assert packet["type"] == "HERDR_CORRECTION" and packet["decision"] == decision
    assert packet["assignment"]["review_id"] == reviews[0].id
    assert answer["ack"]["session_id"] == after.codex_session_id
    prompt = render_review_prompt(s.store.get_operation("p", s.KIND, "fix-one").result["context"])
    assert "Never implement the Worker task yourself" in prompt and decision["context_id"] in prompt


def test_reopen_and_repeat_do_not_send_review_or_state_twice(setup):
    s, a, w, decision, h, history, _ = setup
    first = s.request(a, w.task_run_id, decision, key="stable")
    changes, sends = s.store.db.total_changes, len(h.sent)
    assert (
        s.request(a, w.task_run_id, decision, key="stable")["operation_id"] == first["operation_id"]
    )
    assert s.store.db.total_changes == changes
    with StateStore(s.settings.sqlite_path) as db:
        repeated = TaskChangesService(s.settings, db, h, history).request(
            a, w.task_run_id, decision, key="stable"
        )
        assert repeated["status"] == "EXISTING"
        assert len(db.get_reviews(w.task_run_id)) == 1
    assert len(h.sent) == sends


def test_correction_requires_new_native_report_and_new_review_for_new_commit(setup):
    s, a, w, decision, h, history, report = setup
    s.request(a, w.task_run_id, decision, key="fix")
    reports = WorkerReportService(s.settings, s.store, h, history)
    with pytest.raises(ReportError, match="REPORT_NOT_AVAILABLE"):
        reports.collect(w, w.task_run_id)
    task = s.store.get_task(w.task_run_id)
    path = Path(task.worktree_path)
    (path / "fix.txt").write_text("Persistence result checked after reopening\n")
    s.integration.git.run("add", "fix.txt", cwd=path)
    s.integration.git.run("commit", "-m", "address review", cwd=path)
    sha = s.integration.git.head(task.branch)
    fixed = report | {"commit": sha, "files_changed": ["result.txt", "fix.txt"]}
    history.turns.append(
        {
            "id": "fixed-final",
            "status": "completed",
            "items": [
                {
                    "id": "fixed-report",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": json.dumps(fixed),
                }
            ],
        }
    )
    result = reports.collect(w, task.id)
    assert result["status"] == "READY_FOR_REVIEW"
    op = s.store.get_operations(a.epic_run_id, kind="worker_report")[-1]
    assert (
        op.result["provenance"]["correction_operation_id"]
        == s.store.get_operation("p", s.KIND, "fix").id
    )
    review = TaskReviewService(s.settings, s.store).request(a, task.id, key="review-after-fix")
    assert review["context"]["task_commit"] == sha
    assert review["context"]["context_id"] != decision["context_id"]
    assert (
        len(s.store.get_reviews(task.id)) == 1
        and s.store.get_task(task.id).internal_status == TaskState.REVIEWING
    )


@pytest.mark.parametrize(
    "attack",
    [
        "worker",
        "foreign-epic",
        "foreign-context",
        "unknown-criterion",
        "stale-task",
        "no-slot",
        "foreign-session",
    ],
)
def test_unverified_scope_context_criteria_and_runtime_cannot_record_or_dispatch(setup, attack):
    s, a, w, decision, h, _history, _report = setup
    before = len(h.sent)
    if attack == "worker":
        a = w
    elif attack == "foreign-epic":
        a = a.model_copy(update={"epic_run_id": "foreign"})
    elif attack == "foreign-context":
        decision = decision | {"context_id": "f" * 64}
    elif attack == "unknown-criterion":
        decision = decision | {
            "issues": [decision["issues"][0] | {"acceptance_criteria": ["Invented scope"]}]
        }
    elif attack == "stale-task":
        t = s.store.get_task(w.task_run_id)
        path = Path(t.worktree_path)
        (path / "later.txt").write_text("unreported later work\n")
        s.integration.git.run("add", ".", cwd=path)
        s.integration.git.run("commit", "-m", "later", cwd=path)
    elif attack == "no-slot":
        t = s.store.get_task(w.task_run_id)
        s.store.update_runtime_metadata(t.model_copy(update={"worker_slot": None}))
    elif attack == "foreign-session":
        h.session_id = "00000000-0000-4000-8000-000000000002"
    with pytest.raises(TaskChangesError):
        s.request(a, w.task_run_id, decision, key="attack")
    assert len(h.sent) == before and not s.store.get_reviews(w.task_run_id)
    assert not s.store.get_operations(w.epic_run_id, kind=s.KIND)
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.REVIEWING


@pytest.mark.parametrize(
    "change",
    [
        {"issues": []},
        {"result": "APPROVED"},
        {
            "issues": [
                {"number": 2, "problem": "x", "requested_change": "y", "acceptance_criteria": ["z"]}
            ]
        },
        {
            "issues": [
                {"number": 1, "problem": " ", "requested_change": "y", "acceptance_criteria": ["z"]}
            ]
        },
        {"role": "Integration"},
    ],
)
def test_empty_unstructured_unnumbered_and_approval_decisions_are_invalid(change):
    base = dict(
        version=1,
        result="CHANGES_REQUESTED",
        context_id="a" * 64,
        issues=[dict(number=1, problem="x", requested_change="y", acceptance_criteria=["z"])],
    )
    with pytest.raises(ValueError):
        ChangesDecision.model_validate(base | change)


def test_lost_transport_observes_delivered_ack_without_resend(setup):
    s, a, w, decision, h, _, _ = setup
    original = h.prompt

    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    h.prompt = lost
    assert s.request(a, w.task_run_id, decision, key="lost")["status"] == "CONFIRMED"
    before = len(h.sent)
    assert s.request(a, w.task_run_id, decision, key="lost")["status"] == "EXISTING"
    assert len(h.sent) == before


def test_unknown_delivery_timeout_keeps_changes_requested_slot_and_rejects_late_ack(
    setup, monkeypatch
):
    s, a, w, decision, h, history, _ = setup
    h.prompt = lambda *args, **kwargs: h.sent.append(args[1])
    assert s.request(a, w.task_run_id, decision, key="unknown")["status"] == "WAITING"
    monkeypatch.setattr(
        "orchestrator.application.task_changes_service.utc_now",
        lambda: utc_now() + timedelta(seconds=60),
    )
    with pytest.raises(TaskChangesError, match="ACK_TIMEOUT"):
        s.observe(a, w.task_run_id, key="unknown")
    history.reply(h.sent[-1], turn_id="late-ack")
    with pytest.raises(TaskChangesError, match="ACK_TIMEOUT"):
        s.request(a, w.task_run_id, decision, key="unknown")
    task = s.store.get_task(w.task_run_id)
    assert task.internal_status == TaskState.CHANGES_REQUESTED and task.worker_slot == 1
    assert len(h.sent) == 2 and len(s.store.get_reviews(task.id)) == 1


@pytest.mark.parametrize("completion", ["early", "late", "missing"])
def test_delayed_correction_observation_requires_proven_original_deadline(
    setup, monkeypatch, completion
):
    s, a, w, decision, h, history, _ = setup
    h.prompt = lambda *args, **kwargs: h.sent.append(args[1])
    assert s.request(a, w.task_run_id, decision, key="dated-fix")["status"] == "WAITING"
    delivered = int(utc_now().timestamp())
    monkeypatch.setattr(
        "orchestrator.application.task_changes_service.utc_now",
        lambda: utc_now() + timedelta(seconds=60),
    )
    with pytest.raises(TaskChangesError, match="ACK_TIMEOUT"):
        s.observe(a, w.task_run_id, key="dated-fix")
    history.reply(h.sent[-1], turn_id="delayed-correction-observation")
    if completion != "missing":
        history.turns[-1]["completedAt"] = delivered + (60 if completion == "late" else 0)
    if completion == "early":
        result = s.request(a, w.task_run_id, decision, key="dated-fix")
        assert result["status"] == "CONFIRMED"
        assert result["ack"]["timely_completion_verified"] is True
        assert s.store.get_task(w.task_run_id).internal_status == TaskState.WORKING
    else:
        with pytest.raises(TaskChangesError, match="ACK_TIMEOUT"):
            s.request(a, w.task_run_id, decision, key="dated-fix")
    assert len(h.sent) == 2 and len(s.store.get_reviews(w.task_run_id)) == 1


def test_crash_after_review_recovers_registration_without_duplicate_or_new_session(
    setup, monkeypatch
):
    s, a, w, decision, h, history, _ = setup
    original = s._checkpoint

    def crash(op, **kwargs):
        if kwargs.get("stage") == "REGISTERED":
            raise RuntimeError("lost after review")
        return original(op, **kwargs)

    monkeypatch.setattr(s, "_checkpoint", crash)
    with pytest.raises(RuntimeError, match="lost after review"):
        s.request(a, w.task_run_id, decision, key="recover")
    assert len(s.store.get_reviews(w.task_run_id)) == 1 and len(h.sent) == 1
    with StateStore(s.settings.sqlite_path) as db:
        result = TaskChangesService(s.settings, db, h, history).request(
            a, w.task_run_id, decision, key="recover"
        )
        assert result["status"] == "CONFIRMED" and len(db.get_reviews(w.task_run_id)) == 1
    assert len(h.sent) == 2


def test_real_mcp_roles_and_injected_command_do_not_dispatch(setup):
    s, a, w, decision, h, _, _ = setup
    args = dict(project_id="p", task_run_id=w.task_run_id, request_key="mcp", decision=decision)
    worker = RuntimeService(s.store, w, EventLog(stream=io.StringIO()), task_changes=s)
    integration = RuntimeService(s.store, a, EventLog(stream=io.StringIO()), task_changes=s)

    async def exercise():
        async with Client(create_server(worker)) as client:
            result = await client.call_tool("task_request_changes", args)
            assert result.structured_content["code"] == "FORBIDDEN"
        async with Client(create_server(integration)) as client:
            result = await client.call_tool("task_request_changes", args | {"command": ["bad"]})
            assert result.structured_content["code"] == "INVALID_ARGUMENT"
            result = await client.call_tool("task_request_changes", args)
            assert (
                result.structured_content["ok"]
                and result.structured_content["data"]["status"] == "CONFIRMED"
            )

    asyncio.run(exercise())
    assert len(h.sent) == 2


@pytest.mark.parametrize("field", ["review_id", "context_id", "task_run_id"])
def test_foreign_review_or_context_ack_cannot_start_correction(setup, field):
    s, a, w, decision, h, history, _ = setup

    def prompt(name, text, **kwargs):
        h.sent.append(text)
        packet = json.loads(text)
        history.reply(text, turn_id="foreign-ack", ack=packet["assignment"] | {field: "foreign"})

    h.prompt = prompt
    assert s.request(a, w.task_run_id, decision, key="bad-ack")["status"] == "WAITING"
    task = s.store.get_task(w.task_run_id)
    assert task.internal_status == TaskState.CHANGES_REQUESTED and task.worker_slot == 1


def test_crash_after_dispatch_intent_before_send_never_resends_unknown_intent(setup, monkeypatch):
    s, a, w, decision, h, history, _ = setup
    original, calls = s._runtime, []

    def interrupted(*args, **kwargs):
        calls.append(1)
        if len(calls) == 4:
            raise RuntimeError("process lost before send")
        return original(*args, **kwargs)

    monkeypatch.setattr(s, "_runtime", interrupted)
    with pytest.raises(RuntimeError, match="lost before send"):
        s.request(a, w.task_run_id, decision, key="dispatch-intent")
    op = s.store.get_operation("p", s.KIND, "dispatch-intent")
    assert op.result["stage"] == "DISPATCH_REQUESTED" and len(h.sent) == 1
    with StateStore(s.settings.sqlite_path) as db:
        service = TaskChangesService(s.settings, db, h, history)
        assert (
            service.request(a, w.task_run_id, decision, key="dispatch-intent")["status"]
            == "WAITING"
        )
    assert len(h.sent) == 1 and len(s.store.get_reviews(w.task_run_id)) == 1


def test_process_loss_after_native_ack_is_reconciled_without_new_send(setup):
    s, a, w, decision, h, history, _ = setup
    original = h.prompt

    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("process lost after send")

    h.prompt = lost
    with pytest.raises(RuntimeError, match="lost after send"):
        s.request(a, w.task_run_id, decision, key="after-send")
    with StateStore(s.settings.sqlite_path) as db:
        recovered = TaskChangesService(s.settings, db, h, history).request(
            a, w.task_run_id, decision, key="after-send"
        )
        assert recovered["status"] == "CONFIRMED"
    assert len(h.sent) == 2 and len(s.store.get_reviews(w.task_run_id)) == 1


def test_concurrent_identical_requests_register_and_send_once(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    s, a, w, decision, h, history, _ = setup
    barrier = Barrier(2)

    def request():
        with StateStore(s.settings.sqlite_path) as db:
            service = TaskChangesService(s.settings, db, h, history)
            barrier.wait(5)
            return service.request(a, w.task_run_id, decision, key="concurrent")

    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: request(), range(2)))
    assert len({answer["operation_id"] for answer in answers}) == 1
    assert len(h.sent) == 2 and len(s.store.get_reviews(w.task_run_id)) == 1
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.WORKING
