"""Prepare pinned review material; no review decision, delivery merge or Done."""

import base64
import os
from dataclasses import asdict

from pydantic import ValidationError

from orchestrator.adapters.git import GitAdapter, GitError
from orchestrator.application.git_integration_service import GitIntegrationService, IntegrationError
from orchestrator.application.git_review_service import GitReviewError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.worktree_service import WorktreeError
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Role
from orchestrator.domain.review_contracts import source_path
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import ContractError, LocalTaskSpec, canonical_json
from orchestrator.persistence.store import StoreError


class TaskReviewError(RuntimeError):
    """Safe rejection; never expose raw Git, test or source contents in errors."""


class TaskReviewService:
    KIND = "task_review_request"
    MAX_SOURCE_BYTES = 256 * 1024
    MAX_CONTEXT_BYTES = 4 * 1024 * 1024

    def __init__(self, settings, store, *, max_diff_bytes=GitAdapter.DEFAULT_DIFF_BYTES):
        if type(max_diff_bytes) is not int or not 1 <= max_diff_bytes <= 4 * 1024 * 1024:
            raise TaskReviewError("INVALID_REVIEW_LIMIT")
        self.settings, self.store = settings, store
        self.max_diff_bytes = max_diff_bytes
        self.integration = GitIntegrationService(
            settings,
            store,
            test_command=settings.worker_test_command,
            test_timeout=settings.worker_test_timeout,
            test_environment={
                "PATH": os.environ.get("PATH", os.defpath),
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        self.git = self.integration.git

    def _scope(self, actor, run_id, *, require_ready=True):
        task = self.store.get_task(run_id)
        if actor.role != Role.INTEGRATION or task is None:
            raise TaskReviewError("REVIEW_SCOPE_DENIED")
        StateService.authorize_scope(actor, task)
        epic = self.store.get_epic(task.epic_run_id)
        if epic is None or epic.status != EpicState.ACTIVE:
            raise TaskReviewError("REVIEW_EPIC_NOT_ACTIVE")
        if require_ready and task.internal_status not in {
            TaskState.READY_FOR_REVIEW,
            TaskState.REVIEWING,
        }:
            raise TaskReviewError("REVIEW_TASK_NOT_READY")
        return task, epic

    def _spec(self, task, epic):
        start = self.store.get_operation(task.project_id, "task_start", task.task_id)
        if (
            start is None
            or start.status != "SUCCEEDED"
            or start.task_run_id != task.id
            or start.epic_run_id != epic.id
        ):
            raise TaskReviewError("REVIEW_TASK_SPEC_UNVERIFIED")
        spec = LocalTaskSpec.model_validate(start.result["spec"]).bind(epic)
        if (
            spec.task_id != task.task_id
            or digest(canonical_json(spec.model_dump(mode="json"))) != start.result["spec_hash"]
        ):
            raise TaskReviewError("REVIEW_TASK_SPEC_CHANGED")
        return spec, start

    def _handoff(self, task, head):
        handoffs = [
            op
            for op in self.store.get_operations(task.epic_run_id, kind="worker_report")
            if op.task_run_id == task.id
            and op.status == "SUCCEEDED"
            and op.result.get("stage") == "READY_FOR_REVIEW"
        ]
        if not handoffs:
            raise TaskReviewError("REVIEW_HANDOFF_UNVERIFIED")
        handoff = max(handoffs, key=lambda op: op.updated_at)
        verified = next(
            (
                op
                for op in self.store.get_operations(task.epic_run_id, kind="verify_task")
                if op.id == handoff.result.get("verification_id")
            ),
            None,
        )
        commit = handoff.result.get("verified_commit")
        if (
            verified is None
            or verified.task_run_id != task.id
            or verified.status != "SUCCEEDED"
            or verified.result.get("exit_code") != 0
            or verified.result.get("source_commit") != commit
            or handoff.result.get("exit_code") != 0
        ):
            raise TaskReviewError("REVIEW_HANDOFF_TEST_UNVERIFIED")
        # Only registered Integration sync commits may extend a verified Worker
        # delivery. A new Worker commit needs a new handoff, never just ancestry.
        syncs = self.store.get_operations(task.epic_run_id, kind="sync_task_with_epic")
        seen = set()
        while head != commit:
            if head in seen:
                raise TaskReviewError("REVIEW_HANDOFF_CHAIN_INVALID")
            seen.add(head)
            prior = next(
                (
                    op
                    for op in syncs
                    if op.task_run_id == task.id
                    and op.status == "SUCCEEDED"
                    and op.result.get("merge_commit") == head
                    and op.result.get("target_commit") != head
                    and op.result.get("requires_reconciliation") is False
                    and self.git.parents(head)
                    == (op.result.get("target_commit"), op.result.get("source_commit"))
                ),
                None,
            )
            if prior is None:
                raise TaskReviewError("REVIEW_TASK_CHANGED_SINCE_HANDOFF")
            head = prior.result["target_commit"]
        return handoff

    def _configuration(self, task, epic, spec):
        rules = self.settings.review_context
        if rules is None:
            raise TaskReviewError("REVIEW_EPIC_CONTEXT_REQUIRED")
        if (rules.project_id, rules.epic_id) != (task.project_id, epic.epic_id):
            raise TaskReviewError("REVIEW_EPIC_CONTEXT_MISMATCH")
        if not self.integration.test_command:
            raise TaskReviewError("REVIEW_TEST_COMMAND_REQUIRED")
        return {
            "spec_hash": digest(canonical_json(spec.model_dump(mode="json"))),
            "epic_context_hash": digest(canonical_json(rules.model_dump(mode="json"))),
            "command_hash": self.integration.command_hash,
            "test_timeout": self.integration.test_timeout,
            "max_diff_bytes": self.max_diff_bytes,
        }

    def _checkpoint(self, op, *, stage, **updates):
        with self.store.transaction():
            current = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
            if current.id != op.id:
                raise TaskReviewError("REVIEW_OPERATION_CHANGED")
            if current.status != "PENDING":
                return current
            value = current.model_copy(
                update={
                    "updated_at": utc_now(),
                    "result": current.result | {"stage": stage, **updates},
                }
            )
            self.store.update_operation(value)
            return value

    def _failure(self, op, code, **observations):
        with self.store.transaction():
            current = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
            if current.status != "PENDING":
                return current
            context = {
                "version": 1,
                "reviewable": False,
                "reason": code,
                "project_id": op.project_id,
                "epic_run_id": op.epic_run_id,
                "task_run_id": op.task_run_id,
                **observations,
            }
            context["context_id"] = digest(canonical_json(context))
            current = current.model_copy(
                update={
                    "status": "FAILED",
                    "error_code": code,
                    "updated_at": utc_now(),
                    "result": current.result | {"stage": "NOT_REVIEWABLE", "context": context},
                }
            )
            self.store.update_operation(current)
            return current

    def _saved(self, actor, task, op, config):
        context = op.result["context"]
        identity = context["context_id"]
        if (
            digest(canonical_json({k: v for k, v in context.items() if k != "context_id"}))
            != identity
        ):
            raise TaskReviewError("REVIEW_CONTEXT_CORRUPT")
        if op.result["configuration"] != config:
            raise TaskReviewError("REVIEW_CONFIGURATION_CHANGED")
        if op.status == "SUCCEEDED":
            evidence = self.integration.reviews.task_review(
                actor,
                task.id,
                expected_commit=context["task_commit"],
                max_diff_bytes=self.max_diff_bytes,
            )
            if not evidence.reviewable or evidence.target_commit != context["epic_commit"]:
                raise TaskReviewError("REVIEW_CONTEXT_STALE")
        return {
            "status": "EXISTING" if op.status == "SUCCEEDED" else "NOT_REVIEWABLE",
            "operation_id": op.id,
            "context": context,
        }

    def current_context(self, actor, run_id, identity):
        task, epic = self._scope(actor, run_id, require_ready=False)
        matches = [
            op
            for op in self.store.get_operations(epic.id, kind=self.KIND)
            if op.task_run_id == task.id
            and op.status == "SUCCEEDED"
            and op.result.get("context", {}).get("context_id") == identity
        ]
        if len(matches) != 1:
            raise TaskReviewError("REVIEW_CONTEXT_UNVERIFIED")
        op = matches[0]
        requests = [
            item
            for item in self.store.get_operations(epic.id, kind=self.KIND)
            if item.task_run_id == task.id
        ]
        if max(requests, key=lambda item: item.created_at).id != op.id:
            raise TaskReviewError("REVIEW_CONTEXT_SUPERSEDED")
        spec, _ = self._spec(task, epic)
        config = self._configuration(task, epic, spec)
        context = self._saved(actor, task, op, config)["context"]
        if self._handoff(task, context["task_commit"]).id != context["handoff_operation_id"]:
            raise TaskReviewError("REVIEW_HANDOFF_CHANGED")
        test = self.store.get_operation(task.project_id, "verify_task", context["tests"]["key"])
        if (
            test is None
            or test.id != context["tests"]["operation_id"]
            or test.task_run_id != task.id
            or test.epic_run_id != epic.id
            or test.status != "SUCCEEDED"
            or test.result.get("exit_code") != 0
            or test.result.get("source_commit") != context["task_commit"]
            or test.result.get("target_commit") != context["epic_commit"]
            or test.result.get("command_hash") != self.integration.command_hash
        ):
            raise TaskReviewError("REVIEW_TEST_UNVERIFIED")
        return context

    def _source(self, revision, path, side):
        path = source_path(path)
        entry = self.git.run("ls-tree", "-z", revision, "--", path).rstrip("\0")
        if not entry:
            raise TaskReviewError("REVIEW_SOURCE_UNAVAILABLE")
        metadata, name = entry.split("\t", 1)
        mode, kind, oid = metadata.split()
        if name != path or mode not in {"100644", "100755"} or kind != "blob":
            raise TaskReviewError("REVIEW_SOURCE_NOT_REGULAR_FILE")
        size = int(self.git.run("cat-file", "-s", oid).strip())
        if size > self.MAX_SOURCE_BYTES:
            raise TaskReviewError("REVIEW_SOURCE_TOO_LARGE")
        text = self.git.run("cat-file", "blob", oid)
        if "\x00" in text:
            raise TaskReviewError("REVIEW_SOURCE_NOT_TEXT")
        return {"path": path, "side": side, "commit": revision, "blob": oid, "text": text}

    def _context(self, task, epic, spec, start, handoff, evidence, verification, sync):
        rules = self.settings.review_context
        if len(spec.sources) > 32:
            raise TaskReviewError("REVIEW_SOURCE_LIMIT")
        sources = [
            self._source(evidence.source.current_commit, path, "task")
            for path in dict.fromkeys(spec.sources)
        ] + [
            self._source(evidence.target_commit, path, "epic")
            for path in dict.fromkeys(rules.sources)
        ]
        patch = evidence.source.commit_diff
        context = {
            "version": 1,
            "reviewable": True,
            "project_id": task.project_id,
            "epic_id": epic.epic_id,
            "epic_run_id": epic.id,
            "task_id": task.task_id,
            "task_run_id": task.id,
            "task_branch": task.branch,
            "task_worktree": task.worktree_path,
            "epic_branch": epic.branch,
            "task_commit": evidence.source.current_commit,
            "epic_commit": evidence.target_commit,
            "task_specification": spec.model_dump(mode="json"),
            "acceptance_criteria": spec.acceptance_criteria,
            "epic_requirements": rules.model_dump(mode="json"),
            "sources": sources,
            "diff": {
                "complete": patch.complete,
                "bytes": patch.total_bytes,
                "sha256": patch.sha256,
                "patch_base64": base64.b64encode(patch.patch).decode("ascii"),
                "display_text": patch.patch.decode("utf-8", errors="replace"),
                "full_command": list(patch.full_command),
            },
            "changed_files": [
                asdict(file) | {"binary": file.binary} for file in evidence.source.changed_files
            ],
            "tests": {
                "operation_id": verification.id,
                "key": verification.idempotency_key,
                "status": verification.status,
                **verification.result,
                "command": list(self.integration.test_command),
                "timeout": self.integration.test_timeout,
            },
            "task_start_operation_id": start.id,
            "handoff_operation_id": handoff.id,
            "sync_operation_id": sync.id,
        }
        if len(canonical_json(context).encode()) > self.MAX_CONTEXT_BYTES:
            raise TaskReviewError("REVIEW_CONTEXT_TOO_LARGE")
        context["context_id"] = digest(canonical_json(context))
        return context

    def request(self, actor, run_id, *, key):
        op = None
        try:
            if not isinstance(key, str) or not key.strip() or len(key) > 128 or "\x00" in key:
                raise TaskReviewError("INVALID_REVIEW_KEY")
            task, epic = self._scope(actor, run_id, require_ready=False)
            spec, start = self._spec(task, epic)
            config = self._configuration(task, epic, spec)
            failed_prior = self.store.get_operation(task.project_id, self.KIND, key)
            if failed_prior is not None and failed_prior.status == "FAILED":
                if failed_prior.task_run_id != task.id or failed_prior.epic_run_id != epic.id:
                    raise TaskReviewError("REVIEW_KEY_SCOPE_MISMATCH")
                # Historical conflict/failure is readable without touching the
                # dirty or blocked worktree, and cannot revive REVIEWING.
                return self._saved(actor, task, failed_prior, config)
            task, epic = self._scope(actor, run_id)
            with self.integration._lock(), self.store.transaction():
                task, epic = self._scope(actor, run_id)
                head, epic_head = self.integration._owned_pair(task, epic)
                handoff = self._handoff(task, head)
                op = self.store.get_operation(task.project_id, self.KIND, key)
                if op:
                    if op.task_run_id != task.id or op.epic_run_id != epic.id:
                        raise TaskReviewError("REVIEW_KEY_SCOPE_MISMATCH")
                    if op.result["configuration"] != config:
                        raise TaskReviewError("REVIEW_CONFIGURATION_CHANGED")
                    if op.status in {"SUCCEEDED", "FAILED"}:
                        return self._saved(actor, task, op, config)
                else:
                    if any(
                        other.task_run_id == task.id and other.status == "PENDING"
                        for kind in ("task_approve", "task_request_changes")
                        for other in self.store.get_operations(epic.id, kind=kind)
                    ):
                        raise TaskReviewError("REVIEW_DECISION_PENDING")
                    op = Operation(
                        project_id=task.project_id,
                        epic_run_id=epic.id,
                        task_run_id=task.id,
                        kind=self.KIND,
                        idempotency_key=key,
                        result={
                            "stage": "INTENT",
                            "configuration": config,
                            "handoff_operation_id": handoff.id,
                            "source_before": head,
                            "epic_before": epic_head,
                        },
                    )
                    self.store.add_operation(op)
            # F-07 owns its repository lock and durable sync/test intents. Do not
            # nest its lock under another descriptor or repeat unknown tests.
            sync = self.integration.sync_task_with_epic(actor, task.id, key="review-sync:" + op.id)
            if sync.status != "SUCCEEDED":
                failed = self._failure(op, "REVIEW_SYNC_CONFLICT", sync_operation_id=sync.id)
                return self._saved(actor, task, failed, config)
            self._checkpoint(op, stage="SYNCED", sync_operation_id=sync.id)
            evidence = self.integration.reviews.task_review(
                actor,
                task.id,
                max_diff_bytes=self.max_diff_bytes,
            )
            if not evidence.reviewable:
                failed = self._failure(
                    op,
                    "REVIEW_GIT_INCOMPLETE",
                    task_commit=evidence.source.current_commit,
                    epic_commit=evidence.target_commit,
                    diff_complete=evidence.source.commit_diff.complete,
                )
                return self._saved(actor, task, failed, config)
            try:
                verification = self.integration.verify_task(
                    actor, task.id, key="review-test:" + op.id
                )
            except IntegrationError:
                unknown = self.store.get_operation(
                    task.project_id, "verify_task", "review-test:" + op.id
                )
                if unknown is None or unknown.status != "PENDING":
                    raise
                failed = self._failure(
                    op,
                    "REVIEW_TEST_OUTCOME_UNKNOWN",
                    verification_id=unknown.id,
                    task_commit=unknown.result["source_commit"],
                    epic_commit=unknown.result["target_commit"],
                )
                return self._saved(actor, task, failed, config)
            if verification.status != "SUCCEEDED":
                failed = self._failure(
                    op,
                    "REVIEW_TEST_FAILED",
                    task_commit=verification.result["source_commit"],
                    epic_commit=verification.result["target_commit"],
                    tests=verification.model_dump(mode="json"),
                )
                return self._saved(actor, task, failed, config)
            self._checkpoint(op, stage="TESTED", verification_id=verification.id)
            with self.integration._lock(), self.store.transaction():
                task, epic = self._scope(actor, run_id)
                fresh = self.integration.reviews.task_review(
                    actor,
                    task.id,
                    expected_commit=verification.result["source_commit"],
                    max_diff_bytes=self.max_diff_bytes,
                )
                if (
                    not fresh.reviewable
                    or fresh.target_commit != verification.result["target_commit"]
                    or (fresh.source.current_commit, fresh.target_commit)
                    != (evidence.source.current_commit, evidence.target_commit)
                    or self._configuration(task, epic, spec) != config
                ):
                    raise TaskReviewError("REVIEW_CONTEXT_STALE")
                context = self._context(task, epic, spec, start, handoff, fresh, verification, sync)
                if self.integration._owned_pair(task, epic) != (
                    context["task_commit"],
                    context["epic_commit"],
                ):
                    raise TaskReviewError("REVIEW_CONTEXT_STALE")
                current = self.store.get_operation(task.project_id, self.KIND, key)
                if current.status == "SUCCEEDED":
                    return self._saved(actor, task, current, config)
                if task.internal_status == TaskState.READY_FOR_REVIEW:
                    StateService(self.store).transition_task(
                        task.id,
                        TaskState.REVIEWING,
                        expected=TaskState.READY_FOR_REVIEW,
                        event_id="review-context:" + op.id,
                        actor=actor,
                    )
                self.store.update_operation(
                    current.model_copy(
                        update={
                            "status": "SUCCEEDED",
                            "error_code": None,
                            "updated_at": utc_now(),
                            "result": current.result | {"stage": "READY", "context": context},
                        }
                    )
                )
                return {"status": "READY", "operation_id": op.id, "context": context}
        except TaskReviewError as error:
            if op is not None and op.task_run_id == run_id and op.status == "PENDING":
                failed = self._failure(op, str(error))
                return self._saved(actor, task, failed, config)
            raise
        except (
            IntegrationError,
            GitReviewError,
            GitError,
            WorktreeError,
            StateError,
            StoreError,
            ContractError,
            ValidationError,
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
        ):
            # The original operation remains available for reconciliation. An
            # interrupted test is not silently retried with a new key.
            if op is not None and op.task_run_id == run_id and op.status == "PENDING":
                failed = self._failure(op, "REVIEW_UNVERIFIED")
                return self._saved(actor, task, failed, config)
            raise TaskReviewError("REVIEW_UNVERIFIED") from None
