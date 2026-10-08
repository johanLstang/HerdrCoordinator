"""Observe, park/stop and resume only a previously registered isolated runtime."""

import time
from uuid import UUID

from orchestrator.adapters.codex import CodexAdapter, CodexError
from orchestrator.adapters.git import GitError
from orchestrator.adapters.herdr import HerdrError
from orchestrator.adapters.processes import ProcessError, ProcessObserver
from orchestrator.application.runtime_start_service import RuntimeStartError, RuntimeStartService
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.worktree_service import WorktreeError
from orchestrator.domain.models import Operation, TaskRun, utc_now
from orchestrator.domain.policy import Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState


class LifecycleError(RuntimeError):
    pass


class RuntimeLifecycleService:
    def __init__(self, settings, store, herdr, *, codex=None, processes=None):
        self.settings, self.store, self.herdr = settings, store, herdr
        self.codex = codex or CodexAdapter()
        self.processes = processes or ProcessObserver()
        self.start = RuntimeStartService(settings, store, herdr)

    def _load(self, run_id, task):
        return self.store.get_task(run_id) if task else self.store.get_epic(run_id)

    def _validate(self, actor, run_id, task):
        run = self._load(run_id, task)
        if run is None:
            raise LifecycleError("RUN_NOT_FOUND")
        StateService.authorize_scope(actor, run)
        if actor.role != (Role.INTEGRATION if task else Role.COORDINATOR):
            raise LifecycleError("LIFECYCLE_ROLE_DENIED")
        self.start.worktrees.verify_owned_worktree(run)
        op = self.store.get_operation(run.project_id, self.start.KIND, run.id)
        name = run.worker_agent_id if task else run.integration_agent_id
        if (
            op is None
            or op.status != "SUCCEEDED"
            or op.result.get("stage") != "READY"
            or op.epic_run_id != (run.epic_run_id if task else run.id)
            or op.task_run_id != (run.id if task else None)
            or run.herdr_server_session != self.herdr.server_session
            or op.result.get("server_session") != self.herdr.server_session
            or op.result.get("sandbox") != self.herdr.sandbox
            or op.result.get("cwd") != run.worktree_path
            or op.result.get("branch") != run.branch
            or op.result.get("name") != name
            or any(getattr(run, "herdr_" + k) != v for k, v in op.result["binding"].items())
        ):
            raise LifecycleError("RUNTIME_BINDING_UNVERIFIED")
        return run, op

    def _pane(self, run, start):
        binding = start.result["binding"]
        pane = self.herdr.pane(binding["pane_id"])
        if (
            any(pane.get(k) != v for k, v in binding.items())
            or pane.get("cwd") != run.worktree_path
        ):
            raise LifecycleError("RUNTIME_PANE_CHANGED")
        return self.herdr.process_info(binding["pane_id"])

    def _live(self, run, start, *, changed_process=False):
        self._pane(run, start)
        agent = self.herdr.get_agent(start.result["name"])
        if agent is None:
            return None
        facts = self.herdr.verify_agent(
            agent, start.result["binding"], run.worktree_path, start.result["name"]
        )
        if not changed_process and facts["processes"] != start.result["processes"]:
            raise LifecycleError("RUNTIME_PROCESS_CHANGED")
        if run.codex_session_id is not None and facts["session_id"] != run.codex_session_id:
            raise LifecycleError("CODEX_SESSION_CHANGED")
        return facts

    def _ops(self, run, kind):
        epic = run.epic_run_id if isinstance(run, TaskRun) else run.id
        return [
            op
            for op in self.store.get_operations(epic, kind=kind)
            if op.task_run_id == (run.id if isinstance(run, TaskRun) else None)
        ]

    def _busy(self, run, current=None):
        if any(
            o.status == "PENDING" and o.id != current
            for kind in ("stop_runtime", "resume_runtime")
            for o in self._ops(run, kind)
        ):
            raise LifecycleError("LIFECYCLE_OPERATION_PENDING")

    def _finish(self, op, **updates):
        current = self.store.get_operation(op.project_id, op.kind, op.idempotency_key)
        if current.id != op.id:
            raise LifecycleError("LIFECYCLE_OPERATION_CHANGED")
        changes = updates.pop("result", {})
        result = current.model_copy(
            update={"updated_at": utc_now(), "result": current.result | changes, **updates}
        )
        self.store.update_operation(result)
        return result

    def _inactive(self, run, start, op):
        info = self._pane(run, start)
        foreground = info.get("foreground_processes", [])
        return (
            self.herdr.get_agent(start.result["name"]) is None
            and info.get("pane_id") == start.result["binding"]["pane_id"]
            and len(foreground) == 1
            and foreground[0]["pid"] == info["shell_pid"]
            and self.processes.inactive(op.result["process_proof"])
        )

    def reconnect_task(self, actor, run_id):
        return self._call(self._reconnect, actor, run_id, True)

    def reconnect_epic(self, actor, run_id):
        return self._call(self._reconnect, actor, run_id, False)

    def _reconnect(self, actor, run_id, task):
        run, start = self._validate(actor, run_id, task)
        facts = self._live(run, start)
        if facts:
            if task:
                self.start._slot(run)
            return {
                "status": "LIVE",
                "session_id": facts["session_id"],
                "runtime_status": facts["status"],
                "run": run.model_dump(mode="json"),
            }
        stopped = [
            o
            for o in self._ops(run, "stop_runtime")
            if o.status == "SUCCEEDED"
            and o.result["generation"] == start.result.get("generation", start.id)
        ]
        if stopped and self._inactive(run, start, stopped[-1]):
            return {
                "status": "STOPPED",
                "session_id": run.codex_session_id,
                "run": run.model_dump(mode="json"),
            }
        raise LifecycleError("RUNTIME_MISSING_UNCONFIRMED")

    def stop_task(self, actor, run_id, *, key, reason="Operator requested runtime stop"):
        return self._call(self._stop, actor, run_id, True, key, reason)

    def stop_epic(self, actor, run_id, *, key):
        return self._call(self._stop, actor, run_id, False, key, "")

    def _stop(self, actor, run_id, task, key, reason):
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 128
            or task
            and (not isinstance(reason, str) or not reason.strip())
        ):
            raise LifecycleError("INVALID_LIFECYCLE_REQUEST")
        with self.store.transaction():
            run, start = self._validate(actor, run_id, task)
            generation = start.result.get("generation", start.id)
            op = self.store.get_operation(run.project_id, "stop_runtime", key)
            if op and (
                op.task_run_id != (run.id if task else None)
                or op.result["generation"] != generation
                or op.result["reason"] != reason
            ):
                raise LifecycleError("STALE_STOP_INTENT")
            self._busy(run, op.id if op else None)
            if op is None:
                previous = [
                    o
                    for o in self._ops(run, "stop_runtime")
                    if o.status == "SUCCEEDED" and o.result["generation"] == generation
                ]
                if previous:
                    if not self._inactive(run, start, previous[-1]):
                        raise LifecycleError("STOPPED_RUNTIME_REAPPEARED")
                    alias = Operation(
                        project_id=run.project_id,
                        epic_run_id=run.epic_run_id if task else run.id,
                        task_run_id=run.id if task else None,
                        kind="stop_runtime",
                        idempotency_key=key,
                        status="SUCCEEDED",
                        result=previous[-1].result
                        | {"reason": reason, "prior_stop_id": previous[-1].id},
                    )
                    self.store.add_operation(alias)
                    return alias
                facts = self._live(run, start)
                if facts is None:
                    raise LifecycleError("RUNTIME_MISSING_UNCONFIRMED")
                if facts["status"] not in {"working", "idle", "done"}:
                    raise LifecycleError("RUNTIME_NOT_STOPPABLE")
                info = self._pane(run, start)
                proof = self.processes.capture(facts["processes"], info["shell_pid"])
                op = Operation(
                    project_id=run.project_id,
                    epic_run_id=run.epic_run_id if task else run.id,
                    task_run_id=run.id if task else None,
                    kind="stop_runtime",
                    idempotency_key=key,
                    result={
                        "generation": generation,
                        "reason": reason,
                        "stage": "STOP_REQUESTED",
                        "process_proof": proof,
                    },
                )
                self.store.add_operation(op)
                if task and run.internal_status not in {TaskState.DONE, TaskState.BLOCKED}:
                    run = StateService(self.store).transition_task(
                        run.id,
                        TaskState.BLOCKED,
                        expected=run.internal_status,
                        event_id="runtime-block:" + op.id,
                        actor=actor,
                        facts=VerifiedFacts(reason=reason),
                    )
            if op.status == "SUCCEEDED":
                if not self._inactive(run, start, op):
                    raise LifecycleError("STOPPED_RUNTIME_REAPPEARED")
                return op
        run, start = self._validate(actor, run_id, task)
        facts = self._live(run, start)
        if facts is not None:
            if facts["status"] == "working":
                try:
                    self.herdr.interrupt(start.result["name"])
                except HerdrError:
                    pass
                facts = self._live(run, start)
            if facts is None or not facts["ready"]:
                raise LifecycleError("STOP_UNCONFIRMED")
            if run.codex_session_id:
                thread = self.codex.read_thread(run.codex_session_id, run.worktree_path)
                if any(t["status"] == "inProgress" for t in thread["turns"]):
                    raise LifecycleError("TURN_STOP_UNCONFIRMED")
            proof = self.processes.capture(facts["processes"], self._pane(run, start)["shell_pid"])
            with self.store.transaction():
                op = self._finish(
                    op,
                    result={
                        "process_proof": self.processes.combine(op.result["process_proof"], proof),
                        "stage": "EXIT_REQUESTED",
                    },
                )
            try:
                self.herdr.exit_agent(start.result["name"])
            except HerdrError:
                pass
        deadline = time.monotonic() + 3
        while not self._inactive(run, start, op):
            if time.monotonic() >= deadline:
                raise LifecycleError("STOP_UNCONFIRMED")
            time.sleep(0.1)
        with self.store.transaction():
            run, current = self._validate(actor, run_id, task)
            if current.result.get("generation", current.id) != generation:
                raise LifecycleError("STOP_GENERATION_CHANGED")
            if not self._inactive(run, current, op):
                raise LifecycleError("STOP_UNCONFIRMED")
            if task:
                if run.internal_status == TaskState.BLOCKED:
                    run = StateService(self.store).transition_task(
                        run.id,
                        TaskState.PARKED,
                        expected=TaskState.BLOCKED,
                        event_id="runtime-park:" + op.id,
                        actor=actor,
                        facts=VerifiedFacts(inactivity_confirmed=True),
                    )
                self.store.update_runtime_metadata(run.model_copy(update={"worker_slot": None}))
            return self._finish(
                op,
                status="SUCCEEDED",
                error_code=None,
                result={"stage": "STOPPED", "inactive": True},
            )

    def resume_task(self, actor, run_id, *, key):
        return self._call(self._resume, actor, run_id, True, key)

    def resume_epic(self, actor, run_id, *, key):
        return self._call(self._resume, actor, run_id, False, key)

    def _resume(self, actor, run_id, task, key):
        if not isinstance(key, str) or not key or len(key) > 128:
            raise LifecycleError("INVALID_LIFECYCLE_REQUEST")
        with self.store.transaction():
            run, start = self._validate(actor, run_id, task)
            epic = self.store.get_epic(run.epic_run_id) if task else run
            if epic.status != EpicState.ACTIVE or epic.completed_at is not None:
                raise LifecycleError("EPIC_NOT_ACTIVE")
            if run.codex_session_id is None:
                raise LifecycleError("CODEX_SESSION_MISSING")
            str(UUID(run.codex_session_id))
            op = self.store.get_operation(run.project_id, "resume_runtime", key)
            if op and (
                op.task_run_id != (run.id if task else None)
                or op.result["session_id"] != run.codex_session_id
            ):
                raise LifecycleError("RESUME_INTENT_MISMATCH")
            self._busy(run, op.id if op else None)
            if op and op.status == "SUCCEEDED":
                if start.result.get("generation") != op.id or self._live(run, start) is None:
                    raise LifecycleError("RESUMED_RUNTIME_CHANGED")
                return op
            fresh = op is None
            if fresh:
                stopped = [
                    o
                    for o in self._ops(run, "stop_runtime")
                    if o.status == "SUCCEEDED"
                    and o.result["generation"] == start.result.get("generation", start.id)
                ]
                if (
                    not stopped
                    or not self._inactive(run, start, stopped[-1])
                    or task
                    and run.internal_status != TaskState.PARKED
                ):
                    raise LifecycleError("RESUME_REQUIRES_VERIFIED_STOP")
                self.codex.read_session(run.codex_session_id, run.worktree_path)
                if task:
                    occupied = {
                        t.worker_slot
                        for t in self.store.get_runs()
                        if isinstance(t, TaskRun)
                        and t.project_id == run.project_id
                        and t.id != run.id
                        and t.worker_slot is not None
                    }
                    slot = next(
                        (n for n in range(1, self.settings.max_workers + 1) if n not in occupied),
                        None,
                    )
                    if slot is None:
                        raise LifecycleError("WORKER_CAPACITY_UNAVAILABLE")
                    run = run.model_copy(update={"worker_slot": slot})
                    self.store.update_runtime_metadata(run)
                op = Operation(
                    project_id=run.project_id,
                    epic_run_id=run.epic_run_id if task else run.id,
                    task_run_id=run.id if task else None,
                    kind="resume_runtime",
                    idempotency_key=key,
                    result={
                        "session_id": run.codex_session_id,
                        "stop_operation_id": stopped[-1].id,
                        "stage": "RESUME_REQUESTED",
                    },
                )
                self.store.add_operation(op)
        run, start = self._validate(actor, run_id, task)
        if task:
            self.start._slot(run)
        if fresh:
            try:
                self.herdr.resume_agent(
                    start.result["name"],
                    start.result["binding"]["pane_id"],
                    run.worktree_path,
                    run.codex_session_id,
                )
            except HerdrError:
                pass
        facts = self._live(run, start, changed_process=True)
        if facts is None:
            raise LifecycleError("RESUME_OUTCOME_UNKNOWN")
        if not facts["ready"] or facts["session_id"] != run.codex_session_id:
            raise LifecycleError("RESUME_UNCONFIRMED")
        self.codex.read_session(run.codex_session_id, run.worktree_path)
        with self.store.transaction():
            run, current = self._validate(actor, run_id, task)
            prior = self.store.get_operation(run.project_id, op.kind, op.idempotency_key)
            if prior.status == "SUCCEEDED":
                if current.result.get("generation") != prior.id:
                    raise LifecycleError("RESUMED_RUNTIME_CHANGED")
                return prior
            if task:
                self.start._slot(run)
                if run.internal_status != TaskState.PARKED:
                    raise LifecycleError("RESUME_STATE_CHANGED")
                run = StateService(self.store).transition_task(
                    run.id,
                    run.resume_state,
                    expected=TaskState.PARKED,
                    event_id="runtime-resume:" + op.id,
                    actor=actor,
                    facts=VerifiedFacts(slot_reserved=True, start_confirmed=True),
                )
            self.store.update_operation(
                current.model_copy(
                    update={
                        "updated_at": utc_now(),
                        "result": current.result
                        | {"generation": op.id, "processes": facts["processes"]},
                    }
                )
            )
            return self._finish(
                op,
                status="SUCCEEDED",
                error_code=None,
                result={"stage": "RESUMED", "processes": facts["processes"]},
            )

    def _record_error(self, method, args, code):
        if method.__name__ not in {"_stop", "_resume"}:
            return
        actor, run_id, task, key = args[:4]
        if not isinstance(key, str):
            return
        run = self._load(run_id, task)
        if (
            run is None
            or actor.project_id != run.project_id
            or actor.role != (Role.INTEGRATION if task else Role.COORDINATOR)
            or task
            and actor.epic_run_id != run.epic_run_id
        ):
            return
        kind = "stop_runtime" if method.__name__ == "_stop" else "resume_runtime"
        with self.store.transaction():
            op = self.store.get_operation(run.project_id, kind, key)
            if (
                op is not None
                and op.status == "PENDING"
                and op.epic_run_id == (run.epic_run_id if task else run.id)
                and op.task_run_id == (run.id if task else None)
            ):
                self._finish(op, error_code=code)

    def _call(self, method, *args):
        try:
            return method(*args)
        except LifecycleError as e:
            self._record_error(method, args, str(e))
            raise
        except (
            RuntimeStartError,
            HerdrError,
            CodexError,
            GitError,
            WorktreeError,
            StateError,
            ProcessError,
            ValueError,
            KeyError,
            TypeError,
        ) as e:
            code = (
                e.code
                if isinstance(e, HerdrError)
                else "CODEX_SESSION_UNAVAILABLE"
                if isinstance(e, CodexError)
                else str(e)
                if isinstance(e, (ProcessError, RuntimeStartError))
                else "LIFECYCLE_UNVERIFIED"
            )
            self._record_error(method, args, code)
            raise LifecycleError(code) from None
