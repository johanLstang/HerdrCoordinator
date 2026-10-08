"""Recheck an already performed delivery without pretending its old base is current."""

from orchestrator.application.git_integration_service import GitIntegrationService, IntegrationError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.domain.states import TaskState
from orchestrator.domain.worker_contracts import canonical_json


def known_merge(settings, store, task, parent, *, require_tip=True):
    manager = GitIntegrationService(settings, store)
    epic = store.get_epic(task.epic_run_id)
    r = parent.result
    if (
        parent.kind != "task_merge"
        or parent.status not in {"PENDING", "SUCCEEDED"}
        or parent.project_id != task.project_id
        or parent.task_run_id != task.id
        or parent.epic_run_id != task.epic_run_id
        or task.internal_status not in {TaskState.MERGING, TaskState.DONE}
    ):
        raise IntegrationError("DELIVERY_INTENT_UNVERIFIED")
    approval = store.get_operation(task.project_id, "task_approve", r["approval_key"])
    if (
        approval is None
        or approval.status != "SUCCEEDED"
        or approval.id != r["approval_id"]
        or digest(canonical_json(approval.model_dump(mode="json"))) != r["approval_digest"]
    ):
        raise IntegrationError("DELIVERY_APPROVAL_CHANGED")
    context = store.get_operation(task.project_id, "task_review_request", r["context_key"])
    if (
        context is None
        or context.status != "SUCCEEDED"
        or context.task_run_id != task.id
        or context.epic_run_id != task.epic_run_id
        or context.result.get("context", {}).get("context_id") != r["context_id"]
        or digest(canonical_json(context.model_dump(mode="json"))) != r["context_digest"]
    ):
        raise IntegrationError("DELIVERY_CONTEXT_CHANGED")
    merge = store.get_operation(task.project_id, "merge_task_to_epic", parent.id)
    reviews = store.get_reviews(task.id)
    source, target = r["source_commit"], r["target_commit"]
    sha = task.merge_commit
    if (
        merge is None
        or merge.status != "SUCCEEDED"
        or merge.task_run_id != task.id
        or merge.epic_run_id != epic.id
        or merge.result.get("requires_reconciliation") is not False
        or (merge.result.get("source_commit"), merge.result.get("target_commit"))
        != (source, target)
        or merge.result.get("merge_commit") != sha
        or not sha
        or manager.git.parents(sha) != (target, source)
        or manager.git.find_operation_merge(epic.branch, merge.id, target, source) != sha
        or not manager.git.contains_commit(epic.branch, sha)
        or task.current_commit != source
        or (task.approved_source_commit, task.approved_target_commit) != (source, target)
        or manager.git.head(task.branch) != source
        or not reviews
        or reviews[-1].id != r["review_id"]
        or reviews[-1].review_result != "APPROVED"
        or (reviews[-1].review_commit, reviews[-1].epic_commit) != (source, target)
    ):
        raise IntegrationError("DELIVERY_MERGE_UNVERIFIED")
    if require_tip and manager._owned_pair(task, epic) != (source, sha):
        raise IntegrationError("DELIVERY_RESULT_CHANGED")
    return sha


def passed_test(store, task, parent, sha):
    r = parent.result
    test = store.get_operation(task.project_id, "task_delivery_test", r["test_key"])
    if (
        test is None
        or test.id != r["test_id"]
        or test.task_run_id != task.id
        or test.epic_run_id != task.epic_run_id
        or test.status != "SUCCEEDED"
        or test.result.get("exit_code") != 0
        or test.result.get("delivery_id") != parent.id
        or test.result.get("merge_commit") != sha
        or test.result.get("source_commit") != r["source_commit"]
        or test.result.get("target_commit") != r["target_commit"]
        or test.result.get("command_hash") != r["command_hash"]
        or test.result.get("timeout") != r["test_timeout"]
    ):
        raise IntegrationError("DELIVERY_TEST_UNVERIFIED")
    return test
