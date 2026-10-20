"""An operator's finite project plan. Tool arguments never supply executable specs."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from orchestrator.domain.integration_contracts import EpicIntegrationSpec
from orchestrator.domain.review_contracts import source_path
from orchestrator.domain.teamplayer import ExternalID
from orchestrator.domain.worker_contracts import ID, Contract, Text


class CoordinatorEpic(Contract):
    native_epic_id: ExternalID
    native_task_ids: Annotated[list[ExternalID], Field(min_length=1, max_length=128)]
    priority: Literal["P0", "P1", "P2", "P3"]
    spec: EpicIntegrationSpec

    @model_validator(mode="after")
    def unique_task_mapping(self):
        if len(set(self.native_task_ids)) != len(self.native_task_ids) or (
            len(self.native_task_ids) != len(self.spec.tasks)
        ):
            raise ValueError("each task requires exactly one native identity")
        return self


class CoordinatorSpec(Contract):
    project_id: ID
    native_project_id: ExternalID
    title: Text
    sources: Annotated[list[Text], Field(min_length=1, max_length=32)]
    # Operator list order is the priority tie breaker, independent of board order.
    epics: Annotated[list[CoordinatorEpic], Field(min_length=1, max_length=128)]

    @field_validator("sources")
    @classmethod
    def versioned_sources(cls, value):
        return [source_path(source) for source in value]

    @model_validator(mode="after")
    def closed_plan(self):
        native = [e.native_epic_id for e in self.epics]
        local = [e.spec.epic_id for e in self.epics]
        tasks = [t for e in self.epics for t in e.native_task_ids]
        local_tasks = [t.task.task_id for e in self.epics for t in e.spec.tasks]
        if len(set(native)) != len(native) or len(set(local)) != len(local) or (
            len(set(tasks)) != len(tasks)
        ) or set(tasks) & set(native) or len(set(local_tasks)) != len(local_tasks):
            raise ValueError("project identities must be unique")
        for epic in self.epics:
            if epic.spec.project_id != self.project_id or any(
                d not in local or d == epic.spec.epic_id for d in epic.spec.dependencies
            ):
                raise ValueError("epic dependencies must belong to this project plan")
        graph = {e.spec.epic_id: e.spec.dependencies for e in self.epics}
        checked = set()

        def visit(node, path):
            if node in path:
                raise ValueError("cyclic epic dependencies")
            if node in checked:
                return
            for dependency in graph[node]:
                visit(dependency, path | {node})
            checked.add(node)

        for node in graph:
            visit(node, set())
        owners = {t.task.task_id: e.spec.epic_id for e in self.epics for t in e.spec.tasks}
        for epic in self.epics:
            for task in epic.spec.tasks:
                if any(
                    d not in owners or owners[d] not in {
                        epic.spec.epic_id, *epic.spec.dependencies,
                    } for d in task.task.dependencies
                ):
                    raise ValueError("task dependencies require a configured epic dependency")
        return self


class RuntimeGateEvidence(Contract):
    """Trusted operator probe result, never accepted in an MCP request."""

    configuration_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    main_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    probe_source: Text
    outside_read_denied: bool
    protected_state_denied: bool
    git_role_boundaries_verified: bool
    verification_process_isolated: bool
    input_readiness_verified: bool

    @property
    def ready(self):
        return all((
            self.outside_read_denied, self.protected_state_denied,
            self.git_role_boundaries_verified, self.verification_process_isolated,
            self.input_readiness_verified,
        ))
