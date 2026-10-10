"""Fresh project selection and durable single-epic reservation before F34 side effects."""

from uuid import uuid4

from orchestrator.application.coordinator_runtime_service import CoordinatorError
from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.git_integration_service import GitIntegrationService
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.teamplayer_evidence import TeamPlayerEvidence
from orchestrator.domain.coordinator import ProjectOperation
from orchestrator.domain.coordinator_contracts import RuntimeGateEvidence
from orchestrator.domain.models import EpicRun, utc_now
from orchestrator.domain.policy import Role
from orchestrator.domain.states import EpicState
from orchestrator.domain.teamplayer import BoardStatus
from orchestrator.domain.worker_contracts import canonical_json


class CoordinatorClaimService:
    KIND = "epic_claim"

    def __init__(
        self, runtime, reader, integration_factory, *, runtime_gate=None,
        delivery_contexts=None, scopes=None, prerequisite_probe=None,
    ):
        self.runtime, self.reader, self.factory = runtime, reader, integration_factory
        self.settings, self.store, self.spec = runtime.settings, runtime.store, runtime.spec
        self.integration = GitIntegrationService(self.settings, self.store)
        self.gate = runtime_gate
        self.prerequisite_probe = prerequisite_probe
        self.contexts, self.scopes = dict(delivery_contexts or {}), dict(scopes or {})
        if reader.project_id != self.spec.native_project_id:
            raise CoordinatorError("COORDINATOR_BOARD_CONFIGURATION_INVALID")

    def _scope(self, actor, project_id):
        if project_id != self.spec.project_id:
            raise CoordinatorError("COORDINATOR_PROJECT_DENIED")
        self.runtime.connection_principal(actor)

    def _board_spec(self, configured, board):
        native = board.epic(configured.native_epic_id)
        if native is None or native.binding is None or (
            native.binding.local_id != configured.spec.epic_id
            or native.name != configured.spec.title
            or native.project_id != self.spec.native_project_id
        ):
            raise CoordinatorError("EPIC_SPEC_BOARD_MISMATCH")
        rows = board.epic_tasks(native.id)
        if {r.id for r in rows} != set(configured.native_task_ids):
            raise CoordinatorError("EPIC_TASK_SCOPE_CHANGED")
        for identity, entry in zip(configured.native_task_ids, configured.spec.tasks, strict=True):
            task = board.task(identity)
            dependencies = [board.task(d) for d in task.dependencies]
            if task.binding is None or any(d is None or d.binding is None for d in dependencies):
                raise CoordinatorError("TASK_BINDING_MISSING")
            if task.execution_owner_kind != "User" or (
                task.responsible_user_id != self.reader.user_id
            ):
                raise CoordinatorError("TASK_OWNER_CHANGED")
            if task.binding.local_id != entry.task.task_id or task.name != entry.task.name or (
                tuple(entry.task.acceptance_criteria) != task.acceptance_criteria
                or set(entry.task.dependencies) != {d.binding.local_id for d in dependencies}
                or len(set(entry.task.dependencies)) != len(entry.task.dependencies)
            ):
                raise CoordinatorError("TASK_SPEC_BOARD_MISMATCH")
            if int(task.priority) != {"P0": 3, "P1": 2, "P2": 1, "P3": 0}[entry.priority]:
                raise CoordinatorError("TASK_PRIORITY_CHANGED")
        if any(
            i.task_id in {native.id, *configured.native_task_ids}
            and not (i.code == "DEPENDENCY_NOT_DONE"
                     and i.related_id in configured.native_task_ids)
            for i in board.issues
        ):
            raise CoordinatorError("EPIC_BOARD_ISSUES")
        return native, rows

    def _dependency(self, local_id, board, actor, main):
        configured = next(e for e in self.spec.epics if e.spec.epic_id == local_id)
        native, rows = self._board_spec(configured, board)
        if native.status != "Done" or any(r.status != BoardStatus.DONE for r in rows):
            raise CoordinatorError("EPIC_DEPENDENCY_NOT_DONE")
        runs = [r for r in self.store.get_runs() if isinstance(r, EpicRun)
                and r.project_id == self.spec.project_id and r.epic_id == local_id]
        if len(runs) != 1 or runs[0].status != EpicState.DONE:
            raise CoordinatorError("EPIC_DEPENDENCY_RUN_UNVERIFIED")
        previous = runs[0]
        context = self.contexts.get(previous.id)
        if context is None or context.repository != self.settings.repository or (
            context.sqlite_path != self.settings.sqlite_path
            or tuple(sorted(self.scopes.get(previous.id, ())))
            != tuple(sorted(t.task.task_id for t in configured.spec.tasks))
        ):
            raise CoordinatorError("EPIC_DEPENDENCY_CONTEXT_UNVERIFIED")
        try:
            evidence = TeamPlayerEvidence(
                context, self.store, scopes=self.scopes, codex=self.runtime.codex,
                processes=self.runtime.processes,
            )
            for entry in configured.spec.tasks:
                tasks = [t for t in self.store.get_tasks(previous.id)
                         if t.task_id == entry.task.task_id]
                if len(tasks) != 1 or evidence.spec(tasks[0]) != entry.task or (
                    evidence.task(tasks[0])[0] != "Done"
                ):
                    raise ValueError
            status, proof, _ = evidence.epic(actor, previous)
            if status != "Done" or not self.runtime.git.contains_commit(
                "main", proof["merge_commit"]
            ) or self.runtime.main() != main:
                raise ValueError
        except Exception:
            raise CoordinatorError("EPIC_MAIN_DELIVERY_UNVERIFIED") from None
        return proof

    def _gate(self, main):
        if self.gate is None:
            raise CoordinatorError("RUNTIME_SECURITY_GATE_UNVERIFIED")
        try:
            evidence = RuntimeGateEvidence.model_validate(self.gate(self.runtime, main))
            if not evidence.ready or evidence.configuration_hash != self.runtime.configuration_hash:
                raise ValueError
            if evidence.main_commit != main or self.runtime.main() != main:
                raise ValueError
        except Exception:
            raise CoordinatorError("RUNTIME_SECURITY_GATE_UNVERIFIED") from None
        return evidence.model_dump(mode="json")

    def _evaluate(self, board, actor, main):
        if board.project_id != self.spec.native_project_id or (
            board.authenticated_user_id != self.reader.user_id
        ):
            raise CoordinatorError("COORDINATOR_BOARD_SCOPE_MISMATCH")
        candidates, blockers = [], []
        for index, configured in enumerate(self.spec.epics):
            try:
                native, rows = self._board_spec(configured, board)
                if native.status == "Done":
                    continue
                if native.status != "Pending":
                    raise CoordinatorError("UNOWNED_ACTIVE_EPIC")
                if any(r.status != BoardStatus.PENDING for r in rows):
                    raise CoordinatorError("EPIC_TASKS_NOT_PLANNED")
                self._prerequisites(configured)
                for dependency in configured.spec.dependencies:
                    self._dependency(dependency, board, actor, main)
                candidates.append((configured.priority, index, configured))
            except CoordinatorError as error:
                blockers.append(dict(epic_id=configured.spec.epic_id, code=str(error)))
        candidates.sort(key=lambda row: (row[0], row[1]))
        return [c[2] for c in candidates], blockers

    def _prerequisites(self, configured):
        if configured.spec.external_prerequisites:
            try:
                if self.prerequisite_probe is None or (
                    self.prerequisite_probe(configured) is not True
                ):
                    raise ValueError
            except Exception:
                raise CoordinatorError("EPIC_PREREQUISITE_UNVERIFIED") from None

    def _active_claims(self):
        rows = self.store.db.execute(
            "SELECT payload FROM project_operations WHERE project_id=? AND kind=?",
            (self.spec.project_id, self.KIND),
        ).fetchall()
        claims = [ProjectOperation.model_validate_json(row[0]) for row in rows]
        return [op for op in claims if op.result.get("stage") != "RELEASED"]

    async def overview(self, actor, project_id):
        self._scope(actor, project_id)
        board = await self.reader.read_project()
        with self.runtime.lock(), self.integration._lock():
            self._scope(actor, project_id)
            main = self.runtime.main()
            candidates, blockers = self._evaluate(board, actor, main)
            active = self._active_claims()
            if active:
                candidates = []
                blockers = [b for b in blockers if not (
                    b["code"] == "UNOWNED_ACTIVE_EPIC" and any(
                        op.result["epic_id"] == b["epic_id"] for op in active
                    )
                )]
            try:
                self._gate(main)
                gate = "READY"
            except CoordinatorError as error:
                gate = str(error)
            return dict(
                project_id=project_id, coordinator_run_id=self.runtime.run_id,
                main_commit=main, candidates=[e.spec.epic_id for e in candidates],
                blockers=blockers, runtime_gate=gate, reservation=False,
                active_epics=[op.result["epic_id"] for op in active],
            )

    async def claim(self, actor, project_id, key):
        self._scope(actor, project_id)
        if not isinstance(key, str) or not key.strip() or len(key) > 128 or any(
            c in key for c in "\x00\r\n"
        ):
            raise CoordinatorError("EPIC_CLAIM_KEY_INVALID")
        with self.runtime.lock(), self.integration._lock():
            self._scope(actor, project_id)
            main = self.runtime.main()
            # The reader re-verifies get_me, Write access and all board versions.
            board = await self.reader.read_project()
            projects = await self.reader.adapter.read("list_projects", {})
            matches = [p for p in projects if isinstance(p, dict)
                       and p.get("projectId") == self.spec.native_project_id]
            if len(matches) != 1 or matches[0].get("access") != "Write":
                raise CoordinatorError("COORDINATOR_BOARD_WRITE_DENIED")
            candidates, blockers = self._evaluate(board, actor, main)
            with self.store.transaction():
                existing = self.store.get_project_operation(project_id, self.KIND, key)
                active = self._active_claims()
                if any(o.coordinator_run_id != self.runtime.run_id for o in active):
                    raise CoordinatorError("EPIC_CLAIM_FOREIGN_OWNER")
                if len(active) > 1:
                    raise CoordinatorError("EPIC_CLAIM_OWNERSHIP_AMBIGUOUS")
                if existing is not None and existing.coordinator_run_id != self.runtime.run_id:
                    raise CoordinatorError("EPIC_CLAIM_KEY_CONFLICT")
                if active and existing is None:
                    selected = next(e for e in self.spec.epics
                                    if e.spec.epic_id == active[0].result["epic_id"])
                    self._board_spec(selected, board)
                    return dict(status="EXISTING", **active[0].result)
                if existing is None:
                    if not candidates:
                        return dict(status="WAIT", reason="NO_RUNNABLE_EPIC", blockers=blockers)
                    try:
                        gate = self._gate(main)
                    except CoordinatorError as error:
                        return dict(status="WAIT", reason=str(error), blockers=blockers)
                    selected = candidates[0]
                    spec_hash = digest(canonical_json(selected.model_dump(mode="json")))
                    existing = ProjectOperation(
                        project_id=project_id, coordinator_run_id=self.runtime.run_id,
                        kind=self.KIND, idempotency_key=key, result=dict(
                            stage="RESERVED", epic_id=selected.spec.epic_id,
                            native_epic_id=selected.native_epic_id, epic_run_id=str(uuid4()),
                            main_commit=main, spec_hash=spec_hash, configuration_hash=
                            self.runtime.configuration_hash, runtime_gate=gate,
                            selection_blockers=blockers,
                        ),
                    )
                    self.store.add_project_operation(existing)
            try:
                return await self._drive(actor, existing, board, main)
            except Exception:
                # Keep the reservation and the child journal; never replace an unknown start.
                current = self.store.get_project_operation(project_id, self.KIND, key)
                self.store.update_project_operation(current.model_copy(update={
                    "error_code": "EPIC_CLAIM_START_UNVERIFIED", "updated_at": utc_now(),
                }))
                raise CoordinatorError("EPIC_CLAIM_START_UNVERIFIED") from None

    async def _drive(self, actor, op, board, main):
        selected = next(
            (e for e in self.spec.epics if e.spec.epic_id == op.result["epic_id"]), None
        )
        if selected is None or op.result["configuration_hash"] != self.runtime.configuration_hash:
            raise CoordinatorError("EPIC_CLAIM_CONFIGURATION_CHANGED")
        if op.result["spec_hash"] != digest(canonical_json(selected.model_dump(mode="json"))):
            raise CoordinatorError("EPIC_CLAIM_SPEC_CHANGED")
        if op.result["main_commit"] != main:
            raise CoordinatorError("EPIC_CLAIM_MAIN_CHANGED")
        native, rows = self._board_spec(selected, board)
        if op.result["stage"] != "REGISTERED" and (
            native.status not in {"Pending", "InProgress"}
            or any(r.status != BoardStatus.PENDING for r in rows)
        ):
            raise CoordinatorError("EPIC_CLAIM_BOARD_CHANGED")
        for dependency in selected.spec.dependencies:
            self._dependency(dependency, board, actor, main)
        self._prerequisites(selected)
        self._gate(main)
        service = self.factory(selected, op.result["epic_run_id"])
        if not isinstance(service, EpicStartService) or service.spec != selected.spec or (
            service.store is not self.store or service.settings != self.settings
            or service.principal.role != Role.INTEGRATION
            or service.principal.epic_run_id != op.result["epic_run_id"]
            or service.external_epic_id != selected.native_epic_id or service.sync is None
        ):
            raise CoordinatorError("EPIC_CLAIM_INTEGRATION_CONFIGURATION_INVALID")
        if op.result["stage"] == "REGISTERED":
            service.connection_principal(service.principal)
            return dict(status="REGISTERED", **op.result)
        # Genuine F05 preparation, after the reservation, under the repository lock.
        # This is a product run intended for this actual F34 start, never a bootstrap adoption.
        prepared = service.worktrees.create_epic_worktree(
            actor, epic_id=selected.spec.epic_id, run_id=op.result["epic_run_id"],
            prepare_only=True,
        )
        if prepared.base_commit != main or self.runtime.main() != main:
            raise CoordinatorError("EPIC_CLAIM_PREPARED_BASE_CHANGED")
        self._scope(actor, op.project_id)
        # F34 owns detailed start intents, known resource recovery and its native ACK.
        outcome = await service.start(actor, op.project_id, selected.spec.epic_id,
                                      op.result["epic_run_id"])
        if outcome.get("session_id") == self.store.get_coordinator(
            self.runtime.run_id
        ).codex_session_id:
            raise CoordinatorError("EPIC_CLAIM_SESSION_NOT_ISOLATED")
        with self.store.transaction():
            current = self.store.get_project_operation(op.project_id, self.KIND, op.idempotency_key)
            result = current.result | dict(start=outcome)
            if outcome["stage"] == "REGISTERED":
                service.connection_principal(service.principal)
                result["stage"] = "REGISTERED"
            current = current.model_copy(update={
                "result": result, "updated_at": utc_now(),
                "status": "SUCCEEDED" if result["stage"] == "REGISTERED" else "PENDING",
            })
            self.store.update_project_operation(current)
        return dict(status=result["stage"], **result)
