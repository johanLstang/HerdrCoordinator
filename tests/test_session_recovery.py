"""F43 actual temporary Git/SQLite; explicitly controlled native and board peers."""

# Fixture injection intentionally shadows imported fixture names.
# ruff: noqa: F811
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_coordinator_claim import setup as claims_setup  # noqa: F401
from test_coordinator_review import review_setup  # noqa: F401
from test_coordinator_runtime import setup as runtime_setup  # noqa: F401
from test_epic_start import setup as epic_setup  # noqa: F401
from test_integration_control import setup as control_setup  # noqa: F401
from test_reconciliation import start_one
from test_teamplayer_reader import T1, T2, T3

from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.coordinator_runtime_service import CoordinatorRuntimeService
from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.session_recovery_service import (
    SessionRecoveryError,
    SessionRecoveryService,
)
from orchestrator.application.task_attention_service import TaskAttentionService
from orchestrator.application.task_resume_service import TaskResumeService
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.domain.attention import InputDecision
from orchestrator.persistence.store import StateStore


def run(a):
    return asyncio.run(a)


def service(control, *, db=None):
    s = control.scheduler
    store = db or s.store
    original = control.epic_start
    parent = EpicStartService(
        s.settings, store, original.herdr, original.spec, original.principal,
        codex=original.codex, processes=s.merge.lifecycle.processes,
    )
    sync = TeamPlayerSyncService(
        s.settings, store, s.sync.adapter, project_id=s.sync.project_id,
        external_project_id=s.sync.external_project_id, user_id=s.sync.user_id,
        scopes=s.selection.scopes, codex=parent.codex, processes=parent.lifecycle.processes,
    )
    attention = TaskAttentionService(
        s.settings, store, sync, parent.herdr, parent.codex, processes=parent.lifecycle.processes
    )
    return SessionRecoveryService(
        s.settings, store, s.selection.reader, project_id=s.sync.project_id,
        integrations=[parent],
        resumers={parent.principal.epic_run_id: TaskResumeService(attention)},
    )


def row(result, identity):
    return next(r for r in result["sessions"] if r["run_id"] == identity)


def parked(control, actor, native):
    t = start_one(control, actor)
    turns = native.threads[t.codex_session_id]["turns"]
    turns.append(dict(id="blocked-final", status="completed", items=[dict(
        id="blocked-item", type="agentMessage", phase="final_answer", text=json.dumps(dict(
            version=1, status="BLOCKED", project_id=t.project_id, epic_id="E-34",
            epic_run_id=t.epic_run_id, task_id=t.task_id, task_run_id=t.id, branch=t.branch,
            summary="Missing harmless retention input", reason="Retention is unspecified",
            input_required="How many days?",
        )),
    )]))
    result = run(control.scheduler.attention.park_blocked(actor, t.id))
    return control.store.get_task(t.id), result


def test_live_restart_same_resources_monitors_native_turns_without_start_or_prompt(control_setup):
    control, _, a, _, native = control_setup
    t = start_one(control, a)
    original = control.store.project_inventory("p")
    (Path(t.worktree_path) / "unsaved.txt").write_text("preserve me\n")
    before = native.starts, len(native.sent), control.scheduler.selection.git.worktrees()
    first = service(control)
    result = run(first.poll())
    assert all(r["stage"] == "CONNECTED" for r in result["sessions"]), result
    with StateStore(control.settings.sqlite_path) as db:
        recovered = service(control, db=db)
        assert run(recovered.poll()) == result
        turns = native.threads[t.codex_session_id]["turns"]
        turns.append(dict(id="work-progress", status="inProgress", items=[]))
        original_verify = native.verify_agent

        def working(*args):
            return original_verify(*args) | {"status": "working", "ready": False}

        native.verify_agent = working
        monitored = row(run(recovered.poll()), t.id)
        assert monitored["runtime_status"] == "working"
        assert monitored["cursor"][-1] == dict(
            id="work-progress", status="inProgress"
        )
    assert before == (native.starts, len(native.sent), control.scheduler.selection.git.worktrees())
    current = control.store.project_inventory("p")
    assert original["tasks"] == current["tasks"] and original["epics"] == current["epics"]
    assert (Path(t.worktree_path) / "unsaved.txt").read_text() == "preserve me\n"


def test_parked_without_answer_survives_reopen_without_slot_or_resume(control_setup):
    control, _, a, _, native = control_setup
    t, _ = parked(control, a, native)
    before = native.starts, len(native.sent), t
    with StateStore(control.settings.sqlite_path) as db:
        recovered = service(control, db=db)
        result = row(run(recovered.poll()), t.id)
        assert result["stage"] == "WAITING_INPUT", result
        assert db.get_task(t.id) == t and t.worker_slot is None
    assert before == (native.starts, len(native.sent), control.store.get_task(t.id))


def test_saved_answer_reopen_resumes_same_sid_once_and_preserves_input(control_setup):
    control, _, a, _, native = control_setup
    t, blocked = parked(control, a, native)
    decision = InputDecision(input_id=str(uuid4()), blocker_id=blocked["blocker_id"], answer="7")
    saved = control.scheduler.resume._saved(a, t, decision)
    before = native.starts, len(native.sent)
    with StateStore(control.settings.sqlite_path) as db:
        recovered = service(control, db=db)
        result = row(run(recovered.poll()), t.id)
        assert result["stage"] == "CONNECTED", result
        current = db.get_task(t.id)
        assert current.internal_status == "WORKING" and current.worker_slot == 1
        assert (current.codex_session_id, current.branch, current.worktree_path) == (
            t.codex_session_id, t.branch, t.worktree_path
        )
        assert run(recovered.poll())
        assert db.get_operation("p", "task_resume", decision.input_id).result["decision"] == (
            saved.result["decision"]
        )
    assert native.starts == before[0] + 1 and len(native.sent) == before[1] + 1
    assert len(native.workspaces) == 2


def test_missing_reference_attention_mirrors_board_and_retains_slot_until_restored(control_setup):
    control, _, a, board, native = control_setup
    t = start_one(control, a)
    missing = t.model_copy(update={"codex_session_id": None})
    control.store.update_runtime_metadata(missing)
    before = native.starts, len(native.sent), control.scheduler.selection.git.worktrees()
    recovered = service(control)
    result = row(run(recovered.poll()), t.id)
    assert result["stage"] == "ATTENTION" and "SESSION_REFERENCE_MISSING" in result["codes"]
    assert result["sync"]["status"] == "SYNCED", result
    board_task = next(n for n in board.tasks if n["taskId"] == T1)
    assert board_task["status"] == "NeedsInput"
    assert control.store.get_task(t.id).internal_status == "WORKING"
    assert control.store.get_task(t.id).worker_slot == t.worker_slot
    assert not run(control.scheduler.reconcile(a, a.epic_run_id))["scheduling_allowed"]
    # Explicit fixture operator repair restores the saved original reference, never a new SID.
    control.store.update_runtime_metadata(t)
    result = row(run(recovered.poll()), t.id)
    assert result["stage"] == "CONNECTED" and result["sync"]["status"] == "SYNCED", result
    assert board_task["status"] == "InProgress"
    assert run(control.scheduler.reconcile(a, a.epic_run_id))["scheduling_allowed"]
    assert before == (native.starts, len(native.sent), control.scheduler.selection.git.worktrees())


def test_transport_unavailable_preserves_original_ids_and_does_not_prove_loss(control_setup):
    control, _, a, _, native = control_setup
    t = start_one(control, a)
    before = native.starts, len(native.sent)
    original = native.get_agent

    def unavailable(*args):
        raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    native.get_agent = unavailable
    result = row(run(service(control).poll()), t.id)
    assert result["stage"] == "ATTENTION" and "HERDR_UNAVAILABLE" in result["codes"]
    assert control.store.get_task(t.id) == t
    native.get_agent = original
    assert row(run(service(control).poll()), t.id)["stage"] == "CONNECTED"
    assert before == (native.starts, len(native.sent))


def test_unconfirmed_missing_process_never_resumes_or_releases_slot(control_setup):
    control, _, a, _, native = control_setup
    t = start_one(control, a)
    del native.agents[t.worker_agent_id]  # No F13 inactivity proof.
    before = native.starts, len(native.sent)
    result = row(run(service(control).poll()), t.id)
    assert result["stage"] == "ATTENTION" and "RUNTIME_MISSING_UNCONFIRMED" in result["codes"]
    assert control.store.get_task(t.id) == t
    assert before == (native.starts, len(native.sent))


def test_confirmed_stopped_integration_resumes_same_session_once(control_setup):
    control, _, a, _, native = control_setup
    recovered = service(control)
    parent = recovered.parents[a.epic_run_id]
    original = control.store.get_epic(a.epic_run_id)
    parent.startup.lifecycle.stop_epic(parent.principal, original.id, key="known-epic-stop")
    before = native.starts, len(native.sent)
    result = row(run(recovered.poll()), original.id)
    assert result["stage"] == "CONNECTED", result
    assert row(run(service(control).poll()), original.id)["stage"] == "CONNECTED"
    assert control.store.get_epic(original.id).codex_session_id == original.codex_session_id
    assert native.starts == before[0] + 1 and len(native.sent) == before[1]
    assert len(native.workspaces) == 1


def test_coordinator_reopen_same_session_and_no_new_run(runtime_setup):
    runtime, actor, native, _ = runtime_setup
    original = runtime.start(actor)
    # Project reader deliberately unavailable: this cannot authorize new work.
    class Reader:
        async def read_project(self, **kwargs):
            raise RuntimeError("transport unavailable")

    with StateStore(runtime.settings.sqlite_path) as db:
        reopened = CoordinatorRuntimeService(
            runtime.settings, db, native, runtime.spec, actor, original.id,
            codex=native, processes=runtime.processes,
        )
        service = SessionRecoveryService(runtime.settings, db, Reader(), project_id="p",
                                         coordinator=reopened)
        result = row(run(service.poll()), original.id)
        assert result["stage"] == "ATTENTION" and result["session_id"] == original.codex_session_id
        assert db.project_coordinators("p") == [original]
    assert native.starts == 1 and len(native.prompts) == 1


def test_coordinator_live_original_context_restores_monitor_after_reopen(claims_setup):
    claims, actor, _, _, natives, _ = claims_setup
    runtime = claims.runtime
    before = runtime.store.get_coordinator(runtime.run_id)
    with StateStore(runtime.settings.sqlite_path) as db:
        reopened = CoordinatorRuntimeService(
            runtime.settings, db, runtime.herdr, runtime.spec, actor, runtime.run_id,
            codex=runtime.codex, processes=runtime.processes,
        )
        recovered = SessionRecoveryService(
            runtime.settings, db, claims.reader, project_id="p", coordinator=reopened
        )
        result = row(run(recovered.poll()), before.id)
        assert result["stage"] == "CONNECTED", result
        assert run(recovered.poll())
        assert db.project_coordinators("p") == [before] and db.get_runs() == []
    assert natives == {} and runtime.herdr.starts == 1 and len(runtime.herdr.prompts) == 1


@pytest.mark.parametrize("change", ["head", "owner", "worktree"])
def test_changed_resources_never_resume_replace_or_overwrite_work(control_setup, change):
    control, _, a, board, native = control_setup
    t = start_one(control, a)
    path = Path(t.worktree_path)
    if change == "head":
        (path / "user.txt").write_text("user's later commit\n")
        g = control.scheduler.selection.git
        g.run("add", "user.txt", cwd=path)
        g.run("commit", "-m", "User change", cwd=path)
    elif change == "owner":
        next(n for n in board.tasks if n["taskId"] == T1)["responsibleUserId"] = str(uuid4())
    else:
        path.rename(path.with_name("preserved-user-work"))
    before = native.starts, len(native.sent), control.scheduler.selection.git.worktrees()
    result = row(run(service(control).poll()), t.id)
    assert result["stage"] == "ATTENTION", result
    assert control.store.get_task(t.id) == t
    assert before == (native.starts, len(native.sent), control.scheduler.selection.git.worktrees())


def test_changed_operator_principal_cannot_adopt_and_invalid_overlay_cannot_sync(control_setup):
    control, _, a, _, native = control_setup
    t = start_one(control, a)
    recovered = service(control)
    recovered.parents[a.epic_run_id].startup.principal = a.model_copy(update={"actor_id": "other"})
    before = native.starts, len(native.sent)
    result = row(run(recovered.poll()), t.id)
    assert result["stage"] == "ATTENTION" and result["codes"] == ["ORIGINAL_CONTEXT_REQUIRED"]
    assert recovered._get(t) is None and before == (native.starts, len(native.sent))
    # An opaque object/caller role string is not a protected startup context.
    with pytest.raises(SessionRecoveryError, match="CONFIGURATION_INVALID"):
        SessionRecoveryService(control.settings, control.store, control.scheduler.selection.reader,
                               project_id="p", coordinator=object())


def test_unknown_input_reply_reconnect_observes_original_dispatch_without_resend(control_setup):
    control, _, a, _, native = control_setup
    t, blocked = parked(control, a, native)
    d = InputDecision(input_id=str(uuid4()), blocker_id=blocked["blocker_id"], answer="7")
    control.scheduler.resume._saved(a, t, d)
    original, delivered = native.prompt, []

    def unknown(name, text, *, timeout_ms):
        delivered.append((name, text, timeout_ms))
        raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    native.prompt = unknown
    recovered = service(control)
    first = row(run(recovered.poll()), t.id)
    assert first["stage"] == "INPUT_PENDING", first
    starts = native.starts
    with StateStore(control.settings.sqlite_path) as db:
        again = service(control, db=db)
        assert row(run(again.poll()), t.id)["stage"] == "INPUT_PENDING"
        assert len(delivered) == 1 and native.starts == starts
        name, text, timeout = delivered[0]
        original(name, text, timeout_ms=timeout)  # Late actual native receipt, not a resend.
        assert row(run(again.poll()), t.id)["stage"] == "CONNECTED"
        assert len(delivered) == 1 and native.starts == starts
        assert db.get_task(t.id).codex_session_id == t.codex_session_id


@pytest.mark.parametrize("control_setup", ["independent"], indirect=True)
def test_pending_answer_waits_for_two_real_registered_slots_then_same_session_resume(control_setup):
    control, _, a, _, native = control_setup
    t, blocked = parked(control, a, native)
    s = control.scheduler
    run(s.start_selected(a, a.epic_run_id, s.selection.specs[T2]))
    run(s.start_selected(a, a.epic_run_id, s.selection.specs[T3]))
    d = InputDecision(input_id=str(uuid4()), blocker_id=blocked["blocker_id"], answer="7")
    s.resume._saved(a, t, d)
    before = native.starts, len(native.sent)
    with StateStore(control.settings.sqlite_path) as db:
        recovered = service(control, db=db)
        result = row(run(recovered.poll()), t.id)
        assert result["stage"] == "WAITING_CAPACITY", result
        assert db.get_task(t.id) == t and t.worker_slot is None
        assert sum(x.worker_slot is not None for x in db.get_tasks(a.epic_run_id)) == 2
        assert before == (native.starts, len(native.sent))
        other = next(x for x in db.get_tasks(a.epic_run_id) if x.task_id == "F-02")
        s.merge.lifecycle.stop_task(a, other.id, key="free-second-slot", reason="Fixture input")
        assert run(s.sync.sync_task(a, other.id))["status"] == "SYNCED"
        result = row(run(recovered.poll()), t.id)
        assert result["stage"] == "CONNECTED", result
        assert db.get_task(t.id).codex_session_id == t.codex_session_id
        assert sum(x.worker_slot is not None for x in db.get_tasks(a.epic_run_id)) == 2
    assert native.starts == before[0] + 1 and len(native.sent) == before[1] + 1


def test_integration_lost_resume_result_observes_original_key_without_second_start(control_setup):
    control, _, a, _, native = control_setup
    recovered = service(control)
    parent = recovered.parents[a.epic_run_id]
    epic = control.store.get_epic(a.epic_run_id)
    parent.startup.lifecycle.stop_epic(parent.principal, epic.id, key="stop-before-resume-loss")
    original = native.read_session
    starts = native.starts

    def unknown_after_effect(sid, cwd):
        if native.starts > starts:
            raise RuntimeError("metadata transport temporarily unavailable")
        return original(sid, cwd)

    native.read_session = unknown_after_effect
    assert row(run(recovered.poll()), epic.id)["stage"] == "ATTENTION"
    assert native.starts == starts + 1
    native.read_session = original
    result = row(run(service(control).poll()), epic.id)
    assert result["stage"] == "CONNECTED", result
    assert native.starts == starts + 1 and len(native.workspaces) == 1


def test_recovery_does_not_override_later_manual_attention_version(control_setup):
    control, _, a, board, _ = control_setup
    t = start_one(control, a)
    control.store.update_runtime_metadata(t.model_copy(update={"codex_session_id": None}))
    recovered = service(control)
    assert row(run(recovered.poll()), t.id)["sync"]["status"] == "SYNCED"
    external = next(n for n in board.tasks if n["taskId"] == T1)
    external["description"] += "\nManual decision: wait for a separate operator instruction."
    external["version"] += 1
    control.store.update_runtime_metadata(t)
    result = row(run(recovered.poll()), t.id)
    assert result["stage"] == "CONNECTED" and result["sync"]["status"] == "PENDING", result
    assert external["status"] == "NeedsInput"
    assert row(run(recovered.poll()), t.id)["stage"] == "ORIGINAL_OPERATION_REQUIRED"
    assert external["status"] == "NeedsInput"


def test_forged_recovery_overlay_is_rejected_before_board_write(control_setup):
    from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
    from orchestrator.application.session_recovery_record import fingerprint

    control, _, a, board, native = control_setup
    t = start_one(control, a)
    control.store.update_runtime_metadata(t.model_copy(update={"codex_session_id": None}))
    recovered = service(control)
    run(recovered.poll())
    op = recovered._get(t)
    data = op.result | {"principal": op.result["principal"] | {"actor_id": "forged"}}
    data["observation_hash"] = fingerprint(
        {k: v for k, v in data.items() if k != "observation_hash"}
    )
    control.store.update_operation(op.model_copy(update={"result": data}))
    before = len(board.applied), native.starts, len(native.sent)
    with pytest.raises(TeamPlayerError, match="SOURCE_UNVERIFIED"):
        run(control.scheduler.sync.sync_task(a, t.id))
    assert before == (len(board.applied), native.starts, len(native.sent))


def test_active_recovery_hold_blocks_existing_pipeline_and_direct_control_write(control_setup):
    control, _, a, _, native = control_setup
    t = start_one(control, a)
    control.store.update_runtime_metadata(t.model_copy(update={"codex_session_id": None}))
    run(service(control).poll())
    before = control.store.get_task(t.id), len(control.store.get_reviews(t.id)), native.starts
    assert not run(control.scheduler._advance(a, control.store.get_task(t.id)))
    result = run(control.call(a, "task_review_request", dict(
        project_id="p", task_run_id=t.id, request_key="not-authorized-by-recovery-report",
    )))
    assert result.code == "SESSION_RECOVERY_REQUIRED" and not result.ok
    assert before == (
        control.store.get_task(t.id), len(control.store.get_reviews(t.id)), native.starts
    )


@pytest.mark.parametrize("review_setup", ["delivery"], indirect=True)
def test_registered_effective_plan_recovers_stopped_original_integration(review_setup):
    f = review_setup
    control = f.subject.context()[0]
    runtime = f.subject.runtime
    view = control.epic_start
    assert view.original is f.subject.original_start
    parent = view.original
    op = parent.store.get_operation("p", parent.KIND, parent.principal.epic_run_id)
    original = parent.store.get_epic(op.epic_run_id)
    owner = parent._coordinator(op)
    parent.lifecycle.stop_epic(owner, original.id, key="stop-effective-plan-integration")
    recovered = SessionRecoveryService(
        runtime.settings, runtime.store, control.scheduler.selection.reader,
        project_id="p", coordinator=runtime, integrations=[view],
        resumers={original.id: control.scheduler.resume},
    )
    starts = f.sessions.starts
    result = row(run(recovered.poll()), original.id)
    assert result["stage"] == "CONNECTED", result
    assert parent.store.get_epic(original.id).codex_session_id == original.codex_session_id
    assert f.sessions.starts == starts + 1
