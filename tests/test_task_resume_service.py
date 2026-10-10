"""F32 real Git/SQLite/service chains; native runtime/board controlled here."""

import asyncio
import io
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from mcp import Client
from test_task_attention_service import park
from test_task_attention_service import setup as attention_setup  # noqa: F401
from test_task_start import setup as start_setup  # noqa: F401
from test_worker_report_service import setup as report_setup  # noqa: F401

from orchestrator.adapters.codex import CodexError
from orchestrator.adapters.git import GitError
from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.runtime_lifecycle_service import LifecycleError
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_attention_service import TaskAttentionService
from orchestrator.application.task_resume_service import ResumeError, TaskResumeService
from orchestrator.application.task_start_service import TaskStartError, TaskStartService
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.domain.models import utc_now
from orchestrator.domain.policy import Role
from orchestrator.domain.states import Kanban, TaskState
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


def run(a):
    return asyncio.run(a)


@pytest.fixture
def setup(attention_setup):  # noqa: F811
    s, a, w, b, h, history, p, d = attention_setup
    parked = park(s, a, w, d)
    task = s.store.get_task(w.task_run_id)
    history.read_session = lambda sid, cwd: {"session_id": sid, "id": sid, "cwd": cwd}
    h.resumes = 0

    def resume(name, pane, cwd, sid):
        assert sid == task.codex_session_id
        h.resumes += 1
        h.agents[name] = h.workspaces[pane] | {"name": name}
        p.alive = True

    h.resume_agent = resume

    def prompt(name, text, *, timeout_ms):
        assert name in h.agents
        h.sent.append(text)
        history.reply(text, turn_id="input-" + str(len(h.sent)))

    h.prompt = prompt
    decision = dict(
        input_id=str(uuid4()), blocker_id=parked["blocker_id"], answer="RETENTION_DAYS=7"
    )
    yield TaskResumeService(s), a, w, b, h, history, p, decision


def call(s, a, w, d):
    return run(s.resume(a, w.task_run_id, d))


def test_complete_input_saved_before_effects_same_sid_ack_and_exact_replay(setup):
    s, a, w, b, h, history, p, d = setup
    before = s.store.get_task(w.task_run_id)
    old = h.resume_agent

    def resume(*args):
        op = s.store.get_operation("p", s.KIND, d["input_id"])
        assert op.result["decision"] == dict(version=1, **d)
        assert s.store.get_task(before.id).worker_slot == 1
        assert s.store.get_task(before.id).kanban_status == Kanban.ATTENTION
        old(*args)

    h.resume_agent = resume
    result = call(s, a, w, d)
    assert result["stage"] == "ACTIVE_AND_SYNCED"
    after = s.store.get_task(before.id)
    assert after.internal_status == TaskState.WORKING and after.kanban_status == Kanban.ACTIVE
    assert (before.codex_session_id, before.branch, before.worktree_path) == (
        after.codex_session_id,
        after.branch,
        after.worktree_path,
    )
    assert b.task["status"] == "InProgress"
    assert h.resumes == 1 and len(h.sent) == 2
    assert call(s, a, w, d) == result and h.resumes == 1 and len(h.sent) == 2
    assert s.git.head(after.branch) == s.git.head(before.branch)


def test_physical_resume_without_input_ack_stays_attention_and_retry_observes(setup):
    s, a, w, b, h, history, _, d = setup
    delivered = []

    def prompt(name, text, *, timeout_ms):
        h.sent.append(text)
        delivered.append(text)
        raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    h.prompt = prompt
    result = call(s, a, w, d)
    assert result["stage"] == "DISPATCH_REQUESTED"
    task = s.store.get_task(w.task_run_id)
    assert task.internal_status == TaskState.PARKED and task.worker_slot == 1
    assert run(s.sync.sync_task(a, task.id))["status"] == "SYNCED"
    assert b.task["status"] == "NeedsInput"
    assert (
        call(s, a, w, d)["stage"] == "DISPATCH_REQUESTED" and h.resumes == 1 and len(delivered) == 1
    )
    history.reply(delivered[0], turn_id="late-native-input")
    assert call(s, a, w, d)["stage"] == "ACTIVE_AND_SYNCED"
    assert h.resumes == 1 and len(delivered) == 1


def test_second_response_and_same_id_changed_answer_are_rejected(setup):
    s, a, w, _, h, _, _, d = setup
    call(s, a, w, d)
    for decision in [d | {"answer": "different"}, d | {"input_id": str(uuid4())}]:
        with pytest.raises(ResumeError):
            call(s, a, w, decision)
    assert h.resumes == 1 and len(h.sent) == 2


@pytest.mark.parametrize("change", ["session", "worktree", "head", "journal"])
def test_missing_or_changed_resources_preserve_answer_without_start(setup, change):
    s, a, w, _, h, history, _, d = setup
    if change == "session":

        def missing(*args):
            raise CodexError("CODEX_METADATA_REJECTED")

        history.read_session = missing
    elif change == "worktree":
        path = Path(s.store.get_task(w.task_run_id).worktree_path)
        path.rename(path.with_name("preserved-away"))
    elif change == "head":
        # Persist pending intent and exact HEAD, then actual user commit changes it.
        op = s._saved(a, s.store.get_task(w.task_run_id), s_input(d))
        s._save(op, resource_commit=s.git.head(s.store.get_task(w.task_run_id).branch))
        path = Path(s.store.get_task(w.task_run_id).worktree_path)
        (path / "later.txt").write_text("later")
        s.git.run("add", "later.txt", cwd=path)
        s.git.run("commit", "-m", "later", cwd=path)
    else:
        op = s._saved(a, s.store.get_task(w.task_run_id), s_input(d))
        s.store.update_operation(
            op.model_copy(
                update={
                    "result": op.result
                    | {"decision": op.result["decision"] | {"answer": "tampered"}}
                }
            )
        )
    with pytest.raises(ResumeError):
        call(s, a, w, d)
    saved = s.store.get_operation("p", s.KIND, d["input_id"])
    assert saved is not None and h.resumes == 0 and len(h.sent) == 1
    if change != "journal":
        assert saved.result["decision"]["answer"] == d["answer"]


def s_input(d):
    from orchestrator.domain.attention import InputDecision

    return InputDecision.model_validate(d)


@pytest.mark.parametrize("role", [Role.WORKER, Role.COORDINATOR])
def test_wrong_role_creates_no_input(setup, role):
    s, a, w, _, h, _, _, d = setup
    with pytest.raises(ResumeError):
        call(s, a.model_copy(update={"role": role}), w, d)
    assert not s.store.get_operations(a.epic_run_id, kind=s.KIND) and h.resumes == 0


def test_ack_timeout_keeps_input_and_reservation_and_never_resends(setup):
    s, a, w, _, h, _, _, d = setup
    h.prompt = lambda name, text, timeout_ms: h.sent.append(text)
    call(s, a, w, d)
    op = s.store.get_operation("p", s.KIND, d["input_id"])
    s.store.update_operation(
        op.model_copy(
            update={
                "result": op.result | {"deadline": (utc_now() - timedelta(seconds=1)).isoformat()}
            }
        )
    )
    with pytest.raises(ResumeError, match="INPUT_ACK_TIMEOUT"):
        call(s, a, w, d)
    with pytest.raises(ResumeError, match="INPUT_ACK_TIMEOUT"):
        call(s, a, w, d)
    assert s.store.get_task(w.task_run_id).worker_slot == 1 and h.resumes == 1 and len(h.sent) == 2


@pytest.mark.parametrize("completion", ["early", "late", "missing"])
def test_delayed_input_ack_observation_requires_proven_original_deadline(
    setup, monkeypatch, completion
):
    s, a, w, _, h, history, _, d = setup
    h.prompt = lambda name, text, timeout_ms: h.sent.append(text)
    call(s, a, w, d)
    delivered = int(utc_now().timestamp())
    sid = s.store.get_task(w.task_run_id).codex_session_id
    monkeypatch.setattr(
        "orchestrator.application.task_resume_service.utc_now",
        lambda: utc_now() + timedelta(seconds=60),
    )
    with pytest.raises(ResumeError, match="INPUT_ACK_TIMEOUT"):
        call(s, a, w, d)
    history.reply(h.sent[-1], turn_id="delayed-input-observation")
    if completion != "missing":
        history.turns[-1]["completedAt"] = delivered + (60 if completion == "late" else 0)
    if completion == "early":
        assert call(s, a, w, d)["stage"] == "ACTIVE_AND_SYNCED"
        op = s.store.get_operation("p", s.KIND, d["input_id"])
        assert op.result["ack"]["timely_completion_verified"] is True
        assert call(s, a, w, d)["stage"] == "ACTIVE_AND_SYNCED"
    else:
        with pytest.raises(ResumeError, match="INPUT_ACK_TIMEOUT"):
            call(s, a, w, d)
    assert s.store.get_task(w.task_run_id).codex_session_id == sid
    assert len(h.sent) == 2 and h.resumes == 1


def test_reopen_after_unknown_native_dispatch_only_observes_then_sync(setup):
    s, a, w, b, h, history, p, d = setup
    delivered = []
    h.prompt = lambda name, text, timeout_ms: delivered.append(text)
    call(s, a, w, d)
    history.reply(delivered[0], turn_id="reopened-input")
    with StateStore(s.settings.sqlite_path) as db:
        sync = TeamPlayerSyncService(
            s.settings,
            db,
            b,
            project_id="p",
            external_project_id=s.sync.external_project_id,
            user_id=s.sync.user_id,
            codex=history,
            processes=p,
        )
        other = TaskResumeService(
            TaskAttentionService(s.settings, db, sync, h, history, processes=p)
        )
        assert call(other, a, w, d)["stage"] == "ACTIVE_AND_SYNCED"
    assert h.resumes == 1 and len(delivered) == 1


def test_generic_lifecycle_resume_cannot_bypass_saved_input_ack(setup):
    s, a, w, _, h, _, _, d = setup
    s._saved(a, s.store.get_task(w.task_run_id), s_input(d))
    with pytest.raises(LifecycleError, match="INPUT_RESUME_REQUIRED"):
        s.lifecycle.resume_task(a, w.task_run_id, key="caller-bypass")
    assert h.resumes == 0 and s.store.get_task(w.task_run_id).worker_slot is None


def test_sdk_tools_bound_to_integration_and_no_answer_in_result_or_logs(setup):
    s, a, w, _, h, _, _, d = setup

    async def check():
        output = io.StringIO()
        service = RuntimeService(s.store, a, EventLog(output), task_resume=s)
        async with Client(create_server(service)) as client:
            tools = await client.list_tools()
            assert {"resume_task", "worker_resume"} <= {t.name for t in tools.tools}
            request = dict(project_id="p", task_run_id=w.task_run_id, decision=d)
            result = await client.call_tool("resume_task", request)
            assert result.structured_content["ok"]
            assert d["answer"] not in json.dumps(result.structured_content) + output.getvalue()
            repeat = await client.call_tool("worker_resume", request)
            assert repeat.structured_content == result.structured_content
            rejected = await client.call_tool("resume_task", request | {"worker_slot": 1})
            assert not rejected.structured_content["ok"]
        assert h.resumes == 1

    run(check())


def test_full_capacity_waits_reopened_input_then_real_failed_unstarted_claim_releases(
    setup, monkeypatch
):
    s, a, w, b, h, history, p, d = setup
    original = s.store.get_operation("p", "task_start", "T").result["spec"]
    start = TaskStartService(s.settings, s.store, h, history, processes=p)
    occupied, _ = start.claim(a, a.epic_run_id, original | {"task_id": "T2"})
    result = call(s, a, w, d)
    assert result["reason"] == "WORKER_CAPACITY_UNAVAILABLE"
    assert h.resumes == 0 and s.store.get_task(w.task_run_id).worker_slot is None
    assert (
        s.store.get_operation("p", s.KIND, d["input_id"]).result["decision"]["answer"]
        == d["answer"]
    )
    with StateStore(s.settings.sqlite_path) as db:
        sync = TeamPlayerSyncService(
            s.settings,
            db,
            b,
            project_id="p",
            external_project_id=s.sync.external_project_id,
            user_id=s.sync.user_id,
            codex=history,
            processes=p,
        )
        other = TaskResumeService(
            TaskAttentionService(s.settings, db, sync, h, history, processes=p)
        )
        assert call(other, a, w, d)["reason"] == "WORKER_CAPACITY_UNAVAILABLE"

    def fail(*args, **kwargs):
        raise GitError("controlled pre-runtime failure")

    monkeypatch.setattr(start.worktrees.git, "add_worktree", fail)
    with pytest.raises(TaskStartError):
        start.prepare_git(a, a.epic_run_id, original | {"task_id": "T2"})
    assert s.store.get_task(occupied.id).worker_slot is None
    assert call(s, a, w, d)["stage"] == "ACTIVE_AND_SYNCED" and h.resumes == 1


def test_ack_current_report_boundary_excludes_old_blocked_report(setup):
    from orchestrator.application.worker_report_service import ReportError

    s, a, w, _, h, history, _, d = setup
    call(s, a, w, d)
    with pytest.raises(ReportError, match="REPORT_NOT_AVAILABLE"):
        s.attention.reports.collect(w, w.task_run_id, expected_status="BLOCKED")
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.WORKING


@pytest.mark.parametrize("attention_setup", ["review"], indirect=True)
def test_review_input_restores_phase_without_approval(setup):
    # fixture parametrization is supplied to attention_setup below
    s, a, w, _, h, _, _, d = setup
    call(s, a, w, d)
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.REVIEWING
    assert not s.store.get_operations(a.epic_run_id, kind="task_approve")
    assert h.resumes == 1


def test_atomic_race_new_claim_against_input_resume_reserves_only_one(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    s, a, w, b, h, history, p, d = setup
    original = s.store.get_operation("p", "task_start", "T").result["spec"]
    barrier = Barrier(2)

    def new_claim():
        with StateStore(s.settings.sqlite_path) as db:
            start = TaskStartService(s.settings, db, h, history, processes=p)
            barrier.wait(timeout=5)
            try:
                start.claim(a, a.epic_run_id, original | {"task_id": "race-new"})
                return "CLAIMED"
            except TaskStartError as error:
                assert str(error) == "WORKER_CAPACITY_UNAVAILABLE"
                return "CAPACITY"

    def input_resume():
        with StateStore(s.settings.sqlite_path) as db:
            sync = TeamPlayerSyncService(
                s.settings,
                db,
                b,
                project_id="p",
                external_project_id=s.sync.external_project_id,
                user_id=s.sync.user_id,
                codex=history,
                processes=p,
            )
            other = TaskResumeService(
                TaskAttentionService(s.settings, db, sync, h, history, processes=p)
            )
            barrier.wait(timeout=5)
            return call(other, a, w, d)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f = pool.submit(new_claim)
        g = pool.submit(input_resume)
        new, result = f.result(), g.result()
    reserved = [t for t in s.store.get_tasks(a.epic_run_id) if t.worker_slot is not None]
    assert len(reserved) == 1 and len({t.worker_slot for t in reserved}) == 1
    if new == "CLAIMED":
        assert result["reason"] == "WORKER_CAPACITY_UNAVAILABLE" and h.resumes == 0
    else:
        assert result["stage"] == "ACTIVE_AND_SYNCED" and h.resumes == 1


def test_interrupt_after_actual_resume_before_checkpoint_reuses_runtime(setup, monkeypatch):
    s, a, w, _, h, _, _, d = setup
    original = s._save

    def checkpoint(op, **updates):
        if updates.get("stage") == "RESUMED":
            raise RuntimeError("controlled lost checkpoint")
        return original(op, **updates)

    monkeypatch.setattr(s, "_save", checkpoint)
    with pytest.raises(ResumeError, match="INPUT_RESOURCE_UNVERIFIED"):
        call(s, a, w, d)
    assert h.resumes == 1 and len(h.sent) == 1
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.PARKED
    monkeypatch.setattr(s, "_save", original)
    assert call(s, a, w, d)["stage"] == "ACTIVE_AND_SYNCED" and h.resumes == 1 and len(h.sent) == 2


def test_unknown_resume_retains_reservation_and_never_starts_again(setup):
    s, a, w, _, h, _, _, d = setup
    old = h.resume_agent
    calls = []

    def unknown(*args):
        calls.append(args)
        raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    h.resume_agent = unknown
    with pytest.raises(ResumeError, match="RESUME_OUTCOME_UNKNOWN"):
        call(s, a, w, d)
    with pytest.raises(ResumeError, match="RESUME_OUTCOME_UNKNOWN"):
        call(s, a, w, d)
    assert len(calls) == 1 and s.store.get_task(w.task_run_id).worker_slot == 1
    # Native observer later finds that the original accepted intent completed.
    old(*calls[0])
    assert call(s, a, w, d)["stage"] == "ACTIVE_AND_SYNCED" and len(calls) == 1


def test_mismatched_ack_does_not_activate_or_resend(setup):
    s, a, w, b, h, history, _, d = setup

    def prompt(name, text, *, timeout_ms):
        h.sent.append(text)
        expected = json.loads(text)["assignment"]
        history.reply(text, ack=expected | {"input_id": str(uuid4())}, turn_id="wrong-input-ack")

    h.prompt = prompt
    assert call(s, a, w, d)["stage"] == "DISPATCH_REQUESTED"
    assert call(s, a, w, d)["stage"] == "DISPATCH_REQUESTED" and h.resumes == 1 and len(h.sent) == 2
    assert s.store.get_task(w.task_run_id).kanban_status == Kanban.ATTENTION
    assert b.task["status"] == "NeedsInput"


def test_ack_saved_during_board_outage_then_only_missing_sync(setup):
    from orchestrator.adapters.teamplayer_mcp import TeamPlayerError

    s, a, w, b, h, _, _, d = setup
    original = b.read

    async def offline(*args):
        raise TeamPlayerError("TEAMPLAYER_TRANSPORT_UNKNOWN")

    b.read = offline
    result = call(s, a, w, d)
    assert result["stage"] == "CONFIRMED" and result["reason"] == "INPUT_SYNC_PENDING"
    assert s.store.get_task(w.task_run_id).internal_status == TaskState.WORKING
    b.read = original
    assert call(s, a, w, d)["stage"] == "ACTIVE_AND_SYNCED"
    assert h.resumes == 1 and len(h.sent) == 2


def test_changed_generation_after_native_resume_rejects_delivery(setup, monkeypatch):
    s, a, w, _, h, _, _, d = setup
    original = s.lifecycle.resume_task

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        start = s.store.get_operation("p", "start_runtime", w.task_run_id)
        s.store.update_operation(
            start.model_copy(update={"result": start.result | {"generation": "foreign-generation"}})
        )
        return result

    monkeypatch.setattr(s.lifecycle, "resume_task", changed)
    with pytest.raises(ResumeError, match="INPUT_RESUME_CHANGED"):
        call(s, a, w, d)
    assert h.resumes == 1 and len(h.sent) == 1
    assert s.store.get_task(w.task_run_id).kanban_status == Kanban.ATTENTION


def test_two_reserved_slots_preserve_input_without_third_native_worker(setup):
    s, a, w, b, h, history, p, d = setup
    settings = s.settings.model_copy(update={"max_workers": 2})
    sync = TeamPlayerSyncService(
        settings,
        s.store,
        b,
        project_id="p",
        external_project_id=s.sync.external_project_id,
        user_id=s.sync.user_id,
        codex=history,
        processes=p,
    )
    other = TaskResumeService(
        TaskAttentionService(settings, s.store, sync, h, history, processes=p)
    )
    spec = s.store.get_operation("p", "task_start", "T").result["spec"]
    start = TaskStartService(settings, s.store, h, history, processes=p)
    for identity in ["second", "third"]:
        start.claim(a, a.epic_run_id, spec | {"task_id": identity})
    result = call(other, a, w, d)
    assert result["stage"] == "WAITING_RESUME" and result["reason"] == "WORKER_CAPACITY_UNAVAILABLE"
    assert sorted(
        t.worker_slot for t in s.store.get_tasks(a.epic_run_id) if t.worker_slot is not None
    ) == [1, 2]
    assert s.store.get_task(w.task_run_id).worker_slot is None and h.resumes == 0 and h.starts == 1
    assert (
        s.store.get_operation("p", s.KIND, d["input_id"]).result["decision"]["answer"]
        == d["answer"]
    )
