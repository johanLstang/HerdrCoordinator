"""Operator-supplied epic requirements; a review request cannot replace these."""

from pathlib import PurePosixPath
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from orchestrator.domain.worker_contracts import ID, Contract, Text, canonical_json


def source_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 4096
        or path.is_absolute()
        or any(
            part in {"..", ".git", ".codex", ".agents", ".aws", ".ssh", ".herdr"}
            for part in path.parts
        )
        or any(char in value for char in "\x00\r\n\\")
        or str(path) != value
        or value == "."
    ):
        raise ValueError("review sources must be relative versioned repository files")
    return value


class EpicReviewSpec(Contract):
    project_id: ID
    epic_id: ID
    requirements: Annotated[list[Text], Field(min_length=1, max_length=128)]
    acceptance_criteria: Annotated[list[Text], Field(min_length=1, max_length=128)]
    sources: Annotated[list[Text], Field(min_length=1, max_length=32)]

    @field_validator("requirements", "acceptance_criteria")
    @classmethod
    def meaningful(cls, values):
        if any(not value.strip() or "\x00" in value for value in values):
            raise ValueError("epic requirements must be meaningful")
        return values

    @field_validator("sources")
    @classmethod
    def versioned_sources(cls, values):
        return [source_path(value) for value in values]

    @model_validator(mode="after")
    def bounded(self):
        if len(canonical_json(self.model_dump(mode="json")).encode()) > 65536:
            raise ValueError("epic review specification exceeds bounded contract")
        return self
