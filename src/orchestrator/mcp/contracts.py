from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from orchestrator.domain.attention import ReviewBlockDecision
from orchestrator.domain.models import Identity
from orchestrator.domain.review_contracts import ApprovalDecision, ChangesDecision
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


class TaskReviewRequest(BaseModel):
    model_config = Target.model_config
    project_id: Identity
    task_run_id: Identity
    request_key: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00\r\n]+$")


class TaskChangesRequest(TaskReviewRequest):
    decision: ChangesDecision


class TaskApprovalRequest(TaskReviewRequest):
    decision: ApprovalDecision


class TaskMergeRequest(TaskReviewRequest):
    verification_key: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00\r\n]+$")


class TaskGetNextRequest(BaseModel):
    model_config = Target.model_config
    project_id: Identity
    epic_run_id: Identity


class TaskParkRequest(BaseModel):
    model_config = Target.model_config
    project_id: Identity
    task_run_id: Identity


class TaskBlockReviewRequest(TaskParkRequest):
    decision: ReviewBlockDecision
