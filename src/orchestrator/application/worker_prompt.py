"""Pure validated assignment rendering, with a packaged versioned Worker policy."""

from importlib.resources import files
from pathlib import Path

from pydantic import ValidationError

from orchestrator.domain.worker_contracts import (
    AssignmentContext,
    ContractError,
    WorkerAssignment,
    WorkerReport,
    canonical_json,
    check_run_binding,
    validate_task_json,
)


def load_local_task(path, epic):
    try:
        with Path(path).open("rb") as f:
            text = f.read(65537).decode("utf-8")
    except (OSError, UnicodeError):
        raise ContractError("LOCAL_TASK_UNAVAILABLE") from None
    return validate_task_json(text, epic)


def build_assignment(spec, task, epic):
    spec.bind(epic)
    check_run_binding(task, epic)
    if spec.task_id != task.task_id or task.base_commit is None:
        raise ContractError("ASSIGNMENT_RUN_MISMATCH")
    if not Path(task.worktree_path).is_absolute():
        raise ContractError("ASSIGNMENT_WORKTREE_INVALID")
    try:
        return WorkerAssignment(
            version=1,
            policy_version=1,
            report_version=1,
            context=AssignmentContext(
                project_id=task.project_id,
                epic_id=epic.epic_id,
                epic_run_id=epic.id,
                task_id=task.task_id,
                task_run_id=task.id,
                branch=task.branch,
                worktree_path=task.worktree_path,
                epic_branch=epic.branch,
                base_commit=task.base_commit,
            ),
            task=spec,
        )
    except ValidationError:
        raise ContractError("ASSIGNMENT_INVALID") from None


def render_worker_prompt(assignment: WorkerAssignment):
    # Installed wheels contain the policy; editable installs use this code checkout's policy.
    resource = files("orchestrator").joinpath("prompts/worker-v1.md")
    try:
        policy = (
            resource.read_text(encoding="utf-8")
            if resource.is_file()
            else (Path(__file__).resolve().parents[3] / "prompts/worker-v1.md").read_text()
        )
    except OSError:
        raise ContractError("WORKER_POLICY_UNAVAILABLE") from None
    return (
        policy.rstrip()
        + "\n\n## Assignment data (JSON)\n\n"
        + canonical_json(assignment.model_dump(mode="json"))
        + "\n\n## Final report JSON schema (version 1)\n\n"
        + canonical_json(WorkerReport.model_json_schema())
        + "\n"
    )
