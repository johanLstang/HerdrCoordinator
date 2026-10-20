"""Own-task commit broker for a verified, filesystem-restricted native Worker."""

import hashlib
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath

from orchestrator.application.git_integration_service import GitIntegrationService
from orchestrator.application.runtime_lifecycle_service import RuntimeLifecycleService
from orchestrator.application.state_service import StateService
from orchestrator.application.worker_slots import WorkerSlots
from orchestrator.domain.codex_execution import CodexExecutionPolicy
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Role
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import canonical_json


class WorkerCommitError(RuntimeError):
    pass


class WorkerCommitService:
    KIND = "worker_commit"
    MAX_BYTES = 8 * 1024 * 1024
    MAX_FILES = 256

    def __init__(self, settings, store, herdr, principal, *, client_parent_pid,
                 processes=None):
        self.settings, self.store, self.herdr, self.principal = settings, store, herdr, principal
        self.client_parent_pid = client_parent_pid
        self.integration = GitIntegrationService(settings, store)
        self.worktrees, self.git = self.integration.worktrees, self.integration.git
        self.lifecycle = RuntimeLifecycleService(settings, store, herdr, processes=processes)
        self.slots = WorkerSlots(settings, store, processes=processes)
        policy = getattr(herdr, "execution_policy", None)
        if not isinstance(policy, CodexExecutionPolicy) or policy.workspace_access != "write":
            raise WorkerCommitError("WORKER_COMMIT_REQUIRES_RESTRICTED_PROFILE")
        if principal is None or principal.role != Role.WORKER or not principal.task_run_id:
            raise WorkerCommitError("WORKER_COMMIT_PRINCIPAL_REQUIRED")

    def _scope(self, actor):
        if actor != self.principal or actor is None or actor.role != Role.WORKER:
            raise WorkerCommitError("WORKER_COMMIT_SCOPE_DENIED")
        task = self.store.get_task(actor.task_run_id)
        if task is None:
            raise WorkerCommitError("WORKER_COMMIT_TASK_MISSING")
        StateService.authorize_scope(actor, task)
        epic = self.store.get_epic(task.epic_run_id)
        if epic is None or epic.status != EpicState.ACTIVE or task.completed_at is not None:
            raise WorkerCommitError("WORKER_COMMIT_PARENT_NOT_ACTIVE")
        self.worktrees.verify_owned_worktree(task)
        self.slots.verify(task)
        self.lifecycle._busy(task)
        # Even a read-only `git status` may run a configured clean/process filter
        # selected by Worker-written attributes. Deny it before any such read.
        if self.git.run("config", "--get-regexp", r"^filter\.", missing=True):
            raise WorkerCommitError("WORKER_COMMIT_FILTER_CONFIGURATION_DENIED")
        start = self.store.get_operation(task.project_id, "start_runtime", task.id)
        assignment = self.store.get_operation(task.project_id, "task_start", task.task_id)
        if assignment is None or assignment.status != "SUCCEEDED" or (
            assignment.result.get("stage") != "WORKING" or assignment.task_run_id != task.id
            or assignment.epic_run_id != task.epic_run_id
        ):
            raise WorkerCommitError("WORKER_COMMIT_ASSIGNMENT_UNVERIFIED")
        binding = start.result.get("binding") if start is not None else None
        if start is None or start.status != "SUCCEEDED" or (
            start.result.get("stage") != "READY" or start.task_run_id != task.id
            or start.epic_run_id != task.epic_run_id
            or start.result.get("name") != task.worker_agent_id
            or start.result.get("cwd") != task.worktree_path
            or start.result.get("branch") != task.branch
            or start.result.get("server_session") != task.herdr_server_session
            or task.herdr_server_session != self.herdr.server_session
            or start.result.get("sandbox") != self.herdr.sandbox
            or start.result.get("mcp_configuration") != self.herdr.mcp_fingerprint
            or not isinstance(binding, dict)
            or set(binding) != {"workspace_id", "tab_id", "pane_id", "terminal_id"}
            or any(getattr(task, "herdr_" + k) != v for k, v in binding.items())
        ):
            raise WorkerCommitError("WORKER_COMMIT_REGISTRATION_UNVERIFIED")
        live = self.lifecycle._live(task, start)
        if live is None or not task.codex_session_id or (
            live["session_id"] != task.codex_session_id
            or live["status"] not in {"working", "idle", "done"}
            or type(self.client_parent_pid) is not int
            or self.client_parent_pid not in {p["pid"] for p in live["processes"]}
        ):
            raise WorkerCommitError("WORKER_COMMIT_CLIENT_RUNTIME_UNVERIFIED")
        return task, start

    def _save(self, op, *, stage, **changes):
        with self.store.transaction():
            current = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
            if current is None or current.id != op.id:
                raise WorkerCommitError("WORKER_COMMIT_INTENT_CHANGED")
            for key in {"parent", "input_hash", "message", "actor", "session_id", "tree",
                        "commit", "configuration", "author", "timestamp"}:
                if key in changes and key in current.result and current.result[key] != changes[key]:
                    raise WorkerCommitError("WORKER_COMMIT_HISTORY_IMMUTABLE")
            updated = current.model_copy(update=dict(
                status="SUCCEEDED" if stage == "COMMITTED" else current.status,
                result=current.result | changes | dict(stage=stage), updated_at=utc_now(),
            ))
            self.store.update_operation(updated)
            return updated

    @staticmethod
    def _read_file(root, name):
        """Open every component beneath the owned root without following symlinks."""
        parts = PurePosixPath(name).parts
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
            with os.fdopen(fd, "rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or (
                    before.st_size > WorkerCommitService.MAX_BYTES
                ):
                    raise WorkerCommitError("WORKER_COMMIT_FILE_TYPE_OR_SIZE_DENIED")
                data = source.read(WorkerCommitService.MAX_BYTES + 1)
                after = os.fstat(source.fileno())
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_ino, after.st_size, after.st_mtime_ns
                ) or len(data) != before.st_size:
                    raise WorkerCommitError("WORKER_COMMIT_INPUT_CHANGED")
                return (0o100755 if before.st_mode & 0o111 else 0o100644), data
        finally:
            os.close(directory)

    def _snapshot(self, task):
        path = Path(task.worktree_path)
        self.git.inspect(path, task.branch, clean=False)
        if self.git.in_progress(path) or self.git.unsafe_index_paths(path):
            raise WorkerCommitError("WORKER_COMMIT_GIT_STATE_UNVERIFIED")
        changes = self.git.working_changes(path)
        if not changes or len(changes) > self.MAX_FILES:
            raise WorkerCommitError("WORKER_COMMIT_CHANGES_REQUIRED")
        files, summary, total = {}, {}, 0
        for change in changes:
            name = change.path
            parts = PurePosixPath(name).parts
            if not parts or PurePosixPath(name).is_absolute() or (
                any(p in {"..", ".git", ".codex", ".agents", ".aws"} for p in parts)
                or any(c in name for c in "\x00\r\n") or len(name) > 4096
                or change.index_status not in {" ", "?"}
                or change.original_path is not None
            ):
                raise WorkerCommitError("WORKER_COMMIT_PATH_OR_STAGED_INPUT_DENIED")
            if change.worktree_status == "D":
                files[name], summary[name] = None, None
                continue
            try:
                entry = self._read_file(path, name)
            except OSError:
                raise WorkerCommitError("WORKER_COMMIT_FILE_UNVERIFIED") from None
            mode, data = entry
            total += len(data)
            if total > self.MAX_BYTES:
                raise WorkerCommitError("WORKER_COMMIT_SIZE_LIMIT")
            files[name] = entry
            summary[name] = dict(mode=mode, sha256=hashlib.sha256(data).hexdigest())
        return files, summary, hashlib.sha256(canonical_json(summary).encode()).hexdigest()

    def _index_matches(self, path, commit):
        return self.git.run("diff", "--cached", "--quiet", commit, "--", cwd=path,
                            missing=True) is not None

    def _commit_proof(self, op):
        commit = self.git.require_commit(op.result["commit"])
        if self.git.parents(commit) != (op.result["parent"],) or (
            self.git.run("rev-parse", commit + "^{tree}").strip() != op.result["tree"]
            or "herdr-worker-commit:" + op.id not in self.git.run(
                "show", "-s", "--format=%B", commit,
            ).splitlines()
        ):
            raise WorkerCommitError("WORKER_COMMIT_OBJECT_UNVERIFIED")

    def status(self, actor):
        task, _ = self._scope(actor)
        path = Path(task.worktree_path)
        return dict(task_run_id=task.id, branch=task.branch,
                    commit=self.git.inspect(path, task.branch, clean=False),
                    clean=not self.git.working_changes(path))

    def commit(self, actor, key, message):
        if not isinstance(key, str) or not key or len(key) > 128 or (
            any(c in key for c in "\x00\r\n")
            or not isinstance(message, str) or not message.strip() or len(message) > 4096
            or "\x00" in message or "herdr-worker-commit:" in message
        ):
            raise WorkerCommitError("WORKER_COMMIT_ARGUMENT_DENIED")
        with self.integration._lock():
            task, start = self._scope(actor)
            path = Path(task.worktree_path)
            named = hashlib.sha256(canonical_json([task.id, key]).encode()).hexdigest()
            op = self.store.get_operation(task.project_id, self.KIND, named)
            if op is not None and (op.task_run_id != task.id or op.result["message"] != message):
                raise WorkerCommitError("WORKER_COMMIT_KEY_CONFLICT")
            if (op is None or op.status != "SUCCEEDED") and (
                task.internal_status != TaskState.WORKING
            ):
                raise WorkerCommitError("WORKER_COMMIT_TASK_NOT_WORKING")
            if op is None:
                if any(o.task_run_id == task.id and o.status == "PENDING"
                       for o in self.store.get_operations(task.epic_run_id, kind=self.KIND)):
                    raise WorkerCommitError("WORKER_COMMIT_PENDING_USE_ORIGINAL_KEY")
                parent = self.git.head(task.branch)
                if not self._index_matches(path, parent):
                    raise WorkerCommitError("WORKER_COMMIT_INDEX_NOT_CLEAN")
                _, summary, digest = self._snapshot(task)
                author = [self.git.run("config", "user." + k).strip() for k in ("name", "email")]
                if any(not v or len(v) > 256 or any(c in v for c in "\x00\r\n") for v in author):
                    raise WorkerCommitError("WORKER_COMMIT_AUTHOR_UNVERIFIED")
                op = Operation(project_id=task.project_id, epic_run_id=task.epic_run_id,
                               task_run_id=task.id, kind=self.KIND, idempotency_key=named,
                               result=dict(stage="INTENT", message=message, parent=parent,
                                           input_hash=digest, files=summary, author=author,
                                           actor=actor.model_dump(mode="json"),
                                           session_id=task.codex_session_id,
                                           configuration=self.herdr.mcp_fingerprint,
                                           start_operation_id=start.id))
                op = op.model_copy(update={"result": op.result | dict(
                    timestamp=f"{int(op.created_at.timestamp())} +0000",
                )})
                self.store.add_operation(op)
            if op.result["actor"] != actor.model_dump(mode="json") or (
                op.result["session_id"] != task.codex_session_id
                or op.result["configuration"] != self.herdr.mcp_fingerprint
                or op.result["start_operation_id"] != start.id
            ):
                raise WorkerCommitError("WORKER_COMMIT_BINDING_CHANGED")
            head = self.git.head(task.branch)
            if head not in {op.result["parent"], op.result.get("commit")} or (
                op.result["stage"] in {"INTENT", "TREE_READY"}
                and head != op.result["parent"]
            ):
                raise WorkerCommitError("WORKER_COMMIT_BRANCH_CHANGED")
            if op.result["stage"] in {"INTENT", "TREE_READY", "REF_PENDING"} and (
                self.git.head(task.branch) == op.result["parent"]
            ):
                files, _, digest = self._snapshot(task)
                if digest != op.result["input_hash"] or not self._index_matches(
                    path, op.result["parent"],
                ):
                    raise WorkerCommitError("WORKER_COMMIT_INPUT_CHANGED")
            if op.result["stage"] == "INTENT":
                with tempfile.TemporaryDirectory(prefix="worker-index-",
                                                 dir=self.settings.sqlite_path.parent) as scratch:
                    tree = self.git.worker_tree(path, op.result["parent"], files,
                                                Path(scratch) / "index")
                op = self._save(op, stage="TREE_READY", tree=tree)
            if op.result["stage"] == "TREE_READY":
                commit = self.git.worker_commit_object(
                    path, op.result["tree"], op.result["parent"], message, op.id,
                    op.result["author"], op.result["timestamp"],
                )
                op = self._save(op, stage="REF_PENDING", commit=commit)
            self._commit_proof(op)
            if op.result["stage"] == "REF_PENDING":
                if self.git.head(task.branch) == op.result["parent"]:
                    self._scope(actor)
                    if self._snapshot(task)[2] != op.result["input_hash"]:
                        raise WorkerCommitError("WORKER_COMMIT_INPUT_CHANGED")
                    self.git.worker_update_ref(path, task.branch, op.result["commit"],
                                               op.result["parent"])
                if self.git.head(task.branch) != op.result["commit"]:
                    raise WorkerCommitError("WORKER_COMMIT_BRANCH_CHANGED")
                op = self._save(op, stage="INDEX_PENDING")
            if self.git.head(task.branch) != op.result["commit"]:
                raise WorkerCommitError("WORKER_COMMIT_BRANCH_CHANGED")
            if op.result["stage"] == "INDEX_PENDING":
                if not self._index_matches(path, op.result["commit"]):
                    if not self._index_matches(path, op.result["parent"]):
                        raise WorkerCommitError("WORKER_COMMIT_INDEX_CHANGED")
                    self.git.worker_sync_index(path, op.result["commit"])
                if not self._index_matches(path, op.result["commit"]):
                    raise WorkerCommitError("WORKER_COMMIT_INDEX_UNCONFIRMED")
                op = self._save(op, stage="COMMITTED")
            return dict(status="COMMITTED", operation_id=op.id, commit=op.result["commit"],
                        branch=task.branch, files=list(op.result["files"]),
                        clean=not self.git.working_changes(path))
