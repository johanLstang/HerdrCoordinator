"""A routing request is descriptive input, never runtime or role authorization."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from orchestrator.domain.worker_contracts import Contract, canonical_json


class BlockerDetails(Contract):
    version: Literal[1] = 1
    reason: Annotated[str, Field(min_length=1, max_length=65536)]
    input_required: Annotated[str, Field(min_length=1, max_length=65536)]
    responsible_role: Literal["User", "Integration", "Coordinator"]

    @model_validator(mode="after")
    def meaningful_and_bounded(self):
        if (
            any(not t.strip() or "\x00" in t for t in (self.reason, self.input_required))
            or len(canonical_json(self.model_dump(mode="json")).encode()) > 131072
        ):
            raise ValueError("a bounded reason and concrete input are required")
        return self

    def message(self):
        return (
            f"Reason: {self.reason}\nInput required: {self.input_required}"
            f"\nResponsible role: {self.responsible_role}"
        )


class ReviewBlockDecision(BlockerDetails):
    result: Literal["NEEDS_INPUT"]
    context_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def bounded_review_decision(self):
        if len(canonical_json(self.model_dump(mode="json")).encode()) > 16384:
            raise ValueError("review input decision exceeds bound")
        return self
