"""Project-level Coordinator records, separate from real epic/task runs."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from orchestrator.domain.models import Commit, Identity, Record, utc_now


class CoordinatorRun(Record):
    project_id: Identity
    actor_id: Identity
    branch: Literal["main"] = "main"
    worktree_path: Identity
    initial_commit: Commit
    configuration_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    status: Literal["STARTING", "ACTIVE", "STOPPED"] = "STARTING"
    coordinator_agent_id: Identity | None = None
    herdr_workspace_id: Identity | None = None
    herdr_server_session: Identity | None = None
    herdr_tab_id: Identity | None = None
    herdr_pane_id: Identity | None = None
    herdr_terminal_id: Identity | None = None
    codex_session_id: Identity | None = None
    started_at: AwareDatetime = Field(default_factory=utc_now)
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def registered_identity(self):
        if self.status == "ACTIVE" and any(
            getattr(self, name) is None
            for name in (
                "coordinator_agent_id", "herdr_workspace_id", "herdr_server_session",
                "herdr_tab_id", "herdr_pane_id", "herdr_terminal_id", "codex_session_id",
            )
        ):
            raise ValueError("active Coordinator requires actual registered native identities")
        if (self.status == "STOPPED") != (self.completed_at is not None):
            raise ValueError("only stopped Coordinator has a completion time")
        return self


class ProjectOperation(Record):
    project_id: Identity
    coordinator_run_id: Identity
    kind: Identity
    idempotency_key: Identity
    status: Literal["PENDING", "SUCCEEDED", "FAILED"] = "PENDING"
    result: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)
