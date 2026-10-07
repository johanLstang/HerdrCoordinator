"""Minimal guarded Git integration. This service does not set tasks or epics Done."""

import fcntl
import hashlib
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from orchestrator.adapters.git import GitError
from orchestrator.application.git_review_service import GitReviewService
from orchestrator.application.state_service import StateService
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.models import Operation, Review, utc_now
from orchestrator.domain.policy import Actor, Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.persistence.store import StateStore


class IntegrationError(RuntimeError):
    """Safe policy/recovery rejection. Existing work and raw command output are preserved."""


class GitIntegrationService:
    def __init__(
        self,
        settings: Settings,
        store: StateStore,
        *,
        test_command: tuple[str, ...] = (),
        test_timeout: float = 300,
    ):
        self.store = store
        self.worktrees = WorktreeService(settings, store)
        self.reviews = GitReviewService(settings, store)
        self.git = self.worktrees.git
        # Operator configuration, never an agent-supplied tool argument.
        self.test_command, self.test_timeout = test_command, test_timeout
        self.command_hash = hashlib.sha256(json.dumps(test_command).encode()).hexdigest()

    @contextmanager
    def _lock(self):
        try:
            descriptor = os.open(
                self.git.common_dir / "herdr-integration.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
        except OSError:
            raise IntegrationError("repository integration lock unavailable") from None
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise IntegrationError("repository integration is busy") from None
                    time.sleep(0.05)
            yield
        finally:
            os.close(descriptor)

    def _task(self, actor: Actor, task_run_id: str):
        task = self.store.get_task(task_run_id)
        if (
            actor.role != Role.INTEGRATION
            or task is None
            or actor.project_id != task.project_id
            or actor.epic_run_id != task.epic_run_id
        ):
            raise IntegrationError("only the task's registered Integration may integrate")
        epic = self.store.get_epic(task.epic_run_id)
        if epic is None or epic.status not in {
            EpicState.PLANNED,
            EpicState.ACTIVE,
            EpicState.CHANGES_REQUESTED,
        }:
            raise IntegrationError("epic is not accepting task integration")
        return task, epic

    @staticmethod
    def _key(key: str):
        if not isinstance(key, str) or not key or len(key) > 200:
            raise IntegrationError("operation key must be a bounded stable identifier")

    def _owned_pair(self, task, epic):
        source = self.worktrees.verify_owned_worktree(task)
        target = self.worktrees.verify_owned_worktree(epic)
        for record in (task, epic):
            path = Path(record.worktree_path)
            if (
                self.git.working_changes(path)
                or self.git.unsafe_index_paths(path)
                or self.git.in_progress(path)
            ):
                raise IntegrationError("integration requires clean, stable owned worktrees")
        return source, target

    def _patch(self, record, **changes):
        updated = type(record).model_validate(record.model_dump() | changes)
        self.store.update_run_metadata(updated)
        return updated

    def _finish(self, operation, status, *, error=None, **result):
        updated = Operation.model_validate(
            operation.model_dump()
            | {
                "status": status,
                "error_code": error,
                "updated_at": utc_now(),
                "result": operation.result | result,
            }
        )
        self.store.update_operation(updated)
        return updated

    def _prior(self, task, kind, key):
        self._key(key)
        op = self.store.get_operation(task.project_id, kind, key)
        if op is not None and (op.task_run_id != task.id or op.epic_run_id != task.epic_run_id):
            raise IntegrationError("operation key belongs to another task")
        return op

    def verify_task(self, actor: Actor, task_run_id: str, *, key: str) -> Operation:
        if not self.test_command:
            raise IntegrationError("operator test command is not configured")
        with self._lock():
            task, epic = self._task(actor, task_run_id)
            evidence = self.reviews.task_review(actor, task.id)
            if not evidence.reviewable:
                raise IntegrationError("test evidence requires a complete current task review")
            source, target = evidence.source.current_commit, evidence.target_commit
            old = self._prior(task, "verify_task", key)
            request = {
                "source_commit": source,
                "target_commit": target,
                "command_hash": self.command_hash,
            }
            if old:
                if any(old.result.get(k) != v for k, v in request.items()):
                    raise IntegrationError("verification key refers to stale evidence")
                if old.status == "PENDING":
                    raise IntegrationError("test outcome unknown; request a new verification")
                return old
            with self.store.transaction():
                self._patch(task, current_commit=source)
                self._patch(epic, current_commit=target)
                op = Operation(
                    project_id=task.project_id,
                    epic_run_id=epic.id,
                    task_run_id=task.id,
                    kind="verify_task",
                    idempotency_key=key,
                    result=request,
                )
                self.store.add_operation(op)
            error, exit_code = None, None
            try:
                exit_code = subprocess.run(
                    self.test_command,
                    cwd=task.worktree_path,
                    env=self.git._environment(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=self.test_timeout,
                    check=False,
                ).returncode
                if exit_code != 0:
                    error = "TEST_FAILED"
                fresh = self.reviews.task_review(actor, task.id, expected_commit=source)
                if not fresh.reviewable or fresh.target_commit != target:
                    error = "STALE_TEST_EVIDENCE"
            except (OSError, subprocess.TimeoutExpired):
                error = "TEST_PROCESS_FAILED"
            except (GitError, RuntimeError):
                error = "STALE_TEST_EVIDENCE"
            return self._finish(
                op,
                "FAILED" if error else "SUCCEEDED",
                error=error,
                exit_code=exit_code,
            )

    def register_task_review(
        self,
        actor: Actor,
        task_run_id: str,
        *,
        verification_key: str,
        key: str,
        approved: bool,
        feedback: str = "",
    ) -> Review:
        """Register a trusted manual review; does not automate the reviewer's decision."""
        if type(approved) is not bool:
            raise IntegrationError("review decision must be an explicit boolean")
        with self._lock(), self.store.transaction():
            task, epic = self._task(actor, task_run_id)
            prior = self._prior(task, "task_review", key)
            request = {
                "verification_key": verification_key,
                "approved": approved,
                "feedback_hash": hashlib.sha256(feedback.encode()).hexdigest(),
            }
            if prior:
                if any(prior.result.get(k) != v for k, v in request.items()):
                    raise IntegrationError("review key belongs to another decision")
                matches = [r for r in self.store.get_reviews(task.id) if r.id == key]
                if prior.status != "SUCCEEDED" or len(matches) != 1:
                    raise IntegrationError("registered review requires reconciliation")
                return matches[0]
            evidence = self.reviews.task_review(actor, task.id)
            verification = self._prior(task, "verify_task", verification_key)
            source, target = evidence.source.current_commit, evidence.target_commit
            if (
                not evidence.reviewable
                or verification is None
                or verification.status != "SUCCEEDED"
                or verification.result.get("exit_code") != 0
                or verification.result.get("source_commit") != source
                or verification.result.get("target_commit") != target
                or verification.result.get("command_hash") != self.command_hash
            ):
                raise IntegrationError("review lacks actual current test evidence")
            if task.internal_status == TaskState.READY_FOR_REVIEW:
                task = StateService(self.store).transition_task(
                    task.id,
                    TaskState.REVIEWING,
                    expected=TaskState.READY_FOR_REVIEW,
                    event_id=f"review-start:{key}",
                    actor=actor,
                )
            if task.internal_status != TaskState.REVIEWING:
                raise IntegrationError("task is not in its review phase")
            task = self._patch(task, current_commit=source)
            self._patch(epic, current_commit=target)
            review = Review(
                id=key,
                task_run_id=task.id,
                review_number=len(self.store.get_reviews(task.id)) + 1,
                review_result="APPROVED" if approved else "CHANGES_REQUESTED",
                review_commit=source,
                epic_commit=target,
                feedback=feedback,
            )
            self.store.add_review(review)
            StateService(self.store).transition_task(
                task.id,
                TaskState.APPROVED if approved else TaskState.CHANGES_REQUESTED,
                expected=TaskState.REVIEWING,
                event_id=f"review-decision:{key}",
                actor=actor,
                facts=VerifiedFacts(reason=feedback if not approved else ""),
            )
            op = Operation(
                project_id=task.project_id,
                epic_run_id=epic.id,
                task_run_id=task.id,
                kind="task_review",
                idempotency_key=key,
                status="SUCCEEDED",
                result=request | {"source_commit": source, "target_commit": target},
            )
            self.store.add_operation(op)
            return review

    def _approval(self, task, source, target):
        reviews = self.store.get_reviews(task.id)
        if not reviews:
            raise IntegrationError("registered task approval is missing")
        review = reviews[-1]
        op = self._prior(task, "task_review", review.id)
        verification = (
            self._prior(task, "verify_task", op.result.get("verification_key")) if op else None
        )
        if (
            task.internal_status not in {TaskState.APPROVED, TaskState.MERGING}
            or (task.approved_source_commit, task.approved_target_commit) != (source, target)
            or review.review_result != "APPROVED"
            or (review.review_commit, review.epic_commit) != (source, target)
            or op is None
            or op.status != "SUCCEEDED"
            or verification is None
            or verification.status != "SUCCEEDED"
            or verification.result.get("exit_code") != 0
            or verification.result.get("command_hash") != self.command_hash
            or (verification.result.get("source_commit"), verification.result.get("target_commit"))
            != (source, target)
        ):
            raise IntegrationError("approval or tests do not match actual current commits")

    def sync_task_with_epic(self, actor: Actor, task_run_id: str, *, key: str) -> Operation:
        return self._merge(actor, task_run_id, key, sync=True)

    def merge_task_to_epic(self, actor: Actor, task_run_id: str, *, key: str) -> Operation:
        return self._merge(actor, task_run_id, key, sync=False)

    def _invalidate(self, actor, task):
        if task.internal_status == TaskState.APPROVED:
            task = StateService(self.store).transition_task(
                task.id,
                TaskState.CHANGES_REQUESTED,
                expected=TaskState.APPROVED,
                event_id=f"sync-invalidate:{utc_now().isoformat()}:{task.id}",
                actor=actor,
                facts=VerifiedFacts(reason="epic synchronization invalidates the old review"),
            )
        return self._patch(task, approved_source_commit=None, approved_target_commit=None)

    def _complete_merge(self, task, epic, op, merged, *, sync):
        with self.store.transaction():
            task = self.store.get_task(task.id)
            if sync:
                current = self.worktrees.verify_owned_worktree(task)
                self._patch(task, current_commit=current)
            else:
                self._patch(task, merge_commit=merged)
                current = self.worktrees.verify_owned_worktree(self.store.get_epic(epic.id))
                self._patch(self.store.get_epic(epic.id), current_commit=current)
            source_changed = (
                not sync
                and self.worktrees.verify_owned_worktree(task) != op.result["source_commit"]
            )
            return self._finish(
                op,
                "SUCCEEDED",
                merge_commit=merged,
                current_destination_commit=current,
                requires_reconciliation=source_changed,
            )

    def _recover(self, task, epic, op, *, sync):
        destination = task if sync else epic
        head = self.worktrees.verify_owned_worktree(destination)
        result = op.result
        if op.status == "SUCCEEDED":
            merged = result.get("merge_commit")
            if (
                not merged
                or not self.git.contains_commit(destination.branch, merged)
                or (
                    merged != result["target_commit"]
                    and self.git.parents(merged)
                    != (result["target_commit"], result["source_commit"])
                )
            ):
                raise IntegrationError("recorded merge result is no longer verifiable")
            return op
        if op.status != "PENDING":
            raise IntegrationError("merge operation needs conflict/input reconciliation")
        merged = self.git.find_operation_merge(
            destination.branch,
            op.id,
            result["target_commit"],
            result["source_commit"],
        )
        if merged:
            return self._complete_merge(task, epic, op, merged, sync=sync)
        if head != result["target_commit"]:
            raise IntegrationError("destination advanced without a verified operation merge")
        return None

    def _merge(self, actor, task_run_id, key, *, sync):
        kind = "sync_task_with_epic" if sync else "merge_task_to_epic"
        with self._lock():
            task, epic = self._task(actor, task_run_id)
            op = self._prior(task, kind, key)
            if op:
                recovered = self._recover(task, epic, op, sync=sync)
                if recovered:
                    return recovered
            with self.store.transaction():
                task, epic = self._task(actor, task_run_id)
                task_head, epic_head = self._owned_pair(task, epic)
                source, target = (epic_head, task_head) if sync else (task_head, epic_head)
                if op and (source, target) != (
                    op.result["source_commit"],
                    op.result["target_commit"],
                ):
                    raise IntegrationError("pending merge inputs changed")
                if sync:
                    if task.internal_status not in {
                        TaskState.READY_FOR_REVIEW,
                        TaskState.REVIEWING,
                        TaskState.CHANGES_REQUESTED,
                        TaskState.APPROVED,
                    }:
                        raise IntegrationError("task cannot be synchronized in its current phase")
                else:
                    self._approval(task, source, target)
                    if not self.git.contains_commit(task.branch, target):
                        raise IntegrationError("task must be synchronized with the current epic")
                if op is None:
                    if not sync and task.merge_commit is not None:
                        raise IntegrationError(
                            "task already has a merge; reconcile its existing result"
                        )
                    op = Operation(
                        project_id=task.project_id,
                        epic_run_id=epic.id,
                        task_run_id=task.id,
                        kind=kind,
                        idempotency_key=key,
                        result={"source_commit": source, "target_commit": target},
                    )
                    self.store.add_operation(op)
                if sync and (
                    not self.git.contains_commit(task.branch, source)
                    or (
                        task.approved_source_commit is not None
                        and (task.approved_source_commit, task.approved_target_commit)
                        != (task_head, epic_head)
                    )
                ):
                    task = self._invalidate(actor, task)
                if not sync and task.internal_status == TaskState.APPROVED:
                    self._patch(epic, current_commit=epic_head)
                    task = StateService(self.store).transition_task(
                        task.id,
                        TaskState.MERGING,
                        expected=TaskState.APPROVED,
                        event_id=f"merge-start:{op.id}",
                        actor=actor,
                        facts=VerifiedFacts(source_commit=source, target_commit=target),
                    )
            # Intent and state are committed before Git. The lock spans both transactions.
            with self.store.transaction():
                fresh_task, fresh_epic = self._task(actor, task_run_id)
                if self._owned_pair(fresh_task, fresh_epic) != (task_head, epic_head):
                    raise IntegrationError("Git changed before the protected merge")
                if not sync:
                    self._approval(fresh_task, source, target)
                destination = task if sync else epic
                path = Path(destination.worktree_path)
                if sync and self.git.contains_commit(task.branch, source):
                    return self._complete_merge(task, epic, op, target, sync=True)
                try:
                    self.git.merge_commit(path, source, op.id)
                except GitError:
                    if self.git.in_progress(path):
                        blocked = self.store.get_task(task.id)
                        StateService(self.store).transition_task(
                            blocked.id,
                            TaskState.BLOCKED,
                            expected=blocked.internal_status,
                            event_id=f"merge-conflict:{op.id}",
                            actor=actor,
                            facts=VerifiedFacts(
                                reason=f"{kind} conflict; preserve worktree; {op.id}"
                            ),
                        )
                        return self._finish(op, "CONFLICT", error="MERGE_CONFLICT")
                    raise
                merged = self.git.find_operation_merge(destination.branch, op.id, target, source)
                if merged is None:
                    raise IntegrationError(
                        "Git result requires reconciliation; do not repeat merge"
                    )
                return self._complete_merge(task, epic, op, merged, sync=sync)
