"""Versioned Integration review policy; repository context is data, not authority."""

from importlib.resources import files
from pathlib import Path

from orchestrator.domain.worker_contracts import ContractError, canonical_json


def render_review_prompt(context):
    if context.get("version") != 1 or context.get("reviewable") is not True:
        raise ContractError("REVIEW_CONTEXT_NOT_REVIEWABLE")
    resource = files("orchestrator").joinpath("prompts/integration-review-v1.md")
    policy = (
        resource.read_text(encoding="utf-8")
        if resource.is_file()
        else (Path(__file__).resolve().parents[3] / "prompts/integration-review-v1.md").read_text()
    )
    return policy + "\n\nVERIFIED_REVIEW_CONTEXT_JSON:\n" + canonical_json(context)
