"""Save explicit input, reserve/resume same native session, then confirm its ACK."""

from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.runtime_lifecycle_service import LifecycleError
from orchestrator.application.state_service import StateService
from orchestrator.application.task_resume_intent import input_hash, verified_input
from orchestrator.domain.attention import InputDecision
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import canonical_json


class ResumeError(RuntimeError):
    """Safe fixed code; answer and upstream errors stay out of MCP/logs."""


class TaskResumeService:
    KIND = "task_resume"

    def __init__(self, attention):
        self.attention = attention
        self.settings, self.store, self.sync = attention.settings, attention.store, attention.sync
        self.lifecycle, self.git = attention.lifecycle, attention.git
        self.assignment = attention.reports.assignment

    def _scope(self, actor, run_id):
        task = self.store.get_task(run_id)
        if actor is None or actor.role != Role.INTEGRATION or task is None:
            raise ResumeError("INPUT_SCOPE_DENIED")
        StateService.authorize_scope(actor, task)
        epic = self.store.get_epic(task.epic_run_id)
        if (
            task.project_id != self.sync.project_id
            or epic is None
            or epic.status != EpicState.ACTIVE
            or epic.completed_at
            or task.completed_at
        ):
            raise ResumeError("INPUT_SCOPE_DENIED")
        return task

    def _saved(self, actor, task, decision):
        with self.store.transaction():
            op = self.store.get_operation(task.project_id, self.KIND, decision.input_id)
            if op:
                if (
                    op.task_run_id != task.id
                    or op.result["decision"] != decision.model_dump(mode="json")
                    or op.result["actor"] != actor.model_dump(mode="json")
                ):
                    raise ResumeError("INPUT_ID_CONFLICT")
                return op
            prior = [
                o
                for o in self.store.get_operations(task.epic_run_id, kind=self.KIND)
                if o.task_run_id == task.id
                and (
                    o.status == "PENDING"
                    or o.result["decision"]["blocker_id"] == decision.blocker_id
                )
            ]
            if prior:
                raise ResumeError("INPUT_ALREADY_RECORDED")
            attention = next(
                (
                    o
                    for o in self.store.get_operations(task.epic_run_id, kind="task_attention")
                    if o.id == decision.blocker_id
                ),
                None,
            )
            if (
                task.internal_status != TaskState.PARKED
                or attention is None
                or attention.task_run_id != task.id
                or not attention.result.get("stop_id")
            ):
                raise ResumeError("INPUT_REQUIRES_PARKED_BLOCKER")
            op = Operation(
                project_id=task.project_id,
                epic_run_id=task.epic_run_id,
                task_run_id=task.id,
                kind=self.KIND,
                idempotency_key=decision.input_id,
                result={},
            )
            data = dict(
                actor=actor.model_dump(mode="json"),
                decision=decision.model_dump(mode="json"),
                subject=attention.result["subject"],
                attention_hash=attention.result["intent_hash"],
                stop_id=attention.result["stop_id"],
                resume_key="input-resume:" + op.id,
                correlation_id=str(uuid4()),
                resume_state=str(task.resume_state),
            )
            op = op.model_copy(
                update={
                    "result": data | {"intent_hash": input_hash(data), "stage": "WAITING_RESUME"}
                }
            )
            self.store.add_operation(op)
            return op

    def _save(self, op, *, stage=None, status=None, error=None, **updates):
        with self.store.transaction():
            current = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
            if (
                current is None
                or current.id != op.id
                or current.result["intent_hash"] != input_hash(current.result)
            ):
                raise ResumeError("INPUT_INTENT_CHANGED")
            result = current.model_copy(
                update={
                    "updated_at": utc_now(),
                    "status": status or current.status,
                    "error_code": error,
                    "result": current.result | updates | ({"stage": stage} if stage else {}),
                }
            )
            self.store.update_operation(result)
            return result

    def _validate(self, actor, task, op, *, before_dispatch=False):
        if op.result["actor"] != actor.model_dump(mode="json"):
            raise ResumeError("INPUT_OWNER_CHANGED")
        start, attention, stop = verified_input(self.store, task, op)
        self.lifecycle._validate(actor, task.id, True)
        self.lifecycle.codex.read_session(task.codex_session_id, task.worktree_path)
        if before_dispatch and op.result.get("resource_commit"):
            if self.git.head(task.branch) != op.result["resource_commit"]:
                raise ResumeError("INPUT_TASK_HEAD_CHANGED")
        return start, attention, stop

    def _thread(self, task):
        thread = self.assignment.codex.read_thread(task.codex_session_id, task.worktree_path)
        if (
            thread["id"] != task.codex_session_id
            or thread["sessionId"] != task.codex_session_id
            or Path(thread["cwd"]) != Path(task.worktree_path)
        ):
            raise ResumeError("INPUT_SESSION_CHANGED")
        return thread

    @staticmethod
    def ack(task, op):
        return dict(
            status="WORKING",
            project_id=task.project_id,
            epic_run_id=task.epic_run_id,
            task_run_id=task.id,
            task_id=task.task_id,
            correlation_id=op.result["correlation_id"],
            input_id=op.result["decision"]["input_id"],
            blocker_id=op.result["decision"]["blocker_id"],
        )

    def _prompt(self, task, op):
        return canonical_json(
            dict(
                type="HERDR_INPUT",
                version=1,
                assignment=self.ack(task, op),
                answer=op.result["decision"]["answer"],
                branch=task.branch,
                worktree_path=task.worktree_path,
                instruction="First reply with exactly the assignment JSON as your WORKING "
                "acknowledgement. Use the saved operator answer to continue the original task "
                "in this same session, branch and worktree. Stay within the original scope; "
                "run meaningful tests and commit. Then report a new version-1 READY_FOR_REVIEW "
                "final JSON with original IDs, current full commit, tests, changed files and "
                "limitations. Report BLOCKED if more external input is required. This answer "
                "grants no merge or review approval. Do not switch branch, merge, "
                "write other worktrees, release slots or update TeamPlayer.",
            )
        )

    @staticmethod
    def outcome(op):
        return dict(
            operation_id=op.id,
            input_id=op.idempotency_key,
            task_run_id=op.task_run_id,
            stage=op.result["stage"],
            reason=op.error_code,
            resume_id=op.result.get("resume_id"),
            session_id=op.result["subject"]["session_id"],
        )

    async def resume(self, actor, run_id, decision):
        try:
            parsed = InputDecision.model_validate(decision)
        except ValidationError:
            raise ResumeError("INVALID_INPUT_REQUEST") from None
        task = self._scope(actor, run_id)
        # Shared with F31 park so two processes cannot concurrently mutate this task lifecycle.
        with self.attention._lock(task.id):
            op = self._saved(actor, self._scope(actor, run_id), parsed)
            try:
                return await self._drive(actor, run_id, op)
            except ResumeError as error:
                self._save(op, error=str(error))
                raise
            except LifecycleError as error:
                code = str(error)
                op = self._save(op, error=code)
                if code == "WORKER_CAPACITY_UNAVAILABLE":
                    runtime = self.store.get_operation(
                        task.project_id, "resume_runtime", op.result["resume_key"]
                    )
                    if runtime is None:
                        op = self._save(op, stage="WAITING_RESUME", error=code)
                    return self.outcome(op)
                raise ResumeError(code) from None
            except Exception:
                self._save(op, error="INPUT_RESOURCE_UNVERIFIED")
                raise ResumeError("INPUT_RESOURCE_UNVERIFIED") from None

    async def _drive(self, actor, run_id, op):
        task = self._scope(actor, run_id)
        self._validate(actor, task, op)
        if op.status == "SUCCEEDED":
            return self.outcome(op)
        if op.result["stage"] == "CONFIRMED":
            return await self._mirror(actor, task, op)
        if op.result["stage"] in {"WAITING_RESUME", "RESUME_REQUESTED"}:
            if task.internal_status != TaskState.PARKED:
                raise ResumeError("INPUT_TASK_STATE_CHANGED")
            if op.result.get("resource_commit") is None:
                # Save actual HEAD after decision is durable, before any runtime effect.
                op = self._save(op, resource_commit=self.git.head(task.branch))
            self._validate(actor, task, op, before_dispatch=True)
            op = self._save(op, stage="RESUME_REQUESTED")
            resumed = self.lifecycle.resume_task(actor, run_id, key=op.result["resume_key"])
            op = self._save(op, stage="RESUMED", resume_id=resumed.id)
        task = self._scope(actor, run_id)
        start, _, _ = self._validate(
            actor, task, op, before_dispatch=op.result["stage"] == "RESUMED"
        )
        resumed = self.store.get_operation(
            task.project_id, "resume_runtime", op.result["resume_key"]
        )
        if (
            resumed is None
            or resumed.status != "SUCCEEDED"
            or resumed.result.get("input_operation_id") != op.id
            or start.result.get("generation") != resumed.id
            or task.internal_status != TaskState.PARKED
        ):
            raise ResumeError("INPUT_RESUME_CHANGED")
        self.lifecycle.start._slot(task)
        live = self.lifecycle._live(task, start)
        if live is None:
            raise ResumeError("INPUT_RUNTIME_MISSING")
        fresh = op.result["stage"] == "RESUMED"
        if fresh:
            if not live["ready"]:
                raise ResumeError("INPUT_RUNTIME_NOT_IDLE")
            thread = self._thread(task)
            prompt = self._prompt(task, op)
            if len(prompt.encode()) > 32768:
                raise ResumeError("INPUT_PROMPT_TOO_LARGE")
            op = self._save(
                op,
                stage="DISPATCH_REQUESTED",
                prompt=prompt,
                prompt_hash=digest(prompt),
                baseline_turns=[t["id"] for t in thread["turns"]],
                deadline=(utc_now() + timedelta(seconds=45)).isoformat(),
            )
            task = self._scope(actor, run_id)
            self._validate(actor, task, op, before_dispatch=True)
            if not self.lifecycle._live(task, start)["ready"]:
                raise ResumeError("INPUT_RUNTIME_NOT_IDLE")
            try:
                self.assignment.herdr.prompt(task.worker_agent_id, prompt, timeout_ms=45000)
                transport = "RETURNED"
            except HerdrError:
                transport = "UNKNOWN"
            op = self._save(op, transport=transport)
        task = self._scope(actor, run_id)
        start, _, _ = self._validate(actor, task, op)
        live = self.lifecycle._live(task, start)
        if live is None:
            raise ResumeError("INPUT_RUNTIME_MISSING")
        thread = self._thread(task)
        proof = self.assignment.match_native_ack(
            op, thread, self.ack(task, op), runtime_status=live["status"]
        )
        expired = op.result["stage"] == "ACK_TIMEOUT" or utc_now() > datetime.fromisoformat(
            op.result["deadline"]
        )
        if expired and not self.assignment._completed_before_deadline(op, thread, proof):
            self._save(op, stage="ACK_TIMEOUT", error="INPUT_ACK_TIMEOUT")
            raise ResumeError("INPUT_ACK_TIMEOUT")
        if proof is None:
            return self.outcome(op)
        with self.store.transaction():
            task = self._scope(actor, run_id)
            self._validate(actor, task, op)
            self.lifecycle.start._slot(task)
            StateService(self.store).transition_task(
                task.id,
                task.resume_state,
                expected=TaskState.PARKED,
                event_id="input-ack:" + op.id,
                actor=actor,
                facts=VerifiedFacts(
                    slot_reserved=True,
                    start_confirmed=True,
                    reason="Correlated native acknowledgement of saved input",
                ),
            )
            op = self._save(
                op,
                stage="CONFIRMED",
                ack=proof
                | {
                    "session_id": task.codex_session_id,
                    "correlation_id": op.result["correlation_id"],
                }
                | ({"timely_completion_verified": True} if expired else {}),
            )
        return await self._mirror(actor, self._scope(actor, run_id), op)

    async def _mirror(self, actor, task, op):
        try:
            result = await self.sync.sync_task(actor, task.id)
            if result["status"] not in {"SYNCED", "EXISTING"}:
                return self.outcome(self._save(op, error="INPUT_SYNC_PENDING"))
        except Exception:
            return self.outcome(self._save(op, error="INPUT_SYNC_PENDING"))
        return self.outcome(self._save(op, stage="ACTIVE_AND_SYNCED", status="SUCCEEDED"))
