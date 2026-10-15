"""Operator review policy and bounded agent proposals; neither grants authority."""

import re
from typing import Annotated

from pydantic import Field, model_validator

from orchestrator.domain.epic_completion import CompletionCommand, EpicCompletionSpec
from orchestrator.domain.integration_contracts import EpicIntegrationSpec, IntegrationTask
from orchestrator.domain.teamplayer import ExternalID
from orchestrator.domain.worker_contracts import ID, Contract, Text, canonical_json


class CorrectiveTaskTemplate(Contract):
    template_id: ID
    criteria: Annotated[list[Text], Field(min_length=1, max_length=128)]
    entry: IntegrationTask

    @model_validator(mode="after")
    def bounded_identity(self):
        if not re.fullmatch(r"[A-Za-z0-9-]{1,60}", self.entry.task.task_id):
            raise ValueError("correction base identity must leave room for its revision")
        if len(set(self.criteria)) != len(self.criteria):
            raise ValueError("unique correction criteria required")
        return self


class CoordinatorReviewPolicy(Contract):
    native_project_id: ExternalID
    native_epic_id: ExternalID
    user_id: ExternalID
    native_task_ids: Annotated[list[ExternalID], Field(min_length=1, max_length=128)]
    original: EpicIntegrationSpec
    completion: EpicCompletionSpec
    correction_completion: EpicCompletionSpec | None = None
    verification: CompletionCommand
    templates: Annotated[list[CorrectiveTaskTemplate], Field(max_length=32)]
    max_context_chars: Annotated[int, Field(ge=1024, le=16 * 1024 * 1024)] = 4 * 1024 * 1024

    @model_validator(mode="after")
    def closed_policy(self):
        s, c = self.original, self.completion
        ids = [entry.task.task_id for entry in s.tasks]
        if len(self.native_task_ids) != len(ids) or len(set(self.native_task_ids)) != len(ids):
            raise ValueError("one actual native identity per original task required")
        if (c.project_id, c.epic_id) != (s.project_id, s.epic_id) or (
            list(c.task_ids) != ids or c.requirements != s.requirements
            or c.acceptance_criteria != s.acceptance_criteria or c.sources != s.sources
        ):
            raise ValueError("completion must describe the registered original epic")
        if self.templates and self.correction_completion is None:
            raise ValueError("corrections require explicit operator aggregate commands")
        if self.correction_completion is not None:
            extended = self.correction_completion
            if any(getattr(extended, name) != getattr(c, name) for name in (
                "project_id", "epic_id", "requirements", "acceptance_criteria", "sources",
                "acceptance_version", "task_ids",
            )):
                raise ValueError("correction aggregate cannot replace epic criteria or identity")
        templates = [entry.template_id for entry in self.templates]
        if len(set(templates)) != len(templates):
            raise ValueError("correction template identities must be unique")
        for entry in self.templates:
            task = entry.entry.task
            if (task.project_id, task.epic_id) != (s.project_id, s.epic_id) or (
                not set(entry.criteria) <= set(s.acceptance_criteria)
                or not set(task.dependencies) <= set(ids)
            ):
                raise ValueError("correction scope must belong to the configured epic")
        if len(canonical_json(self.model_dump(mode="json")).encode()) > 512 * 1024:
            raise ValueError("review policy exceeds bound")
        return self


class CorrectionChoice(Contract):
    issue_number: Annotated[int, Field(ge=1, le=32)]
    template_id: ID
