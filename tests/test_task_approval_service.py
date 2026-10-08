import asyncio
import io
from pathlib import Path

import pytest
from mcp import Client
from test_task_changes_service import report_setup, review_setup, start_setup  # noqa: F401
from test_task_changes_service import setup as changes_setup  # noqa: F401

from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_approval_service import TaskApprovalError, TaskApprovalService
from orchestrator.application.task_changes_service import TaskChangesError
from orchestrator.application.task_review_service import TaskReviewError
from orchestrator.domain.states import Kanban, TaskState
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


@pytest.fixture
def setup(changes_setup):  # noqa: F811
    changes, actor, worker, negative, h, history, _ = changes_setup
    context = changes.store.get_operation("p", "task_review_request", "review-before-fix").result[
        "context"
    ]
    decision = dict(
        version=1,
        result="APPROVED",
        context_id=context["context_id"],
        summary="Reviewed bounded result, sources, full diff and independent tests",
        verified_criteria=context["acceptance_criteria"],
    )
    yield (
        TaskApprovalService(changes.settings, changes.store),
        actor,
        worker,
        decision,
        changes,
        h,
        history,
        negative,
    )


def test_current_authorized_complete_review_approves_without_merge_done_or_release(setup):
    s, a, w, d, _, _, _, _ = setup
    before = s.store.get_task(w.task_run_id)
    e = s.store.get_epic(a.epic_run_id)
    main, epic = s.integration.git.head("main"), s.integration.git.head(e.branch)
    result = s.approve(a, before.id, d, key="approve-one")
    assert result["status"] == "APPROVED"
    task = s.store.get_task(before.id)
    assert task.internal_status == TaskState.APPROVED and task.kanban_status == Kanban.ACTIVE
    assert task.worker_slot == 1 and task.merge_commit is None and task.completed_at is None
    assert (
        task.approved_source_commit == before.current_commit and task.approved_target_commit == epic
    )
    reviews = s.store.get_reviews(task.id)
    assert len(reviews) == 1 and reviews[0].review_result == "APPROVED"
    op = s.store.get_operation("p", s.KIND, "approve-one")
    assert op.result["reviewer"] == a.model_dump(mode="json")
    assert op.result["context_id"] == d["context_id"] and op.result["review_id"] == reviews[0].id
    proof = s.require_current(a, task.id)
    assert proof["task_commit"] == before.current_commit and proof["epic_commit"] == epic
    assert proof["verification_id"] == op.result["verification_id"]
    assert s.integration.git.head("main") == main and s.integration.git.head(e.branch) == epic


def test_repeat_and_reopen_return_current_exact_approval_without_new_review(setup):
    s, a, w, d, _, _, _, _ = setup
    first = s.approve(a, w.task_run_id, d, key="once")
    before = s.store.db.total_changes
    again = s.approve(a, w.task_run_id, d, key="once")
    assert again["status"] == "EXISTING" and again["operation_id"] == first["operation_id"]
    assert s.store.db.total_changes == before
    with StateStore(s.settings.sqlite_path) as db:
        reopened = TaskApprovalService(s.settings, db)
        assert reopened.approve(a, w.task_run_id, d, key="once")["status"] == "EXISTING"
        assert len(db.get_reviews(w.task_run_id)) == 1


@pytest.mark.parametrize("side", ["task", "epic", "configuration"])
def test_changed_code_base_or_configuration_cannot_reuse_approval_but_history_survives(setup, side):
    s, a, w, d, _, _, _, _ = setup
    s.approve(a, w.task_run_id, d, key="original")
    review = s.store.get_reviews(w.task_run_id)[0]
    if side == "configuration":
        s = TaskApprovalService(s.settings.model_copy(update={"worker_test_timeout": 30}), s.store)
    else:
        record = (
            s.store.get_task(w.task_run_id) if side == "task" else s.store.get_epic(a.epic_run_id)
        )
        path = Path(record.worktree_path)
        (path / "later.txt").write_text("later version\n")
        s.integration.git.run("add", "later.txt", cwd=path)
        s.integration.git.run("commit", "-m", "later", cwd=path)
    with pytest.raises(TaskApprovalError):
        s.require_current(a, w.task_run_id)
    with pytest.raises(TaskApprovalError):
        s.approve(a, w.task_run_id, d, key="original")
    assert s.store.get_reviews(w.task_run_id) == [review]
    assert s.store.get_task(w.task_run_id).merge_commit is None


@pytest.mark.parametrize(
    "attack",
    [
        "worker",
        "foreign-epic",
        "foreign-context",
        "missing-test",
        "missing-handoff",
        "incomplete-criteria",
        "duplicate-criteria",
        "corrupt-context",
    ],
)
def test_unverified_actor_evidence_and_acceptance_cannot_advance_review(setup, attack):
    s, a, w, d, _, _, _, _ = setup
    context = s.store.get_operation("p", "task_review_request", "review-before-fix")
    if attack == "worker":
        a = w
    elif attack == "foreign-epic":
        a = a.model_copy(update={"epic_run_id": "foreign"})
    elif attack == "foreign-context":
        d = d | {"context_id": "f" * 64}
    elif attack == "missing-test":
        test = s.store.get_operation("p", "verify_task", context.result["context"]["tests"]["key"])
        s.store.update_operation(test.model_copy(update={"status": "FAILED"}))
    elif attack == "missing-handoff":
        op = next(
            o
            for o in s.store.get_operations(a.epic_run_id, kind="worker_report")
            if o.id == context.result["context"]["handoff_operation_id"]
        )
        s.store.update_operation(op.model_copy(update={"status": "FAILED"}))
    elif attack == "incomplete-criteria":
        d = d | {"verified_criteria": ["Unreviewed invented criterion"]}
    elif attack == "duplicate-criteria":
        d = d | {"verified_criteria": d["verified_criteria"] * 2}
    elif attack == "corrupt-context":
        changed = context.result["context"] | {"changed_files": []}
        s.store.update_operation(
            context.model_copy(update={"result": context.result | {"context": changed}})
        )
    with pytest.raises(TaskApprovalError):
        s.approve(a, w.task_run_id, d, key="attack")
    task = s.store.get_task(w.task_run_id)
    assert task.internal_status == TaskState.REVIEWING and task.approved_source_commit is None
    assert not s.store.get_reviews(task.id) and not s.store.get_operations(
        task.epic_run_id, kind=s.KIND
    )


def test_superseded_context_at_same_commits_is_not_current_review(setup):
    s, a, w, d, _, _, _, _ = setup
    latest = s.contexts.request(a, w.task_run_id, key="new-context")["context"]
    with pytest.raises(TaskApprovalError):
        s.approve(a, w.task_run_id, d, key="old-context")
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.REVIEWING
    d = d | {"context_id": latest["context_id"]}
    assert s.approve(a, w.task_run_id, d, key="new-context")["status"] == "APPROVED"


def test_process_loss_after_approved_review_recovers_same_review_and_pins(setup, monkeypatch):
    s, a, w, d, _, _, _, _ = setup

    def lost(*args, **kwargs):
        raise RuntimeError("lost after approval review")

    monkeypatch.setattr(s, "_finish", lost)
    with pytest.raises(RuntimeError, match="lost after approval"):
        s.approve(a, w.task_run_id, d, key="recover")
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.APPROVED
    assert s.store.get_operation("p", s.KIND, "recover").status == "PENDING"
    with pytest.raises(TaskApprovalError):
        s.require_current(a, w.task_run_id)
    with StateStore(s.settings.sqlite_path) as db:
        service = TaskApprovalService(s.settings, db)
        answer = service.approve(a, w.task_run_id, d, key="recover")
        assert answer["status"] == "APPROVED" and len(db.get_reviews(w.task_run_id)) == 1
        assert service.require_current(a, w.task_run_id)["operation_id"] == answer["operation_id"]


def test_pending_positive_decision_blocks_new_context_and_negative_takeover(setup, monkeypatch):
    s, a, w, d, changes, _, _, negative = setup

    def interrupted(*args, **kwargs):
        raise RuntimeError("lost before review")

    monkeypatch.setattr(s.integration, "register_task_review", interrupted)
    with pytest.raises(RuntimeError):
        s.approve(a, w.task_run_id, d, key="intent")
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.REVIEWING
    with pytest.raises(TaskReviewError, match="REVIEW_DECISION_PENDING"):
        s.contexts.request(a, w.task_run_id, key="overtaking-context")
    with pytest.raises(TaskChangesError):
        changes.request(a, w.task_run_id, negative, key="overtaking-negative")
    assert not s.store.get_reviews(w.task_run_id)


def test_pending_negative_decision_blocks_positive_takeover(setup, monkeypatch):
    s, a, w, d, changes, _, _, negative = setup

    def interrupted(*args, **kwargs):
        raise RuntimeError("lost before negative review")

    monkeypatch.setattr(changes, "_register", interrupted)
    with pytest.raises(RuntimeError):
        changes.request(a, w.task_run_id, negative, key="negative-intent")
    with pytest.raises(TaskApprovalError):
        s.approve(a, w.task_run_id, d, key="overtaking-positive")
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.REVIEWING
    assert not s.store.get_reviews(w.task_run_id)


def test_concurrent_identical_positive_decisions_create_one_approved_review(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    s, a, w, d, _, _, _, _ = setup
    barrier = Barrier(2)

    def request():
        with StateStore(s.settings.sqlite_path) as db:
            service = TaskApprovalService(s.settings, db)
            barrier.wait(5)
            return service.approve(a, w.task_run_id, d, key="concurrent")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request(), range(2)))
    assert (
        len({r["operation_id"] for r in results}) == 1
        and len(s.store.get_reviews(w.task_run_id)) == 1
    )
    assert s.require_current(a, w.task_run_id)["operation_id"] == results[0]["operation_id"]


def test_real_mcp_worker_foreign_run_and_injected_proof_cannot_approve(setup):
    s, a, w, d, _, _, _, _ = setup
    args = dict(project_id="p", task_run_id=w.task_run_id, request_key="mcp", decision=d)
    worker = RuntimeService(s.store, w, EventLog(stream=io.StringIO()), task_approval=s)
    integration = RuntimeService(s.store, a, EventLog(stream=io.StringIO()), task_approval=s)

    async def exercise():
        async with Client(create_server(worker)) as client:
            answer = await client.call_tool("task_approve", args)
            assert answer.structured_content["code"] == "FORBIDDEN"
        async with Client(create_server(integration)) as client:
            answer = await client.call_tool("task_approve", args | {"tests_passed": True})
            assert answer.structured_content["code"] == "INVALID_ARGUMENT"
            answer = await client.call_tool("task_approve", args | {"task_run_id": "foreign"})
            assert answer.structured_content["code"] == "FORBIDDEN"
            answer = await client.call_tool("task_approve", args)
            assert (
                answer.structured_content["ok"]
                and answer.structured_content["data"]["status"] == "APPROVED"
            )

    asyncio.run(exercise())


def test_same_key_cannot_change_reviewer_or_decision(setup):
    s, a, w, d, _, _, _, _ = setup
    s.approve(a, w.task_run_id, d, key="bound")
    before = s.store.db.total_changes
    with pytest.raises(TaskApprovalError, match="INTENT_MISMATCH"):
        s.approve(
            a.model_copy(update={"actor_id": "another-reviewer"}), w.task_run_id, d, key="bound"
        )
    with pytest.raises(TaskApprovalError, match="INTENT_MISMATCH"):
        s.approve(a, w.task_run_id, d | {"summary": "Changed decision"}, key="bound")
    assert s.store.db.total_changes == before and len(s.store.get_reviews(w.task_run_id)) == 1


def test_concurrent_positive_and_negative_decisions_have_one_winner(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from orchestrator.application.task_changes_service import TaskChangesService

    s, a, w, d, _, h, history, negative = setup
    barrier = Barrier(2)

    def request(kind):
        with StateStore(s.settings.sqlite_path) as db:
            approval = TaskApprovalService(s.settings, db)
            changes = TaskChangesService(s.settings, db, h, history)
            barrier.wait(5)
            try:
                result = (
                    approval.approve(a, w.task_run_id, d, key="positive-race")
                    if kind == "positive"
                    else changes.request(a, w.task_run_id, negative, key="negative-race")
                )
                return kind, result
            except (TaskApprovalError, TaskChangesError):
                return kind, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request, ("positive", "negative")))
    winners = [(kind, result) for kind, result in results if result is not None]
    assert len(winners) == 1 and len(s.store.get_reviews(w.task_run_id)) == 1
    kind, result = winners[0]
    task = s.store.get_task(w.task_run_id)
    assert task.internal_status == (TaskState.APPROVED if kind == "positive" else TaskState.WORKING)
    assert task.worker_slot == 1 and task.merge_commit is None
    assert len(h.sent) == (1 if kind == "positive" else 2)
