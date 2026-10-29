"""F44 actual temporary Git/SQLite, controlled native/board peers; no native restart claim."""

# Imported fixture names are injected by pytest.
# ruff: noqa: F811
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_coordinator_claim import setup as claims_setup  # noqa: F401
from test_coordinator_delivery import approved, delivery_setup  # noqa: F401
from test_coordinator_review import review_setup  # noqa: F401
from test_coordinator_runtime import setup as runtime_setup  # noqa: F401
from test_epic_start import setup as epic_setup  # noqa: F401
from test_integration_control import finish, task
from test_integration_control import setup as control_setup  # noqa: F401
from test_session_recovery import service as session_service
from test_task_scheduler import approve
from test_teamplayer_reader import T1

from orchestrator.application.epic_integration_service import EpicIntegrationService
from orchestrator.application.integration_control_service import IntegrationControlService
from orchestrator.application.operation_recovery_service import (
    OperationRecoveryError,
    OperationRecoveryService,
)
from orchestrator.application.session_recovery_service import SessionRecoveryService
from orchestrator.domain.models import Operation
from orchestrator.event_log import EventLog


def run(value):
    return asyncio.run(value)


def recovery(control):
    sessions = session_service(control)
    parent = sessions.parents[control.actor.epic_run_id].startup
    reopened = IntegrationControlService(parent, control.scheduler, EventLog())
    return OperationRecoveryService(sessions, controls=[reopened])


def start(control):
    return run(control.scheduler.start_selected(control.actor, control.actor.epic_run_id,
                                               control.scheduler.selection.specs[T1]))


def original(control, kind, local_id="F-01"):
    t = task(control, local_id)
    return next(o for o in control.store.get_operations(t.epic_run_id, kind=kind)
                if o.task_run_id == t.id)


def prepare_approval(control, native):
    start(control)
    finish(control, native, "F-01")
    t = task(control, "F-01")
    run(control.scheduler._advance(control.actor, t))
    package = original(control, "task_review_request")
    control.scheduler.approval.approve(control.actor, t.id, approve(package.result["context"]),
                                       key="fixture-current-approval")
    return control.store.get_task(t.id)


@pytest.mark.parametrize("checkpoint", ["RUNTIME_READY", "WORKING"])
def test_start_checkpoint_loss_uses_same_run_native_session_and_one_assignment(
    control_setup, monkeypatch, checkpoint,
):
    control, _, _, _, native = control_setup
    save = control.scheduler.start._checkpoint
    with monkeypatch.context() as patch:
        def lost(op, stage, **kwargs):
            if stage == checkpoint:
                raise RuntimeError("controlled lost parent checkpoint")
            return save(op, stage, **kwargs)
        patch.setattr(control.scheduler.start, "_checkpoint", lost)
        with pytest.raises(RuntimeError, match="controlled lost"):
            start(control)
    op = original(control, "task_start")
    t = task(control, "F-01")
    starts, worktrees = native.starts, control.scheduler.selection.git.worktrees()
    operator = recovery(control)
    result = run(operator.recover(op.id))
    assert result["stage"] == "RECOVERED", result
    current = control.store.get_task(t.id)
    assert current.internal_status == "WORKING" and current.worker_slot == t.worker_slot
    assert current.codex_session_id and native.starts == starts
    assert control.scheduler.selection.git.worktrees() == worktrees
    assert len(control.store.get_tasks(t.epic_run_id)) == 1
    assert sum(json.loads(p).get("type") != "HERDR_EPIC_ASSIGNMENT"
               for p in native.sent) == 1
    assert run(recovery(control).recover(op.id))["stage"] == "RECORDED_COMPLETE"


def test_unknown_workspace_never_adopts_or_repeats_create_and_preserves_slot(
    control_setup, monkeypatch,
):
    control, _, _, _, native = control_setup
    create = native.create_workspace
    with monkeypatch.context() as patch:
        def lost(*args):
            create(*args)
            raise RuntimeError("controlled lost workspace response")
        patch.setattr(native, "create_workspace", lost)
        with pytest.raises(RuntimeError, match="controlled lost"):
            start(control)
    op = original(control, "task_start")
    before = native.starts, len(native.workspaces), task(control, "F-01")
    answer = run(recovery(control).recover(op.id))
    assert answer["stage"] == "ATTENTION", answer
    assert before == (native.starts, len(native.workspaces), task(control, "F-01"))
    assert task(control, "F-01").worker_slot == 1
    assert original(control, "start_runtime").result["stage"] == "WORKSPACE_CREATE_REQUESTED"


@pytest.mark.parametrize("boundary", ["merge", "test", "unknown-test", "unknown-test-result"])
def test_task_delivery_recovers_actual_merge_only_missing_steps_and_bound_tests(
    control_setup, monkeypatch, boundary,
):
    control, _, actor, board, native = control_setup
    t = prepare_approval(control, native)
    merger = control.scheduler.merge
    with monkeypatch.context() as patch:
        if boundary == "merge":
            def lost(*args, **kwargs):
                raise RuntimeError("controlled lost actual merge checkpoint")
            patch.setattr(merger.git_manager, "_complete_merge", lost)
        elif boundary == "test":
            def lost(*args, **kwargs):
                raise RuntimeError("controlled lost after actual passed test")
            patch.setattr(merger.lifecycle, "stop_delivered_task", lost)
        elif boundary == "unknown-test-result":
            finish_test = merger.git_manager._finish
            def lost(op, status, **kwargs):
                if op.kind == "task_delivery_test":
                    raise RuntimeError("controlled unknown actual test result")
                return finish_test(op, status, **kwargs)
            patch.setattr(merger.git_manager, "_finish", lost)
        else:
            import subprocess
            actual_run = subprocess.run
            def lost(command, *args, **kwargs):
                if tuple(command) == tuple(merger.git_manager.test_command):
                    raise RuntimeError("controlled unknown test process")
                return actual_run(command, *args, **kwargs)
            patch.setattr("orchestrator.application.task_merge_service.subprocess.run", lost)
        with pytest.raises(RuntimeError, match="controlled"):
            merger.merge(actor, t.id, key="original-delivery", verification_key="original-test")
    merge = original(control, "task_merge")
    before_sha = control.scheduler.selection.git.head(control.store.get_epic(t.epic_run_id).branch)
    before_starts = native.starts
    answer = run(recovery(control).recover(merge.id))
    assert answer["stage"] == "RECOVERED", answer
    done = control.store.get_task(t.id)
    assert done.internal_status == "DONE" and done.worker_slot is None
    assert done.codex_session_id == t.codex_session_id and done.merge_commit == before_sha
    assert native.starts == before_starts
    assert len(control.store.get_operations(t.epic_run_id, kind="merge_task_to_epic")) == 1
    tests = control.store.get_operations(t.epic_run_id, kind="task_delivery_test")
    assert len(tests) == (2 if boundary.startswith("unknown-test") else 1)
    assert sum(o.status == "SUCCEEDED" for o in tests) == 1
    if boundary.startswith("unknown-test"):
        assert len([o for o in tests if o.status == "SUPERSEDED"]) == 1
    assert next(n for n in board.tasks if n["taskId"] == T1)["status"] == "Done"
    assert run(recovery(control).recover(merge.id))["stage"] == "RECORDED_COMPLETE"
    assert len(control.store.get_operations(t.epic_run_id, kind="task_delivery_test")) == len(tests)


@pytest.mark.parametrize("change", ["worktree", "head", "owner"])
def test_changed_resource_stops_recovery_without_more_effects(control_setup, monkeypatch, change):
    control, _, actor, board, native = control_setup
    t = prepare_approval(control, native)
    with monkeypatch.context() as patch:
        def lost(*args, **kwargs):
            raise RuntimeError("controlled premerge crash")
        patch.setattr(control.scheduler.merge.git_manager.git, "merge_commit", lost)
        with pytest.raises(RuntimeError):
            control.scheduler.merge.merge(actor, t.id, key="delivery", verification_key="test")
    op = original(control, "task_merge")
    p = Path(t.worktree_path)
    if change == "worktree":
        p.rename(p.with_name("preserved-user-work"))
    elif change == "head":
        (p / "manual.txt").write_text("User's actual change\n")
        g = control.scheduler.selection.git
        g.run("add", "manual.txt", cwd=p)
        g.run("commit", "-m", "manual change", cwd=p)
    else:
        next(n for n in board.tasks if n["taskId"] == T1)["responsibleUserId"] = str(uuid4())
    before = native.starts, len(native.sent), control.store.get_task(t.id).worker_slot
    answer = run(recovery(control).recover(op.id))
    assert answer["stage"] == "ATTENTION", answer
    assert before == (native.starts, len(native.sent), control.store.get_task(t.id).worker_slot)
    assert not control.store.get_operations(t.epic_run_id, kind="task_delivery_test")


@pytest.mark.parametrize("manual", [False, True])
def test_unknown_history_write_is_read_back_without_duplicate_or_overwriting_manual_decision(
    control_setup, monkeypatch, manual,
):
    control, _, actor, board, native = control_setup
    t = prepare_approval(control, native)
    assert control.scheduler.merge.merge(actor, t.id, key="delivery", verification_key="test")
    write = board.write
    with monkeypatch.context() as patch:
        async def lost(name, arguments):
            result = await write(name, arguments)
            if name == "update_task_details":
                raise RuntimeError("controlled lost history response")
            return result
        patch.setattr(board, "write", lost)
        answer = run(control.scheduler.sync.sync_task(actor, t.id))
        assert answer["status"] == "PENDING", answer
    op = control.store.get_operation("p", "teamplayer_sync",
                                     next(o.idempotency_key for o in control.store.get_operations(
                                         t.epic_run_id, kind="teamplayer_sync"
                                     ) if o.id == answer["operation_id"]))
    external = next(n for n in board.tasks if n["taskId"] == T1)
    if manual:
        external["status"] = "NeedsInput"
        external["description"] += "\nManual operator decision preserved."
        external["version"] += 1
    before = external["description"], len(board.applied), native.starts
    result = run(recovery(control).recover(op.id))
    assert result["stage"] == ("WAITING" if manual else "RECOVERED"), result
    assert before == (external["description"], len(board.applied), native.starts)


def test_unknown_operation_or_foreign_context_never_authorizes_effects(control_setup):
    control, _, _, _, native = control_setup
    operator = recovery(control)
    with pytest.raises(OperationRecoveryError, match="ORIGINAL_OPERATION_REQUIRED"):
        run(operator.recover(str(uuid4())))
    with pytest.raises(OperationRecoveryError, match="CONFIGURATION_INVALID"):
        OperationRecoveryService(operator.sessions, controls=[object()])
    op = Operation(project_id="p", epic_run_id=control.actor.epic_run_id,
                   kind="unknown_external_effect", idempotency_key="unknown")
    control.store.add_operation(op)
    before = native.starts, len(native.sent)
    assert run(operator.recover(op.id))["stage"] == "ATTENTION"
    assert before == (native.starts, len(native.sent))


def test_stopped_integration_monitor_settles_without_resume_or_business_state_change(control_setup):
    control, _, _, _, native = control_setup
    operator = recovery(control)
    parent = operator.sessions.parents[control.actor.epic_run_id]
    # Real controlled peer transport failure, then original physical stop proof.
    read = operator.sessions.audit.reader.read_project
    async def unavailable(**kwargs):
        raise RuntimeError("controlled board unavailable")
    operator.sessions.audit.reader.read_project = unavailable
    run(operator.sessions.poll())
    operator.sessions.audit.reader.read_project = read
    epic = control.store.get_epic(control.actor.epic_run_id)
    op = operator.sessions._get(epic)
    parent.startup.lifecycle.stop_epic(parent.principal, epic.id, key="original-physical-stop")
    before = native.starts, len(native.sent), control.store.get_epic(epic.id)
    answer = run(operator.recover(op.id))
    assert answer["stage"] == "RECOVERED", answer
    assert before == (native.starts, len(native.sent), control.store.get_epic(epic.id))
    assert not operator.sessions._get(epic).result["requires_reconciliation"]


@pytest.mark.parametrize("review_setup", ["delivery"], indirect=True)
@pytest.mark.parametrize("boundary", ["merge", "passed-test", "unknown-test"])
def test_coordinator_delivery_recovers_one_main_merge_and_exact_final_verification(
    delivery_setup, monkeypatch, boundary,
):
    d = delivery_setup
    f, delivery, control = d.f, d.delivery, d.control
    async def exercise():
        await approved(f)
        with monkeypatch.context() as patch:
            if boundary == "merge":
                original = EpicIntegrationService.merge_epic_to_main
                def lost(*args, **kwargs):
                    original(*args, **kwargs)
                    raise RuntimeError("controlled lost main merge checkpoint")
                patch.setattr(EpicIntegrationService, "merge_epic_to_main", lost)
            else:
                original = EpicIntegrationService.verify_main_merge
                def lost(manager, *args, **kwargs):
                    if boundary == "passed-test":
                        original(manager, *args, **kwargs)
                        raise RuntimeError("controlled lost final test checkpoint")
                    with monkeypatch.context() as command_patch:
                        def no_process(*args, **kwargs):
                            raise RuntimeError("controlled unknown final test")
                        command_patch.setattr(manager, "_execute", no_process)
                        return original(manager, *args, **kwargs)
                patch.setattr(EpicIntegrationService, "verify_main_merge", lost)
            with pytest.raises(RuntimeError, match="controlled"):
                await delivery.deliver(f.coordinator, "p", "e", "delivery", "original")
        parent = delivery._rows()[0]
        main = f.review.runtime.git.head("main")
        starts = f.sessions.starts
        sessions = SessionRecoveryService(f.review.runtime.settings, delivery.store,
                                          control.scheduler.selection.reader, project_id="p",
                                          coordinator=delivery.runtime,
                                          integrations=[control.epic_start])
        operator = OperationRecoveryService(sessions, controls=[control], delivery=delivery)
        result = await operator.recover(parent.id)
        assert result["stage"] == "RECOVERED", result
        epic = delivery.store.get_epic("e")
        assert epic.status == "DONE" and epic.merge_commit == main
        assert f.review.runtime.git.head("main") == main and f.sessions.starts == starts
        assert len(delivery.store.get_operations("e", kind="merge_epic_to_main")) == 1
        tests = delivery.store.get_operations("e", kind="verify_main_merge")
        assert len(tests) == (2 if boundary == "unknown-test" else 1)
        assert sum(t.status == "SUCCEEDED" for t in tests) == 1
        assert all(t.status != "PENDING" for t in tests)
        assert f.board.epics[0]["status"] == "Done"
    run(exercise())


@pytest.mark.parametrize("checkpoint", ["RUNTIME_READY", "REGISTERED"])
def test_coordinator_partial_registration_uses_original_agent_and_prompt_only_once(
    runtime_setup, monkeypatch, checkpoint,
):
    runtime, actor, native, _ = runtime_setup
    save = runtime._save
    with monkeypatch.context() as patch:
        def lost(op, **updates):
            if updates.get("stage") == checkpoint:
                raise RuntimeError("controlled Coordinator checkpoint loss")
            return save(op, **updates)
        patch.setattr(runtime, "_save", lost)
        with pytest.raises(RuntimeError, match="controlled"):
            runtime.start(actor)
    op = runtime.store.get_project_operation("p", runtime.KIND, runtime.run_id)
    sessions = SessionRecoveryService(runtime.settings, runtime.store, object(), project_id="p",
                                      coordinator=runtime)
    before = native.starts, len(native.workspaces)
    result = run(OperationRecoveryService(sessions).recover(op.id))
    assert result["stage"] == "RECOVERED", result
    assert (native.starts, len(native.workspaces)) == before
    assert len(native.prompts) == 1
    assert runtime.store.get_coordinator(runtime.run_id).codex_session_id


def test_unknown_coordinator_workspace_escalates_without_adoption_or_second_create(runtime_setup):
    from orchestrator.adapters.herdr import HerdrError

    runtime, actor, native, _ = runtime_setup
    native.fail_create = True
    with pytest.raises(HerdrError):
        runtime.start(actor)
    native.fail_create = False
    op = runtime.store.get_project_operation("p", runtime.KIND, runtime.run_id)
    sessions = SessionRecoveryService(runtime.settings, runtime.store, object(), project_id="p",
                                      coordinator=runtime)
    answer = run(OperationRecoveryService(sessions).recover(op.id))
    assert answer["stage"] == "ATTENTION", answer
    assert len(native.workspaces) == 1 and native.starts == 0 and len(native.prompts) == 0


@pytest.mark.parametrize("case", ["inactive", "lingering", "owner"])
def test_completed_coordinator_monitor_requires_actual_inactivity_without_reopening(
    claims_setup, case,
):
    claims, actor, _, _, _, _ = claims_setup
    runtime = claims.runtime
    sessions = SessionRecoveryService(runtime.settings, runtime.store, claims.reader,
                                      project_id="p", coordinator=runtime)
    coordinator = runtime.store.get_coordinator(runtime.run_id)
    registration = runtime.store.get_project_operation("p", runtime.KIND, runtime.run_id)
    op = sessions._record(coordinator, (registration, actor, runtime.codex), "ATTENTION",
                          codes=["TEAMPLAYER_UNAVAILABLE"])
    runtime.stop(actor, "original-observed-stop")
    if case == "lingering":
        runtime.processes.lingering = True
    elif case == "owner":
        closed = runtime.store.get_coordinator(runtime.run_id)
        # Fault injection only: the valid API forbids changing a completed owner.
        corrupted = closed.model_copy(update={"actor_id": "changed-owner"})
        with runtime.store.transaction() as db:
            db.execute("UPDATE coordinator_runs SET payload=? WHERE id=?",
                       (corrupted.model_dump_json(), closed.id))
    before = runtime.store.get_coordinator(runtime.run_id), runtime.herdr.starts
    result = run(OperationRecoveryService(sessions).recover(op.id))
    assert result["stage"] == ("RECOVERED" if case == "inactive" else "ATTENTION"), result
    assert before == (runtime.store.get_coordinator(runtime.run_id), runtime.herdr.starts)
    assert sessions._get(before[0]).result["requires_reconciliation"] is (case != "inactive")


@pytest.mark.parametrize("original_coordinator", [True, False])
def test_partial_epic_registration_requires_protected_original_coordinator_context(
    claims_setup, monkeypatch, original_coordinator,
):
    from orchestrator.application.epic_start_service import EpicStartError

    claims, actor, _, factory, natives, _ = claims_setup
    configured = claims.spec.epics[0]
    identity = str(uuid4())
    parent = factory(configured, identity)  # Setup only; recovery never receives a factory.
    save = parent._save
    with monkeypatch.context() as patch:
        def lost(op, **updates):
            if updates.get("stage") == "REGISTERED":
                raise RuntimeError("controlled Integration registration checkpoint loss")
            return save(op, **updates)
        patch.setattr(parent, "_save", lost)
        with pytest.raises(EpicStartError):
            run(parent.start(actor, "p", parent.spec.epic_id, identity))
    op = parent.store.get_operation("p", parent.KIND, identity)
    native = natives[identity]
    before = native.starts, len(native.prompts), len(native.workspaces)
    sessions = SessionRecoveryService(claims.settings, claims.store, claims.reader,
                                      project_id="p", integrations=[parent],
                                      coordinator=claims.runtime if original_coordinator else None)
    result = run(OperationRecoveryService(sessions).recover(op.id))
    assert result["stage"] == ("RECOVERED" if original_coordinator else "ATTENTION"), result
    assert before == (native.starts, len(native.prompts), len(native.workspaces))


@pytest.mark.parametrize("review_setup", ["delivery"], indirect=True)
def test_known_failed_main_verification_is_not_automatically_retried(delivery_setup):
    d = delivery_setup
    f, delivery, control = d.f, d.delivery, d.control
    async def exercise():
        await approved(f)
        f.verification_failure_file.write_text("Controlled actual verifier failure\n")
        failed = await delivery.deliver(f.coordinator, "p", "e", "delivery", "failed")
        assert failed["status"] == "ATTENTION"
        f.verification_failure_file.unlink()
        parent = delivery._rows()[0]
        before = delivery.store.get_operations("e", kind="verify_main_merge")
        main = f.review.runtime.main()
        sessions = SessionRecoveryService(f.review.runtime.settings, delivery.store,
                                          control.scheduler.selection.reader, project_id="p",
                                          coordinator=delivery.runtime,
                                          integrations=[control.epic_start])
        operator = OperationRecoveryService(sessions, controls=[control], delivery=delivery)
        answer = await operator.recover(parent.id)
        assert answer["stage"] == "ATTENTION", answer
        assert answer["reason"] == "FAILED_TEST_REQUIRES_OPERATOR_DECISION"
        assert delivery.store.get_operations("e", kind="verify_main_merge") == before
        assert f.review.runtime.main() == main and delivery.store.get_epic("e").status == "MERGING"
    run(exercise())


@pytest.mark.parametrize("kind", ["task_start", "start_runtime", "dispatch_assignment",
                                  "teamplayer_sync"])
def test_alias_operation_key_cannot_continue_another_original_intent(control_setup, kind):
    control, _, _, board, native = control_setup
    start(control)
    t = task(control, "F-01")
    real = original(control, kind)
    alias = Operation(project_id="p", epic_run_id=t.epic_run_id, task_run_id=t.id,
                      kind=kind, idempotency_key="invalid-alias-key", result=real.result)
    control.store.add_operation(alias)
    before = native.starts, len(native.sent), len(board.applied)
    operator = recovery(control)
    answer = run(operator.recover(alias.id))
    assert answer["stage"] == "ATTENTION", answer
    assert answer["reason"] == ("ORIGINAL_BOARD_INTENT_CHANGED" if kind == "teamplayer_sync"
                                else "ORIGINAL_OPERATION_KEY_CHANGED")
    assert before == (native.starts, len(native.sent), len(board.applied))
    assert operator._find(real.id) == real
