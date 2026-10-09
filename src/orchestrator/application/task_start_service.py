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
from orchestrator.application.worker_slots import SlotError, WorkerSlots
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

    def __init__(
        self,
        settings,
        store,
        herdr,
        codex=None,
        *,
        prerequisite_probe=None,
        processes=None,
        claim_guard=None,
    ):
        self.settings = settings
        self.slots = WorkerSlots(settings, store, processes=processes)
        self.store, self.herdr = store, herdr
        self.worktrees = WorktreeService(self.settings, store)
        self.integration = GitIntegrationService(self.settings, store)
        self.runtime = RuntimeStartService(self.settings, store, herdr, processes=processes)
        self.assignment = RuntimeAssignmentService(self.settings, store, herdr, codex)
        # Trusted operator callback, never a client/Worker boolean or tool argument.
        self.prerequisite_probe = prerequisite_probe
        # Internal F29 composition. Never accepted in task_start/MCP arguments.
        self.claim_guard = claim_guard

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
            claim = None
            if self.claim_guard is not None:
                claim = self.claim_guard(actor, epic, spec, op)
                if (
                    not isinstance(claim, dict)
                    or claim.get("spec_hash") != digest(packet)
                    or claim.get("epic_commit") != self.worktrees.git.head(epic.branch)
                    or claim.get("main_commit") != self.worktrees.git.head("main")
                    or not isinstance(claim.get("scheduler_id"), str)
                    or len(claim["scheduler_id"]) != 64
                    or not set(spec.dependencies).issubset(claim.get("dependency_local_ids", []))
                ):
                    raise TaskStartError("TASK_START_CLAIM_EVIDENCE_UNVERIFIED")
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
                if (
                    claim is not None
                    and op.result.get("claim_evidence", {}).get("scheduler_id")
                    != claim["scheduler_id"]
                ):
                    raise TaskStartError("TASK_START_SCHEDULER_OWNER_MISMATCH")
                if task.internal_status == TaskState.CLAIMED and task.worker_slot is None:
                    task = self.slots.reserve_new(task)
                    op = op.model_copy(
                        update={
                            "result": op.result | {"reservation_released": False},
                            "updated_at": utc_now(),
                        }
                    )
                    self.store.update_operation(op)
                return task, op
            if any(
                isinstance(r, TaskRun)
                and r.project_id == actor.project_id
                and r.task_id == spec.task_id
                for r in self.store.get_runs()
            ):
                raise TaskStartError("TASK_ALREADY_OWNED")
            if claim is None:
                self._dependencies(spec, epic)
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
            task = self.slots.reserve_new(task)
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
                    **({"claim_evidence": claim} if claim is not None else {}),
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

    def claim(self, actor, epic_run_id, spec, *, timeout_seconds=45):
        """Internal durable claim only; no Git/workspace/runtime side effects."""
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 45:
            raise TaskStartError("TASK_START_INVALID_TIMEOUT")
        try:
            spec = LocalTaskSpec.model_validate(
                spec.model_dump() if isinstance(spec, LocalTaskSpec) else spec
            )
            with self.integration._lock():
                return self._prepare(actor, epic_run_id, spec, timeout_seconds)
        except TaskStartError:
            raise
        except SlotError as error:
            raise TaskStartError(str(error)) from None
        except (
            ValidationError,
            ContractError,
            WorktreeError,
            GitError,
            StoreError,
            StateError,
            IntegrationError,
        ):
            raise TaskStartError("TASK_CLAIM_UNVERIFIED") from None

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

    def prepare_git(self, actor, epic_run_id, spec, *, timeout_seconds=45):
        """Internal F29 stage: validated durable claim and Git, no native startup."""
        task, op = self.claim(actor, epic_run_id, spec, timeout_seconds=timeout_seconds)
        try:
            with self.integration._lock():
                # Revalidate under the same lock as the Git effect, not a prior boolean.
                task, op = self._prepare(
                    actor,
                    epic_run_id,
                    LocalTaskSpec.model_validate(
                        spec.model_dump() if isinstance(spec, LocalTaskSpec) else spec
                    ),
                    timeout_seconds,
                )
                if task.internal_status != TaskState.CLAIMED:
                    raise TaskStartError("TASK_GIT_PREPARATION_PHASE_CHANGED")
                self.slots.verify(task)
                self.worktrees.create_task_worktree(
                    actor, epic_run_id=epic_run_id, task_id=task.task_id, run_id=task.id
                )
                self._checkpoint(op, "GIT_READY")
                return self.store.get_task(task.id)
        except (
            TaskStartError,
            WorktreeError,
            GitError,
            StoreError,
            StateError,
            IntegrationError,
            SlotError,
        ):
            self._checkpoint(op, op.result["stage"], error="TASK_GIT_PREPARATION_UNVERIFIED")
            self.slots.release_unstarted(self.store.get_task(task.id), op.id)
            raise TaskStartError("TASK_GIT_PREPARATION_UNVERIFIED") from None

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
                self.slots.verify(task)
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
            SlotError,
        ) as e:
            if op is not None:
                self._checkpoint(
                    op,
                    op.result["stage"],
                    error=str(e)
                    if isinstance(
                        e, (RuntimeStartError, AssignmentError, TaskStartError, SlotError)
                    )
                    else type(e).__name__,
                )
                self.slots.release_unstarted(self.store.get_task(op.task_run_id), op.id)
            raise TaskStartError(
                str(e) if isinstance(e, (TaskStartError, SlotError)) else "TASK_START_UNVERIFIED"
            ) from None
