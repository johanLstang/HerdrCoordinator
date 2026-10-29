"""Trusted recovery observations; no agent supplied roles, loss or inactivity claims."""

from orchestrator.application.runtime_assignment_service import digest
from orchestrator.domain.worker_contracts import canonical_json

KIND = "session_recovery"


def fingerprint(value):
    return digest(canonical_json(value))


def task_record(store, task):
    """Validate the current overlay against original registration and exact run.

    A stale observation is never silently used to authorize a board write.
    Only the operator recovery service creates these records.
    """
    op = store.get_operation(task.project_id, KIND, task.id)
    if op is None:
        return None
    data = op.result
    if data.get("stage") != "ATTENTION" and data.get("subject") != task.model_dump(mode="json"):
        return None  # The business lifecycle has advanced beyond this observation.
    registration = store.get_operation(task.project_id, "epic_start", task.epic_run_id)
    from orchestrator.application.epic_start_service import intent_hash

    if (
        op.project_id != task.project_id
        or op.task_run_id != task.id
        or op.epic_run_id != task.epic_run_id
        or op.status != "SUCCEEDED"
        or registration is None
        or registration.status != "SUCCEEDED"
        or registration.result.get("stage") != "REGISTERED"
        or registration.result.get("intent_hash") != intent_hash(registration.result)
        or data.get("registration_id") != registration.id
        or data.get("registration_hash") != registration.result.get("intent_hash")
        or data.get("principal") != registration.result.get("integration_principal")
        or data.get("subject") != task.model_dump(mode="json")
        or data.get("observation_hash")
        != fingerprint({k: v for k, v in data.items() if k != "observation_hash"})
        or data.get("stage")
        not in {
            "CONNECTED",
            "ATTENTION",
            "WAITING_INPUT",
            "WAITING_CAPACITY",
            "INPUT_PENDING",
            "ORIGINAL_OPERATION_REQUIRED",
        }
    ):
        raise ValueError("RECOVERY_OBSERVATION_CHANGED")
    return op
