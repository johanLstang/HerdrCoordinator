"""Read-only scheduling advice from trusted specs, a fresh board and actual delivery proof."""

from copy import deepcopy
from pathlib import Path

from pydantic import ValidationError

from orchestrator.adapters.git import GitError
from orchestrator.application.git_integration_service import GitIntegrationService, IntegrationError
from orchestrator.application.teamplayer_evidence import TeamPlayerEvidence
from orchestrator.application.worktree_service import WorktreeError, slug
from orchestrator.domain.models import EpicRun, TaskRun
from orchestrator.domain.policy import Role
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.teamplayer import BoardStatus
from orchestrator.domain.worker_contracts import ContractError, LocalTaskSpec, canonical_json
from orchestrator.persistence.store import StoreError


class TaskSelectionError(RuntimeError):
    """Safe scope/configuration failure; no upstream text or credentials."""


class TaskSelectionService:
    def __init__(
        self,
        settings,
        store,
        reader,
        *,
        specs,
        task_order,
        epic_prerequisites,
        delivery_contexts=None,
        scopes=None,
        codex=None,
        processes=None,
        prerequisite_probe=None,
    ):
        self.settings, self.store, self.reader = settings, store, reader
        # All configuration is supplied by the operator, never in MCP tool arguments.
        self.specs = deepcopy(dict(specs))
        self.order = tuple(task_order)
        self.epic_prerequisites = deepcopy(dict(epic_prerequisites))
        self.delivery_contexts, self.scopes = dict(delivery_contexts or {}), dict(scopes or {})
        self.codex, self.processes, self.prerequisite_probe = codex, processes, prerequisite_probe
        if len(set(self.order)) != len(self.order) or set(self.order) != set(self.specs):
            raise TaskSelectionError("TASK_SELECTION_ORDER_INVALID")
        self.integration = GitIntegrationService(settings, store)
        self.git = self.integration.git

    def _scope(self, actor, epic_run_id):
        epic = self.store.get_epic(epic_run_id)
        if (
            actor is None
            or actor.role != Role.INTEGRATION
            or epic is None
            or actor.project_id != epic.project_id
            or actor.epic_run_id != epic.id
        ):
            raise TaskSelectionError("TASK_SELECTION_SCOPE_DENIED")
        return epic

    async def get_next(self, actor, epic_run_id):
        # Reject a foreign principal before even reading TeamPlayer.
        self._scope(actor, epic_run_id)
        board = await self.reader.read_project()
        return self.evaluate(actor, epic_run_id, board)

    def _spec(self, row, epic, board):
        """Names/descriptions do not establish executable scope or identity."""
        if row.binding is None or row.id not in self.specs:
            raise TaskSelectionError("TASK_SPEC_MISSING")
        try:
            raw = self.specs[row.id]
            spec = LocalTaskSpec.model_validate(
                raw.model_dump() if isinstance(raw, LocalTaskSpec) else raw
            )
            spec.bind(epic)
            slug(spec.task_id)
            if len(canonical_json(spec.model_dump(mode="json")).encode()) > 65536:
                raise ValueError
        except (ValidationError, ContractError, WorktreeError, TypeError, ValueError):
            raise TaskSelectionError("TASK_SPEC_INVALID") from None
        dependencies = [board.task(identity) for identity in row.dependencies]
        if any(d is None or d.binding is None for d in dependencies):
            raise TaskSelectionError("DEPENDENCY_BINDING_MISSING")
        if (
            spec.task_id != row.binding.local_id
            or spec.name != row.name
            or tuple(spec.acceptance_criteria) != row.acceptance_criteria
            or len(set(spec.dependencies)) != len(spec.dependencies)
            or set(spec.dependencies) != {d.binding.local_id for d in dependencies}
        ):
            raise TaskSelectionError("TASK_SPEC_BOARD_MISMATCH")
        return spec

    def _task_run(self, row, board, project_id):
        if row.binding is None:
            raise TaskSelectionError("DEPENDENCY_BINDING_MISSING")
        matches = [
            r
            for r in self.store.get_runs()
            if isinstance(r, TaskRun)
            and r.project_id == project_id
            and r.task_id == row.binding.local_id
        ]
        if len(matches) != 1:
            raise TaskSelectionError("DEPENDENCY_RUN_MISSING_OR_AMBIGUOUS")
        task = matches[0]
        epic = self.store.get_epic(task.epic_run_id)
        native_epic = board.epic(row.epic_id)
        if (
            epic is None
            or native_epic is None
            or native_epic.binding is None
            or epic.epic_id != native_epic.binding.local_id
        ):
            raise TaskSelectionError("DEPENDENCY_RUN_SCOPE_MISMATCH")
        if task.internal_status != TaskState.DONE:
            raise TaskSelectionError("DEPENDENCY_RUNTIME_NOT_DONE")
        evidence = TeamPlayerEvidence(
            self.settings, self.store, codex=self.codex, processes=self.processes
        )
        try:
            registered = evidence.spec(task)
            spec = self._spec(row, epic, board)
            if registered != spec:
                raise TaskSelectionError("DEPENDENCY_SPEC_CHANGED")
            status, proof, _ = evidence.task(task)
            if status != "Done":
                raise TaskSelectionError("DEPENDENCY_RUNTIME_NOT_DONE")
        except TaskSelectionError:
            raise
        except Exception:
            raise TaskSelectionError("DEPENDENCY_DELIVERY_UNVERIFIED") from None
        return epic, proof

    def _epic_proof(self, identity, board, current, actor):
        native = board.epic(identity)
        if native is None:
            raise TaskSelectionError("EPIC_DEPENDENCY_MISSING")
        if native.status != "Done":
            raise TaskSelectionError("EPIC_DEPENDENCY_NOT_DONE")
        if native.binding is None:
            raise TaskSelectionError("EPIC_DEPENDENCY_BINDING_MISSING")
        matches = [
            r
            for r in self.store.get_runs()
            if isinstance(r, EpicRun)
            and r.project_id == current.project_id
            and r.epic_id == native.binding.local_id
        ]
        if len(matches) != 1 or matches[0].id == current.id:
            raise TaskSelectionError("EPIC_DEPENDENCY_RUN_MISSING_OR_AMBIGUOUS")
        previous = matches[0]
        context = self.delivery_contexts.get(previous.id)
        if (
            previous.status != EpicState.DONE
            or context is None
            or context.repository != self.settings.repository
            or context.sqlite_path != self.settings.sqlite_path
        ):
            raise TaskSelectionError("EPIC_DEPENDENCY_PROOF_MISSING")
        rows = board.epic_tasks(identity)
        expected = self.scopes.get(previous.id)
        if (
            not expected
            or any(t.status != BoardStatus.DONE or t.binding is None for t in rows)
            or tuple(sorted(t.binding.local_id for t in rows)) != tuple(sorted(expected))
            or any(i.task_id in {identity, *(t.id for t in rows)} for i in board.issues)
        ):
            raise TaskSelectionError("EPIC_DEPENDENCY_SCOPE_UNVERIFIED")
        for row in rows:
            self._task_run(row, board, current.project_id)
        try:
            evidence = TeamPlayerEvidence(
                context, self.store, scopes=self.scopes, codex=self.codex, processes=self.processes
            )
            status, proof, _ = evidence.epic(actor, previous)
            if status != "Done" or not self.git.contains_commit(
                current.branch, proof["merge_commit"]
            ):
                raise ValueError
        except Exception:
            raise TaskSelectionError("EPIC_MAIN_DELIVERY_UNVERIFIED") from None
        return proof

    def evaluate(self, actor, epic_run_id, board):
        try:
            return self._evaluate(actor, epic_run_id, board)
        except (GitError, WorktreeError):
            raise TaskSelectionError("TASK_SELECTION_WORKTREE_UNVERIFIED") from None
        except IntegrationError:
            raise TaskSelectionError("TASK_SELECTION_BUSY") from None
        except StoreError:
            raise TaskSelectionError("TASK_SELECTION_STATE_UNAVAILABLE") from None

    def _evaluate(self, actor, epic_run_id, board):
        """No reservation, transition, journal write, HTTP write or Git mutation."""
        self._scope(actor, epic_run_id)
        if (
            board.project_id != self.reader.project_id
            or board.authenticated_user_id != self.reader.user_id
        ):
            raise TaskSelectionError("TASK_SELECTION_BOARD_SCOPE_MISMATCH")
        with self.integration._lock(), self.store.transaction():
            return self._evaluate_locked(actor, epic_run_id, board)

    def _evaluate_locked(self, actor, epic_run_id, board):
        """Internal F29 preflight under the shared Git lock and claim transaction."""
        if not self.store.db.in_transaction:
            raise TaskSelectionError("TASK_SELECTION_CLAIM_TRANSACTION_REQUIRED")
        self._scope(actor, epic_run_id)
        if (
            board.project_id != self.reader.project_id
            or board.authenticated_user_id != self.reader.user_id
        ):
            raise TaskSelectionError("TASK_SELECTION_BOARD_SCOPE_MISMATCH")
        epic = self._scope(actor, epic_run_id)
        native = [e for e in board.epics if e.binding and e.binding.local_id == epic.epic_id]
        if len(native) != 1:
            raise TaskSelectionError("TASK_SELECTION_EPIC_BINDING_MISSING")
        native = native[0]
        before = self.integration.worktrees.verify_owned_worktree(epic)
        main = self.git.inspect(self.settings.repository, "main", clean=False)
        issues = tuple(board.issues) + tuple(self.reader._issues(board.epics, board.tasks))
        shared = []
        if epic.status != EpicState.ACTIVE or epic.completed_at or native.status != "InProgress":
            shared.append({"code": "EPIC_NOT_ACTIVE", "related_id": native.id})
        if any(
            self.git.working_changes(path)
            or self.git.unsafe_index_paths(path)
            or self.git.in_progress(path)
            for path in (self.settings.repository, Path(epic.worktree_path))
        ):
            shared.append({"code": "EPIC_OR_MAIN_WORKTREE_UNSAFE", "related_id": native.id})
        prerequisites = self.epic_prerequisites.get(epic.epic_id)
        epic_proofs = {}
        if prerequisites is None:
            shared.append({"code": "EPIC_PREREQUISITES_UNSPECIFIED", "related_id": native.id})
        else:
            for identity in prerequisites:
                try:
                    epic_proofs[identity] = self._epic_proof(identity, board, epic, actor)
                except TaskSelectionError as error:
                    shared.append({"code": str(error), "related_id": identity})
        results = []
        for row in board.epic_tasks(native.id):
            blockers = list(shared)

            def block(code, related=None, blockers=blockers):
                entry = {"code": code, "related_id": related}
                if entry not in blockers:
                    blockers.append(entry)

            if row.status != BoardStatus.PENDING:
                block("TASK_NOT_PLANNED")
            if row.execution_owner_kind != "User" or row.responsible_user_id != self.reader.user_id:
                block("TASK_EXECUTION_OWNER_MISMATCH")
            for issue in issues:
                if issue.task_id == row.id:
                    block(issue.code, issue.related_id)
            spec = None
            try:
                spec = self._spec(row, epic, board)
            except TaskSelectionError as error:
                block(str(error))
            if row.binding and any(
                isinstance(r, TaskRun)
                and r.project_id == epic.project_id
                and r.task_id == row.binding.local_id
                for r in self.store.get_runs()
            ):
                block("TASK_ALREADY_OWNED")
            if spec:
                branch = f"task/{slug(epic.epic_id)}-{slug(spec.task_id)}"
                path = self.integration.worktrees._path(
                    epic.project_id, f"task-{slug(epic.epic_id)}-{slug(spec.task_id)}", None
                )
                if (
                    self.git.head(branch) is not None
                    or self.git.owner(branch) is not None
                    or path.exists()
                    or path.is_symlink()
                    or any(
                        t.get("branch") == f"refs/heads/{branch}"
                        for t in self.git.worktrees().values()
                    )
                ):
                    block("TASK_GIT_RESOURCES_OCCUPIED")
            dependencies = {}
            pending, visited = list(row.dependencies), set()
            while pending:
                identity = pending.pop()
                if identity in visited:
                    continue
                visited.add(identity)
                dependency = board.task(identity)
                if dependency is None:
                    block("DEPENDENCY_MISSING", identity)
                    continue
                pending.extend(dependency.dependencies)
                if dependency.status != BoardStatus.DONE:
                    block("DEPENDENCY_NOT_DONE", identity)
                    continue
                try:
                    delivered_epic, proof = self._task_run(dependency, board, epic.project_id)
                    if delivered_epic.id != epic.id:
                        if dependency.epic_id not in epic_proofs:
                            epic_proofs[dependency.epic_id] = self._epic_proof(
                                dependency.epic_id, board, epic, actor
                            )
                    dependencies[identity] = proof
                except TaskSelectionError as error:
                    block(str(error), identity)
            if spec and spec.external_prerequisites:
                try:
                    ready = (
                        self.prerequisite_probe is not None
                        and self.prerequisite_probe(spec) is True
                    )
                except Exception:
                    ready = False
                if not ready:
                    block("TASK_PREREQUISITE_UNVERIFIED")
            results.append(
                {
                    "task_id": row.id,
                    "local_id": row.binding.local_id if row.binding else None,
                    "priority": int(row.priority),
                    "version": row.version,
                    "runnable": not blockers,
                    "blockers": blockers,
                    "dependencies": dependencies,
                    "external_prerequisites": list(spec.external_prerequisites) if spec else [],
                }
            )
        if (
            self.integration.worktrees.verify_owned_worktree(epic) != before
            or self.git.head("main") != main
        ):
            raise TaskSelectionError("TASK_SELECTION_GIT_CHANGED")
        rank = {identity: index for index, identity in enumerate(self.order)}
        results.sort(
            key=lambda r: (-r["priority"], rank.get(r["task_id"], len(rank)), r["task_id"])
        )
        candidates = [r["task_id"] for r in results if r["runnable"]]
        return {
            "epic_run_id": epic.id,
            "epic_commit": before,
            "main_commit": main,
            "next_task_id": candidates[0] if candidates else None,
            "candidates": candidates,
            "tasks": results,
            "epic_dependencies": epic_proofs,
            "reservation": False,
        }
