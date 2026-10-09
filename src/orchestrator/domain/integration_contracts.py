"""Operator configuration and native confirmation, never agent-granted authority."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from orchestrator.domain.review_contracts import source_path
from orchestrator.domain.worker_contracts import ID, Contract, LocalTaskSpec, Text, canonical_json


class IntegrationTask(Contract):
    priority: Literal["P0", "P1", "P2", "P3"]
    task: LocalTaskSpec


class EpicIntegrationSpec(Contract):
    project_id: ID
    epic_id: ID
    title: Text
    goal: Text
    requirements: Annotated[list[Text], Field(min_length=1, max_length=128)]
    acceptance_criteria: Annotated[list[Text], Field(min_length=1, max_length=128)]
    sources: Annotated[list[Text], Field(min_length=1, max_length=32)]
    project_instructions: Annotated[list[Text], Field(min_length=1, max_length=128)]
    dependencies: list[ID] = Field(default_factory=list)
    external_prerequisites: list[Text] = Field(default_factory=list)
    # The explicit list preserves backlog order; dependencies still gate later starts.
    tasks: Annotated[list[IntegrationTask], Field(min_length=1, max_length=128)]

    @field_validator(
        "title", "goal", "requirements", "acceptance_criteria", "project_instructions",
        "external_prerequisites",
    )
    @classmethod
    def meaningful(cls, value):
        values = value if isinstance(value, list) else [value]
        if any(not t.strip() or "\x00" in t for t in values):
            raise ValueError("meaningful epic instructions required")
        return value

    @field_validator("sources")
    @classmethod
    def versioned_sources(cls, value):
        return [source_path(v) for v in value]

    @model_validator(mode="after")
    def same_epic_and_bounded(self):
        identities = [t.task.task_id for t in self.tasks]
        if len(set(identities)) != len(identities) or any(
            t.task.project_id != self.project_id or t.task.epic_id != self.epic_id
            for t in self.tasks
        ):
            raise ValueError("each unique task must belong to the configured epic")
        if len(canonical_json(self.model_dump(mode="json")).encode()) > 65536:
            raise ValueError("epic assignment exceeds bound")
        return self
