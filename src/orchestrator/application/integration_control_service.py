"""Operator composition for agent choices; existing services retain mutation authority."""

from pydantic import ValidationError

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.application.epic_start_service import EpicStartError
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_scheduler_service import SchedulerError
from orchestrator.application.task_start_service import TaskStartError
from orchestrator.domain.models import TaskRun
from orchestrator.domain.policy import Role
from orchestrator.domain.states import TaskState
from orchestrator.domain.worker_contracts import LocalTaskSpec
from orchestrator.mcp.contracts import (
    PolicyRequest,
    Target,
    TaskApprovalRequest,
    TaskBlockReviewRequest,
    TaskChangesRequest,
    TaskGetNextRequest,
    TaskMergeRequest,
    TaskParkRequest,
    TaskResumeRequest,
    TaskReviewRequest,
    TaskStartRequest,
    ToolResponse,
)


class IntegrationControlError(RuntimeError):
    pass


MODELS = {
    "integration_overview": TaskGetNextRequest,
    "runtime_status": Target,
    "policy_check": PolicyRequest,
    "task_get_next": TaskGetNextRequest,
    "task_schedule": TaskGetNextRequest,
    "task_start": TaskStartRequest,
    "task_review_request": TaskReviewRequest,
    "task_request_changes": TaskChangesRequest,
    "task_approve": TaskApprovalRequest,
    "task_merge": TaskMergeRequest,
    "task_park_blocked": TaskParkRequest,
    "task_block_review": TaskBlockReviewRequest,
    "resume_task": TaskResumeRequest,
    "worker_resume": TaskResumeRequest,
    "set_task_status": TaskParkRequest,
}
READS = {"integration_overview", "runtime_status", "policy_check", "task_get_next"}


class IntegrationControlService:
    operations = frozenset(MODELS)

    def __init__(self, epic_start, scheduler, log):
        self.epic_start, self.scheduler = epic_start, scheduler
        self.store, self.settings = scheduler.store, scheduler.settings
        self.actor = epic_start.principal
        configured = {v.task.task_id: v.task for v in epic_start.spec.tasks}
        selected = {
            LocalTaskSpec.model_validate(v).task_id: LocalTaskSpec.model_validate(v)
            for v in scheduler.selection.specs.values()
        }
        if (
            epic_start.store is not self.store
            or epic_start.settings != self.settings
            or self.actor.project_id != scheduler.sync.project_id
            or configured != selected
            or scheduler.review_provider is not None
        ):
            raise IntegrationControlError("INTEGRATION_CONTROL_CONFIGURATION_INVALID")
        # Internal dispatch has no control wrapper; clients receive the guarded runtime below.
        self.delegate = RuntimeService(self.store, self.actor, log, **self._services())

    def _services(self):
        s = self.scheduler
        return dict(
            task_start=s.start,
            worker_reports=s.reports,
            task_review=s.review,
            task_changes=s.changes,
            task_approval=s.approval,
            task_merge=s.merge,
            teamplayer_sync=s.sync,
            task_selection=s.selection,
            task_scheduler=s,
            task_attention=s.attention,
            task_resume=s.resume,
        )

    def runtime(self, log):
        """May handshake before startup ACK; every actual tool call validates registration."""
        services = self._services()
        services.pop("worker_reports")
        return RuntimeService(self.store, self.actor, log, integration_control=self, **services)

    def _scope(self, actor, request):
        if (
            actor is None
            or actor != self.actor
            or actor.role != Role.INTEGRATION
            or request.project_id != actor.project_id
        ):
            raise IntegrationControlError("INTEGRATION_CONTROL_SCOPE_DENIED")
        task = None
        if getattr(request, "task_run_id", None) is not None:
            task = self.store.get_task(request.task_run_id)
            if (
                task is None
                or task.project_id != actor.project_id
                or task.epic_run_id != actor.epic_run_id
            ):
                raise IntegrationControlError("INTEGRATION_CONTROL_SCOPE_DENIED")
        elif request.epic_run_id != actor.epic_run_id:
            raise IntegrationControlError("INTEGRATION_CONTROL_SCOPE_DENIED")
        return task

    async def call(self, actor, operation, arguments):
        try:
            request = MODELS[operation].model_validate(arguments)
        except ValidationError:
            return ToolResponse(ok=False, code="INVALID_ARGUMENT", message="invalid control schema")
        try:
            task = self._scope(actor, request)
            self.epic_start.connection_principal(actor)
        except (IntegrationControlError, EpicStartError):
            return ToolResponse(
                ok=False, code="FORBIDDEN", message="registered Integration scope unavailable"
            )
        except Exception:
            return ToolResponse(
                ok=False, code="INTEGRATION_RUNTIME_UNVERIFIED", message="reconcile epic runtime"
            )
        result = None
        try:
            if operation == "integration_overview":
                result = ToolResponse(ok=True, code="OK", message="Integration overview")
            elif operation == "policy_check" and request.operation == "integration_overview":
                result = ToolResponse(ok=True, code="OK", message="registered overview available")
            elif operation == "task_start":
                data = await self.scheduler.start_selected(actor, actor.epic_run_id, request.task)
                started = data["status"] != "PENDING"
                result = ToolResponse(
                    ok=started,
                    code="OK" if started else "TASK_START_PENDING",
                    message="explicit task start observed",
                    data=data,
                )
            elif operation in READS or operation == "task_schedule":
                result = await self.delegate.call_async(operation, arguments)
            else:
                with self.scheduler._lock(actor.project_id):
                    await self.scheduler._fresh_owned(actor, task)
                    result = await self.delegate.call_async(operation, arguments)
                    if operation == "task_request_changes":
                        saved = self.store.get_operation(
                            actor.project_id, "task_request_changes", request.request_key
                        )
                        if (
                            saved is not None
                            and saved.task_run_id == task.id
                            and saved.result.get("decision")
                            == request.decision.model_dump(mode="json")
                        ):
                            self.scheduler._record(
                                actor,
                                self.store.get_task(task.id),
                                decision=saved.result["decision"],
                                correction_request_key=saved.idempotency_key,
                            )
                    # A successful effect is never replayed to repair a failed board mirror.
                    current = self.store.get_task(task.id)
                    if operation in {"resume_task", "worker_resume"}:
                        saved_input = self.store.get_operation(
                            actor.project_id, "task_resume", request.decision.input_id
                        )
                        journal = self.store.get_operation(
                            actor.project_id, self.scheduler.KIND, task.id
                        )
                        if (
                            result.ok
                            and saved_input is not None
                            and saved_input.task_run_id == task.id
                            and saved_input.status == "SUCCEEDED"
                            and saved_input.result.get("stage") == "ACTIVE_AND_SYNCED"
                            and saved_input.result.get("decision")
                            == request.decision.model_dump(mode="json")
                            and current.internal_status.value
                            == saved_input.result.get("resume_state")
                            and journal is not None
                            and journal.result.get("phase") == "PAUSED"
                        ):
                            self.scheduler._record(
                                actor, current, "OBSERVING", input_id=saved_input.id, reason=None
                            )
                    if current.internal_status != task.internal_status:
                        try:
                            synced = await self.scheduler._sync(actor, current)
                        except Exception:
                            synced = False
                        result = result.model_copy(
                            update={
                                "data": result.data
                                | {
                                    "board_sync": "CONFIRMED" if synced else "PENDING",
                                }
                            }
                        )
        except (SchedulerError, TaskStartError, TeamPlayerError) as error:
            result = self._preserve_result(result, str(error))
        except Exception:
            result = self._preserve_result(result, "INTEGRATION_CONTROL_UNVERIFIED")
        # Refresh failure does not convert a known effect to an unknown/replayed mutation.
        try:
            overview = await self.overview(actor)
        except Exception:
            overview = {
                "status": "UNAVAILABLE",
                "reason": "INTEGRATION_OVERVIEW_UNAVAILABLE",
                "next_allowed_operations": [],
            }
        return result.model_copy(update={"data": result.data | {"overview": overview}})

    @staticmethod
    def _preserve_result(result, reason):
        if result is not None:
            return result.model_copy(
                update={
                    "data": result.data
                    | {
                        "control_refresh": "PENDING",
                        "control_refresh_reason": reason,
                    }
                }
            )
        return ToolResponse(ok=False, code=reason, message="reconcile recorded control step")

    async def overview(self, actor):
        try:
            advice = await self.scheduler.selection.get_next(actor, actor.epic_run_id)
        except Exception:
            return {
                "status": "UNAVAILABLE",
                "reason": "INTEGRATION_OVERVIEW_UNAVAILABLE",
                "next_allowed_operations": [],
            }
        epic = self.store.get_epic(actor.epic_run_id)
        rows = []
        guidance = {
            TaskState.CLAIMED: ["task_start"],
            TaskState.STARTING: ["task_start", "task_schedule"],
            TaskState.WORKING: ["task_schedule"],
            TaskState.READY_FOR_REVIEW: ["task_review_request"],
            TaskState.REVIEWING: ["task_approve", "task_request_changes", "task_block_review"],
            TaskState.CHANGES_REQUESTED: ["task_schedule"],
            TaskState.APPROVED: ["task_merge"],
            TaskState.BLOCKED: ["task_park_blocked"],
            TaskState.PARKED: ["resume_task"],
        }
        for task in self.store.get_tasks(epic.id):
            runtime = {"status": "UNVERIFIED", "reason": "TASK_RUNTIME_UNVERIFIED"}
            try:
                runtime = self.scheduler.merge.lifecycle.reconnect_task(actor, task.id)
                runtime = {
                    k: runtime[k]
                    for k in ("status", "session_id", "runtime_status")
                    if k in runtime
                }
            except Exception:
                pass
            next_operations = list(guidance.get(task.internal_status, []))
            if task.internal_status == TaskState.APPROVED:
                try:
                    self.scheduler.approval.require_current(actor, task.id)
                except Exception:
                    next_operations = ["task_review_request"]
                    runtime = runtime | {"reason": "CURRENT_APPROVAL_UNVERIFIED"}
            journal = self.store.get_operation(task.project_id, self.scheduler.KIND, task.id)
            rows.append(
                dict(
                    task_run_id=task.id,
                    task_id=task.task_id,
                    state=task.internal_status.value,
                    kanban=task.kanban_status.value,
                    slot=task.worker_slot,
                    session_id=task.codex_session_id,
                    branch=task.branch,
                    worktree_path=task.worktree_path,
                    current_commit=self.scheduler.start.worktrees.git.head(task.branch),
                    merge_commit=task.merge_commit,
                    runtime=runtime,
                    reason=journal.result.get("reason") if journal else None,
                    context_id=journal.result.get("context_id") if journal else None,
                    request_key=journal.result.get("request_key") if journal else None,
                    next_operations=next_operations,
                    requirements="Revalidate board/phase/review; resume needs explicit input",
                )
            )
        reserved = sum(
            isinstance(t, TaskRun) and t.worker_slot is not None for t in self.store.get_runs()
        )
        return dict(
            status="FRESH",
            epic_run_id=epic.id,
            epic_state=epic.status.value,
            epic_commit=advice["epic_commit"],
            main_commit=advice["main_commit"],
            worker_capacity=self.settings.max_workers,
            reserved_workers=reserved,
            available_worker_slots=max(0, self.settings.max_workers - reserved),
            selection=advice,
            tasks=rows,
            next_allowed_operations=["integration_overview", "task_get_next", "task_schedule"],
        )
