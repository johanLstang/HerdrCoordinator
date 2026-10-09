"""Bounded, durable scheduling for one operator-registered Integration/epic."""

import fcntl
import inspect
import os
from contextlib import contextmanager
from copy import deepcopy

from pydantic import ValidationError

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.task_approval_service import TaskApprovalError, TaskApprovalService
from orchestrator.application.task_attention_service import AttentionError, TaskAttentionService
from orchestrator.application.task_changes_service import TaskChangesError, TaskChangesService
from orchestrator.application.task_merge_service import TaskMergeError, TaskMergeService
from orchestrator.application.task_resume_intent import verified_input
from orchestrator.application.task_resume_service import ResumeError, TaskResumeService
from orchestrator.application.task_review_service import TaskReviewError, TaskReviewService
from orchestrator.application.task_selection_service import TaskSelectionError
from orchestrator.application.task_start_service import TaskStartError, TaskStartService
from orchestrator.application.worker_report_service import ReportError, WorkerReportService
from orchestrator.domain.attention import ReviewBlockDecision
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.review_contracts import ApprovalDecision, ChangesDecision
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import LocalTaskSpec, canonical_json


class SchedulerError(RuntimeError):
    """Safe scope/configuration failure; individual pipeline errors are persisted."""


class TaskSchedulerService:
    KIND = "task_schedule"

    def __init__(
        self,
        settings,
        store,
        selection,
        sync,
        herdr,
        codex=None,
        *,
        processes=None,
        review_provider=None,
        timeout_seconds=45,
    ):
        self.settings, self.store, self.selection, self.sync = settings, store, selection, sync
        if (
            settings != selection.settings
            or sync.settings != settings
            or selection.store is not store
            or sync.store is not store
            or selection.reader.project_id != sync.external_project_id
            or selection.reader.user_id != sync.user_id
            or not settings.worker_test_command
            or settings.review_context is None
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 45
        ):
            raise SchedulerError("SCHEDULER_CONFIGURATION_INVALID")
        self.review_provider, self.timeout_seconds = review_provider, timeout_seconds
        self.scheduler_id = digest(
            canonical_json(
                {
                    "settings": settings.model_dump(mode="json"),
                    "specs": {
                        k: LocalTaskSpec.model_validate(v).model_dump(mode="json")
                        for k, v in selection.specs.items()
                    },
                    "order": list(selection.order),
                    "epic_prerequisites": selection.epic_prerequisites,
                    "delivery_contexts": {
                        k: v.model_dump(mode="json") for k, v in selection.delivery_contexts.items()
                    },
                    "scopes": selection.scopes,
                    "bindings": {
                        k: v.model_dump(mode="json") for k, v in selection.reader.bindings.items()
                    },
                    "project_id": sync.project_id,
                    "external_project_id": sync.external_project_id,
                    "user_id": sync.user_id,
                    "server_session": herdr.server_session,
                    "sandbox": herdr.sandbox,
                    "timeout_seconds": timeout_seconds,
                }
            )
        )
        self._board = None
        self.start = TaskStartService(
            settings, store, herdr, codex, processes=processes, claim_guard=self._claim_guard
        )
        self.reports = WorkerReportService(settings, store, herdr, codex)
        self.review = TaskReviewService(settings, store)
        self.approval = TaskApprovalService(settings, store)
        self.changes = TaskChangesService(settings, store, herdr, codex)
        self.merge = TaskMergeService(settings, store, herdr, codex, processes=processes)
        self.attention = TaskAttentionService(
            settings, store, sync, herdr, codex, processes=processes
        )
        self.resume = TaskResumeService(self.attention)

    def _scope(self, actor, epic_run_id):
        try:
            epic = self.selection._scope(actor, epic_run_id)
        except TaskSelectionError:
            raise SchedulerError("SCHEDULER_SCOPE_DENIED") from None
        if actor.project_id != self.sync.project_id or epic.status != EpicState.ACTIVE:
            raise SchedulerError("SCHEDULER_EPIC_NOT_ACTIVE")
        active = [
            r
            for r in self.store.get_runs()
            if hasattr(r, "epic_id")
            and r.project_id == epic.project_id
            and r.status != EpicState.DONE
            and r.status != EpicState.PLANNED
        ]
        if len(active) != 1 or active[0].id != epic.id:
            raise SchedulerError("SCHEDULER_MULTIPLE_ACTIVE_EPICS")
        return epic

    @contextmanager
    def _lock(self, project_id):
        path = self.selection.git.common_dir / ("herdr-scheduler-" + digest(project_id) + ".lock")
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                raise SchedulerError("SCHEDULER_BUSY") from None
            except OSError:
                os.close(fd)
                raise
        except OSError:
            raise SchedulerError("SCHEDULER_LOCK_UNAVAILABLE") from None
        try:
            yield
        finally:
            os.close(fd)

    def _identity(self, task):
        op = self.store.get_operation(task.project_id, self.start.KIND, task.task_id)
        if (
            op is None
            or op.task_run_id != task.id
            or op.epic_run_id != task.epic_run_id
            or op.result.get("claim_evidence", {}).get("scheduler_id") != self.scheduler_id
        ):
            raise SchedulerError("TASK_NOT_SCHEDULER_OWNED")
        return op.result["claim_evidence"]["task_id"]

    def _claim_guard(self, actor, epic, spec, existing):
        if self._board is None:
            raise TaskStartError("SCHEDULER_PREFLIGHT_REQUIRED")
        try:
            result = self.selection._evaluate_locked(actor, epic.id, self._board)
            rows = [r for r in result["tasks"] if r["local_id"] == spec.task_id]
            if len(rows) != 1:
                raise TaskStartError("SCHEDULER_TASK_BINDING_INVALID")
            row = rows[0]
            configured = LocalTaskSpec.model_validate(self.selection.specs[row["task_id"]])
            if configured != spec:
                raise TaskStartError("SCHEDULER_TASK_SPEC_CHANGED")
            ignored = set()
            if existing is not None:
                task = self.store.get_task(existing.task_run_id)
                if task is None or self._identity(task) != row["task_id"]:
                    raise TaskStartError("SCHEDULER_TASK_OWNER_CHANGED")
                ignored.add("TASK_ALREADY_OWNED")
                creation = self.store.get_operation(
                    task.project_id, "create_task_worktree", task.id
                )
                if creation is not None and creation.status == "SUCCEEDED":
                    self.start.worktrees.verify_owned_worktree(task)
                    ignored.add("TASK_GIT_RESOURCES_OCCUPIED")
                if task.internal_status == TaskState.WORKING:
                    native = self._board.task(row["task_id"])
                    if native.status in {"InProgress", "Testing"}:
                        ignored.add("TASK_NOT_PLANNED")
            rejected = [b for b in row["blockers"] if b["code"] not in ignored]
            if rejected:
                raise TaskStartError(rejected[0]["code"])
            native = self._board.task(row["task_id"])
            return {
                "scheduler_id": self.scheduler_id,
                "task_id": native.id,
                "version": native.version,
                "user_id": self.selection.reader.user_id,
                "spec_hash": digest(canonical_json(spec.model_dump(mode="json"))),
                "epic_commit": result["epic_commit"],
                "main_commit": result["main_commit"],
                "dependencies": row["dependencies"],
                "epic_dependencies": result["epic_dependencies"],
                "dependency_local_ids": [
                    self._board.task(i).binding.local_id for i in row["dependencies"]
                ],
                "external_prerequisites": row["external_prerequisites"],
            }
        except TaskStartError:
            raise
        except Exception:
            raise TaskStartError("SCHEDULER_PREFLIGHT_UNVERIFIED") from None

    def _record(self, actor, task, phase=None, **updates):
        with self.store.transaction():
            old = self.store.get_operation(task.project_id, self.KIND, task.id)
            if old is not None and (
                old.task_run_id != task.id
                or old.result.get("scheduler_id") != self.scheduler_id
                or old.result.get("actor") != actor.model_dump(mode="json")
            ):
                raise SchedulerError("SCHEDULER_INTENT_CHANGED")
            result = (
                old.result
                if old
                else {
                    "scheduler_id": self.scheduler_id,
                    "actor": actor.model_dump(mode="json"),
                    "phase": "CLAIMED",
                }
            ) | updates
            if phase is not None:
                result["phase"] = phase
            status = "SUCCEEDED" if result["phase"] == "DONE" else "PENDING"
            if old is None:
                old = Operation(
                    project_id=task.project_id,
                    epic_run_id=task.epic_run_id,
                    task_run_id=task.id,
                    kind=self.KIND,
                    idempotency_key=task.id,
                    result=result,
                    status=status,
                )
                self.store.add_operation(old)
            elif old.result != result or old.status != status:
                old = old.model_copy(
                    update={"result": result, "status": status, "updated_at": utc_now()}
                )
                self.store.update_operation(old)
            return old

    def _pause(self, actor, task, code):
        parent = self.store.get_operation(task.project_id, self.start.KIND, task.task_id)
        if task.internal_status == TaskState.CLAIMED and parent is not None:
            self.start._checkpoint(parent, parent.result["stage"], error=code)
            self.start.slots.release_unstarted(task, parent.id)
        return self._record(actor, task, "PAUSED", reason=code)

    async def _sync(self, actor, task):
        identity = self._identity(task)
        try:
            await self.sync.bind_task(actor, task.id, identity)
            result = await self.sync.sync_task(actor, task.id)
        except TeamPlayerError as error:
            # Retry only the protected mirror operation; never repeat Git/runtime success.
            self._record(actor, task, reason=str(error))
            return False
        if result["status"] not in {"SYNCED", "EXISTING"}:
            self._record(actor, task, reason=result.get("reason") or "TEAMPLAYER_SYNC_PENDING")
            return False
        current = self.store.get_operation(task.project_id, self.KIND, task.id)
        if current is not None and current.result.get("reason") is not None:
            self._record(actor, task, reason=None)
        return True

    async def _fresh_owned(self, actor, task):
        board = await self.selection.reader.read_project()
        epic = self._scope(actor, task.epic_run_id)
        identity = self._identity(task)
        row = board.task(identity)
        native_epics = [e for e in board.epics if e.binding and e.binding.local_id == epic.epic_id]
        if (
            row is None
            or len(native_epics) != 1
            or row.epic_id != native_epics[0].id
            or native_epics[0].status != "InProgress"
            or row.execution_owner_kind != "User"
            or row.responsible_user_id != self.sync.user_id
        ):
            raise SchedulerError("SCHEDULER_BOARD_OWNER_CHANGED")
        spec = self.selection._spec(row, epic, board)
        recorded = self.store.get_operation(task.project_id, self.start.KIND, task.task_id)
        if spec.model_dump(mode="json") != recorded.result["spec"]:
            raise SchedulerError("SCHEDULER_BOARD_SPEC_CHANGED")
        phases = {
            TaskState.CLAIMED: {"Pending"},
            TaskState.STARTING: {"Pending"},
            TaskState.DONE: {"InProgress", "Testing", "Done"},
            TaskState.BLOCKED: {"InProgress", "Testing", "NeedsInput", "Blocked"},
            TaskState.PARKED: {"InProgress", "Testing", "NeedsInput", "Blocked"},
        }
        if row.status not in phases.get(task.internal_status, {"InProgress", "Testing"}):
            pending = self._pending_input(actor, task)
            if pending is None or row.status not in {"NeedsInput", "Blocked"}:
                raise SchedulerError("SCHEDULER_BOARD_STATUS_CONFLICT")
        self._board = board
        return board

    async def _fill(self, actor, epic, started):
        # Bounded by configured capacity, never a hidden long-lived loop.
        for _ in range(self.settings.max_workers):
            self._board = await self.selection.reader.read_project()
            advice = self.selection.evaluate(actor, epic.id, self._board)
            identity = advice["next_task_id"]
            if identity is None:
                break
            spec = self.selection.specs[identity]
            try:
                task, _ = self.start.claim(
                    actor, epic.id, spec, timeout_seconds=self.timeout_seconds
                )
            except TaskStartError as error:
                if str(error) == "WORKER_CAPACITY_UNAVAILABLE":
                    break
                raise SchedulerError("SCHEDULER_CLAIM_UNVERIFIED") from None
            self._record(actor, task)
            try:
                await self._fresh_owned(actor, task)
                task = self.start.prepare_git(
                    actor, epic.id, spec, timeout_seconds=self.timeout_seconds
                )
                if not await self._sync(actor, task):
                    self._record(actor, task, "SYNC_PENDING", resume_phase="CLAIMED")
                    break
                await self._fresh_owned(actor, task)
                self.start.start(actor, epic.id, spec, timeout_seconds=self.timeout_seconds)
                task = self.store.get_task(task.id)
                self._record(actor, task, "OBSERVING")
                started.append(task.id)
                if not await self._sync(actor, task):
                    self._record(actor, task, "SYNC_PENDING", resume_phase="OBSERVING")
            except (TeamPlayerError, SchedulerError, TaskStartError) as error:
                self._pause(actor, self.store.get_task(task.id), str(error))

    async def _review_task(self, actor, task):
        handoffs = [
            o
            for o in self.store.get_operations(task.epic_run_id, kind="worker_report")
            if o.task_run_id == task.id
            and o.status == "SUCCEEDED"
            and o.result.get("stage") == "READY_FOR_REVIEW"
        ]
        if not handoffs:
            raise SchedulerError("SCHEDULER_HANDOFF_MISSING")
        handoff = max(handoffs, key=lambda o: o.updated_at)
        epic = self.store.get_epic(task.epic_run_id)
        key = "schedule-review:" + digest(
            canonical_json([task.id, handoff.id, self.selection.git.head(epic.branch)])
        )
        package = self.review.request(actor, task.id, key=key)
        context = package["context"]
        if not context["reviewable"]:
            raise SchedulerError(context["reason"])
        op = self._record(
            actor,
            task,
            "AWAITING_REVIEW",
            context_id=context["context_id"],
            request_key=key,
            task_commit=context["task_commit"],
            epic_commit=context["epic_commit"],
        )
        saved = op.result.get("decision")
        if saved is not None and saved["context_id"] != context["context_id"]:
            saved = None
        if saved is None and self.review_provider is not None:
            try:
                saved = self.review_provider(deepcopy(context))
                if inspect.isawaitable(saved):
                    saved = await saved
            except Exception:
                raise SchedulerError("REVIEW_DECISION_UNAVAILABLE") from None
            if saved is not None:
                if not isinstance(saved, dict):
                    raise SchedulerError("REVIEW_DECISION_INVALID")
                model = {
                    "APPROVED": ApprovalDecision,
                    "CHANGES_REQUESTED": ChangesDecision,
                    "NEEDS_INPUT": ReviewBlockDecision,
                }.get(saved.get("result"))
                if model is None:
                    raise SchedulerError("REVIEW_DECISION_INVALID")
                saved = model.model_validate(saved).model_dump(mode="json")
                if saved["context_id"] != context["context_id"]:
                    raise SchedulerError("REVIEW_DECISION_STALE")
                self._record(actor, task, decision=saved)
        if saved is None:
            return False
        if saved["result"] == "NEEDS_INPUT":
            result = await self.attention.block_review(actor, task.id, saved)
            self._attention_record(actor, self.store.get_task(task.id), result)
            return False
        decision_key = "schedule-decision:" + context["context_id"]
        if saved["result"] == "APPROVED":
            self.approval.approve(actor, task.id, saved, key=decision_key)
            return True
        self.changes.request(actor, task.id, saved, key=decision_key)
        self._record(actor, self.store.get_task(task.id), "OBSERVING")
        return False

    def _attention_record(self, actor, task, result):
        phase = {
            "PARK_PENDING": "PARK_PENDING",
            "SYNC_PENDING": "ATTENTION_SYNC_PENDING",
            "PARKED_AND_SYNCED": "WAITING_INPUT",
        }[result["stage"]]
        self._record(actor, task, phase, blocker_id=result["blocker_id"], reason=result["reason"])

    async def _advance(self, actor, task):
        op = self._record(actor, task)
        if op.result["phase"] == "PAUSED":
            return False
        if op.result["phase"] == "SYNC_PENDING":
            if not await self._sync(actor, task):
                return False
            self._record(actor, task, op.result["resume_phase"])
            # Do not replay an already completed local delivery after mirror recovery.
            if task.internal_status == TaskState.DONE:
                self._record(actor, task, "DONE", merge_commit=task.merge_commit)
                return True
        if task.internal_status == TaskState.DONE:
            if not await self._sync(actor, task):
                self._record(actor, task, "SYNC_PENDING", resume_phase="DONE")
                return False
            self._record(actor, task, "DONE", merge_commit=task.merge_commit)
            return False
        await self._fresh_owned(actor, task)
        pending = self._pending_input(actor, task)
        if pending is not None:
            result = await self.resume.resume(actor, task.id, pending.result["decision"])
            task = self.store.get_task(task.id)
            if result["stage"] != "ACTIVE_AND_SYNCED":
                self._record(
                    actor, task, result["stage"], input_id=pending.id, reason=result["reason"]
                )
                return False
            self._record(actor, task, "OBSERVING", input_id=pending.id, reason=None)
        if task.internal_status in {TaskState.CLAIMED, TaskState.STARTING}:
            parent = self.store.get_operation(task.project_id, self.start.KIND, task.task_id)
            if parent.error_code:
                self._pause(actor, task, parent.error_code)
                return False
            if task.internal_status == TaskState.CLAIMED:
                task = self.start.prepare_git(
                    actor,
                    task.epic_run_id,
                    parent.result["spec"],
                    timeout_seconds=self.timeout_seconds,
                )
                if not await self._sync(actor, task):
                    self._record(actor, task, "SYNC_PENDING", resume_phase="CLAIMED")
                    return False
                await self._fresh_owned(actor, task)
            self.start.start(
                actor, task.epic_run_id, parent.result["spec"], timeout_seconds=self.timeout_seconds
            )
            task = self.store.get_task(task.id)
            self._record(actor, task, "OBSERVING")
        if task.internal_status == TaskState.CHANGES_REQUESTED:
            decision = op.result.get("decision")
            if decision is None or decision["result"] != "CHANGES_REQUESTED":
                raise SchedulerError("SCHEDULER_CORRECTION_NOT_OWNED")
            self.changes.request(
                actor, task.id, decision, key="schedule-decision:" + decision["context_id"]
            )
            task = self.store.get_task(task.id)
        if task.internal_status == TaskState.WORKING:
            try:
                self.reports.collect(actor, task.id)
            except ReportError as error:
                if str(error) in {"REPORT_NOT_AVAILABLE", "REPORT_TURN_NOT_FINISHED"}:
                    if not await self._sync(actor, task):
                        self._record(actor, task, "SYNC_PENDING", resume_phase="OBSERVING")
                    return False
                raise
            task = self.store.get_task(task.id)
        if task.internal_status in {TaskState.BLOCKED, TaskState.PARKED}:
            result = await self.attention.park_blocked(actor, task.id)
            current = self.store.get_task(task.id)
            self._attention_record(actor, current, result)
            return task.worker_slot is not None and current.worker_slot is None
        if task.internal_status == TaskState.APPROVED:
            task = self.approval.requeue_changed_epic(actor, task.id)
        if task.internal_status in {TaskState.READY_FOR_REVIEW, TaskState.REVIEWING}:
            if not await self._review_task(actor, task):
                current = self.store.get_task(task.id)
                if current.internal_status in {TaskState.BLOCKED, TaskState.PARKED}:
                    return task.worker_slot is not None and current.worker_slot is None
                if not await self._sync(actor, current):
                    phase = self.store.get_operation(task.project_id, self.KIND, task.id).result[
                        "phase"
                    ]
                    self._record(actor, current, "SYNC_PENDING", resume_phase=phase)
                return False
            task = self.store.get_task(task.id)
        if task.internal_status in {TaskState.APPROVED, TaskState.MERGING}:
            parent = self.store.get_operation(task.project_id, self.KIND, task.id)
            key = parent.result.get("delivery_key")
            if key is None:
                proof = self.approval.require_current(actor, task.id)
                key = "schedule-delivery:" + digest(proof["operation_id"])
                self._record(actor, task, "MERGING", delivery_key=key)
            result = self.merge.merge(actor, task.id, key=key, verification_key="initial")
            if result["status"] not in {"DONE", "EXISTING"}:
                raise SchedulerError(result.get("reason", "DELIVERY_VERIFICATION_FAILED"))
            task = self.store.get_task(task.id)
            if not await self._sync(actor, task):
                self._record(
                    actor, task, "SYNC_PENDING", resume_phase="DONE", merge_commit=task.merge_commit
                )
                return False
            self._record(actor, task, "DONE", merge_commit=task.merge_commit)
            return True
        return False

    def _pending_input(self, actor, task):
        pending = [
            o
            for o in self.store.get_operations(task.epic_run_id, kind="task_resume")
            if o.task_run_id == task.id and o.status == "PENDING"
        ]
        if not pending:
            return None
        if len(pending) != 1 or pending[0].result["actor"] != actor.model_dump(mode="json"):
            raise SchedulerError("SCHEDULER_INPUT_OWNER_CHANGED")
        try:
            verified_input(self.store, task, pending[0])
        except Exception:
            raise SchedulerError("SCHEDULER_INPUT_UNVERIFIED") from None
        return pending[0]

    async def _park_owned_blockers(self, actor, epic):
        # Physical safety of an already owned run does not depend on board uptime.
        # Only this scheduler's original claim/principal may be handled here;
        # selection, starts and delivery still require the fresh native board below.
        for task in self.store.get_tasks(epic.id):
            try:
                self._identity(task)
            except SchedulerError:
                continue
            try:
                if task.internal_status == TaskState.PARKED and self._pending_input(actor, task):
                    # F32 owns this already parked/resuming generation. Offline tick
                    # neither resumes it nor replays F31 against the new generation.
                    continue
                if task.internal_status == TaskState.WORKING:
                    try:
                        self.reports.collect(actor, task.id, expected_status="BLOCKED")
                    except ReportError as error:
                        if str(error) in {
                            "REPORT_NOT_AVAILABLE",
                            "REPORT_TURN_NOT_FINISHED",
                            "REPORT_STATUS_MISMATCH",
                        }:
                            continue
                        raise
                    task = self.store.get_task(task.id)
                if task.internal_status in {TaskState.BLOCKED, TaskState.PARKED}:
                    result = await self.attention.park_blocked(actor, task.id)
                    self._attention_record(actor, self.store.get_task(task.id), result)
            except (AttentionError, ReportError, SchedulerError) as error:
                self._pause(actor, self.store.get_task(task.id), str(error))

    async def tick(self, actor, epic_run_id):
        epic = self._scope(actor, epic_run_id)
        started = []
        with self._lock(actor.project_id):
            owner = self.store.get_operation(actor.project_id, "epic_schedule", epic.id)
            if owner is not None and (
                owner.result.get("scheduler_id") != self.scheduler_id
                or owner.result.get("actor") != actor.model_dump(mode="json")
            ):
                raise SchedulerError("SCHEDULER_EPIC_OWNER_CHANGED")
            if owner is not None:
                await self._park_owned_blockers(actor, epic)
            await self.sync._identity()
            # Coordinator must bind/start the epic beforehand; never promote Integration.
            board = await self.selection.reader.read_project()
            bound = self.sync._reference(epic, False)
            native = board.epic(bound)
            if (
                native is None
                or native.status != "InProgress"
                or native.binding is None
                or native.binding.local_id != epic.epic_id
            ):
                raise SchedulerError("SCHEDULER_EPIC_BINDING_UNVERIFIED")
            self.selection.integration.worktrees.verify_owned_worktree(epic)
            if owner is None:
                with self.store.transaction():
                    self.store.add_operation(
                        Operation(
                            project_id=actor.project_id,
                            epic_run_id=epic.id,
                            kind="epic_schedule",
                            idempotency_key=epic.id,
                            result={
                                "actor": actor.model_dump(mode="json"),
                                "scheduler_id": self.scheduler_id,
                            },
                        )
                    )
            await self._fill(actor, epic, started)
            tasks = self.store.get_tasks(epic.id)
            rank = {
                LocalTaskSpec.model_validate(self.selection.specs[i]).task_id: n
                for n, i in enumerate(self.selection.order)
            }
            tasks.sort(key=lambda t: (rank.get(t.task_id, len(rank)), t.id))
            for task in tasks:
                try:
                    self._identity(task)
                except SchedulerError:
                    continue  # no adoption of pre-existing/manual product or bootstrap runs
                try:
                    completed = await self._advance(actor, task)
                except (
                    SchedulerError,
                    TeamPlayerError,
                    TaskStartError,
                    TaskReviewError,
                    TaskApprovalError,
                    TaskChangesError,
                    TaskMergeError,
                    ReportError,
                    AttentionError,
                    ResumeError,
                ) as error:
                    self._pause(actor, self.store.get_task(task.id), str(error))
                    continue
                except (ValidationError, KeyError, TypeError, ValueError):
                    self._pause(actor, self.store.get_task(task.id), "SCHEDULER_STEP_UNVERIFIED")
                    continue
                if completed:
                    await self._fill(actor, epic, started)
            records = [o for o in self.store.get_operations(epic.id, kind=self.KIND)]
            return {
                "epic_run_id": epic.id,
                "started": started,
                "tasks": [
                    {
                        "task_run_id": o.task_run_id,
                        "phase": o.result["phase"],
                        "reason": o.result.get("reason"),
                        "context_id": o.result.get("context_id"),
                        "request_key": o.result.get("request_key"),
                        "delivery_key": o.result.get("delivery_key"),
                        "merge_commit": o.result.get("merge_commit"),
                    }
                    for o in records
                ],
            }
