"""Versioned claims and assignments; parsing grants neither authority nor state transitions."""

import json
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from orchestrator.domain.models import Commit, EpicRun, TaskRun

Text = Annotated[str, Field(min_length=1, max_length=32768)]
ID = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\x00\r\n]+$")]


class ContractError(RuntimeError):
    """Safe error: task contents and upstream validation inputs are never printed."""


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    version: Literal[1]

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("unsupported contract version")
        return value


def object_from_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    try:
        if not isinstance(text, str) or len(text.encode()) > 65536:
            raise ValueError
        value = json.loads(text, object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError):
        raise ContractError("INVALID_CONTRACT_JSON") from None


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class LocalTaskSpec(Contract):
    project_id: ID
    epic_id: ID
    task_id: ID
    name: Text
    goal: Text
    requirements: Annotated[list[Text], Field(min_length=1)]
    scope: Annotated[list[Text], Field(min_length=1)]
    out_of_scope: Annotated[list[Text], Field(min_length=1)]
    acceptance_criteria: Annotated[list[Text], Field(min_length=1)]
    sources: Annotated[list[Text], Field(min_length=1)]
    dependencies: list[ID] = Field(default_factory=list)
    external_prerequisites: list[Text] = Field(default_factory=list)
    verification_steps: Annotated[list[Text], Field(min_length=1)]

    @field_validator(
        "name",
        "goal",
        "requirements",
        "scope",
        "out_of_scope",
        "acceptance_criteria",
        "sources",
        "external_prerequisites",
        "verification_steps",
    )
    @classmethod
    def meaningful(cls, value):
        values = value if isinstance(value, list) else [value]
        if any(not text.strip() or "\x00" in text for text in values):
            raise ValueError("nonempty task text required")
        return value

    def bind(self, epic: EpicRun):
        if self.project_id != epic.project_id or self.epic_id != epic.epic_id:
            raise ContractError("TASK_EPIC_MISMATCH")
        return self


class AssignmentContext(BaseModel):
    model_config = Contract.model_config
    project_id: ID
    epic_id: ID
    epic_run_id: ID
    task_id: ID
    task_run_id: ID
    branch: Text
    worktree_path: Text
    epic_branch: Text
    base_commit: Commit


class WorkerAssignment(Contract):
    policy_version: Literal[1]
    report_version: Literal[1]
    context: AssignmentContext
    task: LocalTaskSpec

    @model_validator(mode="after")
    def same_task(self):
        if any(
            getattr(self.task, k) != getattr(self.context, k)
            for k in ("project_id", "epic_id", "task_id")
        ):
            raise ValueError("assignment context and task differ")
        return self


class ReportedTest(BaseModel):
    model_config = Contract.model_config
    command: Annotated[list[Text], Field(min_length=1)]
    exit_code: int
    summary: Text


class WorkerReport(Contract):
    model_config = Contract.model_config | {
        "json_schema_extra": {
            "allOf": [
                {
                    "if": {"properties": {"status": {"const": "READY_FOR_REVIEW"}}},
                    "then": {
                        "required": ["commit", "test_summary"],
                        "properties": {
                            "commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                            "test_summary": {"type": "string", "pattern": r"\S"},
                        },
                    },
                },
                {
                    "if": {"properties": {"status": {"const": "BLOCKED"}}},
                    "then": {
                        "required": ["reason", "input_required"],
                        "properties": {
                            "reason": {"type": "string", "pattern": r"\S"},
                            "input_required": {"type": "string", "pattern": r"\S"},
                        },
                    },
                },
            ]
        }
    }
    status: Literal["READY_FOR_REVIEW", "BLOCKED"]
    project_id: ID
    epic_id: ID
    epic_run_id: ID
    task_id: ID
    task_run_id: ID
    branch: Text
    commit: Commit | None = None
    summary: Text
    test_summary: str = ""
    tests: list[ReportedTest] = Field(default_factory=list)
    files_changed: list[Text] = Field(default_factory=list)
    limitations: list[Text] = Field(default_factory=list)
    reason: str = ""
    input_required: str = ""

    @field_validator("files_changed")
    @classmethod
    def relative_paths(cls, paths):
        for text in paths:
            path = PurePosixPath(text)
            if path.is_absolute() or ".." in path.parts or "\\" in text or "\x00" in text:
                raise ValueError("relative Git paths required")
        return paths

    @model_validator(mode="after")
    def required_evidence(self):
        if not self.summary.strip():
            raise ValueError("summary required")
        if self.status == "READY_FOR_REVIEW" and (
            self.commit is None or not self.test_summary.strip()
        ):
            raise ValueError("commit and claimed test summary required")
        if self.status == "BLOCKED" and (
            not self.reason.strip() or not self.input_required.strip()
        ):
            raise ValueError("reason and concrete input required")
        return self


def validate_task_json(text: str, epic: EpicRun):
    try:
        return LocalTaskSpec.model_validate(object_from_json(text)).bind(epic)
    except ValidationError:
        raise ContractError("TASK_SPEC_INVALID") from None


def check_run_binding(task: TaskRun, epic: EpicRun):
    if task.project_id != epic.project_id or task.epic_run_id != epic.id:
        raise ContractError("RUN_EPIC_MISMATCH")
