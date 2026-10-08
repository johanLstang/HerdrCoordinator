"""Normalize report claims against trusted runtime provenance, never mark Ready or Done."""

import re

from pydantic import ValidationError

from orchestrator.domain.worker_contracts import (
    ContractError,
    WorkerReport,
    check_run_binding,
    object_from_json,
)

LEGACY_FIELDS = {
    "VERSION",
    "STATUS",
    "TASK",
    "TASK_ID",
    "TASK_RUN_ID",
    "RUN_ID",
    "PROJECT_ID",
    "EPIC_ID",
    "EPIC_RUN_ID",
    "BRANCH",
    "COMMIT",
    "SUMMARY",
    "TESTS",
    "FILES_CHANGED",
    "KNOWN_ISSUES",
    "REASON",
    "INPUT_REQUIRED",
}


def _legacy_fields(text):
    fields, current = {}, None
    for line in text.splitlines():
        match = re.match(r"^([A-Z][A-Z_]*):\s*(.*)$", line)
        if match:
            key, value = match.groups()
            if key not in LEGACY_FIELDS or key in fields:
                raise ContractError("LEGACY_FIELD_INVALID")
            fields[key], current = value, key
        elif line.strip():
            if current is None:
                raise ContractError("LEGACY_FORMAT_INVALID")
            fields[current] += "\n" + line
    if ("TASK" in fields and "TASK_ID" in fields) or (
        "RUN_ID" in fields and "TASK_RUN_ID" in fields
    ):
        raise ContractError("LEGACY_IDENTITY_AMBIGUOUS")
    if fields.get("VERSION", "1") != "1":
        raise ContractError("REPORT_VERSION_UNSUPPORTED")
    return {k: v.strip() for k, v in fields.items()}


def _lines(value):
    if value.strip().casefold() in {"", "none", "inga", "no known issues"}:
        return []
    return [line.strip().removeprefix("- ").strip() for line in value.splitlines() if line.strip()]


def parse_worker_report(text, *, task, epic, source_session_id):
    """source_session_id comes from the trusted adapter, never the report body/tool caller."""
    check_run_binding(task, epic)
    if task.codex_session_id is None or source_session_id != task.codex_session_id:
        raise ContractError("REPORT_SESSION_MISMATCH")
    if not isinstance(text, str) or len(text.encode()) > 65536:
        raise ContractError("REPORT_TOO_LARGE")
    expected = dict(
        project_id=task.project_id,
        epic_id=epic.epic_id,
        epic_run_id=epic.id,
        task_id=task.task_id,
        task_run_id=task.id,
        branch=task.branch,
    )
    if text.lstrip().startswith("{"):
        value = object_from_json(text)
    else:
        old = _legacy_fields(text)
        # No alias can override trusted run/session identity; absent fields are bound by provenance.
        for key, canonical in [
            ("TASK", "task_id"),
            ("TASK_ID", "task_id"),
            ("RUN_ID", "task_run_id"),
            ("TASK_RUN_ID", "task_run_id"),
            ("PROJECT_ID", "project_id"),
            ("EPIC_ID", "epic_id"),
            ("EPIC_RUN_ID", "epic_run_id"),
            ("BRANCH", "branch"),
        ]:
            if key in old and old[key] != expected[canonical]:
                raise ContractError("REPORT_IDENTITY_MISMATCH")
        if old.get("STATUS") == "READY_FOR_REVIEW" and not (
            ("TASK" in old or "TASK_ID" in old) and "BRANCH" in old
        ):
            raise ContractError("LEGACY_IDENTITY_INCOMPLETE")
        commit = old.get("COMMIT") or None
        if commit and len(commit) != 40:
            if (
                not re.fullmatch(r"[0-9a-f]{7,39}", commit)
                or task.current_commit is None
                or not task.current_commit.startswith(commit)
            ):
                raise ContractError("LEGACY_COMMIT_UNRESOLVED")
            commit = task.current_commit
        value = expected | dict(
            version=1,
            status=old.get("STATUS"),
            commit=commit,
            summary=old.get("SUMMARY") or old.get("REASON", ""),
            test_summary=old.get("TESTS", ""),
            tests=[],
            files_changed=_lines(old.get("FILES_CHANGED", "")),
            limitations=_lines(old.get("KNOWN_ISSUES", "")),
            reason=old.get("REASON", ""),
            input_required=old.get("INPUT_REQUIRED", ""),
        )
    try:
        report = WorkerReport.model_validate(value)
    except ValidationError:
        raise ContractError("REPORT_INVALID") from None
    if any(getattr(report, k) != v for k, v in expected.items()):
        raise ContractError("REPORT_IDENTITY_MISMATCH")
    return report
