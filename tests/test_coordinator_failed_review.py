"""Real Git/SQLite/subprocess evidence; failed tests never produce an approval."""

import sys

import pytest
from test_epic_integration import deliver_tasks, epic_service
from test_epic_integration import setup_fixture as foundation  # noqa: F401

from orchestrator.application.git_integration_service import IntegrationError
from orchestrator.application.state_service import StateService
from orchestrator.domain.policy import VerifiedFacts
from orchestrator.domain.states import EpicState


def ready(setup):
    service, actor, epic = deliver_tasks(setup)
    actual = service.verify_epic(actor, epic.id, key="baseline-ready")
    assert actual.status == "SUCCEEDED" and actual.result["exit_code"] == 0
    StateService(service.store).transition_epic(
        epic.id, EpicState.READY_FOR_REVIEW, expected=EpicState.ACTIVE,
        event_id="baseline-handoff", actor=actor,
        facts=VerifiedFacts(scope_complete=True, tests_passed=True,
                            verification_commit=actual.result["source_commit"]),
    )
    return service, actor, epic


def test_current_failed_extra_verification_supports_only_explicit_rejection(setup):
    original, actor, epic = ready(setup)
    service = epic_service(original, command=(sys.executable, "-c", "raise SystemExit(7)"))
    check = service.verify_epic(actor, epic.id, key="independent-check")
    assert check.status == "FAILED" and check.result["exit_code"] == 7
    with pytest.raises(IntegrationError):
        service.register_epic_review(
            actor, epic.id, verification_key=check.idempotency_key, key="default-reject",
            approved=False, feedback="Missing edge case",
        )
    with pytest.raises(IntegrationError):
        service.register_epic_review(
            actor, epic.id, verification_key=check.idempotency_key, key="cannot-approve",
            approved=True, allow_failed_rejection=True,
        )
    result = service.register_epic_review(
        actor, epic.id, verification_key=check.idempotency_key, key="reject-current",
        approved=False, feedback="Independent check reproduces a missing edge case.",
        allow_failed_rejection=True,
    )
    saved = service.store.get_epic(epic.id)
    assert saved.status == EpicState.CHANGES_REQUESTED
    assert saved.approved_source_commit is saved.approved_target_commit is None
    assert result.result["source_commit"] == check.result["source_commit"]
    assert result.result["target_commit"] == check.result["target_commit"]
    assert result.result["approved"] is False
    assert service.store.get_operations(epic.id, kind="merge_epic_to_main") == []


def test_unfinished_verification_is_not_relabelled_as_failed_criterion(setup):
    original, actor, epic = ready(setup)
    service = epic_service(original, command=(sys.executable, "-c", "import time;time.sleep(1)"))
    service.test_timeout = 0.01
    check = service.verify_epic(actor, epic.id, key="timeout")
    assert check.status == "FAILED" and check.error_code == "TEST_PROCESS_FAILED"
    with pytest.raises(IntegrationError):
        service.register_epic_review(
            actor, epic.id, verification_key=check.idempotency_key, key="unknown-reject",
            approved=False, feedback="Unavailable result", allow_failed_rejection=True,
        )
    assert service.store.get_epic(epic.id).status == EpicState.READY_FOR_REVIEW
