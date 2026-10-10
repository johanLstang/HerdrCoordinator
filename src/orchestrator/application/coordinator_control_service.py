"""The project connection exposes only its own two F38 operations."""

import re

from pydantic import ValidationError

from orchestrator.application.coordinator_runtime_service import CoordinatorError
from orchestrator.mcp.contracts import CoordinatorOverviewRequest, EpicClaimRequest, ToolResponse


class CoordinatorControlService:
    operations = frozenset({"coordinator_overview", "epic_claim_next"})

    def __init__(self, claims):
        self.claims = claims

    async def call(self, actor, operation, arguments):
        if operation not in self.operations:
            return ToolResponse(
                ok=False, code="UNKNOWN_OPERATION", message="tool is not registered"
            )
        try:
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
