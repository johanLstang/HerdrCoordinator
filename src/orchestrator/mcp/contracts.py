from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from orchestrator.domain.attention import InputDecision, ReviewBlockDecision
from orchestrator.domain.coordinator_review import CorrectionChoice
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


class WorkerWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class WorkerCommitRequest(WorkerWorkspaceRequest):
    request_key: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00\r\n]+$")
    message: str = Field(min_length=1, max_length=4096, pattern=r"^[^\x00]+$")


class TaskStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    project_id: Identity
    epic_run_id: Identity
    task: LocalTaskSpec


class EpicStartRequest(BaseModel):
    model_config = Target.model_config
    project_id: Identity
    epic_id: Identity
    epic_run_id: Identity


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


class TaskResumeRequest(TaskParkRequest):
    decision: InputDecision


class EpicCompleteRequest(TaskGetNextRequest):
    request_key: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00\r\n]+$")


class CoordinatorOverviewRequest(BaseModel):
    model_config = Target.model_config
    project_id: Identity


class EpicClaimRequest(CoordinatorOverviewRequest):
    request_key: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00\r\n]+$")


class CoordinatorReviewContextRequest(TaskGetNextRequest):
    request_key: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00\r\n]+$")


class CoordinatorDeliveryRequest(CoordinatorReviewContextRequest):
    verification_key: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00\r\n]+$")


class CoordinatorReviewReadRequest(TaskGetNextRequest):
    context_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_index: int = Field(strict=True, ge=0, le=2047)


class CoordinatorReviewSubmitRequest(CoordinatorReviewContextRequest):
    decision: ApprovalDecision | ChangesDecision = Field(discriminator="result")
    choices: tuple[CorrectionChoice, ...] = ()
