"""Coordinator-only main integration backed by actual Git and process evidence."""

import hashlib
import json
import subprocess
from pathlib import Path

from orchestrator.adapters.git import GitError
from orchestrator.application.git_integration_service import GitIntegrationService, IntegrationError
from orchestrator.application.state_service import StateService
from orchestrator.domain.models import Operation
from orchestrator.domain.policy import Role, VerifiedFacts
from orchestrator.domain.states import EpicState as E
from orchestrator.domain.states import TaskState as T


class EpicIntegrationService(GitIntegrationService):
    def __init__(self, *args, expected_task_ids: tuple[str, ...], **kwargs):
        super().__init__(*args, **kwargs)
        # A trusted operator scope; never infer completeness from whichever runs exist.
        if not expected_task_ids or len(set(expected_task_ids)) != len(expected_task_ids):
            raise IntegrationError("explicit unique epic task scope is required")
        self.expected_task_ids = tuple(sorted(expected_task_ids))

    def _epic(self, actor, run_id):
        epic = self.store.get_epic(run_id)
        if (
            epic is None
            or actor.role != Role.COORDINATOR
            or actor.project_id != epic.project_id
            or actor.epic_run_id not in {None, epic.id}
        ):
            raise IntegrationError("only the registered Coordinator may integrate this epic")
        return epic

    def _epic_prior(self, epic, kind, key):
        self._key(key)
        op = self.store.get_operation(epic.project_id, kind, key)
        if op and (op.epic_run_id != epic.id or op.task_run_id is not None):
            raise IntegrationError("operation key belongs to another epic")
        return op

    def _epic_pair(self, epic):
        source = self.worktrees.verify_owned_worktree(epic)
        main = self.worktrees.settings.repository
        target = self.git.inspect(main, "main", clean=False)
        for path in (Path(epic.worktree_path), main):
            if (
                self.git.working_changes(path)
                or self.git.unsafe_index_paths(path)
                or self.git.in_progress(path)
            ):
                raise IntegrationError("epic integration requires clean stable worktrees")
        return source, target

    def _manifest(self, epic):
        tasks = self.store.get_tasks(epic.id)
        if tuple(sorted(t.task_id for t in tasks)) != self.expected_task_ids:
            raise IntegrationError("epic task scope is incomplete or changed")
        merges = self.store.get_operations(epic.id, kind="merge_task_to_epic")
        manifest = []
        for task in tasks:
            matching = [
                op
                for op in merges
                if op.task_run_id == task.id
                and op.status == "SUCCEEDED"
                and op.result.get("merge_commit") == task.merge_commit
                and op.result.get("requires_reconciliation") is False
            ]
            reviews = self.store.get_reviews(task.id)
            if (
                task.internal_status != T.DONE
                or not task.completed_at
                or not task.merge_commit
                or len(matching) != 1
                or not reviews
            ):
                raise IntegrationError("all tasks require Done and actual registered delivery")
            op, review = matching[0], reviews[-1]
            pair = (task.approved_source_commit, task.approved_target_commit)
            if (
                pair != (op.result.get("source_commit"), op.result.get("target_commit"))
                or task.current_commit != pair[0]
                or self.git.head(task.branch) not in {None, pair[0]}
                or review.review_result != "APPROVED"
                or (review.review_commit, review.epic_commit) != pair
                or self.git.parents(task.merge_commit) != (pair[1], pair[0])
                or self.git.find_operation_merge(epic.branch, op.id, pair[1], pair[0])
                != task.merge_commit
                or not self.git.contains_commit(epic.branch, task.merge_commit)
                or not self.git.contains_commit(epic.branch, pair[0])
            ):
                raise IntegrationError("task review and Git delivery proof disagree")
            registered = self.store.get_operation(task.project_id, "task_review", review.id)
            verified = (
                self.store.get_operation(
                    task.project_id, "verify_task", registered.result.get("verification_key")
                )
                if registered
                else None
            )
            if (
                registered is None
                or registered.task_run_id != task.id
                or registered.status != "SUCCEEDED"
                or not registered.result.get("approved")
                or verified is None
                or verified.task_run_id != task.id
                or verified.status != "SUCCEEDED"
                or verified.result.get("exit_code") != 0
                or (verified.result.get("source_commit"), verified.result.get("target_commit"))
                != pair
            ):
                raise IntegrationError("task delivery lacks registered review/test evidence")
            manifest.append(
                {
                    "task_run_id": task.id,
                    "task_id": task.task_id,
                    "source_commit": pair[0],
                    "merge_commit": task.merge_commit,
                    "review_id": review.id,
                    "operation_id": op.id,
                }
            )
        return manifest

    @staticmethod
    def _manifest_hash(manifest):
        return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()

    def _execute(self, path, fresh):
        error, code = None, None
        try:
            code = subprocess.run(
                self.test_command,
                cwd=path,
                env=self.git._environment(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.test_timeout,
                check=False,
            ).returncode
            error = None if code == 0 else "TEST_FAILED"
            if not fresh():
                error = "STALE_TEST_EVIDENCE"
        except (OSError, subprocess.TimeoutExpired):
            error = "TEST_PROCESS_FAILED"
        except RuntimeError:
            error = "STALE_TEST_EVIDENCE"
        return error, code

    def verify_epic(self, actor, run_id, *, key):
        if not self.test_command:
            raise IntegrationError("operator test command is not configured")
        with self._lock():
            epic = self._epic(actor, run_id)
            if epic.status in {E.MERGING, E.DONE}:
                raise IntegrationError("epic is past aggregate verification")
            source, target = self._epic_pair(epic)
            evidence = self.reviews.epic_review(actor, epic.id, expected_commit=source)
            if not evidence.reviewable:
                raise IntegrationError(
                    "epic must include current main and complete review evidence"
                )
            manifest = self._manifest(epic)
            request = dict(
                source_commit=source,
                target_commit=target,
                manifest=manifest,
                manifest_hash=self._manifest_hash(manifest),
                command_hash=self.command_hash,
            )
            old = self._epic_prior(epic, "verify_epic", key)
            if old:
                if any(old.result.get(k) != v for k, v in request.items()):
                    raise IntegrationError("verification key refers to stale evidence")
                if old.status == "PENDING":
                    raise IntegrationError("test outcome unknown; use a new verification key")
                return old
            with self.store.transaction():
                self._patch(epic, current_commit=source)
                op = Operation(
                    project_id=epic.project_id,
                    epic_run_id=epic.id,
                    kind="verify_epic",
                    idempotency_key=key,
                    result=request,
                )
                self.store.add_operation(op)

            def fresh():
                current = self._epic(actor, run_id)
                return (
                    self._epic_pair(current) == (source, target)
                    and self._manifest(current) == manifest
                    and self.reviews.epic_review(actor, run_id).reviewable
                )

            error, code = self._execute(epic.worktree_path, fresh)
            return self._finish(op, "FAILED" if error else "SUCCEEDED", error=error, exit_code=code)

    def register_epic_review(
        self, actor, run_id, *, verification_key, key, approved: bool, feedback=""
    ):
        if type(approved) is not bool or (not approved and not feedback.strip()):
            raise IntegrationError("explicit review decision and rejection reason required")
        with self._lock(), self.store.transaction():
            epic = self._epic(actor, run_id)
            request = dict(
                verification_key=verification_key,
                approved=approved,
                feedback_hash=hashlib.sha256(feedback.encode()).hexdigest(),
            )
            old = self._epic_prior(epic, "epic_review", key)
            if old:
                if any(old.result.get(k) != v for k, v in request.items()):
                    raise IntegrationError("review key belongs to another decision")
                return old
            source, target = self._epic_pair(epic)
            manifest = self._manifest(epic)
            verification = self._epic_prior(epic, "verify_epic", verification_key)
            if (
                not self.reviews.epic_review(actor, run_id).reviewable
                or verification is None
                or verification.status != "SUCCEEDED"
                or verification.result.get("exit_code") != 0
                or verification.result.get("command_hash") != self.command_hash
                or verification.result.get("manifest") != manifest
                or (
                    verification.result.get("source_commit"),
                    verification.result.get("target_commit"),
                )
                != (source, target)
            ):
                raise IntegrationError("review lacks actual current aggregate test evidence")
            epic = self._patch(epic, current_commit=source)
            states = StateService(self.store)
            facts = VerifiedFacts(
                scope_complete=True,
                tests_passed=True,
                verification_commit=source,
                source_commit=source,
                target_commit=target,
                review_approved=approved,
                reason=feedback,
            )
            if epic.status == E.CHANGES_REQUESTED:
                epic = states.transition_epic(
                    epic.id,
                    E.ACTIVE,
                    expected=E.CHANGES_REQUESTED,
                    event_id=f"epic-rework:{key}",
                    actor=actor,
                )
            if epic.status == E.ACTIVE:
                epic = states.transition_epic(
                    epic.id,
                    E.READY_FOR_REVIEW,
                    expected=E.ACTIVE,
                    event_id=f"epic-ready:{key}",
                    actor=actor,
                    facts=facts,
                )
            if epic.status == E.READY_FOR_REVIEW:
                epic = states.transition_epic(
                    epic.id,
                    E.REVIEWING,
                    expected=E.READY_FOR_REVIEW,
                    event_id=f"epic-review-start:{key}",
                    actor=actor,
                )
            if epic.status != E.REVIEWING:
                raise IntegrationError("epic is not in its review phase")
            states.transition_epic(
                epic.id,
                E.APPROVED if approved else E.CHANGES_REQUESTED,
                expected=E.REVIEWING,
                event_id=f"epic-review:{key}",
                actor=actor,
                facts=facts,
            )
            op = Operation(
                project_id=epic.project_id,
                epic_run_id=epic.id,
                kind="epic_review",
                idempotency_key=key,
                status="SUCCEEDED",
                result=request
                | dict(
                    source_commit=source,
                    target_commit=target,
                    manifest_hash=self._manifest_hash(manifest),
                ),
            )
            self.store.add_operation(op)
            return op

    def _epic_approval(self, epic, source, target):
        manifest = self._manifest(epic)
        reviews = self.store.get_operations(epic.id, kind="epic_review")
        review = reviews[-1] if reviews else None
        if (
            epic.status not in {E.APPROVED, E.MERGING}
            or (epic.approved_source_commit, epic.approved_target_commit) != (source, target)
            or review is None
            or review.status != "SUCCEEDED"
            or review.result.get("approved") is not True
            or (review.result.get("source_commit"), review.result.get("target_commit"))
            != (source, target)
            or review.result.get("manifest_hash") != self._manifest_hash(manifest)
        ):
            raise IntegrationError("approval does not match actual current epic/main/task scope")
        verification = self._epic_prior(epic, "verify_epic", review.result["verification_key"])
        if (
            verification is None
            or verification.status != "SUCCEEDED"
            or verification.result.get("exit_code") != 0
            or verification.result.get("command_hash") != self.command_hash
            or verification.result.get("manifest") != manifest
            or (verification.result.get("source_commit"), verification.result.get("target_commit"))
            != (source, target)
        ):
            raise IntegrationError("approval lacks actual current aggregate verification")

    def _epic_complete(self, epic, op, merged, *, sync):
        with self.store.transaction():
            epic = self.store.get_epic(epic.id)
            current = self.worktrees.verify_owned_worktree(epic)
            self._patch(epic, **({"current_commit": current} if sync else {"merge_commit": merged}))
            return self._finish(
                op,
                "SUCCEEDED",
                merge_commit=merged,
                requires_reconciliation=not sync and current != op.result["source_commit"],
            )

    def sync_epic_with_main(self, actor, run_id, *, key):
        """Explicit prerequisite synchronization, separate from delivery to main."""
        return self._epic_merge(actor, run_id, key, sync=True)

    def merge_epic_to_main(self, actor, run_id, *, key):
        return self._epic_merge(actor, run_id, key, sync=False)

    def _epic_merge(self, actor, run_id, key, *, sync):
        kind = "sync_epic_with_main" if sync else "merge_epic_to_main"
        with self._lock():
            epic = self._epic(actor, run_id)
            branch = epic.branch if sync else "main"
            path = Path(epic.worktree_path) if sync else self.worktrees.settings.repository
            op = self._epic_prior(epic, kind, key)
            if op:
                self.worktrees.verify_owned_worktree(epic)
                self.git.inspect(path, branch, clean=False)
                if op.status not in {"SUCCEEDED", "PENDING"}:
                    raise IntegrationError("merge needs conflict/input reconciliation")
                result = op.result
                merged = self.git.find_operation_merge(
                    branch, op.id, result["target_commit"], result["source_commit"]
                )
                if sync and result.get("merge_commit") == result["target_commit"]:
                    merged = result["target_commit"]
                if merged:
                    if not self.git.contains_commit(branch, merged):
                        raise IntegrationError("merge result no longer exists in destination")
                    if op.status == "SUCCEEDED":
                        if op.result.get("merge_commit") != merged:
                            raise IntegrationError("recorded merge and Git disagree")
                        return op
                    return self._epic_complete(epic, op, merged, sync=sync)
                if op.status == "SUCCEEDED" or self.git.head(branch) != result["target_commit"]:
                    raise IntegrationError(
                        "Git outcome requires reconciliation; do not repeat merge"
                    )
            with self.store.transaction():
                epic = self._epic(actor, run_id)
                epic_head, main_head = self._epic_pair(epic)
                source, target = (main_head, epic_head) if sync else (epic_head, main_head)
                if op and (source, target) != (
                    op.result["source_commit"],
                    op.result["target_commit"],
                ):
                    raise IntegrationError("pending merge inputs changed")
                if sync:
                    if epic.status not in {E.ACTIVE, E.CHANGES_REQUESTED, E.APPROVED}:
                        raise IntegrationError("epic cannot synchronize in its current phase")
                else:
                    self._epic_approval(epic, source, target)
                    if not self.git.contains_commit(epic.branch, target):
                        raise IntegrationError("epic must include current main before delivery")
                    if epic.merge_commit is not None:
                        raise IntegrationError("epic already merged; reconcile existing operation")
                if op is None:
                    op = Operation(
                        project_id=epic.project_id,
                        epic_run_id=epic.id,
                        kind=kind,
                        idempotency_key=key,
                        result=dict(
                            source_commit=source,
                            target_commit=target,
                            manifest_hash=None
                            if sync
                            else self._manifest_hash(self._manifest(epic)),
                        ),
                    )
                    self.store.add_operation(op)
                states = StateService(self.store)
                if (
                    sync
                    and epic.status == E.APPROVED
                    and (
                        not self.git.contains_commit(epic.branch, source)
                        or (epic.approved_source_commit, epic.approved_target_commit)
                        != (epic_head, main_head)
                    )
                ):
                    epic = states.transition_epic(
                        epic.id,
                        E.CHANGES_REQUESTED,
                        expected=E.APPROVED,
                        event_id=f"epic-sync-invalidate:{op.id}",
                        actor=actor,
                        facts=VerifiedFacts(reason="main synchronization invalidates older review"),
                    )
                if not sync and epic.status == E.APPROVED:
                    epic = states.transition_epic(
                        epic.id,
                        E.MERGING,
                        expected=E.APPROVED,
                        event_id=f"epic-merge-start:{op.id}",
                        actor=actor,
                        facts=VerifiedFacts(source_commit=source, target_commit=target),
                    )
            with self.store.transaction():
                fresh = self._epic(actor, run_id)
                if self._epic_pair(fresh) != (epic_head, main_head):
                    raise IntegrationError("Git changed before protected main integration")
                if not sync:
                    self._epic_approval(fresh, source, target)
                if sync and self.git.contains_commit(epic.branch, source):
                    return self._epic_complete(fresh, op, target, sync=True)
                try:
                    self.git.merge_commit(path, source, op.id)
                except GitError:
                    if self.git.in_progress(path):
                        return self._finish(op, "CONFLICT", error="MERGE_CONFLICT")
                    raise
                merged = self.git.find_operation_merge(branch, op.id, target, source)
                if merged is None:
                    raise IntegrationError("Git outcome unknown; reconcile before retry")
                return self._epic_complete(fresh, op, merged, sync=sync)

    def verify_main_merge(self, actor, run_id, *, merge_key, key):
        """Persist final verification; leave Epic MERGING for the completion service."""
        if not self.test_command:
            raise IntegrationError("operator test command is not configured")
        with self._lock():
            epic = self._epic(actor, run_id)
            merge = self._epic_prior(epic, "merge_epic_to_main", merge_key)
            if epic.status != E.MERGING or merge is None or merge.status != "SUCCEEDED":
                raise IntegrationError("final verification requires a registered main merge")
            merged = merge.result.get("merge_commit")
            source, target = self._epic_pair(epic)
            manifest = self._manifest(epic)
            if (
                epic.merge_commit != merged
                or target != merged
                or source != merge.result["source_commit"]
                or self.git.find_operation_merge(
                    "main", merge.id, merge.result["target_commit"], source
                )
                != merged
                or merge.result.get("requires_reconciliation")
                or self._manifest_hash(manifest) != merge.result.get("manifest_hash")
            ):
                raise IntegrationError("final verification must cover the exact actual merge")
            request = dict(
                merge_key=merge_key,
                merge_commit=merged,
                source_commit=source,
                target_commit=target,
                command_hash=self.command_hash,
                manifest_hash=self._manifest_hash(manifest),
            )
            old = self._epic_prior(epic, "verify_main_merge", key)
            if old:
                if any(old.result.get(k) != v for k, v in request.items()):
                    raise IntegrationError("final verification key is stale")
                if old.status == "PENDING":
                    raise IntegrationError("final test outcome unknown; use a new key")
                return old
            op = Operation(
                project_id=epic.project_id,
                epic_run_id=epic.id,
                kind="verify_main_merge",
                idempotency_key=key,
                result=request,
            )
            self.store.add_operation(op)
            error, code = self._execute(
                self.worktrees.settings.repository,
                lambda: (
                    self._epic_pair(self._epic(actor, run_id)) == (source, target)
                    and self._manifest(self.store.get_epic(run_id)) == manifest
                ),
            )
            return self._finish(op, "FAILED" if error else "SUCCEEDED", error=error, exit_code=code)
