"""The project connection exposes its registered claim and review capabilities."""

import re

from pydantic import ValidationError

from orchestrator.application.coordinator_runtime_service import CoordinatorError
from orchestrator.mcp.contracts import (
    CoordinatorOverviewRequest,
    CoordinatorReviewContextRequest,
    CoordinatorReviewReadRequest,
    CoordinatorReviewSubmitRequest,
    EpicClaimRequest,
    ToolResponse,
)


class CoordinatorControlService:
    operations = frozenset({"coordinator_overview", "epic_claim_next"})

    REVIEW_MODELS = {
        "coordinator_review_context": CoordinatorReviewContextRequest,
        "coordinator_review_read": CoordinatorReviewReadRequest,
        "coordinator_review_submit": CoordinatorReviewSubmitRequest,
    }

    def __init__(self, claims, *, review=None):
        self.claims = claims
        self.review = review
        if review is not None:
            if review.runtime is not claims.runtime:
                raise CoordinatorError("COORDINATOR_REVIEW_RUNTIME_MISMATCH")
            self.operations = self.operations | self.REVIEW_MODELS.keys()

    async def call(self, actor, operation, arguments):
        if operation not in self.operations:
            return ToolResponse(
                ok=False, code="UNKNOWN_OPERATION", message="tool is not registered"
            )
        try:
            if self.review is not None and operation in self.REVIEW_MODELS:
                request = self.REVIEW_MODELS[operation].model_validate(arguments)
                if operation == "coordinator_review_context":
                    data = await self.review.context(actor, request.project_id,
                                                     request.epic_run_id, request.request_key)
                elif operation == "coordinator_review_read":
                    data = await self.review.read(actor, request.project_id, request.epic_run_id,
                                                  request.context_id, request.chunk_index)
                else:
                    data = await self.review.submit(actor, request.project_id, request.epic_run_id,
                                                    request.request_key, request.decision,
                                                    request.choices)
                return ToolResponse(ok=True, code="OK", message="verified Coordinator review",
                                    data=data)
            model = (
                EpicClaimRequest if operation == "epic_claim_next" else CoordinatorOverviewRequest
            )
            request = model.model_validate(arguments)
            if operation == "epic_claim_next":
                data = await self.claims.claim(actor, request.project_id, request.request_key)
            else:
                data = await self.claims.overview(actor, request.project_id)
            return ToolResponse(
                ok=True, code="OK", message="verified Coordinator result", data=data
            )
        except ValidationError:
            return ToolResponse(ok=False, code="INVALID_ARGUMENTS", message="invalid request")
        except CoordinatorError as error:
            code = str(error)
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", code):
                code = "COORDINATOR_OPERATION_UNVERIFIED"
            return ToolResponse(ok=False, code=code, message="Coordinator operation denied")
        except Exception:
            return ToolResponse(ok=False, code="COORDINATOR_OPERATION_UNVERIFIED",
                                message="reconcile the existing project operation")
