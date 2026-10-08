"""External board facts. Parsing/readiness never establishes runtime or Git proof."""

from enum import IntEnum, StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator.domain.states import Kanban

ExternalID = Annotated[str, Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")]


class BoardRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="ignore", hide_input_in_errors=True)


class Priority(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3


class BoardStatus(StrEnum):
    PENDING = "Pending"
    IN_PROGRESS = "InProgress"
    PAUSED = "Paused"
    NEEDS_INPUT = "NeedsInput"
    NEEDS_APPROVAL = "NeedsApproval"
    NEEDS_BUDGET_APPROVAL = "NeedsBudgetApproval"
    NEEDS_REVIEW = "NeedsReview"
    TESTING = "Testing"
    DONE = "Done"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    BLOCKED = "Blocked"


class LocalBoardBinding(BoardRecord):
    """Operator's explicit UUID→backlog identity/sources; never inferred from a title."""

    local_id: Annotated[str, Field(min_length=1, max_length=128)]
    sources: tuple[str, ...] = ()

    @field_validator("local_id", "sources")
    @classmethod
    def meaningful(cls, value):
        texts = value if isinstance(value, tuple) else (value,)
        if any(not t.strip() or "\x00" in t for t in texts):
            raise ValueError("Invalid local binding")
        return value


class BoardEpic(BoardRecord):
    id: ExternalID
    project_id: ExternalID = Field(alias="projectId")
    name: str
    description: str
    status: Literal["Pending", "InProgress", "Done"]
    version: Annotated[int, Field(ge=1)]
    binding: LocalBoardBinding | None = None

    @property
    def kanban(self):
        return {"Pending": Kanban.PLANNED, "InProgress": Kanban.ACTIVE, "Done": Kanban.DONE}[
            self.status
        ]


class BoardTask(BoardRecord):
    id: ExternalID = Field(alias="taskId")
    project_id: ExternalID = Field(alias="projectId")
    epic_id: ExternalID | None = Field(alias="epicId")
    name: str
    description: str
    task_type: Literal[
        "Epic", "Feature", "Story", "Task", "Bug", "Spike", "Documentation", "Review"
    ] = Field(alias="taskType")
    status: BoardStatus
    priority: Priority
    version: Annotated[int, Field(ge=1)]
    acceptance_criteria: tuple[str, ...] = Field(alias="acceptanceCriteria")
    dependencies: tuple[ExternalID, ...] = Field(alias="dependencyTaskIds")
    traceability_artifact_ids: tuple[ExternalID, ...] = Field(alias="traceabilityArtifactIds")
    execution_owner_kind: Literal["User", "Agent", "Unassigned"] = Field(alias="executionOwnerKind")
    responsible_user_id: ExternalID | None = Field(alias="responsibleUserId")
    responsible_agent_id: ExternalID | None = Field(alias="responsibleAgentId")
    binding: LocalBoardBinding | None = None

    @field_validator("status", "priority", mode="before")
    @classmethod
    def enums(cls, value, info):
        if info.field_name == "priority":
            if type(value) is not int:
                raise ValueError("Native priority must be integer")
            return Priority(value)
        if not isinstance(value, str):
            raise ValueError("Native status must be string")
        return BoardStatus(value)

    @field_validator(
        "acceptance_criteria", "dependencies", "traceability_artifact_ids", mode="before"
    )
    @classmethod
    def arrays(cls, value):
        if not isinstance(value, list):
            raise ValueError("Native field must be array")
        return tuple(value)

    @property
    def kanban(self):
        if self.status == BoardStatus.PENDING:
            return Kanban.PLANNED
        if self.status in {BoardStatus.IN_PROGRESS, BoardStatus.TESTING, BoardStatus.PAUSED}:
            return Kanban.ACTIVE
        if self.status in {BoardStatus.DONE, BoardStatus.CANCELLED}:
            return Kanban.DONE
        return Kanban.ATTENTION


class BoardIssue(BoardRecord):
    code: str
    task_id: ExternalID
    related_id: ExternalID | None = None


class BoardSnapshot(BoardRecord):
    project_id: ExternalID
    authenticated_user_id: ExternalID
    epics: tuple[BoardEpic, ...]
    tasks: tuple[BoardTask, ...]
    issues: tuple[BoardIssue, ...] = ()

    def epic(self, epic_id):
        return next((e for e in self.epics if e.id == epic_id), None)

    def task(self, task_id):
        return next((t for t in self.tasks if t.id == task_id), None)

    def epic_tasks(self, epic_id):
        return tuple(t for t in self.tasks if t.epic_id == epic_id and t.task_type != "Epic")

    def candidate(self, task_id):
        """Board candidate only; F27 must additionally verify local specs/merge/dependencies."""
        task = self.task(task_id)
        return bool(
            task
            and task.status == BoardStatus.PENDING
            and task.task_type != "Epic"
            and not any(i.task_id == task_id for i in self.issues)
        )


def external_id(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Canonical external UUID required")
    return value
