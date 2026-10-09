"""Persisted, role-bound startup. This service never submits product assignments."""

from pathlib import Path
from uuid import uuid4

from orchestrator.adapters.git import GitError
from orchestrator.adapters.herdr import HerdrAdapter, HerdrError
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.worker_slots import SlotError, WorkerSlots
from orchestrator.application.worktree_service import WorktreeError, WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.models import EpicRun, ExternalReference, Operation, TaskRun, utc_now
from orchestrator.domain.policy import Actor, Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.persistence.store import StateStore


class RuntimeStartError(RuntimeError):
    """Safe rejection; known resources remain recorded for reconciliation."""


class RuntimeStartService:
    KIND = "start_runtime"

    def __init__(
        self, settings: Settings, store: StateStore, herdr: HerdrAdapter, *, processes=None
    ):
        self.settings, self.store, self.herdr = settings, store, herdr
        self.worktrees = WorktreeService(settings, store)
        self.slots = WorkerSlots(settings, store, processes=processes)

    def start_task(self, actor: Actor, task_run_id: str) -> TaskRun:
        return self._start(actor, task_run_id, True)

    def start_epic(self, actor: Actor, epic_run_id: str) -> EpicRun:
        return self._start(actor, epic_run_id, False)

    def _load(self, run_id: str, task: bool):
        return self.store.get_task(run_id) if task else self.store.get_epic(run_id)

    def _validate(self, actor, run):
        if run is None:
            raise RuntimeStartError("RUN_NOT_FOUND")
        try:
            StateService.authorize_scope(actor, run)
        except StateError:
            raise RuntimeStartError("RUNTIME_SCOPE_DENIED") from None
        task = isinstance(run, TaskRun)
        if actor.role != (Role.INTEGRATION if task else Role.COORDINATOR):
            raise RuntimeStartError("RUNTIME_ROLE_DENIED")
        epic = self.store.get_epic(run.epic_run_id) if task else run
        if epic is None or epic.status != EpicState.ACTIVE or epic.completed_at is not None:
            raise RuntimeStartError("EPIC_NOT_ACTIVE")
        if task and run.internal_status not in {TaskState.CLAIMED, TaskState.STARTING}:
            raise RuntimeStartError("TASK_NOT_STARTABLE")
        if run.completed_at is not None:
            raise RuntimeStartError("RUN_COMPLETED")
        self.worktrees.verify_owned_worktree(run)

    def _prepare(self, actor, run_id, task):
        with self.store.transaction():
            run = self._load(run_id, task)
            self._validate(actor, run)
            op = self.store.get_operation(run.project_id, self.KIND, run.id)
            if op:
                if (
                    op.epic_run_id != (run.epic_run_id if task else run.id)
                    or op.task_run_id != (run.id if task else None)
                    or op.result.get("server_session") != self.herdr.server_session
                    or op.result.get("sandbox") != self.herdr.sandbox
                    or op.result.get("mcp_configuration")
                    != getattr(self.herdr, "mcp_fingerprint", None)
                    or op.result.get("branch") != run.branch
                    or op.result.get("cwd") != run.worktree_path
                ):
                    raise RuntimeStartError("START_INTENT_MISMATCH")
                binding = op.result.get("binding")
                if binding and any(getattr(run, "herdr_" + k) != v for k, v in binding.items()):
                    raise RuntimeStartError("RUNTIME_BINDING_CHANGED")
                agent_id = run.worker_agent_id if task else run.integration_agent_id
                if agent_id != op.result.get("name"):
                    raise RuntimeStartError("RUNTIME_BINDING_CHANGED")
                if run.herdr_server_session != self.herdr.server_session:
                    raise RuntimeStartError("RUNTIME_BINDING_CHANGED")
                if task:
                    self._slot(run)
                return run, op
            if (
                any(
                    getattr(run, k) is not None
                    for k in (
                        "herdr_server_session",
                        "herdr_workspace_id",
                        "herdr_tab_id",
                        "herdr_pane_id",
                        "herdr_terminal_id",
                        "codex_session_id",
                        "worker_agent_id" if task else "integration_agent_id",
                    )
                )
                or task
                and run.internal_status != TaskState.CLAIMED
            ):
                raise RuntimeStartError("UNOWNED_EXISTING_RUNTIME")
            self.worktrees.git.inspect(Path(run.worktree_path), run.branch, clean=True)
            oid = str(uuid4())
            name = "hc-" + oid.replace("-", "")[:24]
            op = Operation(
                id=oid,
                project_id=run.project_id,
                epic_run_id=run.epic_run_id if task else run.id,
                task_run_id=run.id if task else None,
                kind=self.KIND,
                idempotency_key=run.id,
                result={
                    "stage": "INTENT",
                    "server_session": self.herdr.server_session,
                    "sandbox": self.herdr.sandbox,
                    "mcp_configuration": getattr(self.herdr, "mcp_fingerprint", None),
                    "cwd": run.worktree_path,
                    "branch": run.branch,
                    "name": name,
                    "label": "hc-" + oid,
                },
            )
            updates = {
                "herdr_server_session": self.herdr.server_session,
                "worker_agent_id" if task else "integration_agent_id": name,
            }
            if task:
                try:
                    reserved = self.slots.reserve_new(run)
                except SlotError as error:
                    raise RuntimeStartError(str(error)) from None
                updates["worker_slot"] = reserved.worker_slot
                op = op.model_copy(
                    update={"result": op.result | {"worker_slot": reserved.worker_slot}}
                )
            run = type(run).model_validate(run.model_dump() | updates)
            self.store.add_operation(op)
            self.store.update_runtime_metadata(run)
            if task:
                run = StateService(self.store).transition_task(
                    run.id,
                    TaskState.STARTING,
                    expected=TaskState.CLAIMED,
                    event_id="runtime-start:" + oid,
                    actor=actor,
                    facts=VerifiedFacts(slot_reserved=True),
                )
            return run, op

    def _slot(self, run):
        try:
            self.slots.verify(run)
        except SlotError as error:
            raise RuntimeStartError(str(error)) from None

    def _save(self, run, op, expected_stage, changes, runtime_updates=None):
        with self.store.transaction():
            current = self.store.get_operation(run.project_id, self.KIND, run.id)
            if current.id != op.id or current.result.get("stage") != expected_stage:
                raise RuntimeStartError("START_STATE_CHANGED")
            result = current.result | changes
            final = result["stage"] == "READY"
            current = current.model_copy(
                update={
                    "result": result,
                    "updated_at": utc_now(),
                    "error_code": None,
                    "status": "SUCCEEDED" if final else "PENDING",
                }
            )
            actual = self._load(run.id, isinstance(run, TaskRun))
            actual = type(actual).model_validate(actual.model_dump() | (runtime_updates or {}))
            self.store.update_runtime_metadata(actual)
            self.store.update_operation(current)
            if changes.get("binding"):
                for kind, value in changes["binding"].items():
                    self.store.add_reference(
                        ExternalReference(
                            project_id=run.project_id,
                            epic_run_id=current.epic_run_id,
                            task_run_id=current.task_run_id,
                            provider="herdr:" + self.herdr.server_session,
                            kind=kind,
                            external_id=value,
                        )
                    )
            return actual, current

    def _observe(self, actor, run, op):
        run, op = self._prepare(actor, run.id, isinstance(run, TaskRun))
        binding, name = op.result["binding"], op.result["name"]
        pane = self.herdr.pane(binding["pane_id"])
        if (
            any(pane.get(k) != v for k, v in binding.items())
            or pane.get("cwd") != run.worktree_path
        ):
            raise RuntimeStartError("RUNTIME_PANE_CHANGED")
        agent = self.herdr.get_agent(name)
        if agent is None:
            raise RuntimeStartError("START_OUTCOME_UNKNOWN")
        facts = self.herdr.verify_agent(agent, binding, run.worktree_path, name)
        if not facts["ready"]:
            raise RuntimeStartError(
                "RUNTIME_BLOCKED" if facts["status"] == "blocked" else "RUNTIME_NOT_READY"
            )
        updates = {}
        if facts["session_id"]:
            if run.codex_session_id not in {None, facts["session_id"]}:
                raise RuntimeStartError("CODEX_SESSION_CHANGED")
            updates["codex_session_id"] = facts["session_id"]
        if op.status == "SUCCEEDED":
            if facts["processes"] != op.result["processes"]:
                raise RuntimeStartError("RUNTIME_PROCESS_CHANGED")
            return run
        run, _ = self._save(
            run,
            op,
            "AGENT_START_REQUESTED",
            {"stage": "READY", "processes": facts["processes"]},
            updates,
        )
        return run

    def _start(self, actor, run_id, task):
        run, op = self._prepare(actor, run_id, task)
        try:
            if op.result["stage"] == "WORKSPACE_CREATE_REQUESTED":
                raise RuntimeStartError("WORKSPACE_OUTCOME_UNKNOWN")
            if op.result["stage"] == "INTENT":
                run, op = self._save(run, op, "INTENT", {"stage": "WORKSPACE_CREATE_REQUESTED"})
                binding = self.herdr.create_workspace(run.worktree_path, op.result["label"])
                updates = {"herdr_" + k: v for k, v in binding.items()}
                run, op = self._save(
                    run,
                    op,
                    "WORKSPACE_CREATE_REQUESTED",
                    {"stage": "WORKSPACE_CREATED", "binding": binding},
                    updates,
                )
            if op.result["stage"] == "WORKSPACE_CREATED":
                binding = op.result["binding"]
                pane = self.herdr.pane(binding["pane_id"])
                info = self.herdr.process_info(binding["pane_id"])
                if (
                    any(pane.get(k) != v for k, v in binding.items())
                    or pane.get("cwd") != run.worktree_path
                    or self.herdr.get_agent(op.result["name"]) is not None
                    or len(info.get("foreground_processes", [])) != 1
                    or info.get("pane_id") != binding["pane_id"]
                    or type(info.get("shell_pid")) is not int
                    or info["foreground_processes"][0].get("pid") != info["shell_pid"]
                ):
                    raise RuntimeStartError("RUNTIME_PANE_NOT_IDLE_OR_OWNED")
                run, op = self._prepare(actor, run.id, isinstance(run, TaskRun))
                # Commit before external start; another caller may only observe this intent.
                run, op = self._save(
                    run, op, "WORKSPACE_CREATED", {"stage": "AGENT_START_REQUESTED"}
                )
                try:
                    self.herdr.start_agent(op.result["name"], binding["pane_id"], run.worktree_path)
                except HerdrError:
                    # Start may have succeeded, timed out, or reached a trust dialog. Reread.
                    pass
            return self._observe(actor, run, op)
        except (HerdrError, RuntimeStartError, WorktreeError, GitError) as e:
            code = (
                e.code
                if isinstance(e, HerdrError)
                else str(e)
                if isinstance(e, RuntimeStartError)
                else "WORKTREE_UNVERIFIED"
            )
            with self.store.transaction():
                current = self.store.get_operation(run.project_id, self.KIND, run.id)
                if current is not None and current.id == op.id:
                    self.store.update_operation(
                        current.model_copy(
                            update={
                                "error_code": code,
                                "updated_at": utc_now(),
                            }
                        )
                    )
            raise RuntimeStartError(code) from None
