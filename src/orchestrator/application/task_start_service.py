"""Explicit phase-4 task start: one durable owner and slot before external resources."""

from uuid import uuid4

from pydantic import ValidationError

from orchestrator.adapters.git import GitError
from orchestrator.application.git_integration_service import GitIntegrationService, IntegrationError
from orchestrator.application.runtime_assignment_service import (
    AssignmentError,
    RuntimeAssignmentService,
    digest,
)
from orchestrator.application.runtime_start_service import RuntimeStartError, RuntimeStartService
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.worker_prompt import build_assignment, render_worker_prompt
from orchestrator.application.worktree_service import WorktreeError, WorktreeService
from orchestrator.domain.models import Operation, TaskRun, utc_now
from orchestrator.domain.policy import Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import ContractError, LocalTaskSpec, canonical_json
from orchestrator.persistence.store import StoreError


class TaskStartError(RuntimeError):
    """Safe failure; persisted claims and known resources remain available for recovery."""


class TaskStartService:
    KIND = "task_start"

    def __init__(self, settings, store, herdr, codex=None, *, prerequisite_probe=None):
        # Phase 4 permits one claim. Existing claims in either slot still block new work.
        self.settings = settings.model_copy(update={"max_workers": 1})
        self.store, self.herdr = store, herdr
        self.worktrees = WorktreeService(self.settings, store)
        self.integration = GitIntegrationService(self.settings, store)
        self.runtime = RuntimeStartService(self.settings, store, herdr)
        self.assignment = RuntimeAssignmentService(self.settings, store, herdr, codex)
        # Trusted operator callback, never a client/Worker boolean or tool argument.
        self.prerequisite_probe = prerequisite_probe

    def _epic(self, actor, epic_run_id):
        epic = self.store.get_epic(epic_run_id)
        if (
            actor.role != Role.INTEGRATION
            or actor.epic_run_id != epic_run_id
            or epic is None
            or actor.project_id != epic.project_id
        ):
            raise TaskStartError("TASK_START_SCOPE_DENIED")
        if epic.status != EpicState.ACTIVE or epic.completed_at is not None:
            raise TaskStartError("EPIC_NOT_ACTIVE")
        self.worktrees.verify_owned_worktree(epic)
        return epic

    def _dependencies(self, spec, epic):
        for identity in spec.dependencies:
            matches = [t for t in self.store.get_tasks(epic.id) if t.task_id == identity]
            if len(matches) != 1:
                raise TaskStartError("TASK_DEPENDENCY_UNVERIFIED")
            self.integration._delivery_proof(matches[0], epic)
        if spec.external_prerequisites:
            try:
                ready = (
                    self.prerequisite_probe is not None and self.prerequisite_probe(spec) is True
                )
            except Exception:
                ready = False
            if not ready:
                raise TaskStartError("TASK_PREREQUISITE_UNVERIFIED")

    def _prepare(self, actor, epic_run_id, spec, timeout_seconds):
        with self.store.transaction():
            epic = self._epic(actor, epic_run_id)
            spec.bind(epic)
            packet = canonical_json(spec.model_dump(mode="json"))
            if len(packet.encode()) > 65536:
                raise TaskStartError("TASK_SPEC_TOO_LARGE")
            op = self.store.get_operation(actor.project_id, self.KIND, spec.task_id)
            if op:
                task = self.store.get_task(op.task_run_id)
                if (
                    task is None
                    or op.epic_run_id != epic.id
                    or task.epic_run_id != epic.id
                    or task.project_id != actor.project_id
                    or task.task_id != spec.task_id
                    or op.result.get("spec_hash") != digest(packet)
                    or op.result.get("timeout_seconds") != timeout_seconds
                    or op.result.get("server_session") != self.herdr.server_session
                    or op.result.get("sandbox") != self.herdr.sandbox
                    or op.result.get("branch") != task.branch
                    or op.result.get("cwd") != task.worktree_path
                    or op.result.get("base_commit") != task.base_commit
                    or digest(op.result.get("instruction", "")) != op.result.get("instruction_hash")
                ):
                    raise TaskStartError("TASK_START_INTENT_MISMATCH")
                return task, op
            if any(
                isinstance(r, TaskRun)
                and r.project_id == actor.project_id
                and r.task_id == spec.task_id
                for r in self.store.get_runs()
            ):
                raise TaskStartError("TASK_ALREADY_OWNED")
            self._dependencies(spec, epic)
            if any(
                isinstance(r, TaskRun)
                and r.project_id == actor.project_id
                and r.worker_slot is not None
                for r in self.store.get_runs()
            ):
                raise TaskStartError("WORKER_CAPACITY_UNAVAILABLE")
            task = self.worktrees.create_task_worktree(
                actor,
                epic_run_id=epic.id,
                task_id=spec.task_id,
                run_id=str(uuid4()),
                prepare_only=True,
            )
            instruction = render_worker_prompt(build_assignment(spec, task, epic))
            if len(instruction.encode()) > 32768:
                raise TaskStartError("WORKER_PROMPT_TOO_LARGE")
            task = task.model_copy(update={"worker_slot": 1})
            self.store.update_runtime_metadata(task)
            op = Operation(
                project_id=actor.project_id,
                epic_run_id=epic.id,
                task_run_id=task.id,
                kind=self.KIND,
                idempotency_key=spec.task_id,
                result={
                    "stage": "CLAIMED",
                    "spec": spec.model_dump(mode="json"),
                    "spec_hash": digest(packet),
                    "instruction": instruction,
                    "instruction_hash": digest(instruction),
                    "policy_version": 1,
                    "report_version": 1,
                    "timeout_seconds": timeout_seconds,
                    "server_session": self.herdr.server_session,
                    "sandbox": self.herdr.sandbox,
                    "branch": task.branch,
                    "cwd": task.worktree_path,
                    "base_commit": task.base_commit,
                },
            )
            self.store.add_operation(op)
            task = StateService(self.store).transition_task(
                task.id,
                TaskState.CLAIMED,
                expected=TaskState.PLANNED,
                event_id="task-claim:" + op.id,
                actor=actor,
                facts=VerifiedFacts(dependencies_ready=True, slot_reserved=True),
            )
            return task, op

    def _checkpoint(self, op, stage, *, error=None):
        with self.store.transaction():
            current = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
            # Never move a completed operation backward when another caller observed its ACK.
            if current.id != op.id:
                raise TaskStartError("TASK_START_CHANGED")
            if current.status == "SUCCEEDED":
                return current
            ranks = {
                "CLAIMED": 0,
                "GIT_READY": 1,
                "RUNTIME_READY": 2,
                "AWAITING_ACK": 3,
                "WORKING": 4,
            }
            if ranks[current.result["stage"]] > ranks[stage]:
                stage = current.result["stage"]
            current = current.model_copy(
                update={
                    "status": "SUCCEEDED" if stage == "WORKING" else "PENDING",
                    "result": current.result | {"stage": stage},
                    "error_code": error,
                    "updated_at": utc_now(),
                }
            )
            self.store.update_operation(current)
            return current

    def start(self, actor, epic_run_id, spec, *, timeout_seconds=45):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 45:
            raise TaskStartError("TASK_START_INVALID_TIMEOUT")
        op = None
        try:
            spec = LocalTaskSpec.model_validate(
                spec.model_dump() if isinstance(spec, LocalTaskSpec) else spec
            )
            # Share the integration lock while recording the base and creating Git resources.
            with self.integration._lock():
                task, op = self._prepare(actor, epic_run_id, spec, timeout_seconds)
                if op.status == "SUCCEEDED":
                    return {"status": "EXISTING", "task": task.model_dump(mode="json")}
                if task.internal_status not in {
                    TaskState.CLAIMED,
                    TaskState.STARTING,
                    TaskState.WORKING,
                }:
                    raise TaskStartError("TASK_START_PHASE_CHANGED")
                if task.worker_slot != 1:
                    raise TaskStartError("WORKER_SLOT_UNVERIFIED")
                if task.internal_status == TaskState.CLAIMED:
                    self.worktrees.create_task_worktree(
                        actor,
                        epic_run_id=epic_run_id,
                        task_id=spec.task_id,
                        run_id=task.id,
                    )
                    op = self._checkpoint(op, "GIT_READY")
            # Subordinate services own external intents; retries never create replacements.
            task = self.store.get_task(task.id)
            native_start = self.store.get_operation(task.project_id, self.runtime.KIND, task.id)
            if native_start is None or native_start.status != "SUCCEEDED":
                self.runtime.start_task(actor, task.id)
                op = self._checkpoint(op, "RUNTIME_READY")
            if digest(op.result["instruction"]) != op.result["instruction_hash"]:
                raise TaskStartError("WORKER_PROMPT_CHANGED")
            result = self.assignment.dispatch(
                actor,
                task.id,
                op.result["instruction"],
                timeout_seconds=timeout_seconds,
            )
            self._checkpoint(op, "WORKING" if result["status"] == "CONFIRMED" else "AWAITING_ACK")
            return result
        except (ValidationError, ContractError):
            raise TaskStartError("TASK_SPEC_INVALID") from None
        except (
            WorktreeError,
            GitError,
            StoreError,
            StateError,
            IntegrationError,
            RuntimeStartError,
            AssignmentError,
            TaskStartError,
        ) as e:
            if op is not None:
                self._checkpoint(
                    op,
                    op.result["stage"],
                    error=str(e)
                    if isinstance(e, (RuntimeStartError, AssignmentError, TaskStartError))
                    else type(e).__name__,
                )
            raise TaskStartError(
                str(e) if isinstance(e, TaskStartError) else "TASK_START_UNVERIFIED"
            ) from None
