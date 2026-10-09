"""Shared atomic Worker reservations. Callers must first authorize the owning run."""

from orchestrator.adapters.processes import ProcessObserver
from orchestrator.domain.models import TaskRun, utc_now
from orchestrator.domain.states import TaskState


class SlotError(RuntimeError):
    pass


class WorkerSlots:
    def __init__(self, settings, store, *, processes=None, inactive_probe=None):
        self.settings, self.store = settings, store
        self.processes = processes or ProcessObserver()
        # Trusted F13 native observer, never a caller-provided inactivity flag.
        self.inactive_probe = inactive_probe

    def _task(self, run):
        current = self.store.get_task(run.id)
        if current is None or (current.project_id, current.epic_run_id, current.task_id) != (
            run.project_id,
            run.epic_run_id,
            run.task_id,
        ):
            raise SlotError("WORKER_RESERVATION_OWNER_CHANGED")
        return current

    def _stopped(self, run, stop_id=None):
        start = self.store.get_operation(run.project_id, "start_runtime", run.id)
        if (
            start is None
            or start.status != "SUCCEEDED"
            or start.result.get("stage") != "READY"
            or start.task_run_id != run.id
            or start.epic_run_id != run.epic_run_id
        ):
            return False
        binding = start.result.get("binding")
        if (
            start.result.get("branch") != run.branch
            or start.result.get("cwd") != run.worktree_path
            or start.result.get("server_session") != run.herdr_server_session
            or start.result.get("name") != run.worker_agent_id
            or not isinstance(binding, dict)
            or set(binding) != {"workspace_id", "tab_id", "pane_id", "terminal_id"}
            or any(not value or getattr(run, "herdr_" + k) != value for k, value in binding.items())
        ):
            return False
        generation = start.result.get("generation", start.id)
        if any(
            o.task_run_id == run.id and o.status == "PENDING"
            for o in self.store.get_operations(run.epic_run_id, kind="resume_runtime")
        ):
            return False
        matches = [
            o
            for o in self.store.get_operations(run.epic_run_id, kind="stop_runtime")
            if o.task_run_id == run.id
            and o.status == "SUCCEEDED"
            and o.result.get("stage") == "STOPPED"
            and o.result.get("inactive") is True
            and o.result.get("generation") == generation
            and (stop_id is None or o.id == stop_id)
        ]

        def valid(op):
            proof = op.result["process_proof"]
            roots = start.result.get("processes", [])
            return (
                bool(roots)
                and bool(proof["identities"])
                and bool(proof["groups"])
                and all(root in proof["identities"] for root in roots)
                and self.processes.inactive(proof)
            )

        try:
            return any(valid(o) for o in matches)
        except Exception:
            return False

    def _unreserved(self, run):
        start = self.store.get_operation(run.project_id, "start_runtime", run.id)
        bindings = (
            run.worker_agent_id,
            run.codex_session_id,
            run.herdr_server_session,
            run.herdr_workspace_id,
            run.herdr_pane_id,
            run.herdr_terminal_id,
            run.herdr_tab_id,
        )
        if (
            start is None
            and not any(bindings)
            and run.internal_status
            in {
                TaskState.PLANNED,
                TaskState.CLAIMED,
            }
        ):
            return
        if run.internal_status in {TaskState.PARKED, TaskState.DONE} and self._stopped(run):
            return
        raise SlotError("WORKER_UNRESERVED_RUNTIME_UNVERIFIED")

    def _occupied(self, project_id, *, exclude=None):
        occupied = {}
        for run in self.store.get_runs():
            if not isinstance(run, TaskRun) or run.project_id != project_id or run.id == exclude:
                continue
            if run.worker_slot is None:
                self._unreserved(run)
            elif run.worker_slot in occupied:
                raise SlotError("WORKER_RESERVATION_CONFLICT")
            else:
                occupied[run.worker_slot] = run.id
        return occupied

    def verify(self, run):
        with self.store.transaction():
            run = self._task(run)
            occupied = self._occupied(run.project_id, exclude=run.id)
            if (
                run.worker_slot is None
                or run.worker_slot > self.settings.max_workers
                or run.worker_slot in occupied
            ):
                raise SlotError("WORKER_SLOT_UNVERIFIED")
            return run

    def _reserve(self, run):
        occupied = self._occupied(run.project_id, exclude=run.id)
        if len(occupied) >= self.settings.max_workers or any(
            n > self.settings.max_workers for n in occupied
        ):
            raise SlotError("WORKER_CAPACITY_UNAVAILABLE")
        slot = run.worker_slot or next(
            (n for n in range(1, self.settings.max_workers + 1) if n not in occupied), None
        )
        if slot is None or slot in occupied or slot > self.settings.max_workers:
            raise SlotError("WORKER_CAPACITY_UNAVAILABLE")
        if run.worker_slot != slot:
            run = run.model_copy(update={"worker_slot": slot})
            self.store.update_runtime_metadata(run)
        return run

    def reserve_new(self, run):
        with self.store.transaction():
            run = self._task(run)
            if run.worker_slot is None:
                self._unreserved(run)
                if self.store.get_operation(run.project_id, "start_runtime", run.id) is not None:
                    raise SlotError("WORKER_START_RESERVATION_UNVERIFIED")
            return self._reserve(run)

    def reserve_resume(self, run, stop_id):
        with self.store.transaction():
            run = self._task(run)
            if (
                run.internal_status != TaskState.PARKED
                or run.worker_slot is not None
                or not self._stopped(run, stop_id)
            ):
                raise SlotError("WORKER_RESUME_RESERVATION_UNVERIFIED")
            return self._reserve(run)

    def release_unstarted(self, run, parent_id):
        """Only F15's failed claim with no runtime intent/binding can release without F13."""
        with self.store.transaction():
            run = self._task(run)
            parent = self.store.get_operation(run.project_id, "task_start", run.task_id)
            if (
                parent is None
                or parent.id != parent_id
                or parent.task_run_id != run.id
                or parent.epic_run_id != run.epic_run_id
                or parent.status != "PENDING"
                or not parent.error_code
                or run.internal_status != TaskState.CLAIMED
                or self.store.get_operation(run.project_id, "start_runtime", run.id) is not None
                or any(
                    (
                        run.worker_agent_id,
                        run.codex_session_id,
                        run.herdr_server_session,
                        run.herdr_workspace_id,
                        run.herdr_pane_id,
                        run.herdr_tab_id,
                        run.herdr_terminal_id,
                    )
                )
            ):
                return False
            self.store.update_runtime_metadata(run.model_copy(update={"worker_slot": None}))
            self.store.update_operation(
                parent.model_copy(
                    update={
                        "updated_at": utc_now(),
                        "result": parent.result
                        | {
                            "reservation_released": True,
                            "release_reason": "NO_RUNTIME_INTENT_OR_BINDING",
                        },
                    }
                )
            )
            return True

    def release_stopped(self, run, stop):
        """F13 saves exit proof before releasing and committing stop success together."""
        with self.store.transaction():
            run = self._task(run)
            start = self.store.get_operation(run.project_id, "start_runtime", run.id)
            current = self.store.get_operation(run.project_id, "stop_runtime", stop.idempotency_key)
            if (
                start is None
                or current is None
                or current.id != stop.id
                or current.task_run_id != run.id
                or current.epic_run_id != run.epic_run_id
                or current.result.get("stage") != "EXIT_REQUESTED"
                or current.result.get("generation") != start.result.get("generation", start.id)
                or self.inactive_probe is None
            ):
                raise SlotError("WORKER_RELEASE_PROOF_UNVERIFIED")
            try:
                inactive = self.inactive_probe(run, start, current) is True
            except Exception:
                inactive = False
            if not inactive:
                raise SlotError("WORKER_RELEASE_PROOF_UNVERIFIED")
            self.store.update_runtime_metadata(run.model_copy(update={"worker_slot": None}))
