from pydantic import ValidationError

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.application.epic_start_service import EpicStartError
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.task_approval_service import TaskApprovalError
from orchestrator.application.task_attention_service import AttentionError
from orchestrator.application.task_changes_service import TaskChangesError
from orchestrator.application.task_merge_service import TaskMergeError
from orchestrator.application.task_resume_service import ResumeError
from orchestrator.application.task_review_service import TaskReviewError
from orchestrator.application.task_scheduler_service import SchedulerError
from orchestrator.application.task_selection_service import TaskSelectionError
from orchestrator.application.task_start_service import TaskStartError
from orchestrator.application.worker_report_service import ReportError
from orchestrator.domain.policy import Actor, Role
from orchestrator.event_log import EventLog
from orchestrator.mcp.contracts import (
    EpicStartRequest,
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
from orchestrator.persistence.store import StateStore, StoreError

_ROLES = {
    "integration_overview": {Role.INTEGRATION},
    "runtime_status": set(Role),
    "policy_check": set(Role),
    "task_report_ready": {Role.WORKER},
    "task_report_blocked": {Role.WORKER},
    "task_start": {Role.INTEGRATION},
    "task_get_next": {Role.INTEGRATION},
    "task_schedule": {Role.INTEGRATION},
    "task_review_request": {Role.INTEGRATION},
    "task_request_changes": {Role.INTEGRATION},
    "task_approve": {Role.INTEGRATION},
    "task_merge": {Role.INTEGRATION},
    "task_park_blocked": {Role.INTEGRATION},
    "task_block_review": {Role.INTEGRATION},
    "resume_task": {Role.INTEGRATION},
    "worker_resume": {Role.INTEGRATION},
    "epic_start": {Role.COORDINATOR},
    "epic_merge": {Role.COORDINATOR},
    "set_task_status": {Role.INTEGRATION},
    "set_epic_status": {Role.COORDINATOR},
}
_PUBLIC_FIELDS = {
    "id",
    "project_id",
    "epic_id",
    "epic_run_id",
    "task_id",
    "branch",
    "worktree_path",
    "status",
    "internal_status",
    "kanban_status",
    "worker_slot",
    "codex_session_id",
    "herdr_server_session",
    "herdr_workspace_id",
    "herdr_tab_id",
    "herdr_pane_id",
    "herdr_terminal_id",
    "worker_agent_id",
    "integration_agent_id",
    "base_commit",
    "current_commit",
    "merge_commit",
    "started_at",
    "completed_at",
}


class RuntimeService:
    """Scoped reads and an optional operator-enabled task_start for one trusted connection."""

    def __init__(
        self,
        store: StateStore,
        actor: Actor | None,
        log: EventLog,
        *,
        task_start=None,
        worker_reports=None,
        task_review=None,
        task_changes=None,
        task_approval=None,
        task_merge=None,
        teamplayer_sync=None,
        task_selection=None,
        task_scheduler=None,
        task_attention=None,
        task_resume=None,
        epic_start=None,
        integration_control=None,
    ):
        self.store, self.actor, self.log = store, actor, log
        self.task_start = task_start
        self.worker_reports = worker_reports
        self.task_review = task_review
        self.task_changes = task_changes
        self.task_approval = task_approval
        self.task_merge = task_merge
        self.teamplayer_sync = teamplayer_sync
        self.task_selection = task_selection
        self.task_scheduler = task_scheduler
        self.task_attention = task_attention
        self.task_resume = task_resume
        self.epic_start = epic_start
        self.integration_control = integration_control

    def _target(self, target: Target):
        if self.actor.project_id != target.project_id:
            raise StateError("scope denied")
        record = (
            self.store.get_task(target.task_run_id)
            if target.task_run_id is not None
            else self.store.get_epic(target.epic_run_id)
        )
        if record is None:
            raise StateError("scope denied")
        StateService.authorize_scope(self.actor, record)
        return record

    def call(self, operation: str, arguments: dict) -> ToolResponse:
        if (
            self.integration_control is not None
            and operation not in self.integration_control.operations
        ):
            return ToolResponse(
                ok=False, code="UNKNOWN_OPERATION", message="tool is not registered"
            )
        if (
            self.integration_control is not None
            and operation in self.integration_control.operations
        ):
            return ToolResponse(
                ok=False, code="ASYNC_CONTROL_REQUIRED", message="use guarded async control"
            )
        response = self._call(operation, arguments)
        # Never log caller-supplied operation names, raw arguments or exception payloads.
        self.log.emit(
            "mcp.policy",
            "INFO" if response.ok else "WARNING",
            "tool decision",
            code=response.code,
            role=self.actor.role if self.actor else "Unregistered",
        )
        return response

    async def call_async(self, operation, arguments):
        if (
            self.integration_control is not None
            and operation not in self.integration_control.operations
        ):
            return ToolResponse(
                ok=False, code="UNKNOWN_OPERATION", message="tool is not registered"
            )
        if (
            self.integration_control is not None
            and operation in self.integration_control.operations
        ):
            response = await self.integration_control.call(self.actor, operation, arguments)
            self.log.emit(
                "mcp.policy",
                "INFO" if response.ok else "WARNING",
                "tool decision",
                code=response.code,
                role=self.actor.role if self.actor else "Unregistered",
            )
            return response
        if operation == "epic_start" and self.epic_start is not None:
            response = await self._start_epic(arguments)
            self.log.emit(
                "mcp.policy",
                "INFO" if response.ok else "WARNING",
                "tool decision",
                code=response.code,
                role=self.actor.role if self.actor else "Unregistered",
            )
            return response
        if operation in {"resume_task", "worker_resume"} and self.task_resume is not None:
            response = await self._resume_input(arguments)
            self.log.emit(
                "mcp.policy",
                "INFO" if response.ok else "WARNING",
                "tool decision",
                code=response.code,
                role=self.actor.role if self.actor else "Unregistered",
            )
            return response
        if (
            operation in {"task_park_blocked", "task_block_review"}
            and self.task_attention is not None
        ):
            response = await self._attention(operation, arguments)
            self.log.emit(
                "mcp.policy",
                "INFO" if response.ok else "WARNING",
                "tool decision",
                code=response.code,
                role=self.actor.role if self.actor else "Unregistered",
            )
            return response
        if operation == "task_schedule" and self.task_scheduler is not None:
            response = await self._schedule(arguments)
            self.log.emit(
                "mcp.policy",
                "INFO" if response.ok else "WARNING",
                "tool decision",
                code=response.code,
                role=self.actor.role if self.actor else "Unregistered",
            )
            return response
        if operation == "task_get_next" and self.task_selection is not None:
            response = await self._get_next(arguments)
            self.log.emit(
                "mcp.policy",
                "INFO" if response.ok else "WARNING",
                "tool decision",
                code=response.code,
                role=self.actor.role if self.actor else "Unregistered",
            )
            return response
        if operation not in {"set_task_status", "set_epic_status"} or self.teamplayer_sync is None:
            return self.call(operation, arguments)
        response = await self._sync_teamplayer(operation, arguments)
        self.log.emit(
            "mcp.policy",
            "INFO" if response.ok else "WARNING",
            "tool decision",
            code=response.code,
            role=self.actor.role if self.actor else "Unregistered",
        )
        return response

    async def _start_epic(self, arguments):
        if self.actor is None:
            return ToolResponse(ok=False, code="UNAUTHENTICATED", message="unregistered connection")
        if self.actor.role != Role.COORDINATOR:
            return ToolResponse(ok=False, code="FORBIDDEN", message="epic start role denied")
        try:
            request = EpicStartRequest.model_validate(arguments)
        except ValidationError:
            return ToolResponse(ok=False, code="INVALID_ARGUMENT", message="invalid epic target")
        try:
            result = await self.epic_start.start(
                self.actor, request.project_id, request.epic_id, request.epic_run_id
            )
            registered = result["stage"] == "REGISTERED"
            return ToolResponse(
                ok=registered,
                code="OK" if registered else result["reason"] or "EPIC_START_PENDING",
                message="epic start confirmed" if registered else "epic start pending",
                data=result,
            )
        except EpicStartError as error:
            return ToolResponse(ok=False, code=str(error), message="reconcile recorded epic start")
        except Exception:
            return ToolResponse(
                ok=False, code="EPIC_START_UNVERIFIED", message="reconcile recorded epic start"
            )

    async def _sync_teamplayer(self, operation, arguments):
        if self.actor is None:
            return ToolResponse(ok=False, code="UNAUTHENTICATED", message="unregistered connection")
        if self.actor.role not in _ROLES[operation]:
            return ToolResponse(ok=False, code="FORBIDDEN", message="role cannot sync this target")
        try:
            request = Target.model_validate(arguments)
            task = operation == "set_task_status"
            identity = request.task_run_id if task else request.epic_run_id
            if identity is None:
                raise ValueError
        except (ValidationError, ValueError):
            return ToolResponse(ok=False, code="INVALID_ARGUMENT", message="invalid sync target")
        try:
            self._target(request)
            method = self.teamplayer_sync.sync_task if task else self.teamplayer_sync.sync_epic
            result = await method(self.actor, identity)
            ready = result["status"] in {"SYNCED", "EXISTING"}
            return ToolResponse(
                ok=ready,
                code="OK" if ready else result["reason"],
                message="TeamPlayer sync confirmed" if ready else "TeamPlayer sync pending",
                data=result,
            )
        except StateError:
            return ToolResponse(ok=False, code="FORBIDDEN", message="sync scope denied")
        except TeamPlayerError as error:
            return ToolResponse(
                ok=False, code=str(error), message="reconcile recorded sync and source evidence"
            )
        except Exception:
            return ToolResponse(
                ok=False, code="TEAMPLAYER_SYNC_UNAVAILABLE", message="reconcile recorded sync"
            )

    def _call(self, operation: str, arguments: dict) -> ToolResponse:
        if self.actor is None:
            return ToolResponse(
                ok=False, code="UNAUTHENTICATED", message="connection has no registered principal"
            )
        if (
            operation in {"task_report_ready", "task_report_blocked"}
            and self.worker_reports is not None
        ):
            return self._worker_report(operation, arguments)
        if operation == "task_start" and self.task_start is not None:
            return self._start_task(arguments)
        if operation == "task_review_request" and self.task_review is not None:
            return self._request_review(arguments)
        if operation == "task_request_changes" and self.task_changes is not None:
            return self._request_changes(arguments)
        if operation == "task_approve" and self.task_approval is not None:
            return self._approve_task(arguments)
        if operation == "task_merge" and self.task_merge is not None:
            return self._merge_task(arguments)
        if operation not in {"runtime_status", "policy_check"}:
            return ToolResponse(
                ok=False, code="UNKNOWN_OPERATION", message="tool is not registered"
            )
        try:
            model = PolicyRequest if operation == "policy_check" else Target
            request = model.model_validate(arguments)
        except ValidationError:
            return ToolResponse(
                ok=False,
                code="INVALID_ARGUMENT",
                message="arguments must match the advertised schema",
            )
        try:
            record = self._target(request)
        except StateError:
            return ToolResponse(
                ok=False, code="FORBIDDEN", message="runtime scope is not authorized"
            )
        except (StoreError, ValidationError):
            return ToolResponse(
                ok=False, code="STATE_UNAVAILABLE", message="runtime state is unavailable"
            )
        if isinstance(request, PolicyRequest):
            roles = _ROLES.get(request.operation)
            if roles is None:
                return ToolResponse(
                    ok=False, code="UNKNOWN_OPERATION", message="operation is unknown"
                )
            if self.actor.role not in roles:
                return ToolResponse(
                    ok=False, code="FORBIDDEN", message="role cannot request this operation"
                )
            if request.operation == "integration_overview" and self.integration_control is not None:
                return ToolResponse(ok=True, code="OK", message="registered control is available")
            if request.operation == "epic_start" and self.epic_start is not None:
                if record.id != self.epic_start.principal.epic_run_id:
                    return ToolResponse(
                        ok=False, code="FORBIDDEN", message="epic start scope denied"
                    )
                return ToolResponse(ok=True, code="OK", message="epic start service available")
            if (
                request.operation in {"task_report_ready", "task_report_blocked"}
                and self.worker_reports is not None
            ):
                return ToolResponse(
                    ok=True, code="OK", message="native report service is available"
                )
            if request.operation == "task_get_next" and self.task_selection is not None:
                return ToolResponse(
                    ok=True, code="OK", message="read-only task selection is available"
                )
            if (
                request.operation in {"resume_task", "worker_resume"}
                and self.task_resume is not None
            ):
                return ToolResponse(ok=True, code="OK", message="saved-input resume available")
            if (
                request.operation in {"task_park_blocked", "task_block_review"}
                and self.task_attention is not None
            ):
                return ToolResponse(ok=True, code="OK", message="task Attention service available")
            if request.operation == "task_schedule" and self.task_scheduler is not None:
                return ToolResponse(
                    ok=True, code="OK", message="bounded task scheduler is available"
                )
            if request.operation == "task_start" and self.task_start is not None:
                return ToolResponse(ok=True, code="OK", message="task start service is available")
            if request.operation == "task_review_request" and self.task_review is not None:
                return ToolResponse(
                    ok=True, code="OK", message="review context service is available"
                )
            if request.operation == "task_request_changes" and self.task_changes is not None:
                return ToolResponse(
                    ok=True, code="OK", message="same-session correction service is available"
                )
            if request.operation == "task_approve" and self.task_approval is not None:
                return ToolResponse(
                    ok=True, code="OK", message="task approval service is available"
                )
            if request.operation == "task_merge" and self.task_merge is not None:
                return ToolResponse(
                    ok=True, code="OK", message="verified task delivery service is available"
                )
            if (
                request.operation in {"set_task_status", "set_epic_status"}
                and self.teamplayer_sync is not None
            ):
                return ToolResponse(
                    ok=True, code="OK", message="durable TeamPlayer sync service is available"
                )
            if request.operation not in {"runtime_status", "policy_check"}:
                return ToolResponse(
                    ok=False,
                    code="NOT_IMPLEMENTED",
                    message="operation service is not available yet",
                )
            return ToolResponse(ok=True, code="OK", message="read-only operation is permitted")
        data = {
            key: value
            for key, value in record.model_dump(mode="json").items()
            if key in _PUBLIC_FIELDS
        }
        return ToolResponse(ok=True, code="OK", message="runtime status", data=data)

    async def _schedule(self, arguments):
        if self.actor is None:
            return ToolResponse(ok=False, code="UNAUTHENTICATED", message="unregistered connection")
        if self.actor.role != Role.INTEGRATION:
            return ToolResponse(ok=False, code="FORBIDDEN", message="role cannot schedule tasks")
        try:
            request = TaskGetNextRequest.model_validate(arguments)
        except ValidationError:
            return ToolResponse(
                ok=False, code="INVALID_ARGUMENT", message="invalid scheduling target"
            )
        if (
            request.project_id != self.actor.project_id
            or request.epic_run_id != self.actor.epic_run_id
        ):
            return ToolResponse(ok=False, code="FORBIDDEN", message="scheduling scope denied")
        try:
            result = await self.task_scheduler.tick(self.actor, request.epic_run_id)
            return ToolResponse(
                ok=True, code="OK", message="bounded scheduling observed", data=result
            )
        except SchedulerError as error:
            return ToolResponse(
                ok=False, code=str(error), message="reconcile scheduling and recorded steps"
            )
        except Exception:
            return ToolResponse(
                ok=False, code="SCHEDULER_UNAVAILABLE", message="reconcile recorded scheduling"
            )

    def _start_task(self, arguments):
        if self.actor.role != Role.INTEGRATION:
            return ToolResponse(ok=False, code="FORBIDDEN", message="role cannot start tasks")
        try:
            request = TaskStartRequest.model_validate(arguments)
        except ValidationError:
            return ToolResponse(
                ok=False, code="INVALID_ARGUMENT", message="invalid task start schema"
            )
        if (
            request.project_id != self.actor.project_id
            or request.epic_run_id != self.actor.epic_run_id
        ):
            return ToolResponse(ok=False, code="FORBIDDEN", message="task scope is not authorized")
        try:
            result = self.task_start.start(self.actor, request.epic_run_id, request.task)
            return ToolResponse(ok=True, code="OK", message="task start observed", data=result)
        except TaskStartError:
            op = self.store.get_operation(request.project_id, "task_start", request.task.task_id)
            data = (
                {"task_run_id": op.task_run_id, "stage": op.result["stage"]}
                if op and op.epic_run_id == request.epic_run_id
                else {}
            )
            return ToolResponse(
                ok=False,
                code="TASK_START_UNVERIFIED",
                message="reconcile recorded task start before retry",
                data=data,
            )

    def _worker_report(self, operation, arguments):
        if self.actor.role != Role.WORKER:
            return ToolResponse(
                ok=False, code="FORBIDDEN", message="only the assigned Worker may report"
            )
        try:
            request = Target.model_validate(arguments)
            if request.task_run_id is None:
                raise ValueError
        except (ValidationError, ValueError):
            return ToolResponse(
                ok=False, code="INVALID_ARGUMENT", message="one task target required"
            )
        if (
            request.project_id != self.actor.project_id
            or request.task_run_id != self.actor.task_run_id
        ):
            return ToolResponse(
                ok=False, code="FORBIDDEN", message="Worker task scope is not authorized"
            )
        try:
            result = self.worker_reports.collect(
                self.actor,
                request.task_run_id,
                expected_status="READY_FOR_REVIEW"
                if operation == "task_report_ready"
                else "BLOCKED",
            )
            return ToolResponse(ok=True, code="OK", message="native report verified", data=result)
        except ReportError as e:
            return ToolResponse(
                ok=False, code=str(e), message="native report could not be verified"
            )

    def _request_review(self, arguments):
        if self.actor.role != Role.INTEGRATION:
            return ToolResponse(ok=False, code="FORBIDDEN", message="role cannot request review")
        try:
            request = TaskReviewRequest.model_validate(arguments)
        except ValidationError:
            return ToolResponse(ok=False, code="INVALID_ARGUMENT", message="invalid review request")
        if request.project_id != self.actor.project_id:
            return ToolResponse(
                ok=False, code="FORBIDDEN", message="review scope is not authorized"
            )
        try:
            self._target(Target(project_id=request.project_id, task_run_id=request.task_run_id))
        except StateError:
            return ToolResponse(
                ok=False, code="FORBIDDEN", message="review scope is not authorized"
            )
        try:
            result = self.task_review.request(
                self.actor, request.task_run_id, key=request.request_key
            )
            ready = result["context"]["reviewable"]
            return ToolResponse(
                ok=ready,
                code="OK" if ready else result["context"]["reason"],
                message="review context verified" if ready else "review context is not approvable",
                data=result,
            )
        except TaskReviewError as error:
            return ToolResponse(
                ok=False, code=str(error), message="reconcile review inputs and recorded operations"
            )
        except StateError:
            return ToolResponse(
                ok=False,
                code="REVIEW_UNVERIFIED",
                message="reconcile review inputs and recorded operations",
            )

    def _request_changes(self, arguments):
        if self.actor.role != Role.INTEGRATION:
            return ToolResponse(ok=False, code="FORBIDDEN", message="role cannot request changes")
        try:
            request = TaskChangesRequest.model_validate(arguments)
        except ValidationError:
            return ToolResponse(
                ok=False, code="INVALID_ARGUMENT", message="invalid review decision"
            )
        try:
            self._target(Target(project_id=request.project_id, task_run_id=request.task_run_id))
        except StateError:
            return ToolResponse(
                ok=False, code="FORBIDDEN", message="review scope is not authorized"
            )
        try:
            result = self.task_changes.request(
                self.actor, request.task_run_id, request.decision, key=request.request_key
            )
            return ToolResponse(ok=True, code="OK", message="correction recorded", data=result)
        except TaskChangesError as error:
            return ToolResponse(
                ok=False,
                code=str(error),
                message="reconcile correction and native runtime evidence",
            )

    def _approve_task(self, arguments):
        if self.actor.role != Role.INTEGRATION:
            return ToolResponse(ok=False, code="FORBIDDEN", message="role cannot approve tasks")
        try:
            request = TaskApprovalRequest.model_validate(arguments)
        except ValidationError:
            return ToolResponse(
                ok=False, code="INVALID_ARGUMENT", message="invalid approval decision"
            )
        try:
            self._target(Target(project_id=request.project_id, task_run_id=request.task_run_id))
        except StateError:
            return ToolResponse(
                ok=False, code="FORBIDDEN", message="approval scope is not authorized"
            )
        try:
            result = self.task_approval.approve(
                self.actor, request.task_run_id, request.decision, key=request.request_key
            )
            return ToolResponse(
                ok=True, code="OK", message="current approval verified", data=result
            )
        except TaskApprovalError as error:
            return ToolResponse(
                ok=False,
                code=str(error),
                message="reconcile current review and verification evidence",
            )

    def _merge_task(self, arguments):
        if self.actor.role != Role.INTEGRATION:
            return ToolResponse(ok=False, code="FORBIDDEN", message="role cannot deliver tasks")
        try:
            request = TaskMergeRequest.model_validate(arguments)
        except ValidationError:
            return ToolResponse(
                ok=False, code="INVALID_ARGUMENT", message="invalid delivery request"
            )
        try:
            self._target(Target(project_id=request.project_id, task_run_id=request.task_run_id))
        except StateError:
            return ToolResponse(
                ok=False, code="FORBIDDEN", message="delivery scope is not authorized"
            )
        try:
            result = self.task_merge.merge(
                self.actor,
                request.task_run_id,
                key=request.request_key,
                verification_key=request.verification_key,
            )
            return ToolResponse(
                ok=True, code="OK", message="delivery outcome recorded", data=result
            )
        except TaskMergeError as error:
            return ToolResponse(
                ok=False,
                code=str(error),
                message="reconcile saved merge, test and physical stop evidence",
            )

    async def _get_next(self, arguments):
        if self.actor is None:
            return ToolResponse(ok=False, code="UNAUTHENTICATED", message="unregistered connection")
        if self.actor.role != Role.INTEGRATION:
            return ToolResponse(ok=False, code="FORBIDDEN", message="role cannot select tasks")
        try:
            request = TaskGetNextRequest.model_validate(arguments)
        except ValidationError:
            return ToolResponse(
                ok=False, code="INVALID_ARGUMENT", message="invalid selection target"
            )
        try:
            self._target(Target(project_id=request.project_id, epic_run_id=request.epic_run_id))
            result = await self.task_selection.get_next(self.actor, request.epic_run_id)
            return ToolResponse(
                ok=True, code="OK", message="read-only candidates and blockers", data=result
            )
        except StateError:
            return ToolResponse(ok=False, code="FORBIDDEN", message="selection scope denied")
        except TaskSelectionError as error:
            return ToolResponse(ok=False, code=str(error), message="selection evidence unavailable")
        except TeamPlayerError as error:
            return ToolResponse(ok=False, code=str(error), message="fresh board unavailable")
        except Exception:
            return ToolResponse(
                ok=False, code="TASK_SELECTION_UNAVAILABLE", message="selection unavailable"
            )

    async def _attention(self, operation, arguments):
        if self.actor is None:
            return ToolResponse(ok=False, code="UNAUTHENTICATED", message="unregistered connection")
        if self.actor.role != Role.INTEGRATION:
            return ToolResponse(ok=False, code="FORBIDDEN", message="role cannot park tasks")
        try:
            model = TaskBlockReviewRequest if operation == "task_block_review" else TaskParkRequest
            request = model.model_validate(arguments)
        except ValidationError:
            return ToolResponse(
                ok=False, code="INVALID_ARGUMENT", message="invalid blocker request"
            )
        try:
            self._target(Target(project_id=request.project_id, task_run_id=request.task_run_id))
        except StateError:
            return ToolResponse(ok=False, code="FORBIDDEN", message="task scope denied")
        try:
            if operation == "task_block_review":
                result = await self.task_attention.block_review(
                    self.actor, request.task_run_id, request.decision
                )
            else:
                result = await self.task_attention.park_blocked(self.actor, request.task_run_id)
            ready = result["stage"] == "PARKED_AND_SYNCED"
            return ToolResponse(
                ok=ready,
                code="OK" if ready else result["reason"] or "ATTENTION_PENDING",
                message="Attention parked and mirrored"
                if ready
                else "reconcile recorded Attention",
                data=result,
            )
        except AttentionError as error:
            return ToolResponse(ok=False, code=str(error), message="reconcile recorded blocker")
        except Exception:
            return ToolResponse(
                ok=False, code="ATTENTION_UNVERIFIED", message="reconcile recorded blocker"
            )

    async def _resume_input(self, arguments):
        if self.actor is None:
            return ToolResponse(ok=False, code="UNAUTHENTICATED", message="unregistered connection")
        if self.actor.role != Role.INTEGRATION:
            return ToolResponse(ok=False, code="FORBIDDEN", message="role cannot resume tasks")
        try:
            request = TaskResumeRequest.model_validate(arguments)
        except ValidationError:
            return ToolResponse(ok=False, code="INVALID_ARGUMENT", message="invalid input request")
        try:
            self._target(Target(project_id=request.project_id, task_run_id=request.task_run_id))
        except StateError:
            return ToolResponse(ok=False, code="FORBIDDEN", message="task scope denied")
        try:
            result = await self.task_resume.resume(
                self.actor, request.task_run_id, request.decision
            )
            return ToolResponse(
                ok=result["stage"] == "ACTIVE_AND_SYNCED",
                code="OK"
                if result["stage"] == "ACTIVE_AND_SYNCED"
                else result["reason"] or "INPUT_PENDING",
                message="saved-input outcome recorded",
                data=result,
            )
        except ResumeError as error:
            return ToolResponse(ok=False, code=str(error), message="reconcile recorded input")
        except Exception:
            return ToolResponse(
                ok=False, code="INPUT_UNVERIFIED", message="reconcile recorded input"
            )
