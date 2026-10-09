"""Shared validation of saved input; no caller-supplied runtime facts."""

from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.task_attention_service import attention_hash, attention_subject
from orchestrator.domain.attention import InputDecision
from orchestrator.domain.worker_contracts import canonical_json

INPUT_IMMUTABLE = (
    "actor",
    "decision",
    "subject",
    "attention_hash",
    "stop_id",
    "resume_key",
    "correlation_id",
    "resume_state",
)


def input_hash(data):
    return digest(canonical_json({k: data[k] for k in INPUT_IMMUTABLE}))


def verified_input(store, task, op):
    """Check retained actual F31 source and stop, including after generation changes."""
    data = op.result
    decision = InputDecision.model_validate(data["decision"])
    attention = next(
        (
            o
            for o in store.get_operations(task.epic_run_id, kind="task_attention")
            if o.id == decision.blocker_id
        ),
        None,
    )
    start = store.get_operation(task.project_id, "start_runtime", task.id)
    if attention is None or start is None:
        raise ValueError("INPUT_SOURCE_MISSING")
    stop = store.get_operation(task.project_id, "stop_runtime", attention.result["stop_key"])
    block = store.get_event(task.project_id, attention.result["block_event_id"])
    original = next(
        (
            o
            for o in store.get_operations(
                task.epic_run_id,
                kind="worker_report"
                if attention.result["source_kind"] == "worker"
                else "task_review_request",
            )
            if o.id == attention.result["source_id"]
        ),
        None,
    )
    current = attention_subject(task, start)
    subject = data["subject"]
    if not (
        op.task_run_id == task.id
        and op.epic_run_id == task.epic_run_id
        and op.project_id == task.project_id
        and op.idempotency_key == decision.input_id
        and data["intent_hash"] == input_hash(data)
        and subject == attention.result["subject"]
        and all(current[k] == v for k, v in subject.items() if k != "generation")
        and data["actor"]["role"] == "Integration"
        and data["actor"]["project_id"] == task.project_id
        and data["actor"]["epic_run_id"] == task.epic_run_id
        and attention.result["intent_hash"]
        == data["attention_hash"]
        == attention_hash(attention.result)
        and attention.task_run_id == task.id
        and attention.epic_run_id == task.epic_run_id
        and attention.result["stage"] in {"PARKED_AND_SYNCED", "SYNC_PENDING"}
        and stop
        and stop.id == data["stop_id"] == attention.result["stop_id"]
        and stop.task_run_id == task.id
        and stop.epic_run_id == task.epic_run_id
        and stop.status == "SUCCEEDED"
        and stop.result["stage"] == "STOPPED"
        and stop.result["inactive"] is True
        and stop.result["generation"] == subject["generation"]
        and block
        and attention.result["block_event_hash"]
        == digest(canonical_json(block.model_dump(mode="json")))
        and original
        and original.status == "SUCCEEDED"
        and attention.result["source_hash"]
        == digest(canonical_json(original.model_dump(mode="json")))
        and data["resume_key"] == "input-resume:" + op.id
    ):
        raise ValueError("INPUT_INTENT_CHANGED")
    park = store.get_event(task.project_id, "runtime-park:" + stop.id)
    if park is None or park.result["resume_state"] != data["resume_state"]:
        raise ValueError("INPUT_PARK_HISTORY_CHANGED")
    return start, attention, stop
