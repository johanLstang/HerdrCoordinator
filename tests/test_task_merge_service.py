import asyncio
import io
from pathlib import Path

import pytest
from mcp import Client
from test_runtime_lifecycle import FakeProcesses
from test_task_approval_service import (  # noqa: F401
    changes_setup,
    report_setup,
    review_setup,
    start_setup,
)

from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_approval_service import TaskApprovalService
from orchestrator.application.task_merge_service import TaskMergeError, TaskMergeService
from orchestrator.application.task_review_service import TaskReviewService
from orchestrator.domain.states import Kanban, TaskState
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


@pytest.fixture
def setup(review_setup, report_setup, request, tmp_path):  # noqa: F811
    reviews, actor, worker, _ = review_setup
    _, _, _, h, history, _ = report_setup
    marker = tmp_path / "allow-integration"
    settings = reviews.settings
    if getattr(request, "param", None) == "fail-integration":
        epic = reviews.store.get_epic(actor.epic_run_id)
        command = (
            *settings.worker_test_command[:2],
            "from pathlib import Path; assert Path('result.txt').is_file(); "
            f"assert Path.cwd()!=Path({epic.worktree_path!r}) or Path({str(marker)!r}).exists()",
        )
        settings = settings.model_copy(update={"worker_test_command": command})
        reviews = TaskReviewService(settings, reviews.store)
    context = reviews.request(actor, worker.task_run_id, key="review-delivery")["context"]
    approval = TaskApprovalService(settings, reviews.store)
    decision = dict(
        version=1,
        result="APPROVED",
        context_id=context["context_id"],
        summary="Reviewed complete current delivery",
        verified_criteria=context["acceptance_criteria"],
    )
    approval.approve(actor, worker.task_run_id, decision, key="approved")
    processes = FakeProcesses()
    h.exits = 0

    def exit_agent(name):
        assert name in h.agents
        h.exits += 1
        del h.agents[name]
        processes.alive = False

    h.exit_agent = exit_agent
    service = TaskMergeService(approval.settings, approval.store, h, history, processes=processes)
    service.integration_marker = marker
    yield service, actor, worker, h, history, processes


def deliver(s, a, w, key="deliver", test="verify"):
    return s.merge(a, w.task_run_id, key=key, verification_key=test)


def test_merge_tests_stop_done_in_order_with_actual_parents_and_retention(setup, monkeypatch):
    s, a, w, h, _, _ = setup
    task = s.store.get_task(w.task_run_id)
    epic = s.store.get_epic(a.epic_run_id)
    git = s.git_manager.git
    main, source, target = git.head("main"), git.head(task.branch), git.head(epic.branch)
    original = h.exit_agent

    def checked_exit(name):
        current = s.store.get_task(task.id)
        assert current.internal_status == TaskState.MERGING and current.worker_slot == 1
        assert current.merge_commit == git.head(epic.branch)
        tests = s.store.get_operations(epic.id, kind="task_delivery_test")
        assert len(tests) == 1 and tests[0].status == "SUCCEEDED"
        assert tests[0].result["merge_commit"] == current.merge_commit
        original(name)

    monkeypatch.setattr(h, "exit_agent", checked_exit)
    answer = deliver(s, a, w)
    done = s.store.get_task(task.id)
    assert answer["status"] == "DONE" and done.internal_status == TaskState.DONE
    assert done.kanban_status == Kanban.DONE and done.worker_slot is None and done.completed_at
    assert git.parents(done.merge_commit) == (target, source)
    assert git.head("main") == main and git.head(epic.branch) == done.merge_commit
    assert Path(task.worktree_path).is_dir() and git.head(task.branch) == source
    assert h.exits == h.starts == 1 and len(h.sent) == 1
    assert s.lifecycle.confirm_task_inactive(a, task.id)


def test_repeat_reopen_and_explicit_safe_cleanup_preserve_delivery_and_session(setup):
    s, a, w, h, history, p = setup
    first = deliver(s, a, w)
    task = s.store.get_task(w.task_run_id)
    before = s.store.db.total_changes
    again = deliver(s, a, w)
    assert again["status"] == "EXISTING" and again["merge_commit"] == first["merge_commit"]
    assert s.store.db.total_changes == before and h.exits == h.starts == 1
    with StateStore(s.settings.sqlite_path) as db:
        reopened = TaskMergeService(s.settings, db, h, history, processes=p)
        assert deliver(reopened, a, w)["status"] == "EXISTING"
        cleaned = reopened.cleanup(a, task.id, key="remove-after-done")
        assert cleaned.status == "SUCCEEDED" and not Path(task.worktree_path).exists()
        assert deliver(reopened, a, w)["status"] == "EXISTING"
        assert reopened.git_manager.git.head(task.branch) == task.current_commit
        assert db.get_task(task.id).codex_session_id == task.codex_session_id
        assert len(db.get_operations(a.epic_run_id, kind="merge_task_to_epic")) == 1


@pytest.mark.parametrize(
    "attack", ["worker", "foreign", "stale-task", "stale-epic", "missing-parent", "dirty"]
)
def test_unverified_approval_actor_or_git_cannot_begin_delivery(setup, attack):
    s, a, w, h, _, _ = setup
    task, epic = s.store.get_task(w.task_run_id), s.store.get_epic(a.epic_run_id)
    target = s.git_manager.git.head(epic.branch)
    if attack == "worker":
        a = w
    elif attack == "foreign":
        a = a.model_copy(update={"epic_run_id": "foreign"})
    elif attack == "missing-parent":
        approval = s.store.get_operation("p", "task_approve", "approved")
        s.store.update_operation(approval.model_copy(update={"status": "PENDING"}))
    else:
        path = Path(epic.worktree_path if attack == "stale-epic" else task.worktree_path)
        (path / "changed.txt").write_text("Different actual work\n")
        if attack != "dirty":
            s.git_manager.git.run("add", ".", cwd=path)
            s.git_manager.git.run("commit", "-m", "different", cwd=path)
        if attack == "stale-epic":
            target = s.git_manager.git.head(epic.branch)
    with pytest.raises(TaskMergeError):
        deliver(s, a, w)
    assert s.store.get_task(task.id).internal_status == TaskState.APPROVED
    assert s.store.get_task(task.id).merge_commit is None and h.exits == 0
    assert s.git_manager.git.head(epic.branch) == target
    assert not s.store.get_operations(epic.id, kind="task_merge")


@pytest.mark.parametrize("setup", ["fail-integration"], indirect=True)
def test_failed_integration_keeps_real_merge_and_slot_and_fresh_test_key_can_finish(setup):
    s, a, w, h, _, _ = setup
    marker = s.integration_marker
    failure = deliver(s, a, w)
    t = s.store.get_task(w.task_run_id)
    assert failure["status"] == "TEST_FAILED" and t.internal_status == TaskState.MERGING
    assert t.merge_commit == failure["merge_commit"] and t.worker_slot == 1
    assert Path(t.worktree_path).exists() and h.exits == 0
    marker.touch()
    assert deliver(s, a, w)["status"] == "TEST_FAILED" and h.exits == 0
    fixed = deliver(s, a, w, test="explicit-new-attempt")
    assert fixed["status"] == "DONE" and fixed["merge_commit"] == failure["merge_commit"]
    assert len(s.store.get_operations(a.epic_run_id, kind="merge_task_to_epic")) == 1
    assert len(s.store.get_operations(a.epic_run_id, kind="task_delivery_test")) == 2
    assert h.exits == h.starts == 1


def test_loss_after_git_before_journal_completion_recovers_tag_without_second_merge(
    setup, monkeypatch
):
    s, a, w, h, history, p = setup
    original = s.git_manager._complete_merge

    def crash(*args, **kwargs):
        raise RuntimeError("lost after actual Git merge")

    monkeypatch.setattr(s.git_manager, "_complete_merge", crash)
    with pytest.raises(RuntimeError):
        deliver(s, a, w)
    epic = s.store.get_epic(a.epic_run_id)
    sha = s.git_manager.git.head(epic.branch)
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.MERGING and h.exits == 0
    monkeypatch.setattr(s.git_manager, "_complete_merge", original)
    with StateStore(s.settings.sqlite_path) as db:
        recovered = TaskMergeService(s.settings, db, h, history, processes=p)
        assert deliver(recovered, a, w)["merge_commit"] == sha
    assert h.exits == h.starts == 1


def test_loss_after_passed_test_does_not_rerun_test_or_merge(setup, monkeypatch):
    s, a, w, h, history, p = setup
    original = s.lifecycle.stop_delivered_task

    def lost(*args, **kwargs):
        raise RuntimeError("lost before stop")

    monkeypatch.setattr(s.lifecycle, "stop_delivered_task", lost)
    with pytest.raises(RuntimeError):
        deliver(s, a, w)
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.MERGING
    assert s.store.get_task(w.task_run_id).worker_slot == 1
    monkeypatch.setattr(s.lifecycle, "stop_delivered_task", original)
    with StateStore(s.settings.sqlite_path) as db:
        service = TaskMergeService(s.settings, db, h, history, processes=p)
        assert deliver(service, a, w, test="another-key")["status"] == "DONE"
    assert len(s.store.get_operations(a.epic_run_id, kind="task_delivery_test")) == 1
    assert h.exits == 1


def test_unconfirmed_stop_keeps_merging_reservation_and_known_merge(setup):
    s, a, w, h, _, p = setup
    p.lingering = True
    with pytest.raises(TaskMergeError):
        deliver(s, a, w)
    task = s.store.get_task(w.task_run_id)
    assert task.internal_status == TaskState.MERGING and task.worker_slot == 1 and task.merge_commit
    assert task.completed_at is None and Path(task.worktree_path).exists()
    p.lingering = False
    assert deliver(s, a, w)["status"] == "DONE" and h.exits == 1
    assert len(s.store.get_operations(a.epic_run_id, kind="task_delivery_test")) == 1


def test_actual_mcp_scope_and_input_cannot_inject_done_or_merge_proof(setup):
    s, a, w, _, _, _ = setup
    args = dict(
        project_id="p", task_run_id=w.task_run_id, request_key="mcp", verification_key="test"
    )

    async def exercise():
        runtime = RuntimeService(s.store, w, EventLog(stream=io.StringIO()), task_merge=s)
        async with Client(create_server(runtime)) as client:
            result = await client.call_tool("task_merge", args)
            assert result.structured_content["code"] == "FORBIDDEN"
        runtime = RuntimeService(s.store, a, EventLog(stream=io.StringIO()), task_merge=s)
        async with Client(create_server(runtime)) as client:
            result = await client.call_tool("task_merge", args | {"tests_passed": True})
            assert result.structured_content["code"] == "INVALID_ARGUMENT"
            result = await client.call_tool("task_merge", args | {"task_run_id": "foreign"})
            assert result.structured_content["code"] == "FORBIDDEN"
            result = await client.call_tool("task_merge", args)
            assert result.structured_content["data"]["status"] == "DONE"

    asyncio.run(exercise())


def test_concurrent_identical_deliveries_have_one_merge_test_stop_and_done(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    s, a, w, h, history, processes = setup
    barrier = Barrier(2)

    def request():
        with StateStore(s.settings.sqlite_path) as db:
            service = TaskMergeService(s.settings, db, h, history, processes=processes)
            barrier.wait(5)
            try:
                return deliver(service, a, w)
            except TaskMergeError as error:
                assert str(error) == "DELIVERY_BUSY"
                return {"status": "BUSY"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request(), range(2)))
    assert {r["status"] for r in results} <= {"DONE", "EXISTING", "BUSY"}
    successful = [r for r in results if r["status"] != "BUSY"]
    assert successful and len({r["merge_commit"] for r in successful}) == 1
    assert deliver(s, a, w)["status"] == "EXISTING"
    for kind in ("task_merge", "merge_task_to_epic", "task_delivery_test", "stop_runtime"):
        assert len(s.store.get_operations(a.epic_run_id, kind=kind)) == 1
    assert h.exits == h.starts == 1


def test_unknown_test_result_is_not_rerun_until_explicit_new_key(setup, monkeypatch):
    s, a, w, h, _, _ = setup
    finish = s.git_manager._finish

    def lost(op, status, **kwargs):
        if op.kind == "task_delivery_test":
            raise RuntimeError("lost actual test result")
        return finish(op, status, **kwargs)

    monkeypatch.setattr(s.git_manager, "_finish", lost)
    with pytest.raises(RuntimeError):
        deliver(s, a, w)
    task = s.store.get_task(w.task_run_id)
    assert task.internal_status == TaskState.MERGING and task.merge_commit and h.exits == 0
    monkeypatch.setattr(s.git_manager, "_finish", finish)
    with pytest.raises(TaskMergeError, match="TEST_OUTCOME_UNKNOWN"):
        deliver(s, a, w)
    assert len(s.store.get_operations(a.epic_run_id, kind="task_delivery_test")) == 1
    assert deliver(s, a, w, test="operator-reverify")["merge_commit"] == task.merge_commit
    assert len(s.store.get_operations(a.epic_run_id, kind="merge_task_to_epic")) == 1


def test_loss_after_physical_exit_recovers_same_stopped_runtime_without_new_worker(
    setup, monkeypatch
):
    s, a, w, h, history, processes = setup
    original = h.exit_agent

    def lost(name):
        original(name)
        raise RuntimeError("lost after physical exit")

    monkeypatch.setattr(h, "exit_agent", lost)
    with pytest.raises(RuntimeError):
        deliver(s, a, w)
    t = s.store.get_task(w.task_run_id)
    assert t.worker_slot == 1 and t.internal_status == TaskState.MERGING and t.merge_commit
    with StateStore(s.settings.sqlite_path) as db:
        recovered = TaskMergeService(s.settings, db, h, history, processes=processes)
        assert deliver(recovered, a, w)["status"] == "DONE"
    assert h.exits == h.starts == 1
    assert len(s.store.get_operations(a.epic_run_id, kind="task_delivery_test")) == 1


@pytest.mark.parametrize("change", ["task", "epic", "approval", "test"])
def test_changed_postmerge_proof_cannot_finish_saved_delivery(setup, monkeypatch, change):
    s, a, w, h, _, _ = setup

    def pause(*args, **kwargs):
        raise RuntimeError("pause after tested merge")

    monkeypatch.setattr(s.lifecycle, "stop_delivered_task", pause)
    with pytest.raises(RuntimeError):
        deliver(s, a, w)
    t = s.store.get_task(w.task_run_id)
    sha = t.merge_commit
    if change in {"task", "epic"}:
        record = t if change == "task" else s.store.get_epic(a.epic_run_id)
        path = Path(record.worktree_path)
        (path / "later.txt").write_text("Changed after verified delivery\n")
        s.git_manager.git.run("add", ".", cwd=path)
        s.git_manager.git.run("commit", "-m", "later", cwd=path)
    elif change == "approval":
        op = s.store.get_operation("p", "task_approve", "approved")
        s.store.update_operation(op.model_copy(update={"status": "PENDING"}))
    else:
        op = s.store.get_operations(a.epic_run_id, kind="task_delivery_test")[0]
        s.store.update_operation(op.model_copy(update={"status": "FAILED"}))
    with pytest.raises(TaskMergeError):
        deliver(s, a, w)
    t = s.store.get_task(w.task_run_id)
    assert t.merge_commit == sha and t.internal_status == TaskState.MERGING and t.worker_slot == 1
    assert h.exits == 0 and Path(t.worktree_path).exists()


@pytest.mark.parametrize("runtime", ["working", "missing", "no-slot", "foreign-session"])
def test_runtime_binding_and_idle_checked_inside_first_git_merge(setup, runtime):
    s, a, w, h, _, _ = setup
    t = s.store.get_task(w.task_run_id)
    e = s.store.get_epic(a.epic_run_id)
    target = s.git_manager.git.head(e.branch)
    if runtime == "working":
        h.status = "working"
    elif runtime == "missing":
        h.agents.clear()
    elif runtime == "no-slot":
        s.store.update_runtime_metadata(t.model_copy(update={"worker_slot": None}))
    else:
        h.session_id = "00000000-0000-4000-8000-000000000002"
    with pytest.raises(TaskMergeError):
        deliver(s, a, w)
    assert s.store.get_task(t.id).merge_commit is None
    assert s.git_manager.git.head(e.branch) == target and h.exits == 0
