"""F36: persist current aggregate evidence without approving or merging main."""

import base64
import subprocess
from pathlib import Path

from orchestrator.application.git_integration_service import GitIntegrationService
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.state_service import StateService
from orchestrator.application.task_delivery_evidence import known_merge, passed_test
from orchestrator.domain.epic_completion import EpicCompletionSpec
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import canonical_json


class EpicCompletionError(RuntimeError):
    """Safe code only: no process output, credentials or agent-supplied authority."""


class EpicCompletionService:
    KIND = "epic_complete"
    STEP = "epic_completion_step"
    MAX_REPORT_BYTES = 16 * 1024 * 1024

    def __init__(self, epic_start, scheduler, spec):
        self.epic_start, self.scheduler = epic_start, scheduler
        self.store, self.settings = scheduler.store, scheduler.settings
        self.actor = epic_start.principal
        self.git_manager = GitIntegrationService(self.settings, self.store)
        self.git = self.git_manager.git
        self._spec_json = canonical_json(
            EpicCompletionSpec.model_validate_json(
                canonical_json(
                    spec.model_dump(mode="json") if isinstance(spec, EpicCompletionSpec) else spec
                )
            ).model_dump(mode="json")
        )
        self.config_hash = digest(self._spec_json)
        self._registration = canonical_json(epic_start.spec.model_dump(mode="json"))
        self._settings_json = canonical_json(self.settings.model_dump(mode="json"))
        configured = self.spec
        registered = epic_start.spec
        if (
            epic_start.store is not self.store
            or epic_start.settings != self.settings
            or (configured.project_id, configured.epic_id)
            != (registered.project_id, registered.epic_id)
            or set(configured.task_ids) != {t.task.task_id for t in registered.tasks}
            or configured.requirements != registered.requirements
            or configured.acceptance_criteria != registered.acceptance_criteria
            or configured.sources != registered.sources
            or set(configured.task_ids)
            != {
                v["task_id"] if isinstance(v, dict) else v.task_id
                for v in scheduler.selection.specs.values()
            }
        ):
            raise EpicCompletionError("EPIC_COMPLETION_CONFIGURATION_INVALID")

    @property
    def spec(self):
        # Fresh copy: nested lists cannot mutate the registered configuration.
        return EpicCompletionSpec.model_validate_json(self._spec_json)

    def _scope(self, actor, run_id):
        if actor != self.actor or actor.role != Role.INTEGRATION or actor.epic_run_id != run_id:
            raise EpicCompletionError("EPIC_COMPLETION_SCOPE_DENIED")
        self.epic_start.connection_principal(actor)
        epic = self.store.get_epic(run_id)
        if (
            epic is None
            or epic.project_id != self.spec.project_id
            or epic.epic_id != self.spec.epic_id
            or epic.status not in {EpicState.ACTIVE, EpicState.READY_FOR_REVIEW}
            or canonical_json(self.epic_start.spec.model_dump(mode="json")) != self._registration
            or canonical_json(self.scheduler.settings.model_dump(mode="json"))
            != self._settings_json
            or len(
                [
                    r
                    for r in self.store.get_runs()
                    if hasattr(r, "epic_id")
                    and r.project_id == epic.project_id
                    and r.status not in {EpicState.PLANNED, EpicState.DONE}
                ]
            )
            != 1
        ):
            raise EpicCompletionError("EPIC_COMPLETION_REGISTRATION_CHANGED")
        return epic

    async def _manifest(self, actor, epic):
        board = await self.scheduler.selection.reader.read_project()
        native_epics = [e for e in board.epics if e.binding and e.binding.local_id == epic.epic_id]
        if len(native_epics) != 1 or native_epics[0].status != "InProgress":
            raise EpicCompletionError("EPIC_COMPLETION_BOARD_EPIC_CHANGED")
        rows = board.epic_tasks(native_epics[0].id)
        expected = set(self.spec.task_ids)
        tasks = self.store.get_tasks(epic.id)
        if (
            len(tasks) != len(expected)
            or {t.task_id for t in tasks} != expected
            or len(rows) != len(expected)
            or any(r.binding is None for r in rows)
            or {r.binding.local_id for r in rows} != expected
            or any(i.task_id in {r.id for r in rows} | {native_epics[0].id} for i in board.issues)
        ):
            raise EpicCompletionError("EPIC_COMPLETION_TASK_SCOPE_CHANGED")
        result = []
        for task in sorted(tasks, key=lambda t: self.spec.task_ids.index(t.task_id)):
            row = next(r for r in rows if r.binding.local_id == task.task_id)
            if (
                task.internal_status != TaskState.DONE
                or task.worker_slot is not None
                or row.status != "Done"
                or row.execution_owner_kind != "User"
                or row.responsible_user_id != self.scheduler.sync.user_id
                or self.scheduler._identity(task) != row.id
            ):
                raise EpicCompletionError("EPIC_COMPLETION_TASK_NOT_DONE")
            spec = self.scheduler.selection._spec(row, epic, board)
            recorded = self.store.get_operation(task.project_id, "task_start", task.task_id)
            if spec.model_dump(mode="json") != recorded.result.get("spec"):
                raise EpicCompletionError("EPIC_COMPLETION_TASK_SPEC_CHANGED")
            proof = self.git_manager._delivery_proof(task, epic)
            deliveries = [
                o
                for o in self.store.get_operations(epic.id, kind="task_merge")
                if o.task_run_id == task.id
                and o.status == "SUCCEEDED"
                and o.result.get("stage") == "DONE"
            ]
            if len(deliveries) != 1:
                raise EpicCompletionError("EPIC_COMPLETION_DELIVERY_MISSING")
            delivery = deliveries[0]
            sha = known_merge(self.settings, self.store, task, delivery, require_tip=False)
            test = passed_test(self.store, task, delivery, sha)
            stop = next(
                (
                    o
                    for o in self.store.get_operations(epic.id, kind="stop_runtime")
                    if o.id == delivery.result.get("stop_id")
                ),
                None,
            )
            if (
                stop is None
                or stop.status != "SUCCEEDED"
                or stop.task_run_id != task.id
                or not self.scheduler.merge.lifecycle.confirm_task_inactive(actor, task.id)
            ):
                raise EpicCompletionError("EPIC_COMPLETION_WORKER_STOP_UNVERIFIED")
            context = self.store.get_operation(
                task.project_id, "task_review_request", delivery.result["context_key"]
            )
            approval = self.store.get_operation(
                task.project_id, "task_approve", delivery.result["approval_key"]
            )
            handoff = next(
                (
                    o
                    for o in self.store.get_operations(epic.id, kind="worker_report")
                    if o.id == context.result["context"]["handoff_operation_id"]
                ),
                None,
            )
            if handoff is None or handoff.status != "SUCCEEDED":
                raise EpicCompletionError("EPIC_COMPLETION_REPORT_MISSING")
            result.append(
                proof
                | {
                    "run": task.model_dump(mode="json"),
                    "board_task_id": row.id,
                    "board_version": row.version,
                    "task_spec_hash": digest(canonical_json(spec.model_dump(mode="json"))),
                    "branch": task.branch,
                    "worktree_path": task.worktree_path,
                    "session_id": task.codex_session_id,
                    "worker_agent_id": task.worker_agent_id,
                    "delivery_id": delivery.id,
                    "aftertest": test.model_dump(mode="json"),
                    "stop_id": stop.id,
                    "runtime_status": "STOPPED",
                    "worker_slot": None,
                    "context": context.result["context"],
                    "approval": approval.result,
                    "worker_report": handoff.result["report"],
                }
            )
        return result

    async def _snapshot(self, actor, run_id):
        epic = self._scope(actor, run_id)
        source = self.git_manager.worktrees.verify_owned_worktree(epic)
        main = self.settings.repository
        target = self.git.inspect(main, "main", clean=False)
        for path in (Path(epic.worktree_path), main):
            if (
                self.git.working_changes(path)
                or self.git.unsafe_index_paths(path)
                or self.git.in_progress(path)
            ):
                raise EpicCompletionError("EPIC_COMPLETION_GIT_UNSTABLE")
        if not self.git.contains_commit(epic.branch, target):
            raise EpicCompletionError("EPIC_COMPLETION_CURRENT_MAIN_REQUIRED")
        manifest = await self._manifest(actor, epic)
        self._scope(actor, run_id)
        if (
            self.git_manager.worktrees.verify_owned_worktree(epic) != source
            or self.git.inspect(main, "main", clean=True) != target
            or self.git.working_changes(Path(epic.worktree_path))
            or self.git.unsafe_index_paths(Path(epic.worktree_path))
            or self.git.in_progress(Path(epic.worktree_path))
        ):
            raise EpicCompletionError("EPIC_COMPLETION_GIT_CHANGED")
        if len(canonical_json(manifest).encode()) > self.MAX_REPORT_BYTES:
            raise EpicCompletionError("EPIC_COMPLETION_REPORT_TOO_LARGE")
        return dict(
            source_commit=source,
            target_commit=target,
            manifest=manifest,
            manifest_hash=digest(canonical_json(manifest)),
            config_hash=self.config_hash,
            acceptance_version=self.spec.acceptance_version,
        )

    def _save(self, op, **changes):
        with self.store.transaction():
            current = self.store.get_operation(op.project_id, op.kind, op.idempotency_key)
            if current is None or current.id != op.id:
                raise EpicCompletionError("EPIC_COMPLETION_INTENT_CHANGED")
            result = current.model_copy(update={"updated_at": utc_now(), **changes})
            self.store.update_operation(result)
            return result

    async def require_current(self, actor, run_id, *, key):
        """Read proof after reopen; a persisted status alone is never current evidence."""
        with self.scheduler._lock(actor.project_id), self.git_manager._lock():
            return await self._current_locked(actor, run_id, key)

    async def _current_locked(self, actor, run_id, key):
        current = await self._snapshot(actor, run_id)
        op = self.store.get_operation(actor.project_id, self.KIND, key)
        latest = self.store.get_operations(run_id, kind=self.KIND)
        if (
            not latest
            or max(latest, key=lambda o: (o.created_at, o.id)).id != (op.id if op else None)
            or op is None
            or op.epic_run_id != run_id
            or op.task_run_id is not None
            or op.status != "SUCCEEDED"
            or op.result.get("stage") != "EPIC_READY_FOR_REVIEW"
            or op.result.get("snapshot") != current
            or self.store.get_epic(run_id).status != EpicState.READY_FOR_REVIEW
            or op.result.get("report_hash") != digest(canonical_json(op.result.get("report")))
        ):
            raise EpicCompletionError("EPIC_COMPLETION_HANDOFF_STALE")
        saved_steps = op.result.get("report", {}).get("steps", [])
        if len(saved_steps) != 3:
            raise EpicCompletionError("EPIC_COMPLETION_STEP_CHANGED")
        for name, saved in zip(("build", "test", "acceptance"), saved_steps, strict=True):
            step = self.store.get_operation(actor.project_id, self.STEP, op.id + ":" + name)
            if (
                step is None
                or step.status != "SUCCEEDED"
                or step.result.get("exit_code") != 0
                or step.model_dump(mode="json") != saved
                or step.result.get("command") != getattr(self.spec, name).model_dump(mode="json")
                or step.result.get("snapshot_hash") != digest(canonical_json(current))
            ):
                raise EpicCompletionError("EPIC_COMPLETION_STEP_CHANGED")
        return op

    async def complete(self, actor, run_id, *, key):
        self.git_manager._key(key)
        with self.scheduler._lock(actor.project_id), self.git_manager._lock():
            snapshot = await self._snapshot(actor, run_id)
            epic = self.store.get_epic(run_id)
            op = self.store.get_operation(actor.project_id, self.KIND, key)
            if op is not None:
                if (
                    op.epic_run_id != run_id
                    or op.task_run_id is not None
                    or op.result.get("snapshot") != snapshot
                ):
                    raise EpicCompletionError("EPIC_COMPLETION_KEY_STALE")
                if op.status == "SUCCEEDED":
                    return await self._current_locked(actor, run_id, key)
                if op.status != "PENDING":
                    return op  # Failed steps need a new explicit verification key.
            else:
                patch = self.git._diff(
                    Path(epic.worktree_path),
                    (snapshot["target_commit"], snapshot["source_commit"]),
                    4 * 1024 * 1024,
                )
                if not patch.complete:
                    raise EpicCompletionError("EPIC_COMPLETION_DIFF_INCOMPLETE")
                report = dict(
                    type="EPIC_VERIFICATION_PENDING",
                    project_id=epic.project_id,
                    epic_id=epic.epic_id,
                    epic_run_id=epic.id,
                    branch=epic.branch,
                    worktree_path=epic.worktree_path,
                    integration_agent_id=epic.integration_agent_id,
                    session_id=epic.codex_session_id,
                    specification=self.spec.model_dump(mode="json"),
                    sources=[
                        self.scheduler.review._source(snapshot["source_commit"], path, "epic")
                        for path in self.spec.sources
                    ],
                    **snapshot,
                    diff=dict(
                        encoding="base64",
                        patch=base64.b64encode(patch.patch).decode(),
                        sha256=patch.sha256,
                        bytes=patch.total_bytes,
                        complete=True,
                        full_command=list(patch.full_command),
                    ),
                    limitations=list(self.spec.known_limitations),
                )
                if len(canonical_json(report).encode()) > self.MAX_REPORT_BYTES:
                    raise EpicCompletionError("EPIC_COMPLETION_REPORT_TOO_LARGE")
                op = Operation(
                    project_id=epic.project_id,
                    epic_run_id=epic.id,
                    kind=self.KIND,
                    idempotency_key=key,
                    result=dict(stage="VERIFYING", snapshot=snapshot, report=report),
                )
                with self.store.transaction():
                    self.store.add_operation(op)
                    if epic.status == EpicState.READY_FOR_REVIEW:
                        StateService(self.store).transition_epic(
                            run_id,
                            EpicState.ACTIVE,
                            expected=EpicState.READY_FOR_REVIEW,
                            event_id="epic-reverify:" + op.id,
                            actor=actor,
                            facts=VerifiedFacts(
                                reason="New aggregate verification supersedes handoff"
                            ),
                        )
            latest = self.store.get_operations(run_id, kind=self.KIND)
            if max(latest, key=lambda o: (o.created_at, o.id)).id != op.id:
                raise EpicCompletionError("EPIC_COMPLETION_KEY_SUPERSEDED")
            steps = []
            for name in ("build", "test", "acceptance"):
                if await self._snapshot(actor, run_id) != snapshot:
                    raise EpicCompletionError("EPIC_COMPLETION_EVIDENCE_CHANGED")
                command = getattr(self.spec, name)
                child_key = op.id + ":" + name
                step = self.store.get_operation(actor.project_id, self.STEP, child_key)
                if step is not None:
                    if (
                        step.epic_run_id != run_id
                        or step.task_run_id is not None
                        or step.result.get("parent_id") != op.id
                        or step.result.get("name") != name
                        or step.result.get("command") != command.model_dump(mode="json")
                        or step.result.get("snapshot_hash") != digest(canonical_json(snapshot))
                    ):
                        raise EpicCompletionError("EPIC_COMPLETION_STEP_CHANGED")
                    if step.status == "PENDING":
                        raise EpicCompletionError("EPIC_COMPLETION_PROCESS_UNKNOWN")
                else:
                    step = Operation(
                        project_id=epic.project_id,
                        epic_run_id=epic.id,
                        kind=self.STEP,
                        idempotency_key=child_key,
                        result=dict(
                            parent_id=op.id,
                            name=name,
                            command=command.model_dump(mode="json"),
                            snapshot_hash=digest(canonical_json(snapshot)),
                        ),
                    )
                    with self.store.transaction():
                        self.store.add_operation(step)  # Durable intent BEFORE the process.
                    error, code = None, None
                    try:
                        code = subprocess.run(
                            command.argv,
                            cwd=epic.worktree_path,
                            env=self.git._environment(),
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=command.timeout,
                            check=False,
                        ).returncode
                        if code != 0:
                            error = "EPIC_COMPLETION_" + name.upper() + "_FAILED"
                    except (OSError, subprocess.TimeoutExpired):
                        error = "EPIC_COMPLETION_" + name.upper() + "_PROCESS_FAILED"
                    # Checkpoint actual process result even if subsequent observation fails.
                    step = self._save(
                        step,
                        status="FAILED" if error else "SUCCEEDED",
                        error_code=error,
                        result=step.result | {"exit_code": code},
                    )
                steps.append(step.model_dump(mode="json"))
                if step.status != "SUCCEEDED" or step.result.get("exit_code") != 0:
                    return self._save(
                        op,
                        status="FAILED",
                        error_code=step.error_code,
                        result=op.result
                        | {
                            "stage": "VERIFICATION_FAILED",
                            "steps": steps,
                            "next_action": "Repair failed step; revalidate with a new request key",
                        },
                    )
            if await self._snapshot(actor, run_id) != snapshot:
                raise EpicCompletionError("EPIC_COMPLETION_EVIDENCE_CHANGED")
            report = op.result["report"] | {
                "type": "EPIC_READY_FOR_REVIEW",
                "steps": steps,
                "acceptance": "PASS",
            }
            with self.store.transaction():
                # Changes in run metadata/state during the Git/board check are detected here.
                if {t.id: t.model_dump(mode="json") for t in self.store.get_tasks(run_id)} != {
                    m["task_run_id"]: m["run"] for m in snapshot["manifest"]
                }:
                    raise EpicCompletionError("EPIC_COMPLETION_EVIDENCE_CHANGED")
                epic = self.store.get_epic(run_id)
                epic = self.git_manager._patch(epic, current_commit=snapshot["source_commit"])
                if epic.status == EpicState.ACTIVE:
                    StateService(self.store).transition_epic(
                        run_id,
                        EpicState.READY_FOR_REVIEW,
                        expected=EpicState.ACTIVE,
                        event_id="epic-complete:" + op.id,
                        actor=actor,
                        facts=VerifiedFacts(
                            scope_complete=True,
                            tests_passed=True,
                            verification_commit=snapshot["source_commit"],
                        ),
                    )
                elif epic.status != EpicState.READY_FOR_REVIEW:
                    raise EpicCompletionError("EPIC_COMPLETION_PHASE_CHANGED")
                return self._save(
                    op,
                    status="SUCCEEDED",
                    result=op.result
                    | {
                        "stage": "EPIC_READY_FOR_REVIEW",
                        "report": report,
                        "report_hash": digest(canonical_json(report)),
                        "steps": steps,
                    },
                )
