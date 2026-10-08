from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from orchestrator.domain.states import EpicState, Kanban, TaskState, task_kanban

Identity = Annotated[str, Field(min_length=1)]
Commit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    id: Identity = Field(default_factory=lambda: str(uuid4()))

    @field_validator("*", mode="after")
    @classmethod
    def normalize_times(cls, value: object) -> object:
        return value.astimezone(UTC) if isinstance(value, datetime) else value


class EpicRun(Record):
    project_id: Identity
    epic_id: Identity
    branch: Identity
    worktree_path: Identity
    status: EpicState = EpicState.PLANNED
    integration_agent_id: str | None = None
    herdr_workspace_id: str | None = None
    herdr_server_session: str | None = None
    herdr_tab_id: str | None = None
    herdr_pane_id: str | None = None
    herdr_terminal_id: str | None = None
    codex_session_id: str | None = None
    base_commit: Commit | None = None
    current_commit: Commit | None = None
    approved_source_commit: Commit | None = None
    approved_target_commit: Commit | None = None
    merge_commit: Commit | None = None
    started_at: AwareDatetime = Field(default_factory=utc_now)
    completed_at: AwareDatetime | None = None


class TaskRun(Record):
    project_id: Identity
    epic_run_id: Identity
    task_id: Identity
    branch: Identity
    worktree_path: Identity
    resume_state: TaskState | None = None
    internal_status: TaskState = TaskState.PLANNED
    kanban_status: Kanban = Kanban.PLANNED
    worker_agent_id: str | None = None
    worker_slot: Annotated[int, Field(ge=1, le=2, strict=True)] | None = None
    herdr_workspace_id: str | None = None
    herdr_server_session: str | None = None
    herdr_tab_id: str | None = None
    herdr_pane_id: str | None = None
    herdr_terminal_id: str | None = None
    codex_session_id: str | None = None
    base_commit: Commit | None = None
    current_commit: Commit | None = None
    approved_source_commit: Commit | None = None
    approved_target_commit: Commit | None = None
    merge_commit: Commit | None = None
    started_at: AwareDatetime = Field(default_factory=utc_now)
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def consistent_kanban(self):
        if self.kanban_status != task_kanban(self.internal_status):
            raise ValueError("Kanban must match internal task state")
        if self.internal_status == TaskState.DONE and (
            self.completed_at is None or self.merge_commit is None
        ):
            raise ValueError("Done record must contain completion time and merge commit")
        return self


class Review(Record):
    task_run_id: Identity
    review_number: Annotated[int, Field(ge=1, strict=True)]
    review_result: Identity
    review_commit: Commit
    epic_commit: Commit
    feedback: str = ""
    created_at: AwareDatetime = Field(default_factory=utc_now)


class Operation(Record):
    project_id: Identity
    epic_run_id: Identity
    task_run_id: Identity | None = None
    kind: Identity
    idempotency_key: Identity
    status: Identity = "PENDING"
    result: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class ExternalReference(Record):
    project_id: Identity
    epic_run_id: Identity
    task_run_id: Identity | None = None
    provider: Identity
    kind: Identity
    external_id: Identity
    created_at: AwareDatetime = Field(default_factory=utc_now)


class TransitionEvent(Record):
    project_id: Identity
    epic_run_id: Identity
    task_run_id: Identity | None = None
    request: dict[str, JsonValue]
    result: dict[str, JsonValue]
    created_at: AwareDatetime = Field(default_factory=utc_now)
