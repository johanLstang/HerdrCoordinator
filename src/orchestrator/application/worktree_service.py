import re
from pathlib import Path

from orchestrator.adapters.git import GitAdapter
from orchestrator.application.state_service import StateError, StateService
from orchestrator.config import Settings
from orchestrator.domain.models import EpicRun, Operation, TaskRun, utc_now
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import EpicState
from orchestrator.event_log import EventLog
from orchestrator.persistence.store import StateStore


class WorktreeError(RuntimeError):
    """Rejected creation or unsafe recovery; existing resources are preserved."""


def slug(identity: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,79}", identity):
        raise WorktreeError("identity must be a bounded alphanumeric identifier")
    if re.fullmatch(r"[EF]-[0-9]+", identity, re.IGNORECASE):
        return identity.lower().replace("-", "")
    return identity.lower()


class WorktreeService:
    """Internal role-bound service. Creation does not start agents or change run state."""

    def __init__(self, settings: Settings, store: StateStore, *, log: EventLog | None = None):
        self.settings, self.store = settings, store
        self.git = GitAdapter(settings.repository)
        self.log = log or EventLog()

    def _path(self, project_id: str, name: str, requested: Path | None) -> Path:
        root = self.settings.worktree_root
        expected = root / slug(project_id) / name
        path = (requested if requested is not None else expected).resolve()
        if (
            root.resolve() != root
            or root not in path.parents
            or path != expected
            or any(part in {".git", ".codex", ".agents", ".aws"} for part in path.parts)
        ):
            raise WorktreeError("worktree path must match its identity under the configured root")
        return path

    def create_epic_worktree(
        self, actor: Actor, *, epic_id: str, run_id: str, path: Path | None = None
    ) -> EpicRun:
        if actor.role != Role.COORDINATOR:
            raise WorktreeError("only Coordinator may create an epic worktree")
        branch = f"feature/epic-{slug(epic_id)}"
        destination = self._path(actor.project_id, f"epic-{slug(epic_id)}", path)
        record = EpicRun(
            id=run_id,
            project_id=actor.project_id,
            epic_id=epic_id,
            branch=branch,
            worktree_path=str(destination),
        )
        if actor.epic_run_id is not None and actor.epic_run_id != run_id:
            raise WorktreeError("principal is bound to another epic")
        return self._create(record, "create_epic_worktree", "main", self.settings.repository)

    def create_task_worktree(
        self,
        actor: Actor,
        *,
        epic_run_id: str,
        task_id: str,
        run_id: str,
        path: Path | None = None,
    ) -> TaskRun:
        if actor.role != Role.INTEGRATION or actor.epic_run_id != epic_run_id:
            raise WorktreeError("only the epic's Integration principal may create tasks")
        epic = self.store.get_epic(epic_run_id)
        if epic is None:
            raise WorktreeError("epic run does not exist")
        try:
            StateService.authorize_scope(actor, epic)
        except StateError:
            raise WorktreeError("epic scope is not authorized") from None
        operation = self.store.get_operation(epic.project_id, "create_epic_worktree", epic.id)
        if operation is None or operation.status != "SUCCEEDED":
            raise WorktreeError("epic worktree creation is not complete")
        self._verify(epic, operation, complete=True)
        branch = f"task/{slug(epic.epic_id)}-{slug(task_id)}"
        destination = self._path(
            actor.project_id, f"task-{slug(epic.epic_id)}-{slug(task_id)}", path
        )
        record = TaskRun(
            id=run_id,
            project_id=actor.project_id,
            epic_run_id=epic_run_id,
            task_id=task_id,
            branch=branch,
            worktree_path=str(destination),
        )
        return self._create(
            record, "create_task_worktree", epic.branch, Path(epic.worktree_path), epic.id
        )

    def _load(self, record: EpicRun | TaskRun):
        return (
            self.store.get_epic(record.id)
            if isinstance(record, EpicRun)
            else self.store.get_task(record.id)
        )

    def verify_owned_worktree(self, record: EpicRun | TaskRun) -> str:
        """Verify persisted creation ownership before returning the current Git HEAD."""
        kind = "create_epic_worktree" if isinstance(record, EpicRun) else "create_task_worktree"
        operation = self.store.get_operation(record.project_id, kind, record.id)
        if operation is None or operation.status != "SUCCEEDED":
            raise WorktreeError("worktree creation is not complete")
        self._verify(record, operation, complete=True)
        return self.git.inspect(Path(record.worktree_path), record.branch, clean=False)

    def _same(self, actual: EpicRun | TaskRun, requested: EpicRun | TaskRun) -> bool:
        fields = {
            "id",
            "project_id",
            "branch",
            "worktree_path",
            "epic_id",
            "epic_run_id",
            "task_id",
        }
        return all(
            getattr(actual, field, None) == getattr(requested, field, None) for field in fields
        )

    def _unoccupied(self, record: EpicRun | TaskRun) -> None:
        path = Path(record.worktree_path)
        trees = self.git.worktrees()
        if (
            self.git.head(record.branch) is not None
            or self.git.owner(record.branch) is not None
            or path.exists()
            or path.is_symlink()
            or path in trees
            or any(entry.get("branch") == f"refs/heads/{record.branch}" for entry in trees.values())
        ):
            raise WorktreeError("branch or worktree is occupied; no resource was adopted")

    def _verify(self, record: EpicRun | TaskRun, operation: Operation, *, complete: bool) -> bool:
        proof = operation.result
        if (
            operation.project_id != record.project_id
            or operation.epic_run_id
            != (record.id if isinstance(record, EpicRun) else record.epic_run_id)
            or operation.task_run_id != (record.id if isinstance(record, TaskRun) else None)
            or proof.get("repository") != str(self.settings.repository)
            or proof.get("git_common_dir") != str(self.git.common_dir)
            or proof.get("branch") != record.branch
            or proof.get("worktree_path") != record.worktree_path
            or proof.get("base_commit") != record.base_commit
        ):
            raise WorktreeError("stored creation ownership or base does not match")
        path = self._path(
            record.project_id,
            Path(record.worktree_path).name,
            Path(record.worktree_path),
        )
        owner, head = self.git.owner(record.branch), self.git.head(record.branch)
        trees = self.git.worktrees()
        matches = [
            p for p, entry in trees.items() if entry.get("branch") == f"refs/heads/{record.branch}"
        ]
        if owner != operation.id:
            if owner is not None or head is not None or path.exists() or path in trees or complete:
                raise WorktreeError("Git resource ownership is not verified")
            return False
        if matches and matches != [path]:
            raise WorktreeError("owned branch is checked out in another worktree")
        if path in trees:
            current = self.git.inspect(path, record.branch, clean=not complete)
            valid_base = (
                self.git.descends_from(record.branch, record.base_commit)
                if complete
                else current == record.base_commit
            )
            if not valid_base:
                raise WorktreeError("owned worktree no longer has its recorded base")
            return True
        if complete or path.exists() or path.is_symlink():
            raise WorktreeError("worktree is missing or contains unverified files")
        if head is not None and head != record.base_commit:
            raise WorktreeError("unfinished branch has changed; manual reconciliation required")
        return False

    def _source_head(self, record, branch: str, path: Path, epic_id: str | None) -> str:
        if epic_id is not None:
            epic = self.store.get_epic(epic_id)
            operation = self.store.get_operation(record.project_id, "create_epic_worktree", epic_id)
            if (
                epic is None
                or epic.project_id != record.project_id
                or epic.branch != branch
                or epic.worktree_path != str(path)
                or operation is None
                or operation.status != "SUCCEEDED"
                or epic.status
                not in {EpicState.PLANNED, EpicState.ACTIVE, EpicState.CHANGES_REQUESTED}
                or epic.completed_at is not None
            ):
                raise WorktreeError("source epic ownership changed")
            self._verify(epic, operation, complete=True)
        return self.git.inspect(path, branch, clean=True)

    def _create(
        self,
        requested,
        kind: str,
        source_branch: str,
        source_path: Path,
        source_epic_id: str | None = None,
    ):
        # Commit intent before any Git mutation. SQLite serializes creation attempts.
        with self.store.transaction():
            actual = self._load(requested)
            operation = self.store.get_operation(requested.project_id, kind, requested.id)
            if actual is not None:
                if not self._same(actual, requested) or operation is None:
                    raise WorktreeError("run identity already belongs to another resource")
            else:
                if operation is not None:
                    raise WorktreeError("creation intent has no owning run")
                self._unoccupied(requested)
                base = self._source_head(requested, source_branch, source_path, source_epic_id)
                actual = type(requested).model_validate(
                    requested.model_dump() | {"base_commit": base}
                )
                operation = Operation(
                    project_id=actual.project_id,
                    epic_run_id=actual.id if isinstance(actual, EpicRun) else actual.epic_run_id,
                    task_run_id=actual.id if isinstance(actual, TaskRun) else None,
                    kind=kind,
                    idempotency_key=actual.id,
                    result={
                        "repository": str(self.settings.repository),
                        "git_common_dir": str(self.git.common_dir),
                        "branch": actual.branch,
                        "worktree_path": actual.worktree_path,
                        "base_commit": base,
                    },
                )
                if isinstance(actual, EpicRun):
                    self.store.add_epic(actual)
                else:
                    self.store.add_task(actual)
                self.store.add_operation(operation)
        with self.store.transaction():
            actual = self._load(requested)
            operation = self.store.get_operation(requested.project_id, kind, requested.id)
            if operation.status == "SUCCEEDED":
                self._verify(actual, operation, complete=True)
                return actual
            if operation.status != "PENDING":
                raise WorktreeError("creation operation requires reconciliation")
            exists = self._verify(actual, operation, complete=False)
            if not exists:
                if (
                    self._source_head(actual, source_branch, source_path, source_epic_id)
                    != actual.base_commit
                ):
                    raise WorktreeError("source base changed before creation; reconcile intent")
                if self.git.owner(actual.branch) is None:
                    self.git.set_owner(actual.branch, operation.id)
                self.git.add_worktree(
                    Path(actual.worktree_path),
                    actual.branch,
                    actual.base_commit,
                    branch_exists=self.git.head(actual.branch) is not None,
                )
                self._verify(actual, operation, complete=False)
            completed = type(actual).model_validate(
                actual.model_dump() | {"current_commit": actual.base_commit}
            )
            done = Operation.model_validate(
                operation.model_dump() | {"status": "SUCCEEDED", "updated_at": utc_now()}
            )
            self.store.complete_worktree_creation(completed, done)
        self.log.emit("git.worktree.create", "INFO", "owned worktree verified", kind=kind)
        return completed
