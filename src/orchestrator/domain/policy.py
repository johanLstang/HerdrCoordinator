from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from orchestrator.domain.models import Commit, Identity


class Role(StrEnum):
    COORDINATOR = "Coordinator"
    INTEGRATION = "Integration"
    WORKER = "Worker"


class Actor(BaseModel):
    """Internal principal supplied by a trusted connection registry, never tool arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    actor_id: Identity
    role: Role
    project_id: Identity
    epic_run_id: Identity | None = None
    task_run_id: Identity | None = None


class VerifiedFacts(BaseModel):
    """Internal service evidence, never an agent tool argument.

    Subsequent adapter/review features must verify these facts before constructing them.
    This foundation does not implement real Git or runtime verification.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True, strict=True)
    source_commit: Commit | None = None
    target_commit: Commit | None = None
    merge_commit: Commit | None = None
    verification_commit: Commit | None = None
    tests_passed: bool = False
    review_approved: bool = False
    dependencies_ready: bool = False
    slot_reserved: bool = False
    start_confirmed: bool = False
    inactivity_confirmed: bool = False
    scope_complete: bool = False
    reason: str = ""
