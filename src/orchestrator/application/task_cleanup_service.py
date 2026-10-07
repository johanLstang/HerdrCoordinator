"""Explicit cleanup of verified, inactive task worktrees; retain all branches/history."""

from pathlib import Path

from orchestrator.application.git_integration_service import GitIntegrationService, IntegrationError
from orchestrator.domain.models import Operation, TaskRun
from orchestrator.domain.policy import Role


class TaskCleanupService(GitIntegrationService):
    def __init__(self, *args, inactivity_probe=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Trusted runtime adapter, not a boolean/tool argument supplied by a Worker.
        self.inactivity_probe = inactivity_probe

    def _cleanup_task(self, actor, run_id):
        task = self.store.get_task(run_id)
        if (
            task is None
            or actor.role != Role.INTEGRATION
            or actor.project_id != task.project_id
            or actor.epic_run_id != task.epic_run_id
        ):
            raise IntegrationError("only the registered Integration may clean up this task")
        epic = self.store.get_epic(task.epic_run_id)
        if epic is None:
            raise IntegrationError("epic run is missing")
        return task, epic

    def _inactive(self, task):
        if task.worker_slot is not None:
            raise IntegrationError("worker slot is still reserved; confirm release before cleanup")
        refs = self.store.get_references(task.epic_run_id)
        registered = any(
            (task.worker_agent_id, task.herdr_workspace_id, task.codex_session_id)
        ) or any(
            ref.task_run_id == task.id and ref.provider.casefold() in {"herdr", "codex"}
            for ref in refs
        )
        if registered:
            try:
                inactive = self.inactivity_probe is not None and self.inactivity_probe(task) is True
            except Exception:
                inactive = False
            if not inactive:
                raise IntegrationError("runtime inactivity is not confirmed by a trusted adapter")

    def _unshared(self, task):
        path = Path(task.worktree_path)
        for other in self.store.get_runs():
            if not (isinstance(other, TaskRun) and other.id == task.id) and (
                other.branch == task.branch or Path(other.worktree_path).resolve() == path
            ):
                raise IntegrationError("another persisted run claims this Git resource")

    def _ownership(self, task):
        creation = self.store.get_operation(task.project_id, "create_task_worktree", task.id)
        if creation is None or creation.status != "SUCCEEDED":
            raise IntegrationError("cleanup ownership is not registered")
        expected = dict(
            repository=str(self.worktrees.settings.repository),
            git_common_dir=str(self.git.common_dir),
            branch=task.branch,
            worktree_path=task.worktree_path,
            base_commit=task.base_commit,
        )
        if (
            creation.task_run_id != task.id
            or creation.epic_run_id != task.epic_run_id
            or any(creation.result.get(k) != v for k, v in expected.items())
            or self.git.owner(task.branch) != creation.id
            or self.git.head(task.branch) != task.approved_source_commit
        ):
            raise IntegrationError("cleanup ownership or branch changed")
        path = Path(task.worktree_path)
        self.worktrees._path(task.project_id, path.name, path)
        return creation

    def _present(self, task):
        path = Path(task.worktree_path)
        trees = self.git.worktrees()
        # Missing resources can only be attributed to a prior durable cleanup intent.
        if not path.exists() and not path.is_symlink() and path not in trees:
            if any(entry.get("branch") == f"refs/heads/{task.branch}" for entry in trees.values()):
                raise IntegrationError("task branch moved to another worktree")
            return False
        self.worktrees.verify_owned_worktree(task)
        if (
            path.is_symlink()
            or self.git.working_changes(path)
            or self.git.ignored_paths(path)
            or self.git.unsafe_index_paths(path)
            or self.git.in_progress(path)
            or "locked" in trees[path]
        ):
            raise IntegrationError("cleanup requires clean unlocked worktree without ignored files")
        return True

    def remove_task_worktree(self, actor, task_run_id, *, key):
        kind = "remove_task_worktree"
        with self._lock():
            with self.store.transaction():
                task, epic = self._cleanup_task(actor, task_run_id)
                self.worktrees.verify_owned_worktree(epic)
                proof = self._delivery_proof(task, epic)
                creation = self._ownership(task)
                request = dict(
                    worktree_path=task.worktree_path,
                    branch=task.branch,
                    creation_operation_id=creation.id,
                    delivery=proof,
                    retention="retain-branches-and-history",
                )
                op = self._prior(task, kind, key)
                if op and any(op.result.get(k) != v for k, v in request.items()):
                    raise IntegrationError("cleanup key refers to different resources or delivery")
                present = self._present(task)
                if op:
                    if op.status == "SUCCEEDED":
                        if present:
                            raise IntegrationError(
                                "cleaned resource reappeared; do not remove again"
                            )
                        return op
                    if op.status != "PENDING":
                        raise IntegrationError("cleanup needs reconciliation")
                else:
                    # New keys may reuse recorded cleanup, never an unknown disappearance.
                    old = [
                        item
                        for item in self.store.get_operations(epic.id, kind=kind)
                        if item.task_run_id == task.id
                        and item.status == "SUCCEEDED"
                        and all(item.result.get(k) == v for k, v in request.items())
                    ]
                    if not present:
                        if old:
                            return old[-1]
                        raise IntegrationError("resource disappeared without a cleanup intent")
                    self._inactive(task)
                    self._unshared(task)
                    op = Operation(
                        project_id=task.project_id,
                        epic_run_id=epic.id,
                        task_run_id=task.id,
                        kind=kind,
                        idempotency_key=key,
                        result=request,
                    )
                    self.store.add_operation(op)
            # The durable intent survives process death before/after Git removal.
            with self.store.transaction():
                task, epic = self._cleanup_task(actor, task_run_id)
                if self._delivery_proof(task, epic) != proof:
                    raise IntegrationError("task delivery changed before cleanup")
                self._inactive(task)
                self._unshared(task)
                self._ownership(task)
                present = self._present(task)
                if present:
                    self.git.remove_worktree(Path(task.worktree_path))
                if self._present(task):
                    raise IntegrationError("Git cleanup outcome unknown; reconcile before retry")
                return self._finish(op, "SUCCEEDED", worktree_removed=True, branch_retained=True)
