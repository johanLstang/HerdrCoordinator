from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from orchestrator.domain.models import Identity
from orchestrator.domain.worker_contracts import LocalTaskSpec


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    project_id: Identity
    task_run_id: Identity | None = None
    epic_run_id: Identity | None = None

    @model_validator(mode="after")
    def one_target(self):
        if (self.task_run_id is None) == (self.epic_run_id is None):
            raise ValueError("exactly one task_run_id or epic_run_id is required")
        return self


class PolicyRequest(Target):
    operation: Identity


class ToolResponse(BaseModel):
    ok: bool
    code: str
    message: str
    data: dict[str, JsonValue] = {}


class TaskStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    project_id: Identity
    epic_run_id: Identity
    task: LocalTaskSpec
