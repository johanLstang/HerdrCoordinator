"""Immutable operator aggregate verification, absent from agent tool arguments."""

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.worker_contracts import Contract, Text


class CompletionCommand(BaseModel):
    model_config = Contract.model_config
    argv: Annotated[tuple[str, ...], Field(min_length=1, max_length=128)]
    timeout: Annotated[int, Field(strict=True, ge=1, le=3600)] = 300

    @field_validator("argv")
    @classmethod
    def valid_arguments(cls, values):
        if any(not v or "\x00" in v or len(v) > 4096 for v in values):
            raise ValueError("bounded explicit argv required")
        return values


class EpicCompletionSpec(EpicReviewSpec):
    # version is the wire format; acceptance_version is the operator criteria revision.
    acceptance_version: Annotated[int, Field(strict=True, ge=1)]
    task_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=128)]
    build: CompletionCommand
    test: CompletionCommand
    acceptance: CompletionCommand
    known_limitations: tuple[Text, ...] = ()

    @field_validator("task_ids")
    @classmethod
    def unique_tasks(cls, values):
        if len(set(values)) != len(values) or any(not v.strip() or "\x00" in v for v in values):
            raise ValueError("nonempty unique task scope required")
        return values
