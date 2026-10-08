"""Persist a negative review and dispatch correction once to its existing Worker."""

from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from orchestrator.adapters.codex import CodexError
from orchestrator.adapters.git import GitError
from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.git_integration_service import IntegrationError
from orchestrator.application.git_review_service import GitReviewError
from orchestrator.application.runtime_assignment_service import (
    AssignmentError,
    RuntimeAssignmentService,
    digest,
)
from orchestrator.application.runtime_start_service import RuntimeStartError
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.task_review_service import TaskReviewError, TaskReviewService
from orchestrator.application.worktree_service import WorktreeError
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Role, VerifiedFacts
from orchestrator.domain.review_contracts import ChangesDecision
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import canonical_json
from orchestrator.persistence.store import StoreError


class TaskChangesError(RuntimeError):
    """Safe rejection. Unknown delivery is retained and never blindly resent."""


class TaskChangesService:
    KIND = "task_request_changes"

    def __init__(self, settings, store, herdr, codex=None):
        self.settings, self.store = settings, store
        self.contexts = TaskReviewService(settings, store)
        self.integration = self.contexts.integration
        self.assignment = RuntimeAssignmentService(settings, store, herdr, codex)

    def _scope(self, actor, run_id):
        task = self.store.get_task(run_id)
        if actor.role != Role.INTEGRATION or task is None:
            raise TaskChangesError("CORRECTION_SCOPE_DENIED")
        StateService.authorize_scope(actor, task)
        epic = self.store.get_epic(task.epic_run_id)
        if epic is None or epic.status != EpicState.ACTIVE:
            raise TaskChangesError("CORRECTION_EPIC_NOT_ACTIVE")
        return task, epic

    def _context(self, actor, task, epic, identity):
        return self.contexts.current_context(actor, task.id, identity)

    def _runtime(self, actor, task, op=None):
        run, start = self.assignment._validate(actor, task.id)
        facts = self.assignment._runtime(run, start)
        if not task.codex_session_id or facts["session_id"] != task.codex_session_id:
            raise TaskChangesError("CORRECTION_SESSION_CHANGED")
        if op is not None and (
            op.result["session_id"] != task.codex_session_id
            or op.result["worker_agent_id"] != task.worker_agent_id
            or op.result["branch"] != task.branch
            or op.result["worktree_path"] != task.worktree_path
            or op.result["start_operation_id"] != start.id
        ):
            raise TaskChangesError("CORRECTION_RUNTIME_CHANGED")
        return facts, start

    def _thread(self, task):
        thread = self.assignment.codex.read_thread(task.codex_session_id, task.worktree_path)
        if (
            thread["id"] != task.codex_session_id
            or thread["sessionId"] != task.codex_session_id
            or Path(thread["cwd"]) != Path(task.worktree_path)
        ):
            raise TaskChangesError("CORRECTION_SESSION_CHANGED")
        return thread

    @staticmethod
    def _ack(task, op):
        return {
            "status": "WORKING",
            "project_id": task.project_id,
            "epic_run_id": task.epic_run_id,
            "task_run_id": task.id,
            "task_id": task.task_id,
            "correlation_id": op.result["correlation_id"],
            "review_id": op.id,
            "review_number": op.result["review_number"],
            "context_id": op.result["decision"]["context_id"],
        }

    def _prompt(self, task, op):
        return canonical_json(
            {
                "type": "HERDR_CORRECTION",
                "version": 1,
                "assignment": self._ack(task, op),
                "instruction": "First reply with exactly the assignment JSON as your WORKING "
                "acknowledgement. Continue the original task in this same session, branch and "
                "worktree. Fix the numbered issues within the original scope, run meaningful tests "
                "and commit. Then provide a new version-1 READY_FOR_REVIEW final JSON report with "
                "the original project/epic/task/run/branch and current full commit, test results, "
                "changed files and limitations. The tests array concerns the delivered commit; "
                "disclose earlier red TDD checks honestly in test_summary/summary, not as "
                "current-commit test results. Never hide a failure of the delivered commit. "
                "Report BLOCKED for genuine external input. "
                "Do not merge, switch branch, use other worktrees, release slots or update "
                "TeamPlayer.",
                "branch": task.branch,
                "worktree_path": task.worktree_path,
                "task_commit": op.result["task_commit"],
                "epic_commit": op.result["epic_commit"],
                "decision": op.result["decision"],
            }
        )

    def _checkpoint(self, op, *, stage=None, expected_stage=None, **updates):
        with self.store.transaction():
            current = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
            if current.id != op.id:
                raise TaskChangesError("CORRECTION_OPERATION_CHANGED")
            if current.status != "PENDING":
                return current
            if expected_stage is not None and current.result["stage"] != expected_stage:
                return current
            value = current.model_copy(
                update={
                    "updated_at": utc_now(),
                    "result": current.result | ({"stage": stage} if stage else {}) | updates,
                }
            )
            self.store.update_operation(value)
            return value

    def _register(self, actor, task, op):
        op = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
        if op.result["stage"] != "INTENT":
            return op
        task, epic = self._scope(actor, task.id)
        prior = self.store.get_operation(task.project_id, "task_review", op.id)
        if prior is None:
            context = self._context(actor, task, epic, op.result["decision"]["context_id"])
            facts, _ = self._runtime(actor, task, op)
            if not facts["ready"]:
                raise TaskChangesError("CORRECTION_RUNTIME_NOT_IDLE")
        else:
            context = op.result["context"]
        review = self.integration.register_task_review(
            actor,
            task.id,
            verification_key=context["tests"]["key"],
            key=op.id,
            approved=False,
            feedback=canonical_json(op.result["decision"]),
        )
        if review.review_number != op.result["review_number"]:
            raise TaskChangesError("CORRECTION_REVIEW_NUMBER_CHANGED")
        return self._checkpoint(
            op, stage="REGISTERED", expected_stage="INTENT", review_id=review.id
        )

    def request(self, actor, run_id, decision, *, key, timeout_seconds=45):
        try:
            parsed = ChangesDecision.model_validate(decision)
            decision = parsed.model_dump(mode="json")
            if (
                not isinstance(key, str)
                or not key.strip()
                or len(key) > 128
                or any(char in key for char in "\x00\r\n")
                or type(timeout_seconds) is not int
                or not 1 <= timeout_seconds <= 45
            ):
                raise TaskChangesError("INVALID_CORRECTION_REQUEST")
            with self.integration._lock(), self.store.transaction():
                task, epic = self._scope(actor, run_id)
                op = self.store.get_operation(task.project_id, self.KIND, key)
                if op:
                    if (
                        op.task_run_id != task.id
                        or op.epic_run_id != epic.id
                        or op.result["decision"] != decision
                        or op.result["timeout_seconds"] != timeout_seconds
                    ):
                        raise TaskChangesError("CORRECTION_INTENT_MISMATCH")
                else:
                    if task.internal_status != TaskState.REVIEWING:
                        raise TaskChangesError("CORRECTION_TASK_NOT_REVIEWING")
                    if any(
                        other.task_run_id == task.id and other.status == "PENDING"
                        for kind in (self.KIND, "task_approve")
                        for other in self.store.get_operations(epic.id, kind=kind)
                    ):
                        raise TaskChangesError("CORRECTION_ALREADY_PENDING")
                    context = self._context(actor, task, epic, parsed.context_id)
                    criteria = set(context["acceptance_criteria"])
                    if any(
                        not set(issue.acceptance_criteria) <= criteria for issue in parsed.issues
                    ):
                        raise TaskChangesError("CORRECTION_CRITERION_UNKNOWN")
                    facts, start = self._runtime(actor, task)
                    if not facts["ready"]:
                        raise TaskChangesError("CORRECTION_RUNTIME_NOT_IDLE")
                    op = Operation(
                        project_id=task.project_id,
                        epic_run_id=epic.id,
                        task_run_id=task.id,
                        kind=self.KIND,
                        idempotency_key=key,
                        result={
                            "stage": "INTENT",
                            "decision": decision,
                            "context": context,
                            "task_commit": context["task_commit"],
                            "epic_commit": context["epic_commit"],
                            "correlation_id": str(uuid4()),
                            "timeout_seconds": timeout_seconds,
                            "review_number": len(self.store.get_reviews(task.id)) + 1,
                            "session_id": task.codex_session_id,
                            "worker_agent_id": task.worker_agent_id,
                            "branch": task.branch,
                            "worktree_path": task.worktree_path,
                            "start_operation_id": start.id,
                        },
                    )
                    prompt = self._prompt(task, op)
                    if len(prompt.encode()) > 32768:
                        raise TaskChangesError("CORRECTION_PROMPT_TOO_LARGE")
                    op = op.model_copy(
                        update={
                            "result": op.result
                            | {
                                "prompt": prompt,
                                "prompt_hash": digest(prompt),
                            }
                        }
                    )
                    self.store.add_operation(op)
            op = self._register(actor, task, op)
            with self.integration._lock(), self.store.transaction():
                task, epic = self._scope(actor, run_id)
                current = self.store.get_operation(task.project_id, self.KIND, key)
                fresh = current.status == "PENDING" and current.result["stage"] == "REGISTERED"
                if fresh:
                    if task.internal_status != TaskState.CHANGES_REQUESTED:
                        raise TaskChangesError("CORRECTION_TASK_CHANGED")
                    self._context(actor, task, epic, parsed.context_id)
                    facts, _ = self._runtime(actor, task, current)
                    if not facts["ready"]:
                        raise TaskChangesError("CORRECTION_RUNTIME_NOT_IDLE")
                    thread = self._thread(task)
                    op = self._checkpoint(
                        current,
                        stage="DISPATCH_REQUESTED",
                        baseline_turns=[turn["id"] for turn in thread["turns"]],
                        deadline=(utc_now() + timedelta(seconds=timeout_seconds)).isoformat(),
                    )
                else:
                    op = current
            if fresh:
                # Durable dispatch intent precedes the only send. Retry observes it.
                task, _ = self._scope(actor, run_id)
                facts, _ = self._runtime(actor, task, op)
                if not facts["ready"] or task.internal_status != TaskState.CHANGES_REQUESTED:
                    raise TaskChangesError("CORRECTION_RUNTIME_NOT_IDLE")
                if utc_now() <= datetime.fromisoformat(op.result["deadline"]):
                    try:
                        self.assignment.herdr.prompt(
                            task.worker_agent_id,
                            op.result["prompt"],
                            timeout_ms=timeout_seconds * 1000,
                        )
                        transport = "RETURNED"
                    except HerdrError:
                        transport = "UNKNOWN"
                    self._checkpoint(op, transport=transport)
            return self.observe(actor, run_id, key=key)
        except TaskChangesError:
            raise
        except (ValidationError, KeyError, TypeError, ValueError):
            raise TaskChangesError("INVALID_CORRECTION_REQUEST") from None
        except (
            TaskReviewError,
            AssignmentError,
            RuntimeStartError,
            IntegrationError,
            GitReviewError,
            GitError,
            WorktreeError,
            StateError,
            StoreError,
            CodexError,
            HerdrError,
        ):
            raise TaskChangesError("CORRECTION_UNVERIFIED") from None

    def observe(self, actor, run_id, *, key):
        try:
            task, _ = self._scope(actor, run_id)
            op = self.store.get_operation(task.project_id, self.KIND, key)
            if op is None or op.task_run_id != task.id:
                raise TaskChangesError("CORRECTION_NOT_FOUND")
            if op.status == "SUCCEEDED":
                return {
                    "status": "EXISTING",
                    "operation_id": op.id,
                    "review_id": op.result["review_id"],
                    "ack": op.result["ack"],
                }
            if op.status == "TIMED_OUT":
                raise TaskChangesError("CORRECTION_ACK_TIMEOUT")
            if op.result["stage"] != "DISPATCH_REQUESTED":
                raise TaskChangesError("CORRECTION_NOT_DISPATCHED")
            if task.internal_status != TaskState.CHANGES_REQUESTED:
                raise TaskChangesError("CORRECTION_TASK_CHANGED")
            facts, _ = self._runtime(actor, task, op)
            proof = self.assignment.match_native_ack(
                op, self._thread(task), self._ack(task, op), runtime_status=facts["status"]
            )
            with self.store.transaction():
                task, _ = self._scope(actor, run_id)
                current = self.store.get_operation(task.project_id, self.KIND, key)
                if current.status == "SUCCEEDED":
                    return {
                        "status": "EXISTING",
                        "operation_id": current.id,
                        "review_id": current.result["review_id"],
                        "ack": current.result["ack"],
                    }
                if current.status == "TIMED_OUT" or utc_now() > datetime.fromisoformat(
                    current.result["deadline"]
                ):
                    self.store.update_operation(
                        current.model_copy(
                            update={
                                "status": "TIMED_OUT",
                                "error_code": "CORRECTION_ACK_TIMEOUT",
                                "updated_at": utc_now(),
                            }
                        )
                    )
                    timed_out = True
                elif proof:
                    StateService(self.store).transition_task(
                        task.id,
                        TaskState.WORKING,
                        expected=TaskState.CHANGES_REQUESTED,
                        event_id="correction-ack:" + op.id,
                        actor=actor,
                        facts=VerifiedFacts(
                            start_confirmed=True,
                            slot_reserved=True,
                            reason="Correlated same-session native correction acknowledgement",
                        ),
                    )
                    ack = proof | {
                        "session_id": task.codex_session_id,
                        "correlation_id": op.result["correlation_id"],
                    }
                    self.store.update_operation(
                        current.model_copy(
                            update={
                                "status": "SUCCEEDED",
                                "error_code": None,
                                "updated_at": utc_now(),
                                "result": current.result | {"stage": "CONFIRMED", "ack": ack},
                            }
                        )
                    )
                    return {
                        "status": "CONFIRMED",
                        "operation_id": op.id,
                        "review_id": current.result["review_id"],
                        "ack": ack,
                    }
                else:
                    timed_out = False
            if timed_out:
                raise TaskChangesError("CORRECTION_ACK_TIMEOUT")
            return {
                "status": "WAITING",
                "operation_id": op.id,
                "review_id": op.result["review_id"],
                "runtime_status": facts["status"],
            }
        except TaskChangesError:
            raise
        except (
            TaskReviewError,
            AssignmentError,
            RuntimeStartError,
            IntegrationError,
            GitReviewError,
            GitError,
            WorktreeError,
            StateError,
            StoreError,
            CodexError,
            HerdrError,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise TaskChangesError("CORRECTION_UNVERIFIED") from None
