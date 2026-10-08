from pydantic import ValidationError

from orchestrator.application.state_service import StateError, StateService
from orchestrator.domain.policy import Actor, Role
from orchestrator.event_log import EventLog
from orchestrator.mcp.contracts import PolicyRequest, Target, ToolResponse
from orchestrator.persistence.store import StateStore, StoreError

_ROLES = {
    "runtime_status": set(Role),
    "policy_check": set(Role),
    "task_report_ready": {Role.WORKER},
    "task_report_blocked": {Role.WORKER},
    "task_start": {Role.INTEGRATION},
    "task_merge": {Role.INTEGRATION},
    "epic_start": {Role.COORDINATOR},
    "epic_merge": {Role.COORDINATOR},
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
    """Read-only service for one operator-registered stdio connection."""

    def __init__(self, store: StateStore, actor: Actor | None, log: EventLog):
        self.store, self.actor, self.log = store, actor, log

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

    def _call(self, operation: str, arguments: dict) -> ToolResponse:
        if self.actor is None:
            return ToolResponse(
                ok=False, code="UNAUTHENTICATED", message="connection has no registered principal"
            )
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
