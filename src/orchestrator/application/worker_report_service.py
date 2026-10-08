"""Native report provenance plus independent Git/test evidence; never merge or mark Done."""

import os
import re
from pathlib import Path

from orchestrator.adapters.codex import CodexError
from orchestrator.adapters.git import GitError
from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.git_integration_service import GitIntegrationService, IntegrationError
from orchestrator.application.git_review_service import GitReviewError
from orchestrator.application.runtime_assignment_service import (
    AssignmentError,
    RuntimeAssignmentService,
    digest,
)
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.worker_report import parse_worker_report
from orchestrator.application.worktree_service import WorktreeError
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Actor, Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import ContractError, canonical_json
from orchestrator.persistence.store import StoreError


class ReportError(RuntimeError):
    """Safe report rejection; Git, slot and history are preserved."""


class WorkerReportService:
    KIND = "worker_report"

    def __init__(self, settings, store, herdr, codex=None):
        self.settings, self.store = settings, store
        self.assignment = RuntimeAssignmentService(settings, store, herdr, codex)
        # Do not hand operator credentials/GIT/PYTHON environment to task test code.
        # Filesystem/process isolation of untrusted code remains an F-17 requirement.
        self.integration = GitIntegrationService(
            settings,
            store,
            test_command=settings.worker_test_command,
            test_timeout=settings.worker_test_timeout,
            test_environment={
                "PATH": os.environ.get("PATH", os.defpath),
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

    def _scope(self, actor, run_id):
        task = self.store.get_task(run_id)
        if task is None or actor.role not in {Role.WORKER, Role.INTEGRATION}:
            raise ReportError("REPORT_SCOPE_DENIED")
        StateService.authorize_scope(actor, task)
        epic = self.store.get_epic(task.epic_run_id)
        if epic is None or epic.status != EpicState.ACTIVE:
            raise ReportError("EPIC_NOT_ACTIVE")
        # Service principal is derived only from authenticated scope, not a Worker role argument.
        verifier = Actor(
            actor_id="report-verifier:" + actor.actor_id,
            role=Role.INTEGRATION,
            project_id=task.project_id,
            epic_run_id=epic.id,
        )
        return task, epic, verifier

    def _native(self, verifier, task):
        run, start = self.assignment._validate(verifier, task.id)
        dispatch = self.store.get_operation(task.project_id, self.assignment.KIND, task.id)
        if dispatch is None or dispatch.status != "SUCCEEDED" or not task.codex_session_id:
            raise ReportError("REPORT_START_UNCONFIRMED")
        facts = self.assignment._runtime(run, start)
        if facts["session_id"] != task.codex_session_id:
            raise ReportError("REPORT_SESSION_MISMATCH")
        thread = self.assignment.codex.read_thread(task.codex_session_id, task.worktree_path)
        if (
            thread["id"] != task.codex_session_id
            or thread["sessionId"] != task.codex_session_id
            or Path(thread["cwd"]) != Path(task.worktree_path)
        ):
            raise ReportError("REPORT_SESSION_MISMATCH")
        ack = dispatch.result["ack"]
        if (
            dispatch.epic_run_id != task.epic_run_id
            or dispatch.task_run_id != task.id
            or dispatch.result["start_operation_id"] != start.id
            or ack["session_id"] != task.codex_session_id
        ):
            raise ReportError("REPORT_ACK_BINDING_MISMATCH")
        turns = thread["turns"]
        positions = [i for i, t in enumerate(turns) if t["id"] == ack["turn_id"]]
        if len(positions) != 1:
            raise ReportError("REPORT_ACK_MISSING")
        ack_turn = turns[positions[0]]
        ack_items = [
            i
            for i, item in enumerate(ack_turn["items"])
            if item["id"] == ack["item_id"]
            and item["type"] == "agentMessage"
            and digest(item["text"]) == ack["message_hash"]
        ]
        delivered = any(
            item["type"] == "userMessage"
            and any(
                part.get("type") == "text"
                and digest(part.get("text", "")) == dispatch.result["prompt_hash"]
                for part in item.get("content", [])
            )
            for item in ack_turn["items"]
        )
        if len(ack_items) != 1 or not delivered:
            raise ReportError("REPORT_ACK_PROVENANCE_CHANGED")
        corrections = [
            op
            for op in self.store.get_operations(task.epic_run_id, kind="task_request_changes")
            if op.task_run_id == task.id and op.result.get("review_id") is not None
        ]
        correction = max(corrections, key=lambda op: op.created_at) if corrections else None
        if correction is not None:
            if correction.status != "SUCCEEDED":
                raise ReportError("REPORT_CORRECTION_UNCONFIRMED")
            ack = correction.result["ack"]
            if (
                ack["session_id"] != task.codex_session_id
                or correction.result["session_id"] != task.codex_session_id
                or correction.result["branch"] != task.branch
                or correction.result["worktree_path"] != task.worktree_path
                or correction.result["worker_agent_id"] != task.worker_agent_id
                or correction.result["start_operation_id"] != start.id
            ):
                raise ReportError("REPORT_CORRECTION_BINDING_CHANGED")
            positions = [i for i, turn in enumerate(turns) if turn["id"] == ack["turn_id"]]
            if len(positions) != 1:
                raise ReportError("REPORT_CORRECTION_ACK_MISSING")
            ack_turn = turns[positions[0]]
            ack_items = [
                i
                for i, item in enumerate(ack_turn["items"])
                if item["id"] == ack["item_id"]
                and item["type"] == "agentMessage"
                and digest(item["text"]) == ack["message_hash"]
            ]
            delivered = any(
                item["type"] == "userMessage"
                and any(
                    part.get("type") == "text"
                    and digest(part.get("text", "")) == correction.result["prompt_hash"]
                    for part in item.get("content", [])
                )
                for item in ack_turn["items"]
            )
            if len(ack_items) != 1 or not delivered:
                raise ReportError("REPORT_CORRECTION_ACK_CHANGED")
        relevant = [ack_turn | {"items": ack_turn["items"][ack_items[0] + 1 :]}] + turns[
            positions[0] + 1 :
        ]
        if any(t["status"] == "inProgress" for t in relevant):
            raise ReportError("REPORT_TURN_NOT_FINISHED")
        candidates = [
            (t, item)
            for t in relevant
            if t["status"] == "completed"
            for item in t["items"]
            if item["type"] == "agentMessage"
            and item.get("phase") in {None, "final_answer"}
            and (t["id"], item["id"]) != (ack["turn_id"], ack["item_id"])
        ]
        if not candidates:
            raise ReportError("REPORT_NOT_AVAILABLE")
        turn, item = candidates[-1]
        if len(item["text"].encode()) > 65536:
            raise ReportError("REPORT_TOO_LARGE")
        observed = self.integration.worktrees.verify_owned_worktree(task)
        bound = task.model_copy(update={"current_commit": observed})
        report = parse_worker_report(
            item["text"],
            task=bound,
            epic=self.store.get_epic(task.epic_run_id),
            source_session_id=thread["id"],
        )
        if report.status == "READY_FOR_REVIEW" and not facts["ready"]:
            raise ReportError("REPORT_RUNTIME_NOT_IDLE")
        return report, {
            "session_id": thread["id"],
            "turn_id": turn["id"],
            "item_id": item["id"],
            "message_hash": digest(item["text"]),
            "assignment_operation_id": dispatch.id,
            **({"correction_operation_id": correction.id} if correction else {}),
        }

    def _git(self, verifier, task, report):
        evidence = self.integration.reviews.task_review(
            verifier, task.id, expected_commit=report.commit
        )
        if not evidence.reviewable or evidence.source.current_commit != report.commit:
            raise ReportError("REPORT_GIT_UNVERIFIED")
        # Record observed paths separately; claims cannot substitute for actual Git facts.
        actual_files = sorted(f.path for f in evidence.source.changed_files)
        if report.files_changed and sorted(report.files_changed) != actual_files:
            raise ReportError("REPORT_CHANGED_FILES_MISMATCH")
        if any(t.exit_code != 0 for t in report.tests):
            raise ReportError("REPORT_CLAIMED_TEST_FAILED")
        return evidence, actual_files

    def _finish(self, op, status, *, error=None, **result):
        current = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
        if current.status == "SUCCEEDED":
            return current
        current = current.model_copy(
            update={
                "status": status,
                "error_code": error,
                "result": current.result | result,
                "updated_at": utc_now(),
            }
        )
        self.store.update_operation(current)
        return current

    def collect(self, actor, task_run_id, *, expected_status=None, retry_key=None):
        # Only the supervisor may deliberately rerun unknown/failed verification.
        if retry_key is not None and (
            actor.role != Role.INTEGRATION or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", retry_key)
        ):
            raise ReportError("REPORT_RETRY_DENIED")
        try:
            with self.integration._lock(), self.store.transaction():
                task, epic, verifier = self._scope(actor, task_run_id)
                report, provenance = self._native(verifier, task)
                if expected_status is not None and report.status != expected_status:
                    raise ReportError("REPORT_STATUS_MISMATCH")
                key = (
                    task.id
                    + ":"
                    + digest(canonical_json(provenance))
                    + (":" + retry_key if retry_key else "")
                )
                op = self.store.get_operation(task.project_id, self.KIND, key)
                if op and op.status == "SUCCEEDED":
                    if report.status == "READY_FOR_REVIEW":
                        self._git(verifier, task, report)
                    return {
                        "status": "EXISTING",
                        "operation_id": op.id,
                        "task": task.model_dump(mode="json"),
                    }
                if task.internal_status != TaskState.WORKING:
                    raise ReportError("REPORT_TASK_NOT_WORKING")
                if op and op.status == "FAILED":
                    raise ReportError("REPORT_VERIFICATION_FAILED")
                if report.status == "READY_FOR_REVIEW":
                    if not self.integration.test_command:
                        raise ReportError("REPORT_TEST_COMMAND_REQUIRED")
                    evidence, files = self._git(verifier, task, report)
                if op is None:
                    op = Operation(
                        project_id=task.project_id,
                        epic_run_id=epic.id,
                        task_run_id=task.id,
                        kind=self.KIND,
                        idempotency_key=key,
                        result={
                            "report": report.model_dump(mode="json"),
                            "provenance": provenance,
                            "stage": "RECEIVED",
                            "event_id": "worker-report:" + key,
                        },
                    )
                    self.store.add_operation(op)
                if report.status == "BLOCKED":
                    task = StateService(self.store).transition_task(
                        task.id,
                        TaskState.BLOCKED,
                        expected=TaskState.WORKING,
                        event_id=op.result["event_id"],
                        actor=actor,
                        facts=VerifiedFacts(
                            reason=report.reason + "\nInput: " + report.input_required
                        ),
                    )
                    self._finish(op, "SUCCEEDED", stage="BLOCKED")
                    return {
                        "status": "BLOCKED",
                        "operation_id": op.id,
                        "task": task.model_dump(mode="json"),
                    }
            # Persist report intent before starting independent operator-selected tests.
            verification = self.integration.verify_task(
                verifier, task.id, key="report-test:" + op.id
            )
            if verification.status != "SUCCEEDED" or verification.result.get("exit_code") != 0:
                with self.store.transaction():
                    self._finish(
                        op, "FAILED", error="REPORT_TEST_FAILED", verification_id=verification.id
                    )
                raise ReportError("REPORT_TEST_FAILED")
            with self.integration._lock(), self.store.transaction():
                task, epic, verifier = self._scope(actor, task.id)
                current_report, current_source = self._native(verifier, task)
                current, files = self._git(verifier, task, current_report)
                if (
                    current_source != provenance
                    or current_report != report
                    or (current.source.current_commit, current.target_commit)
                    != (verification.result["source_commit"], verification.result["target_commit"])
                ):
                    raise ReportError("REPORT_VERIFICATION_STALE")
                done = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
                if done.status == "SUCCEEDED":
                    return {
                        "status": "EXISTING",
                        "operation_id": done.id,
                        "task": task.model_dump(mode="json"),
                    }
                task = StateService(self.store).transition_task(
                    task.id,
                    TaskState.READY_FOR_REVIEW,
                    expected=TaskState.WORKING,
                    event_id=op.result["event_id"],
                    actor=actor,
                    facts=VerifiedFacts(
                        tests_passed=True, verification_commit=current.source.current_commit
                    ),
                )
                self._finish(
                    op,
                    "SUCCEEDED",
                    stage="READY_FOR_REVIEW",
                    verification_id=verification.id,
                    verified_commit=current.source.current_commit,
                    epic_commit=current.target_commit,
                    actual_files=files,
                    test_command=list(self.integration.test_command),
                    exit_code=0,
                )
                return {
                    "status": "READY_FOR_REVIEW",
                    "operation_id": op.id,
                    "task": task.model_dump(mode="json"),
                }
        except ReportError:
            raise
        except (
            ContractError,
            WorktreeError,
            GitReviewError,
            GitError,
            StateError,
            StoreError,
            AssignmentError,
            HerdrError,
            CodexError,
            IntegrationError,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise ReportError("REPORT_UNVERIFIED") from None
