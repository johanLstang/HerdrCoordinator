"""Operator-only recovery of one original durable operation, never a new claim."""

from orchestrator.application.coordinator_delivery_service import CoordinatorDeliveryService
from orchestrator.application.integration_control_service import IntegrationControlService
from orchestrator.application.session_recovery_record import KIND as MONITOR
from orchestrator.application.session_recovery_record import fingerprint
from orchestrator.application.session_recovery_service import SessionRecoveryService
from orchestrator.domain.coordinator import CoordinatorRun, ProjectOperation
from orchestrator.domain.models import Operation, TaskRun, utc_now


class OperationRecoveryError(RuntimeError):
    """Fixed safe codes; raw adapter errors and native messages never leave this service."""


class OperationRecoveryService:
    KIND = "operation_recovery"

    def __init__(self, sessions, *, controls=(), delivery=None):
        if not isinstance(sessions, SessionRecoveryService):
            raise OperationRecoveryError("RECOVERY_CONFIGURATION_INVALID")
        self.sessions, self.store = sessions, sessions.store
        self.project_id = sessions.project_id
        self.controls = {}
        for control in controls:
            if not isinstance(control, IntegrationControlService):
                raise OperationRecoveryError("RECOVERY_CONFIGURATION_INVALID")
            identity = control.actor.epic_run_id
            parent = sessions.parents.get(identity)
            if parent is None or control.store is not self.store or (
                control.epic_start is not (parent.view or parent.startup)
                or identity in self.controls
            ):
                raise OperationRecoveryError("RECOVERY_CONFIGURATION_INVALID")
            self.controls[identity] = control
        if delivery is not None and (
            not isinstance(delivery, CoordinatorDeliveryService)
            or delivery.runtime is not sessions.coordinator
            or delivery.store is not self.store
        ):
            raise OperationRecoveryError("RECOVERY_CONFIGURATION_INVALID")
        self.delivery = delivery

    def _find(self, identity):
        inventory = self.store.project_inventory(self.project_id)
        rows = [o for key in ("operations", "project_operations") for o in inventory[key]
                if o.id == identity]
        if len(rows) != 1 or rows[0].kind == self.KIND:
            raise OperationRecoveryError("ORIGINAL_OPERATION_REQUIRED")
        return rows[0]

    @staticmethod
    def _owner(op):
        return (getattr(op, "task_run_id", None) or getattr(op, "epic_run_id", None)
                or getattr(op, "coordinator_run_id", None))

    def _journal(self, original, *, stage, reason="", result=None):
        """Retain original history; this receipt confers no role or effect permission."""
        with self.store.transaction():
            current = self._find(original.id)
            owner = self._owner(current)
            if owner != self._owner(original):
                raise OperationRecoveryError("RECOVERY_OWNER_CHANGED")
            project = isinstance(original, ProjectOperation)
            getter = self.store.get_project_operation if project else self.store.get_operation
            old = getter(self.project_id, self.KIND, original.id)
            anchor = dict(original_id=original.id, original_kind=original.kind,
                          original_key=original.idempotency_key, original_owner=owner)
            if old and any(old.result.get(k) != v for k, v in anchor.items()):
                raise OperationRecoveryError("RECOVERY_OWNER_CHANGED")
            history = list(old.result.get("history", [])) if old else []
            observation = dict(stage=stage, reason=reason,
                               original_hash=fingerprint(current.model_dump(mode="json")),
                               result=result or {})
            if not history or history[-1] != observation:
                history.append(observation)
            data = anchor | observation | dict(
                history=history, requires_reconciliation=stage in {"ATTENTION", "WAITING"},
                next_action="RESTORE_ORIGINAL_RESOURCE_AND_RETRY_SAME_OPERATION"
                if stage in {"ATTENTION", "WAITING"} else None,
            )
            data["observation_hash"] = fingerprint(data)
            if old:
                value = old.model_copy(update=dict(result=data, updated_at=utc_now()))
                update = (self.store.update_project_operation if project
                          else self.store.update_operation)
                update(value)
            else:
                fields = dict(project_id=self.project_id, kind=self.KIND,
                              idempotency_key=original.id, status="SUCCEEDED", result=data)
                if project:
                    value = ProjectOperation(coordinator_run_id=original.coordinator_run_id,
                                             **fields)
                    self.store.add_project_operation(value)
                else:
                    value = Operation(epic_run_id=original.epic_run_id,
                                      task_run_id=original.task_run_id, **fields)
                    self.store.add_operation(value)
            return dict(operation_id=original.id, recovery_id=value.id, stage=stage,
                        reason=reason, result=result or {}, next_action=data["next_action"])

    def _control(self, op):
        control = self.controls.get(op.epic_run_id)
        if control is None:
            raise OperationRecoveryError("ORIGINAL_INTEGRATION_CONTROL_REQUIRED")
        control.epic_start.connection_principal(control.actor)
        return control

    async def recover(self, operation_id):
        """No report/role/status arguments. Original services revalidate before each effect."""
        with self.sessions._lock():
            original = self._find(operation_id)
            if original.status in {"FAILED", "CONFLICT"}:
                return self._journal(original, stage="ATTENTION",
                                     reason="ORIGINAL_RESULT_REQUIRES_OPERATOR_DECISION")
            # A completed effect is not executed again just to verify a receipt.
            if original.status in {"SUCCEEDED", "SUPERSEDED"} and not original.result.get(
                "requires_reconciliation"
            ):
                return self._journal(original, stage="RECORDED_COMPLETE")
            self._journal(original, stage="WAITING", reason="ORIGINAL_OPERATION_IN_PROGRESS")
            try:
                result = await self._drive(original)
                if result.get("status") in {"TEST_FAILED", "ATTENTION"}:
                    return self._journal(original, stage="ATTENTION",
                                         reason="ORIGINAL_DELIVERY_REQUIRES_OPERATOR_ACTION",
                                         result=result)
                current = self._find(original.id)
                settled = current.status in {"SUCCEEDED", "SUPERSEDED"} and not (
                    current.result.get("requires_reconciliation")
                ) and result.get("sync_status") != "PENDING"
                return self._journal(original, stage="RECOVERED" if settled else "WAITING",
                                     reason="" if settled else "ORIGINAL_OUTCOME_UNCONFIRMED",
                                     result=result)
            except Exception as error:
                # Observe persisted outcome on the next explicit retry. Never reset
                # an external intent or infer inactivity from an exception.
                safe_codes = {
                    "SCHEDULER_BOARD_OWNER_CHANGED", "SCHEDULER_BOARD_STATUS_CONFLICT",
                    "SCHEDULER_BOARD_SPEC_CHANGED", "WORKSPACE_OUTCOME_UNKNOWN",
                    "COORDINATOR_WORKSPACE_OUTCOME_UNKNOWN", "COORDINATOR_AGENT_OUTCOME_UNKNOWN",
                    "COORDINATOR_INPUT_NOT_READY", "COORDINATOR_ACK_UNVERIFIED",
                    "COORDINATOR_ACK_TIMEOUT", "DELIVERY_BUSY", "DELIVERY_UNVERIFIED",
                    "TEAMPLAYER_MANUAL_STATUS_DIVERGED", "TEAMPLAYER_HISTORY_OUTCOME_DIVERGED",
                }
                reason = (str(error) if isinstance(error, OperationRecoveryError)
                          or str(error) in safe_codes else "ORIGINAL_OPERATION_UNVERIFIED")
                return self._journal(original, stage="ATTENTION", reason=reason,
                                     result=await self._diagnostic(original))

    async def _diagnostic(self, original):
        # F42 emits fixed codes/actions, never adapter messages or prompt content.
        # These are diagnostic observations, not permission to retry an effect.
        try:
            report = await self.sessions.audit.inspect()
            findings = list(report["findings"])
            row = next((r for r in report["runs"]
                        if r["run_id"] == self._owner(original)), None)
            if row:
                findings += row["findings"]
            return dict(run_id=self._owner(original), findings=[
                dict(code=f["code"], next_action=f["next_action"]) for f in findings
            ])
        except Exception:
            return dict(run_id=self._owner(original), findings=[dict(
                code="RECOVERY_OBSERVATION_UNAVAILABLE",
                next_action="RESTORE_ORIGINAL_CONNECTION_AND_REPEAT_READ_ONLY_AUDIT",
            )])

    async def _drive(self, op):
        kind = op.kind
        if kind == MONITOR:
            return await self._stopped_monitor(op)
        if kind == "coordinator_start":
            runtime = self.sessions.coordinator
            if runtime is None or runtime.run_id != op.coordinator_run_id or (
                op.idempotency_key != runtime.run_id
            ):
                raise OperationRecoveryError("ORIGINAL_COORDINATOR_REQUIRED")
            run = runtime.start(runtime.principal)
            return dict(run_id=run.id)
        if kind == "coordinator_stop":
            runtime = self.sessions.coordinator
            if runtime is None or runtime.run_id != op.coordinator_run_id:
                raise OperationRecoveryError("ORIGINAL_COORDINATOR_REQUIRED")
            stopped = runtime.stop(runtime.principal, op.idempotency_key)
            return dict(operation_id=stopped.id)
        if kind == "epic_start" or kind == "start_runtime" and not op.task_run_id:
            parent = self.sessions.parents.get(op.epic_run_id)
            if parent is None:
                raise OperationRecoveryError("ORIGINAL_EPIC_START_REQUIRED")
            start = parent.startup
            original = self.store.get_operation(self.project_id, "epic_start", op.epic_run_id)
            if original is None:
                raise OperationRecoveryError("ORIGINAL_EPIC_START_REQUIRED")
            runtime = self.sessions.coordinator
            if runtime is None:
                raise OperationRecoveryError("ORIGINAL_COORDINATOR_REQUIRED")
            actor = runtime.connection_principal(runtime.principal)
            start._verify(actor, original)
            outcome = await start.start(actor, self.project_id, start.spec.epic_id, op.epic_run_id)
            return dict(stage=outcome["stage"])
        if kind in {"task_start", "start_runtime", "dispatch_assignment"}:
            control = self._control(op)
            task = self.store.get_task(op.task_run_id)
            configured = [t.task for t in control.epic_start.spec.tasks
                          if t.task.task_id == task.task_id]
            if len(configured) != 1:
                raise OperationRecoveryError("ORIGINAL_TASK_SPEC_REQUIRED")
            original = self.store.get_operation(self.project_id, "task_start", task.task_id)
            if original is None or original.task_run_id != task.id:
                raise OperationRecoveryError("ORIGINAL_TASK_START_REQUIRED")
            scheduler = control.scheduler
            if task.internal_status == "WORKING":
                # ACK may have committed before its parent checkpoint/board sync.
                # The ordinary start route expects the board already Active. Here
                # the original claim guard and assignment proof finish that exact
                # intent first, with fresh identity/owner/spec/dependency evidence.
                await scheduler.sync._identity()
                scheduler._board = await scheduler.selection.reader.read_project()
                result = scheduler.start.start(control.actor, op.epic_run_id, configured[0],
                                               timeout_seconds=scheduler.timeout_seconds)
                synced = await scheduler.sync.sync_task(control.actor, task.id)
                return dict(status=result["status"], task_run_id=task.id,
                            sync_status=synced["status"])
            result = await scheduler.start_selected(control.actor, op.epic_run_id, configured[0])
            return dict(status=result["status"], task_run_id=task.id)
        if kind in {"task_merge", "merge_task_to_epic", "task_delivery_test"}:
            return await self._task_delivery(op)
        if kind in {"coordinator_epic_delivery", "merge_epic_to_main", "verify_main_merge"}:
            return await self._epic_delivery(op)
        if kind == "teamplayer_sync":
            parent = self.sessions.parents.get(op.epic_run_id)
            control = self.controls.get(op.epic_run_id)
            sync = control.scheduler.sync if control else parent.startup.sync if parent else None
            if sync is None:
                raise OperationRecoveryError("ORIGINAL_BOARD_SYNC_REQUIRED")
            if op.task_run_id and control:
                actor = control.epic_start.connection_principal(control.actor)
            else:
                runtime = self.sessions.coordinator
                if runtime is None:
                    raise OperationRecoveryError("ORIGINAL_COORDINATOR_REQUIRED")
                actor = runtime.connection_principal(runtime.principal)
            if op.result["intent"]["actor"] != actor.model_dump(mode="json"):
                raise OperationRecoveryError("ORIGINAL_BOARD_OWNER_CHANGED")
            result = (await sync.sync_task(actor, op.task_run_id) if op.task_run_id
                      else await sync.sync_epic(actor, op.epic_run_id))
            return {k: result[k] for k in ("status", "operation_id")}
        raise OperationRecoveryError("ORIGINAL_OPERATION_HANDLER_REQUIRED")

    def _task_parent(self, op):
        rows = [o for o in self.store.get_operations(op.epic_run_id, kind="task_merge")
                if o.task_run_id == op.task_run_id and (
                    o.id == op.id if op.kind == "task_merge" else
                    o.id == op.idempotency_key if op.kind == "merge_task_to_epic" else
                    o.id == op.result.get("delivery_id"))]
        if len(rows) != 1:
            raise OperationRecoveryError("ORIGINAL_TASK_DELIVERY_REQUIRED")
        return rows[0]

    async def _task_delivery(self, op):
        control = self._control(op)
        await control.scheduler._fresh_owned(control.actor, self.store.get_task(op.task_run_id))
        parent = self._task_parent(op)
        tests = [o for o in self.store.get_operations(op.epic_run_id, kind="task_delivery_test")
                 if o.task_run_id == op.task_run_id and o.result.get("delivery_id") == parent.id]
        passed = [t for t in tests if t.status == "SUCCEEDED"]
        pending = [t for t in tests if t.status == "PENDING"]
        if passed:
            chosen = max(passed, key=lambda t: (t.created_at, t.id))
            prefix = parent.id + ":"
            if not chosen.idempotency_key.startswith(prefix):
                raise OperationRecoveryError("ORIGINAL_VERIFICATION_KEY_CHANGED")
            key = chosen.idempotency_key[len(prefix):]
        elif pending:
            # Stable key persisted in the recovery intent before an actual test.
            key = "recovery-" + max(pending, key=lambda t: (t.created_at, t.id)).id
        elif tests:
            raise OperationRecoveryError("FAILED_TEST_REQUIRES_OPERATOR_DECISION")
        else:
            key = "recovery-" + parent.id
        self._journal(op, stage="WAITING", reason="VERIFY_ORIGINAL_TASK_DELIVERY",
                      result=dict(delivery_id=parent.id, verification_key=key))
        result = control.scheduler.merge.merge(control.actor, op.task_run_id,
                                              key=parent.idempotency_key, verification_key=key)
        fresh = self.store.get_operation(self.project_id, "task_merge", parent.idempotency_key)
        if fresh.result.get("test_id"):
            test = self._find(fresh.result["test_id"])
            self._supersede_tests(pending, test, "task_delivery_test")
        if result["status"] in {"DONE", "EXISTING"}:
            sync = await control.scheduler.sync.sync_task(control.actor, op.task_run_id)
            result = result | dict(sync_status=sync["status"])
        return {k: result[k] for k in ("status", "merge_commit", "sync_status") if k in result}

    async def _epic_delivery(self, op):
        delivery = self.delivery
        if delivery is None:
            raise OperationRecoveryError("ORIGINAL_COORDINATOR_DELIVERY_REQUIRED")
        run_id = (op.result.get("epic_run_id") if isinstance(op, ProjectOperation)
                  else op.epic_run_id)
        rows = [o for o in delivery._rows() if o.result.get("epic_run_id") == run_id and (
            o.id == op.id if op.kind == delivery.KIND else
            o.result.get("merge_key") == op.idempotency_key if op.kind == "merge_epic_to_main"
            else op.result.get("merge_key") == o.result.get("merge_key"))]
        if len(rows) != 1:
            raise OperationRecoveryError("ORIGINAL_EPIC_DELIVERY_REQUIRED")
        parent = rows[0]
        tests = [o for o in self.store.get_operations(run_id, kind="verify_main_merge")
                 if o.result.get("merge_key") == parent.result["merge_key"]]
        pending = [t for t in tests if t.status == "PENDING"]
        key = "recovery-" + (max(pending, key=lambda t: (t.created_at, t.id)).id
                               if pending else parent.id)
        self._journal(op, stage="WAITING", reason="VERIFY_ORIGINAL_EPIC_DELIVERY",
                      result=dict(delivery_id=parent.id, verification_key=key))
        result = await delivery.deliver(delivery.runtime.principal, self.project_id, run_id,
                                        parent.idempotency_key, key)
        fresh = self.store.get_project_operation(self.project_id, delivery.KIND,
                                                 parent.idempotency_key)
        if fresh.result.get("final_verification_id"):
            self._supersede_tests(pending, self._find(fresh.result["final_verification_id"]),
                                  "verify_main_merge")
        return {k: result[k] for k in ("status", "merge_commit") if k in result}

    def _supersede_tests(self, pending, test, kind):
        if test.kind != kind or test.status != "SUCCEEDED" or test.result.get("exit_code") != 0:
            return
        with self.store.transaction():
            for old in pending:
                fresh = self._find(old.id)
                if fresh.status != "PENDING":
                    continue
                keys = ("merge_commit", "source_commit", "target_commit", "command_hash")
                keys += ("delivery_id", "timeout") if kind == "task_delivery_test" else (
                    "merge_key", "manifest_hash",
                )
                if old.id != test.id and all(old.result.get(k) == test.result.get(k) for k in keys):
                    self.store.update_operation(fresh.model_copy(update=dict(
                        status="SUPERSEDED", updated_at=utc_now(),
                        error_code="VERIFICATION_REPLACED_BY_ACTUAL_BOUND_RESULT",
                        result=fresh.result | dict(superseded_by=test.id),
                    )))

    async def _stopped_monitor(self, op):
        """Read-only physical settlement; never revive a completed/parked probe."""
        run = self.sessions._load(self._owner(op))
        if op.result.get("observation_hash") != fingerprint(
            {k: v for k, v in op.result.items() if k != "observation_hash"}
        ) or op.result.get("stage") != "ATTENTION":
            raise OperationRecoveryError("ORIGINAL_MONITOR_RECORD_CHANGED")
        if isinstance(run, CoordinatorRun):
            runtime = self.sessions.coordinator
            if runtime is None or run.id != runtime.run_id:
                raise OperationRecoveryError("ORIGINAL_COORDINATOR_REQUIRED")
            runtime.scope(runtime.principal)
            registration = self.store.get_project_operation(self.project_id, runtime.KIND, run.id)
            stops = [s for s in self.store.project_operations(run.id, kind="coordinator_stop")
                     if s.status == "SUCCEEDED" and s.result.get("inactive")
                     and s.result.get("start_operation_id") == registration.id]
            if run.configuration_hash != runtime.configuration_hash or run.status != "STOPPED" or (
                run.completed_at is None or len(stops) != 1 or not runtime._stopped(run, stops[0])
            ):
                raise OperationRecoveryError("ORIGINAL_INACTIVITY_UNVERIFIED")
            context = registration, runtime.principal, runtime.codex
        else:
            context = self.sessions._context(run)
            physical = self.sessions._observer(run)(run)
            if physical["status"] != "STOPPED" or isinstance(run, TaskRun) and (
                run.internal_status != "PARKED" or run.worker_slot is not None
            ):
                raise OperationRecoveryError("ORIGINAL_INACTIVITY_UNVERIFIED")
        registration, actor, codex = context
        anchor = dict(registration_id=registration.id,
                      registration_hash=registration.result.get("intent_hash")
                      or getattr(run, "configuration_hash", None),
                      principal=actor.model_dump(mode="json"))
        if any(op.result.get(k) != v for k, v in anchor.items()):
            raise OperationRecoveryError("ORIGINAL_MONITOR_OWNER_CHANGED")
        report = await self.sessions.audit.inspect()
        row = next((r for r in report["runs"] if r["run_id"] == run.id), None)
        if report["findings"] or row is None or any(
            f["code"] != "ORIGINAL_RUNTIME_STOPPED" for f in row["findings"]
        ) or not self.sessions.audit.current(report) or self.sessions._load(run.id) != run:
            raise OperationRecoveryError("ORIGINAL_STOPPED_RESOURCE_UNVERIFIED")
        cursor = self.sessions._cursor(run, codex)
        if isinstance(run, CoordinatorRun):
            if not runtime._stopped(run, stops[0]):
                raise OperationRecoveryError("ORIGINAL_INACTIVITY_UNVERIFIED")
        elif self.sessions._observer(run)(run)["status"] != "STOPPED":
            raise OperationRecoveryError("ORIGINAL_INACTIVITY_UNVERIFIED")
        stage = "WAITING_INPUT" if isinstance(run, TaskRun) else "OBSERVED_STOPPED"
        self.sessions._record(run, context, stage, cursor=cursor, runtime_status="stopped")
        return dict(run_id=run.id, stage=stage)
