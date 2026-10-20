"""Reconnect original registered sessions and restore bounded monitoring.

Operator composition only: no agent arguments, start factories, adoption or guessed loss.
Normal scheduling/review remains in the original control services after recovery.
"""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

from orchestrator.application.coordinator_runtime_service import CoordinatorRuntimeService
from orchestrator.application.epic_correction_plan import RegisteredIntegrationPlan
from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.reconciliation_service import (
    CoordinatorObserver,
    IntegrationObserver,
    LifecycleObserver,
    ReconciliationService,
)
from orchestrator.application.session_recovery_record import KIND, fingerprint
from orchestrator.application.task_resume_intent import verified_input
from orchestrator.application.task_resume_service import TaskResumeService
from orchestrator.domain.coordinator import CoordinatorRun, ProjectOperation
from orchestrator.domain.models import EpicRun, Operation, TaskRun, utc_now
from orchestrator.domain.states import EpicState, TaskState


class SessionRecoveryError(RuntimeError):
    """Fixed safe codes. Never expose native messages, answers or adapter errors."""


class SessionRecoveryService:
    """Recreate from protected original configuration and the same SQLite on restart."""

    def __init__(self, settings, store, reader, *, project_id, coordinator=None,
                 integrations=(), resumers=None):
        self.settings, self.store, self.project_id = settings, store, project_id
        if coordinator is not None and (
            not isinstance(coordinator, CoordinatorRuntimeService)
            or coordinator.store is not store or coordinator.settings != settings
            or coordinator.spec.project_id != project_id
        ):
            raise SessionRecoveryError("RECOVERY_CONFIGURATION_INVALID")
        self.coordinator = coordinator
        self.parents = {}
        for view in integrations:
            if not isinstance(view, (EpicStartService, RegisteredIntegrationPlan)):
                raise SessionRecoveryError("RECOVERY_CONFIGURATION_INVALID")
            observer = IntegrationObserver(view)
            parent = observer.startup
            if not isinstance(parent, EpicStartService) or parent.store is not store or (
                parent.spec.project_id != project_id
                or parent.settings.repository != settings.repository
                or parent.settings.sqlite_path != settings.sqlite_path
                or parent.principal.epic_run_id in self.parents
            ):
                raise SessionRecoveryError("RECOVERY_CONFIGURATION_INVALID")
            self.parents[parent.principal.epic_run_id] = observer
        self.resumers = dict(resumers or {})
        for epic_id, resume in self.resumers.items():
            parent = self.parents.get(epic_id)
            if not isinstance(resume, TaskResumeService) or parent is None or (
                resume.store is not store or resume.settings != parent.startup.settings
                or resume.sync.project_id != project_id
                or resume.sync.external_project_id != reader.project_id
                or resume.sync.user_id != reader.user_id
                or resume.lifecycle.herdr is not parent.startup.herdr
                or resume.lifecycle.codex is not parent.startup.codex
            ):
                raise SessionRecoveryError("RECOVERY_CONFIGURATION_INVALID")
        self.audit = ReconciliationService(
            settings, store, reader, project_id=project_id, observers=self._observer,
            evidence_contexts={k: v.startup.settings for k, v in self.parents.items()},
            scopes={k: tuple(t.task.task_id for t in (v.view or v.startup).spec.tasks)
                    for k, v in self.parents.items()},
            codex=(next(iter(self.parents.values())).startup.codex if self.parents
                   else coordinator.codex if coordinator else None),
            processes=(next(iter(self.parents.values())).startup.lifecycle.processes if self.parents
                       else coordinator.processes if coordinator else None),
        )

    @contextmanager
    def _lock(self):
        try:
            fd = os.open(str(self.settings.sqlite_path) + ".session-recovery.lock",
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(fd)
                raise
        except BlockingIOError:
            raise SessionRecoveryError("RECOVERY_BUSY") from None
        except OSError:
            raise SessionRecoveryError("RECOVERY_LOCK_UNAVAILABLE") from None
        try:
            yield
        finally:
            os.close(fd)

    def _load(self, identity):
        return (self.store.get_coordinator(identity) or self.store.get_epic(identity)
                or self.store.get_task(identity))

    def _observer(self, run):
        if isinstance(run, CoordinatorRun):
            return (CoordinatorObserver(self.coordinator)
                    if self.coordinator is not None and run.id == self.coordinator.run_id else None)
        parent = self.parents.get(run.epic_run_id if isinstance(run, TaskRun) else run.id)
        if parent is None:
            return None
        return (LifecycleObserver(parent.startup.lifecycle, parent.startup.principal)
                if isinstance(run, TaskRun) else parent)

    def _context(self, run):
        """Derive principals from protected original registrations, never call arguments."""
        if run.project_id != self.project_id or run.completed_at is not None:
            raise SessionRecoveryError("RECOVERY_SCOPE_DENIED")
        if isinstance(run, CoordinatorRun):
            s = self.coordinator
            if s is None or run.id != s.run_id:
                raise SessionRecoveryError("ORIGINAL_CONTEXT_REQUIRED")
            s.scope(s.principal)
            op = self.store.get_project_operation(run.project_id, s.KIND, run.id)
            if op is None or op.status != "SUCCEEDED" or op.result.get("stage") != "REGISTERED" or (
                run.configuration_hash != s.configuration_hash
                or run.actor_id != s.principal.actor_id
                or run.worktree_path != str(s.settings.repository)
            ):
                raise SessionRecoveryError("ORIGINAL_CONTEXT_REQUIRED")
            return op, s.principal, s.codex
        parent = self.parents.get(run.epic_run_id if isinstance(run, TaskRun) else run.id)
        if parent is None:
            raise SessionRecoveryError("ORIGINAL_CONTEXT_REQUIRED")
        if parent.view is not None:
            parent.view.verify_membership()
        s = parent.startup
        op = self.store.get_operation(run.project_id, s.KIND, s.principal.epic_run_id)
        if op is None or op.status != "SUCCEEDED" or op.result.get("stage") != "REGISTERED":
            raise SessionRecoveryError("ORIGINAL_CONTEXT_REQUIRED")
        s._verify(parent.principal, op)
        if isinstance(run, TaskRun):
            entries = {e.task.task_id: e.task for e in (parent.view or s).spec.tasks}
            start = self.store.get_operation(run.project_id, "task_start", run.task_id)
            spec = entries.get(run.task_id)
            if start is None or spec is None or start.task_run_id != run.id or (
                start.epic_run_id != run.epic_run_id or start.project_id != run.project_id
                or start.result.get("spec") != spec.model_dump(mode="json")
                or start.result.get("spec_hash") != fingerprint(start.result["spec"])
                or start.result.get("branch") != run.branch
                or start.result.get("cwd") != run.worktree_path
                or start.result.get("base_commit") != run.base_commit
            ):
                raise SessionRecoveryError("ORIGINAL_TASK_OWNER_REQUIRED")
        return op, s.principal, s.codex

    def _get(self, run):
        if isinstance(run, CoordinatorRun):
            return self.store.get_project_operation(run.project_id, KIND, run.id)
        return self.store.get_operation(run.project_id, KIND, run.id)

    def _record(self, run, context, stage, *, codes=(), cursor=None, resume_key=None,
                runtime_status=None):
        registration, actor, _ = context
        with self.store.transaction():
            if self._load(run.id) != run:
                raise SessionRecoveryError("RECOVERY_STATE_CHANGED")
            old = self._get(run)
            anchor = dict(registration_id=registration.id,
                          registration_hash=registration.result.get("intent_hash")
                          or registration.result.get("configuration_hash")
                          or getattr(run, "configuration_hash", None),
                          principal=actor.model_dump(mode="json"))
            if old is not None and any(old.result.get(k) != v for k, v in anchor.items()):
                raise SessionRecoveryError("RECOVERY_OWNER_CHANGED")
            reason = ("Recovery requires operator action: " + ", ".join(sorted(set(codes)))
                      + ". Restore the original registered resource/connection, then rerun "
                      "recovery. Keep session, branch, worktree and reservation; no replacement."
                      if stage == "ATTENTION" else "")
            semantic = dict(subject=run.model_dump(mode="json"), stage=stage, reason=reason)
            changed = old is None or any(old.result.get(k) != v for k, v in semantic.items())
            revision = (old.result["revision"] + int(changed)) if old else 1
            history = list(old.result.get("history", [])) if old else []
            if changed:
                history.append(dict(revision=revision, stage=stage, codes=sorted(set(codes))))
            data = anchor | semantic | dict(
                revision=revision, history=history, cursor=cursor,
                runtime_status=runtime_status,
                requires_reconciliation=stage in {"ATTENTION", "ORIGINAL_OPERATION_REQUIRED"},
                had_attention=stage == "ATTENTION" or bool(old and old.result["had_attention"]),
                resume_key=resume_key or (old.result.get("resume_key") if old else None),
            )
            data["observation_hash"] = fingerprint(data)
            if old is not None and old.result == data:
                return old
            if old is not None:
                value = old.model_copy(update={"result": data, "updated_at": utc_now()})
                if isinstance(run, CoordinatorRun):
                    self.store.update_project_operation(value)
                else:
                    self.store.update_operation(value)
                return value
            fields = dict(project_id=run.project_id, kind=KIND, idempotency_key=run.id,
                          status="SUCCEEDED", result=data)
            if isinstance(run, CoordinatorRun):
                value = ProjectOperation(coordinator_run_id=run.id, **fields)
                self.store.add_project_operation(value)
            else:
                value = Operation(
                    epic_run_id=run.epic_run_id if isinstance(run, TaskRun) else run.id,
                    task_run_id=run.id if isinstance(run, TaskRun) else None, **fields,
                )
                self.store.add_operation(value)
            return value

    async def _mirror(self, run, op):
        resume = self.resumers.get(getattr(run, "epic_run_id", None))
        if not isinstance(run, TaskRun) or resume is None or not op.result["had_attention"]:
            return None
        if op.result["stage"] == "ORIGINAL_OPERATION_REQUIRED":
            return dict(status="PENDING", reason="RECONCILE_ORIGINAL_SYNC")
        try:
            return await resume.sync.sync_task(
                self.parents[run.epic_run_id].startup.principal, run.id
            )
        except Exception:
            return dict(status="PENDING", reason="RECOVERY_SYNC_UNVERIFIED")

    def _cursor(self, run, codex):
        thread = codex.read_thread(run.codex_session_id, run.worktree_path)
        if thread["id"] != run.codex_session_id or thread["sessionId"] != run.codex_session_id or (
            Path(thread["cwd"]) != Path(run.worktree_path)
        ):
            raise SessionRecoveryError("SESSION_IDENTITY_CHANGED")
        # No messages/answers/prompts in recovery output or monitoring journal.
        return [dict(id=t["id"], status=t["status"]) for t in thread["turns"]]

    async def poll(self):
        """One bounded monitor/recovery pass. Repeated calls never replay a start/prompt."""
        with self._lock():
            inventory = self.store.project_inventory(self.project_id)
            targets = [r.id for k in ("coordinators", "epics", "tasks") for r in inventory[k]
                       if r.completed_at is None]
            results = []
            for identity in targets:
                run = self._load(identity)
                try:
                    context = self._context(run)
                except Exception:
                    results.append(dict(run_id=identity, stage="ATTENTION",
                                        codes=["ORIGINAL_CONTEXT_REQUIRED"],
                                        next_action="RESTORE_ORIGINAL_OPERATOR_CONFIGURATION"))
                    continue
                report = await self.audit.inspect()
                row = next((r for r in report["runs"] if r["run_id"] == identity), None)
                codes = [f["code"] for f in report["findings"]]
                codes += [f["code"] for f in row["findings"]] if row else ["RUN_NOT_OBSERVED"]
                if not self.audit.current(report) or self._load(identity) != run:
                    raise SessionRecoveryError("RECOVERY_STATE_CHANGED")
                codes = [c for c in codes if c != "ORIGINAL_RUNTIME_STOPPED"]
                old = self._get(run)
                if old is not None and old.result.get("stage") == "ATTENTION":
                    # The old subject may differ precisely because the operator
                    # restored the original resource. All physical/Git/owner checks
                    # above still apply; _record rechecks the original owner anchor.
                    codes = [c for c in codes if c != "RECOVERY_OBSERVATION_CHANGED"]
                    if row and row.get("board_status") in {"NeedsInput", "Blocked"}:
                        codes = [c for c in codes if c != "BOARD_STATE_MISMATCH"]
                if self._pending_resume(run, context):
                    # Only observe/reconcile this original persisted resume. F13
                    # and F32 will not send a second start when its outcome is unknown.
                    codes = [c for c in codes if c not in {
                        "RUNTIME_PROCESS_CHANGED", "RUNTIME_MISSING_UNCONFIRMED",
                    }]
                # Unfinished foreign effects on this resource belong to F44. F32
                # alone may continue its already saved input intent below.
                owned = [o for o in report["operations"] if o["run_id"] == identity
                         and o["kind"] not in {KIND, "task_resume", "resume_runtime"}]
                if owned:
                    codes.append("ORIGINAL_OPERATION_REQUIRED")
                if not run.codex_session_id:
                    codes.append("SESSION_REFERENCE_MISSING")
                stage, cursor, runtime_status = "CONNECTED", None, None
                if codes:
                    stage = ("ORIGINAL_OPERATION_REQUIRED" if set(codes) <= {
                        "BOARD_STATE_MISMATCH", "ORIGINAL_OPERATION_REQUIRED",
                    } else "ATTENTION")
                else:
                    try:
                        stage = await self._resume(run, context, row)
                        run = self._load(identity)
                        if stage in {"CONNECTED", "INPUT_PENDING"}:
                            # Verify again after any F13/F32 operation before monitoring.
                            physical = self._observer(run)(run)
                            runtime_status = physical.get("runtime_status")
                            cursor = self._cursor(run, context[2])
                    except Exception as error:
                        stage = "ATTENTION"
                        codes = ([str(error)] if isinstance(error, SessionRecoveryError)
                                 else ["ORIGINAL_SESSION_UNVERIFIED"])
                op = self._record(run, context, stage, codes=codes, cursor=cursor,
                                  runtime_status=runtime_status)
                mirror = await self._mirror(run, op)
                results.append(dict(run_id=identity, stage=stage,
                                    session_id=run.codex_session_id,
                                    worker_slot=getattr(run, "worker_slot", None),
                                    codes=sorted(set(codes)), cursor=cursor, sync=mirror,
                                    runtime_status=runtime_status,
                                    next_action="RESTORE_ORIGINAL_RESOURCE_AND_RETRY"
                                    if stage == "ATTENTION" else None))
            return dict(project_id=self.project_id, sessions=results)

    async def _resume(self, run, context, row):
        if isinstance(run, TaskRun):
            pending = [o for o in self.store.get_operations(run.epic_run_id, kind="task_resume")
                       if o.task_run_id == run.id and o.status == "PENDING"]
            if pending:
                if len(pending) != 1 or run.epic_run_id not in self.resumers:
                    raise SessionRecoveryError("ORIGINAL_INPUT_SERVICE_REQUIRED")
                op = pending[0]
                verified_input(self.store, run, op)
                if op.result["actor"] != context[1].model_dump(mode="json"):
                    raise SessionRecoveryError("ORIGINAL_INPUT_OWNER_CHANGED")
                result = await self.resumers[run.epic_run_id].resume(
                    context[1], run.id, op.result["decision"]
                )
                if result.get("reason") == "WORKER_CAPACITY_UNAVAILABLE":
                    return "WAITING_CAPACITY"
                return "CONNECTED" if result["stage"] == "ACTIVE_AND_SYNCED" else "INPUT_PENDING"
            if run.internal_status == TaskState.PARKED:
                if row.get("runtime_observation") != "STOPPED" or run.worker_slot is not None:
                    raise SessionRecoveryError("PARKED_RUNTIME_UNVERIFIED")
                return "WAITING_INPUT"
            if row.get("runtime_observation") != "LIVE":
                raise SessionRecoveryError("ORIGINAL_WORKER_RESUME_DECISION_REQUIRED")
        elif isinstance(run, EpicRun) and (
            row.get("runtime_observation") == "STOPPED" or self._pending_resume(run, context)
        ):
            if run.status != EpicState.ACTIVE:
                return "ORIGINAL_OPERATION_REQUIRED"
            parent = self.parents[run.id]
            start = self.store.get_operation(run.project_id, "start_runtime", run.id)
            old = self._get(run)
            key = old.result.get("resume_key") if self._pending_resume(run, context) else None
            key = key or "reconnect:" + fingerprint(
                [run.id, start.result.get("generation", start.id)]
            )
            self._record(run, context, "ORIGINAL_OPERATION_REQUIRED", resume_key=key)
            parent.startup.lifecycle.resume_epic(parent.principal, run.id, key=key)
        return "CONNECTED"

    def _pending_resume(self, run, context):
        """A known, protected original key permits observation, never a fresh replacement."""
        if isinstance(run, CoordinatorRun):
            return False
        key = None
        if isinstance(run, TaskRun):
            inputs = [o for o in self.store.get_operations(run.epic_run_id, kind="task_resume")
                      if o.task_run_id == run.id and o.status == "PENDING"]
            if len(inputs) != 1:
                return False
            try:
                verified_input(self.store, run, inputs[0])
                if inputs[0].result["actor"] != context[1].model_dump(mode="json"):
                    return False
                key = inputs[0].result["resume_key"]
            except Exception:
                return False
        else:
            prior = self._get(run)
            key = prior.result.get("resume_key") if prior else None
        op = self.store.get_operation(run.project_id, "resume_runtime", key) if key else None
        return bool(op and op.status == "PENDING" and op.epic_run_id == (
            run.epic_run_id if isinstance(run, TaskRun) else run.id
        ) and op.task_run_id == (run.id if isinstance(run, TaskRun) else None)
            and op.result.get("session_id") == run.codex_session_id)
