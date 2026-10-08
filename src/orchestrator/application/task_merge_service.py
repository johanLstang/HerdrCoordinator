"""Approved Task -> Epic, independent integration verification, physical stop, Done."""

import subprocess

from pydantic import ValidationError

from orchestrator.adapters.git import GitError
from orchestrator.application.git_integration_service import GitIntegrationService, IntegrationError
from orchestrator.application.git_review_service import GitReviewError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.runtime_lifecycle_service import (
    LifecycleError,
    RuntimeLifecycleService,
)
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.task_approval_service import TaskApprovalError, TaskApprovalService
from orchestrator.application.task_cleanup_service import TaskCleanupService
from orchestrator.application.task_delivery_evidence import known_merge, passed_test
from orchestrator.application.task_review_service import TaskReviewError
from orchestrator.application.worktree_service import WorktreeError
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import canonical_json
from orchestrator.persistence.store import StoreError


class TaskMergeError(RuntimeError):
    """Safe rejection; known delivery, worktrees, reservations and history survive."""


class DeliveryGitManager(GitIntegrationService):
    def __init__(self, settings, store, lifecycle):
        self.lifecycle = lifecycle
        self.approvals = TaskApprovalService(settings, store)
        configured = self.approvals.integration
        super().__init__(
            settings,
            store,
            test_command=configured.test_command,
            test_timeout=configured.test_timeout,
            test_environment=configured.test_environment,
        )

    def _approval(self, task, source, target):
        super()._approval(task, source, target)
        parents = [
            o
            for o in self.store.get_operations(task.epic_run_id, kind="task_merge")
            if o.task_run_id == task.id and o.status == "PENDING"
        ]
        if len(parents) != 1:
            raise TaskMergeError("DELIVERY_INTENT_UNVERIFIED")
        parent = parents[0]
        if (parent.result["source_commit"], parent.result["target_commit"]) != (source, target):
            raise TaskMergeError("DELIVERY_INPUTS_CHANGED")
        op = self.store.get_operation(
            task.project_id, "task_approve", parent.result["approval_key"]
        )
        if (
            op is None
            or digest(canonical_json(op.model_dump(mode="json")))
            != parent.result["approval_digest"]
        ):
            raise TaskMergeError("DELIVERY_APPROVAL_CHANGED")
        actor = self._delivery_actor(parent)
        self.approvals._proof(actor, task, op, merging=True)
        self.lifecycle.require_task_idle(actor, task.id)

    @staticmethod
    def _delivery_actor(parent):
        from orchestrator.domain.policy import Actor

        return Actor.model_validate(parent.result["actor"])


class TaskMergeService:
    KIND = "task_merge"

    def __init__(self, settings, store, herdr, codex=None, *, processes=None):
        self.settings, self.store = settings, store
        self.lifecycle = RuntimeLifecycleService(
            settings, store, herdr, codex=codex, processes=processes
        )
        self.git_manager = DeliveryGitManager(settings, store, self.lifecycle)

    def _scope(self, actor, run_id):
        task = self.store.get_task(run_id)
        if actor.role != Role.INTEGRATION or task is None:
            raise TaskMergeError("DELIVERY_SCOPE_DENIED")
        StateService.authorize_scope(actor, task)
        epic = self.store.get_epic(task.epic_run_id)
        if epic is None or (
            epic.status != EpicState.ACTIVE and task.internal_status != TaskState.DONE
        ):
            raise TaskMergeError("DELIVERY_EPIC_NOT_ACTIVE")
        return task, epic

    @staticmethod
    def _key(key):
        if (
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 128
            or any(c in key for c in "\x00\r\n")
        ):
            raise TaskMergeError("INVALID_DELIVERY_KEY")

    def _config(self, task, epic):
        contexts = self.git_manager.approvals.contexts
        spec, _ = contexts._spec(task, epic)
        return contexts._configuration(task, epic, spec)

    def _save(self, op, **result):
        current = self.store.get_operation(op.project_id, op.kind, op.idempotency_key)
        if current.id != op.id or current.status != "PENDING":
            raise TaskMergeError("DELIVERY_OPERATION_CHANGED")
        saved = current.model_copy(
            update={"updated_at": utc_now(), "result": current.result | result}
        )
        self.store.update_operation(saved)
        return saved

    def _existing(self, actor, task, parent):
        sha = known_merge(self.settings, self.store, task, parent, require_tip=False)
        passed_test(self.store, task, parent, sha)
        if task.internal_status != TaskState.DONE or not self.lifecycle.confirm_task_inactive(
            actor, task.id
        ):
            raise TaskMergeError("DELIVERY_DONE_UNVERIFIED")
        return {"status": "EXISTING", "operation_id": parent.id, "merge_commit": sha}

    def merge(self, actor, run_id, *, key, verification_key):
        try:
            self._key(key)
            self._key(verification_key)
            manager = self.git_manager
            with manager._lock(), self.store.transaction():
                task, epic = self._scope(actor, run_id)
                parent = self.store.get_operation(task.project_id, self.KIND, key)
                if parent:
                    if (
                        parent.task_run_id != task.id
                        or parent.epic_run_id != epic.id
                        or parent.result["actor"] != actor.model_dump(mode="json")
                        or any(
                            parent.result.get(k) != v for k, v in self._config(task, epic).items()
                        )
                    ):
                        raise TaskMergeError("DELIVERY_INTENT_MISMATCH")
                    if parent.status == "SUCCEEDED":
                        return self._existing(actor, task, parent)
                    if parent.status != "PENDING":
                        raise TaskMergeError("DELIVERY_RECONCILIATION_REQUIRED")
                else:
                    if any(
                        o.status == "PENDING"
                        for o in self.store.get_operations(epic.id, kind=self.KIND)
                    ):
                        raise TaskMergeError("DELIVERY_EPIC_BUSY")
                    # Read directly inside the same protected transaction, not a prior boolean.
                    proofs = [
                        o
                        for o in self.store.get_operations(epic.id, kind="task_approve")
                        if o.task_run_id == task.id and o.status == "SUCCEEDED"
                    ]
                    if not proofs:
                        raise TaskMergeError("DELIVERY_APPROVAL_UNVERIFIED")
                    approval = max(proofs, key=lambda item: item.created_at)
                    context = manager.approvals._proof(actor, task, approval)
                    packages = [
                        o
                        for o in self.store.get_operations(epic.id, kind="task_review_request")
                        if o.task_run_id == task.id
                        and o.status == "SUCCEEDED"
                        and o.result.get("context", {}).get("context_id") == context["context_id"]
                    ]
                    if len(packages) != 1:
                        raise TaskMergeError("DELIVERY_CONTEXT_UNVERIFIED")
                    package = packages[0]
                    parent = Operation(
                        project_id=task.project_id,
                        epic_run_id=epic.id,
                        task_run_id=task.id,
                        kind=self.KIND,
                        idempotency_key=key,
                        result={
                            "stage": "INTENT",
                            "actor": actor.model_dump(mode="json"),
                            "approval_id": approval.id,
                            "approval_key": approval.idempotency_key,
                            "approval_digest": digest(
                                canonical_json(approval.model_dump(mode="json"))
                            ),
                            "review_id": approval.result["review_id"],
                            "context_id": context["context_id"],
                            "context_key": package.idempotency_key,
                            "context_digest": digest(
                                canonical_json(package.model_dump(mode="json"))
                            ),
                            "source_commit": context["task_commit"],
                            "target_commit": context["epic_commit"],
                            **self._config(task, epic),
                        },
                    )
                    self.store.add_operation(parent)
            # F07 owns its lock and recovers its durable Git intent/tag after process loss.
            merged = manager.merge_task_to_epic(actor, task.id, key=parent.id)
            if merged.status != "SUCCEEDED":
                raise TaskMergeError("DELIVERY_MERGE_RECONCILIATION_REQUIRED")
            with manager._lock():
                task, _ = self._scope(actor, run_id)
                parent = self.store.get_operation(task.project_id, self.KIND, key)
                if parent.status == "SUCCEEDED":
                    return self._existing(actor, task, parent)
                sha = known_merge(self.settings, self.store, task, parent)
                with self.store.transaction():
                    parent = self._save(
                        parent, stage="MERGED", merge_commit=sha, merge_operation_id=merged.id
                    )
                test = self._test(actor, task, parent, sha, verification_key)
                if test.status == "PENDING":
                    raise TaskMergeError("DELIVERY_TEST_OUTCOME_UNKNOWN")
                if test.status != "SUCCEEDED":
                    return {
                        "status": "TEST_FAILED",
                        "operation_id": parent.id,
                        "merge_commit": sha,
                        "test_id": test.id,
                        "reason": test.error_code,
                    }
                with self.store.transaction():
                    parent = self._save(
                        parent, stage="TESTED", test_id=test.id, test_key=test.idempotency_key
                    )
                stop = self.lifecycle.stop_delivered_task(
                    actor, task.id, key="delivery-stop:" + parent.id, delivery_key=key
                )
                return self._done(actor, task.id, parent, sha, stop)
        except TaskMergeError:
            raise
        except IntegrationError as error:
            if str(error) == "repository integration is busy":
                raise TaskMergeError("DELIVERY_BUSY") from None
            raise TaskMergeError("DELIVERY_UNVERIFIED") from None
        except (
            TaskApprovalError,
            TaskReviewError,
            GitReviewError,
            GitError,
            WorktreeError,
            LifecycleError,
            StateError,
            StoreError,
            ValidationError,
            ValueError,
            KeyError,
            TypeError,
        ):
            raise TaskMergeError("DELIVERY_UNVERIFIED") from None

    def _test(self, actor, task, parent, sha, key):
        test_key = parent.id + ":" + key
        request = {
            "delivery_id": parent.id,
            "merge_commit": sha,
            "source_commit": parent.result["source_commit"],
            "target_commit": parent.result["target_commit"],
            "command_hash": parent.result["command_hash"],
            "timeout": parent.result["test_timeout"],
            "command": list(self.git_manager.test_command),
        }
        with self.store.transaction():
            current = self.store.get_operation(task.project_id, self.KIND, parent.idempotency_key)
            if "test_id" in current.result:
                return passed_test(self.store, task, current, sha)
            old = self.store.get_operation(task.project_id, "task_delivery_test", test_key)
            if old:
                if old.task_run_id != task.id or any(
                    old.result.get(k) != v for k, v in request.items()
                ):
                    raise TaskMergeError("DELIVERY_TEST_INTENT_MISMATCH")
                return old
            # After successful testing only the missing stop/Done step is retried.
            test = Operation(
                project_id=task.project_id,
                epic_run_id=task.epic_run_id,
                task_run_id=task.id,
                kind="task_delivery_test",
                idempotency_key=test_key,
                result=request,
            )
            self.store.add_operation(test)
        exit_code, error = None, None
        epic = self.store.get_epic(task.epic_run_id)
        try:
            exit_code = subprocess.run(
                self.git_manager.test_command,
                cwd=epic.worktree_path,
                env=self.git_manager.test_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.git_manager.test_timeout,
                check=False,
            ).returncode
            if exit_code != 0:
                error = "INTEGRATION_TEST_FAILED"
            fresh, _ = self._scope(actor, task.id)
            known_merge(self.settings, self.store, fresh, parent)
        except (OSError, subprocess.TimeoutExpired):
            error = "INTEGRATION_TEST_PROCESS_FAILED"
        except (IntegrationError, GitError, WorktreeError, StateError, TaskMergeError):
            error = "INTEGRATION_TEST_RESULT_CHANGED"
        with self.store.transaction():
            return self.git_manager._finish(
                test, "FAILED" if error else "SUCCEEDED", error=error, exit_code=exit_code
            )

    def _done(self, actor, run_id, parent, sha, stop):
        with self.store.transaction():
            task, _ = self._scope(actor, run_id)
            current = self.store.get_operation(task.project_id, self.KIND, parent.idempotency_key)
            known_merge(self.settings, self.store, task, current)
            passed_test(self.store, task, current, sha)
            if (
                stop.status != "SUCCEEDED"
                or stop.task_run_id != task.id
                or not self.lifecycle.confirm_task_inactive(actor, run_id)
            ):
                raise TaskMergeError("DELIVERY_STOP_UNVERIFIED")
            StateService(self.store).transition_task(
                task.id,
                TaskState.DONE,
                expected=TaskState.MERGING,
                event_id="delivery-done:" + parent.id,
                actor=actor,
                facts=VerifiedFacts(
                    source_commit=current.result["source_commit"],
                    target_commit=current.result["target_commit"],
                    merge_commit=sha,
                    verification_commit=sha,
                    tests_passed=True,
                ),
            )
            saved = current.model_copy(
                update={
                    "status": "SUCCEEDED",
                    "updated_at": utc_now(),
                    "result": current.result | {"stage": "DONE", "stop_id": stop.id},
                }
            )
            self.store.update_operation(saved)
            return {
                "status": "DONE",
                "operation_id": parent.id,
                "merge_commit": sha,
                "test_id": saved.result["test_id"],
                "stop_id": stop.id,
            }

    def cleanup(self, actor, run_id, *, key):
        """Explicit retention choice after delivery; failure never repeats merge or deletes data."""
        service = TaskCleanupService(
            self.settings,
            self.store,
            inactivity_probe=lambda task: self.lifecycle.confirm_task_inactive(actor, task.id),
        )
        return service.remove_task_worktree(actor, run_id, key=key)
