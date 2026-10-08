import asyncio
import base64
import hashlib
import io
from pathlib import Path

import pytest
from mcp import Client
from test_task_start import setup as start_setup  # noqa: F401
from test_worker_report_service import setup as report_setup  # noqa: F401

from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_review_service import TaskReviewError, TaskReviewService
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.states import TaskState
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


@pytest.fixture
def setup(report_setup):  # noqa: F811
    reports, worker, report, _, _, actor = report_setup
    assert reports.collect(worker, worker.task_run_id)["status"] == "READY_FOR_REVIEW"
    rules = EpicReviewSpec(
        version=1,
        project_id="p",
        epic_id="E",
        requirements=["Preserve the bounded result"],
        acceptance_criteria=["Independent tests precede review"],
        sources=["README.md"],
    )
    settings = reports.settings.model_copy(update={"review_context": rules})
    yield TaskReviewService(settings, reports.store), actor, worker, report


def commit(s, path, message):
    s.git.run("add", ".", cwd=path)
    s.git.run("commit", "-m", message, cwd=path)
    return s.git.run("rev-parse", "HEAD", cwd=path).strip()


def test_complete_context_binds_spec_sources_diff_tests_and_commits(setup):
    s, actor, worker, report = setup
    t = s.store.get_task(worker.task_run_id)
    e = s.store.get_epic(t.epic_run_id)
    original_epic = s.git.head(e.branch)
    main = s.git.head("main")
    r = s.request(actor, t.id, key="review-one")
    c = r["context"]
    assert r["status"] == "READY" and c["reviewable"]
    assert c["task_commit"] == report["commit"] and c["epic_commit"] == original_epic
    assert c["task_specification"]["goal"] == "Implement bounded result"
    assert c["acceptance_criteria"] == ["Result survives reopening"]
    assert c["epic_requirements"]["requirements"] == ["Preserve the bounded result"]
    assert {f["path"] for f in c["changed_files"]} == {"result.txt"}
    patch = base64.b64decode(c["diff"]["patch_base64"])
    assert (
        b"Persisted bounded result" in patch
        and hashlib.sha256(patch).hexdigest() == c["diff"]["sha256"]
    )
    assert (
        c["diff"]["complete"]
        and c["tests"]["status"] == "SUCCEEDED"
        and c["tests"]["exit_code"] == 0
    )
    assert (
        c["tests"]["source_commit"] == c["task_commit"]
        and c["tests"]["target_commit"] == c["epic_commit"]
    )
    assert {source["side"] for source in c["sources"]} == {"epic", "task"}
    assert all(source["text"] == "Harmless task start fixture\n" for source in c["sources"])
    assert s.store.get_task(t.id).internal_status == TaskState.REVIEWING
    assert s.store.get_task(t.id).merge_commit is None and not s.store.get_reviews(t.id)
    assert s.git.head(e.branch) == original_epic and s.git.head("main") == main
    assert s.store.get_task(t.id).worker_slot == 1


def test_repeat_and_reopen_reuse_context_event_and_tests(setup):
    s, a, w, _ = setup
    r = s.request(a, w.task_run_id, key="once")
    before = s.store.db.total_changes
    repeated = s.request(a, w.task_run_id, key="once")
    assert repeated["status"] == "EXISTING" and repeated["context"] == r["context"]
    assert s.store.db.total_changes == before
    with StateStore(s.settings.sqlite_path) as db:
        again = TaskReviewService(s.settings, db).request(a, w.task_run_id, key="once")
        assert again == repeated
        assert (
            len(
                [
                    o
                    for o in db.get_operations(a.epic_run_id, kind="verify_task")
                    if o.idempotency_key.startswith("review-test:")
                ]
            )
            == 1
        )


def test_changed_epic_syncs_and_runs_new_tests_before_review(setup):
    s, a, w, _ = setup
    t = s.store.get_task(w.task_run_id)
    e = s.store.get_epic(t.epic_run_id)
    (Path(e.worktree_path) / "epic-rule.txt").write_text("New epic result\n")
    new_epic = commit(s, Path(e.worktree_path), "advance epic")
    settings = s.settings.model_copy(
        update={
            "worker_test_command": (
                *s.settings.worker_test_command[:2],
                "from pathlib import Path; assert Path('result.txt').is_file(); "
                "assert Path('epic-rule.txt').read_text()=='New epic result\\n'",
            )
        }
    )
    s = TaskReviewService(settings, s.store)
    c = s.request(a, t.id, key="new-base")["context"]
    assert c["epic_commit"] == new_epic and c["task_commit"] != t.current_commit
    assert s.git.parents(c["task_commit"]) == (t.current_commit, new_epic)
    assert (
        c["tests"]["source_commit"] == c["task_commit"] and c["tests"]["target_commit"] == new_epic
    )
    assert c["tests"]["status"] == "SUCCEEDED"
    assert (Path(t.worktree_path) / "epic-rule.txt").is_file() and s.git.head(e.branch) == new_epic
    assert len(s.store.get_operations(e.id, kind="verify_task")) == 2


def test_conflict_preserves_work_and_does_not_enter_review(setup):
    s, a, w, _ = setup
    t = s.store.get_task(w.task_run_id)
    e = s.store.get_epic(t.epic_run_id)
    (Path(e.worktree_path) / "result.txt").write_text("Conflicting epic result\n")
    target = commit(s, Path(e.worktree_path), "conflict")
    r = s.request(a, t.id, key="conflict")
    assert r["status"] == "NOT_REVIEWABLE" and not r["context"]["reviewable"]
    assert r["context"]["reason"] == "REVIEW_SYNC_CONFLICT"
    assert (
        s.git.in_progress(Path(t.worktree_path))
        and s.store.get_task(t.id).internal_status == TaskState.BLOCKED
    )
    assert s.store.get_task(t.id).worker_slot == 1 and s.git.head(e.branch) == target
    assert len(s.store.get_operations(e.id, kind="verify_task")) == 1
    before = s.store.db.total_changes
    assert s.request(a, t.id, key="conflict") == r
    assert s.store.db.total_changes == before and s.git.in_progress(Path(t.worktree_path))


def test_test_failure_is_explicit_and_same_key_does_not_retry(setup):
    s, a, w, _ = setup
    s = TaskReviewService(
        s.settings.model_copy(
            update={
                "worker_test_command": (
                    s.settings.worker_test_command[0],
                    "-c",
                    "raise SystemExit(3)",
                )
            }
        ),
        s.store,
    )
    r = s.request(a, w.task_run_id, key="fail")
    assert r["status"] == "NOT_REVIEWABLE" and r["context"]["reason"] == "REVIEW_TEST_FAILED"
    assert r["context"]["tests"]["result"]["exit_code"] == 3
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.READY_FOR_REVIEW
    before = s.store.db.total_changes
    assert s.request(a, w.task_run_id, key="fail") == r and s.store.db.total_changes == before


def test_truncated_diff_never_becomes_reviewing(setup):
    s, a, w, _ = setup
    s = TaskReviewService(s.settings, s.store, max_diff_bytes=8)
    r = s.request(a, w.task_run_id, key="bounded")
    assert r["context"]["reason"] == "REVIEW_GIT_INCOMPLETE" and not r["context"]["diff_complete"]
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.READY_FOR_REVIEW
    assert len(s.store.get_operations(a.epic_run_id, kind="verify_task")) == 1


@pytest.mark.parametrize(
    "mode",
    [
        "missing-rules",
        "wrong-epic",
        "missing-command",
        "worker",
        "other-task",
        "missing-spec",
        "tampered-spec",
        "new-worker-commit",
    ],
)
def test_unverified_inputs_do_not_create_review_state(setup, mode):
    s, a, w, _ = setup
    t = s.store.get_task(w.task_run_id)
    if mode == "missing-rules":
        s = TaskReviewService(s.settings.model_copy(update={"review_context": None}), s.store)
    elif mode == "wrong-epic":
        s = TaskReviewService(
            s.settings.model_copy(
                update={
                    "review_context": s.settings.review_context.model_copy(
                        update={"epic_id": "foreign"}
                    )
                }
            ),
            s.store,
        )
    elif mode == "missing-command":
        s = TaskReviewService(s.settings.model_copy(update={"worker_test_command": ()}), s.store)
    elif mode == "worker":
        a = w
    elif mode == "other-task":
        a = a.model_copy(update={"epic_run_id": "foreign"})
    elif mode == "missing-spec":
        op = s.store.get_operation("p", "task_start", t.task_id)
        s.store.update_operation(op.model_copy(update={"status": "FAILED"}))
    elif mode == "tampered-spec":
        op = s.store.get_operation("p", "task_start", t.task_id)
        s.store.update_operation(
            op.model_copy(
                update={
                    "result": op.result
                    | {"spec": op.result["spec"] | {"goal": "Different unverified goal"}}
                }
            )
        )
    elif mode == "new-worker-commit":
        (Path(t.worktree_path) / "extra.txt").write_text("Unreported Worker change")
        commit(s, Path(t.worktree_path), "without new handoff")
    with pytest.raises(TaskReviewError):
        s.request(a, t.id, key="bad")
    assert s.store.get_task(t.id).internal_status == TaskState.READY_FOR_REVIEW
    assert not s.store.get_operations(t.epic_run_id, kind="task_review_request")


def test_stale_saved_context_and_new_configuration_are_rejected(setup):
    s, a, w, _ = setup
    s.request(a, w.task_run_id, key="pinned")
    t = s.store.get_task(w.task_run_id)
    e = s.store.get_epic(t.epic_run_id)
    (Path(e.worktree_path) / "later.txt").write_text("Later epic code\n")
    commit(s, Path(e.worktree_path), "later")
    with pytest.raises(TaskReviewError):
        s.request(a, t.id, key="pinned")
    changed = TaskReviewService(s.settings.model_copy(update={"worker_test_timeout": 2}), s.store)
    with pytest.raises(TaskReviewError):
        changed.request(a, t.id, key="pinned")
    assert s.store.get_task(t.id).internal_status == TaskState.REVIEWING


@pytest.mark.parametrize("mode", ["missing", "symlink", "binary", "oversized"])
def test_sources_must_be_present_bounded_regular_versioned_text(setup, mode):
    s, a, w, _ = setup
    t = s.store.get_task(w.task_run_id)
    e = s.store.get_epic(t.epic_run_id)
    p = Path(e.worktree_path) / "source.md"
    if mode == "symlink":
        p.symlink_to("/etc/passwd")
    elif mode == "binary":
        p.write_bytes(b"\x00not text")
    elif mode == "oversized":
        p.write_text("X" * (s.MAX_SOURCE_BYTES + 1))
    if mode != "missing":
        commit(s, Path(e.worktree_path), "source fixture")
    rules = s.settings.review_context.model_copy(update={"sources": ["source.md"]})
    bad = TaskReviewService(s.settings.model_copy(update={"review_context": rules}), s.store)
    result = bad.request(a, w.task_run_id, key="source")
    assert result["status"] == "NOT_REVIEWABLE" and not result["context"]["reviewable"]
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.READY_FOR_REVIEW


def test_mcp_worker_scope_and_argument_injection_cannot_request_review(setup):
    s, a, w, _ = setup

    async def exercise(actor):
        runtime = RuntimeService(s.store, actor, EventLog(stream=io.StringIO()), task_review=s)
        async with Client(create_server(runtime)) as client:
            tools = await client.list_tools()
            assert "task_review_request" in {t.name for t in tools.tools}
            result = await client.call_tool(
                "task_review_request",
                {
                    "project_id": "p",
                    "task_run_id": w.task_run_id,
                    "request_key": "mcp",
                    "role": "Integration",
                },
            )
            assert result.is_error
            result = await client.call_tool(
                "task_review_request",
                {"project_id": "p", "task_run_id": w.task_run_id, "request_key": "mcp"},
            )
            if actor == w:
                assert result.structured_content["code"] == "FORBIDDEN"
            else:
                assert (
                    not result.is_error
                    and result.structured_content["data"]["context"]["reviewable"]
                )

    before = s.store.db.total_changes
    asyncio.run(exercise(w))
    assert s.store.db.total_changes == before
    asyncio.run(exercise(a))
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.REVIEWING


def test_crash_after_tests_reuses_verification_before_atomic_review_state(setup, monkeypatch):
    s, a, w, _ = setup
    original = s._context

    def interrupted(*args, **kwargs):
        raise RuntimeError("simulated process loss after independent tests")

    monkeypatch.setattr(s, "_context", interrupted)
    with pytest.raises(RuntimeError, match="simulated process loss"):
        s.request(a, w.task_run_id, key="recover")
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.READY_FOR_REVIEW
    operation = s.store.get_operation("p", s.KIND, "recover")
    assert operation.status == "PENDING" and operation.result["stage"] == "TESTED"
    count = len(s.store.get_operations(a.epic_run_id, kind="verify_task"))
    monkeypatch.setattr(s, "_context", original)
    with StateStore(s.settings.sqlite_path) as db:
        r = TaskReviewService(s.settings, db).request(a, w.task_run_id, key="recover")
        assert r["status"] == "READY"
        assert len(db.get_operations(a.epic_run_id, kind="verify_task")) == count
        assert db.get_task(w.task_run_id).internal_status == TaskState.REVIEWING


def test_unknown_test_outcome_is_not_reexecuted_on_same_request(setup, monkeypatch):
    s, a, w, _ = setup
    original = s.integration._finish

    def lost_result(operation, status, **kwargs):
        if operation.kind == "verify_task":
            raise RuntimeError("lost test response")
        return original(operation, status, **kwargs)

    monkeypatch.setattr(s.integration, "_finish", lost_result)
    with pytest.raises(RuntimeError, match="lost test response"):
        s.request(a, w.task_run_id, key="unknown")
    unknown = [
        o
        for o in s.store.get_operations(a.epic_run_id, kind="verify_task")
        if o.idempotency_key.startswith("review-test:")
    ]
    assert len(unknown) == 1 and unknown[0].status == "PENDING"
    monkeypatch.setattr(s.integration, "_finish", original)
    r = s.request(a, w.task_run_id, key="unknown")
    assert (
        r["status"] == "NOT_REVIEWABLE" and r["context"]["reason"] == "REVIEW_TEST_OUTCOME_UNKNOWN"
    )
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.READY_FOR_REVIEW
    assert len(s.store.get_operations(a.epic_run_id, kind="verify_task")) == 2


def test_duplicate_concurrent_requests_share_one_context_and_test_operation(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    s, a, w, _ = setup
    barrier = Barrier(2)

    def request():
        with StateStore(s.settings.sqlite_path) as db:
            service = TaskReviewService(s.settings, db)
            barrier.wait(5)
            return service.request(a, w.task_run_id, key="concurrent")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request(), range(2)))
    assert all(r["context"]["reviewable"] for r in results)
    assert len({r["context"]["context_id"] for r in results}) == 1
    assert (
        len(
            [
                o
                for o in s.store.get_operations(a.epic_run_id, kind="verify_task")
                if o.idempotency_key.startswith("review-test:")
            ]
        )
        == 1
    )
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.REVIEWING


def test_epic_advance_after_test_result_invalidates_current_request(setup, monkeypatch):
    s, a, w, _ = setup
    t = s.store.get_task(w.task_run_id)
    e = s.store.get_epic(t.epic_run_id)
    original = s.integration.verify_task

    def verify(*args, **kwargs):
        result = original(*args, **kwargs)
        (Path(e.worktree_path) / "later.txt").write_text("Advanced after testing\n")
        commit(s, Path(e.worktree_path), "concurrent external epic change")
        return result

    monkeypatch.setattr(s.integration, "verify_task", verify)
    r = s.request(a, t.id, key="race")
    assert not r["context"]["reviewable"] and r["context"]["reason"] == "REVIEW_CONTEXT_STALE"
    assert s.store.get_task(t.id).internal_status == TaskState.READY_FOR_REVIEW
