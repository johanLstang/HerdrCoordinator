"""Authenticated, complete current-context approval. No delivery merge or Done."""

from pydantic import ValidationError

from orchestrator.adapters.git import GitError
from orchestrator.application.git_integration_service import IntegrationError
from orchestrator.application.git_review_service import GitReviewError
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.task_review_service import TaskReviewError, TaskReviewService
from orchestrator.application.worktree_service import WorktreeError
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.review_contracts import ApprovalDecision
from orchestrator.domain.states import TaskState
from orchestrator.domain.worker_contracts import canonical_json
from orchestrator.persistence.store import StoreError


class TaskApprovalError(RuntimeError):
    """Safe rejection. Historical reviews are retained and never imply current approval."""


class TaskApprovalService:
    KIND = "task_approve"

    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.contexts = TaskReviewService(settings, store)
        self.integration = self.contexts.integration

    def _proof(self, actor, task, op, *, merging=False):
        phases = {TaskState.APPROVED, TaskState.MERGING} if merging else {TaskState.APPROVED}
        if op.status != "SUCCEEDED" or task.internal_status not in phases:
            raise TaskApprovalError("APPROVAL_NOT_CURRENT")
        reviewer = Actor.model_validate(op.result["reviewer"])
        StateService.authorize_scope(reviewer, task)
        if reviewer.role != Role.INTEGRATION:
            raise TaskApprovalError("APPROVAL_REVIEWER_UNVERIFIED")
        decision = ApprovalDecision.model_validate(op.result["decision"])
        context = self.contexts.current_context(actor, task.id, op.result["decision"]["context_id"])
        if decision.verified_criteria != context["acceptance_criteria"]:
            raise TaskApprovalError("APPROVAL_ACCEPTANCE_INCOMPLETE")
        reviews = self.store.get_reviews(task.id)
        if (
            not reviews
            or reviews[-1].id != op.result["review_id"]
            or reviews[-1].review_result != "APPROVED"
            or reviews[-1].review_commit != context["task_commit"]
            or reviews[-1].epic_commit != context["epic_commit"]
            or reviews[-1].feedback != canonical_json(op.result["decision"])
            or op.result["task_commit"] != context["task_commit"]
            or op.result["epic_commit"] != context["epic_commit"]
            or op.result["verification_id"] != context["tests"]["operation_id"]
            or task.approved_source_commit != context["task_commit"]
            or task.approved_target_commit != context["epic_commit"]
        ):
            raise TaskApprovalError("APPROVAL_PROOF_MISMATCH")
        return context

    def require_current(self, actor, run_id):
        """A checked read; the delivery operation must recheck its own exact Git pins."""
        try:
            with self.integration._lock(), self.store.transaction():
                task, _ = self.contexts._scope(actor, run_id, require_ready=False)
                matches = [
                    op
                    for op in self.store.get_operations(task.epic_run_id, kind=self.KIND)
                    if op.task_run_id == task.id and op.status == "SUCCEEDED"
                ]
                if not matches:
                    raise TaskApprovalError("APPROVAL_UNVERIFIED")
                op = max(matches, key=lambda item: item.created_at)
                context = self._proof(actor, task, op)
                return {
                    "operation_id": op.id,
                    "review_id": op.result["review_id"],
                    "context_id": context["context_id"],
                    "task_commit": context["task_commit"],
                    "epic_commit": context["epic_commit"],
                    "verification_id": context["tests"]["operation_id"],
                }
        except TaskApprovalError:
            raise
        except (
            TaskReviewError,
            IntegrationError,
            GitReviewError,
            GitError,
            WorktreeError,
            StateError,
            StoreError,
            ValidationError,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise TaskApprovalError("APPROVAL_UNVERIFIED") from None

    def approve(self, actor, run_id, decision, *, key):
        try:
            decision = ApprovalDecision.model_validate(decision).model_dump(mode="json")
            if (
                not isinstance(key, str)
                or not key.strip()
                or len(key) > 128
                or any(char in key for char in "\x00\r\n")
            ):
                raise TaskApprovalError("INVALID_APPROVAL_KEY")
            reviewer = actor.model_dump(mode="json")
            with self.integration._lock(), self.store.transaction():
                task, epic = self.contexts._scope(actor, run_id, require_ready=False)
                op = self.store.get_operation(task.project_id, self.KIND, key)
                if op:
                    if (
                        op.task_run_id != task.id
                        or op.epic_run_id != epic.id
                        or op.result["decision"] != decision
                        or op.result["reviewer"] != reviewer
                    ):
                        raise TaskApprovalError("APPROVAL_INTENT_MISMATCH")
                    if op.status == "SUCCEEDED":
                        self._proof(actor, task, op)
                        return {
                            "status": "EXISTING",
                            "operation_id": op.id,
                            "review_id": op.result["review_id"],
                        }
                else:
                    if task.internal_status != TaskState.REVIEWING:
                        raise TaskApprovalError("APPROVAL_TASK_NOT_REVIEWING")
                    if any(
                        other.task_run_id == task.id and other.status == "PENDING"
                        for kind in (self.KIND, "task_request_changes")
                        for other in self.store.get_operations(epic.id, kind=kind)
                    ):
                        raise TaskApprovalError("APPROVAL_DECISION_PENDING")
                    context = self.contexts.current_context(actor, task.id, decision["context_id"])
                    if decision["verified_criteria"] != context["acceptance_criteria"]:
                        raise TaskApprovalError("APPROVAL_ACCEPTANCE_INCOMPLETE")
                    op = Operation(
                        project_id=task.project_id,
                        epic_run_id=epic.id,
                        task_run_id=task.id,
                        kind=self.KIND,
                        idempotency_key=key,
                        result={
                            "stage": "INTENT",
                            "decision": decision,
                            "reviewer": reviewer,
                            "task_commit": context["task_commit"],
                            "epic_commit": context["epic_commit"],
                            "verification_id": context["tests"]["operation_id"],
                            "verification_key": context["tests"]["key"],
                            "context_id": context["context_id"],
                        },
                    )
                    self.store.add_operation(op)
            # F-07 owns the actual role/Git/test/state transaction and repository lock.
            # Prior exact review recovers process loss after APPROVED before checkpoint.
            prior = self.store.get_operation(task.project_id, "task_review", op.id)
            if prior is None:
                self.contexts.current_context(actor, task.id, decision["context_id"])
            review = self.integration.register_task_review(
                actor,
                task.id,
                verification_key=op.result["verification_key"],
                key=op.id,
                approved=True,
                feedback=canonical_json(decision),
            )
            return self._finish(actor, run_id, op, review)
        except TaskApprovalError:
            raise
        except (ValidationError, KeyError, TypeError, ValueError):
            raise TaskApprovalError("INVALID_APPROVAL_DECISION") from None
        except (
            TaskReviewError,
            IntegrationError,
            GitReviewError,
            GitError,
            WorktreeError,
            StateError,
            StoreError,
        ):
            raise TaskApprovalError("APPROVAL_UNVERIFIED") from None

    def _finish(self, actor, run_id, op, review):
        with self.integration._lock(), self.store.transaction():
            task, _ = self.contexts._scope(actor, run_id, require_ready=False)
            current = self.store.get_operation(task.project_id, self.KIND, op.idempotency_key)
            if current.id != op.id or current.status not in {"PENDING", "SUCCEEDED"}:
                raise TaskApprovalError("APPROVAL_OPERATION_CHANGED")
            saved = current.model_copy(
                update={
                    "status": "SUCCEEDED",
                    "updated_at": utc_now(),
                    "result": current.result
                    | {
                        "stage": "APPROVED",
                        "review_id": review.id,
                        "review_number": review.review_number,
                    },
                }
            )
            self._proof(actor, task, saved)
            if current.status != "SUCCEEDED":
                self.store.update_operation(saved)
            return {
                "status": "APPROVED",
                "operation_id": op.id,
                "review_id": review.id,
                "context_id": saved.result["context_id"],
                "task_commit": saved.result["task_commit"],
                "epic_commit": saved.result["epic_commit"],
            }
