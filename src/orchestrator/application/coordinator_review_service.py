"""Native project-bound final review, immutable evidence and current decisions."""

import base64
import hashlib
import math
from importlib.resources import files
from pathlib import Path

from orchestrator.application.coordinator_runtime_service import CoordinatorError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.domain.coordinator import ProjectOperation
from orchestrator.domain.models import utc_now
from orchestrator.domain.review_contracts import ApprovalDecision, ChangesDecision
from orchestrator.domain.worker_contracts import canonical_json


def review_policy_text():
    resource = files("orchestrator").joinpath("prompts/coordinator-review-v1.md")
    fallback = Path(__file__).resolve().parents[3] / "prompts/coordinator-review-v1.md"
    return (resource if resource.is_file() else fallback).read_text(encoding="utf-8")


def review_capability(policies):
    return digest(canonical_json(dict(
        policies={p.native_epic_id: p.model_dump(mode="json") for p in policies},
        policy_hash=digest(review_policy_text()),
    )))


class CoordinatorReviewService:
    CONTEXT = "coordinator_review_context"
    REVIEW = "coordinator_epic_review"
    READ = "coordinator_review_chunk"
    CHUNK = 8192

    def __init__(self, runtime, subjects):
        self.runtime, self.store = runtime, runtime.store
        self.subjects = dict(subjects)
        self.configuration_hash = review_capability([s.policy for s in self.subjects.values()])
        if not self.subjects or runtime.capabilities.get("coordinator_review") != (
            self.configuration_hash
        ) or len({s.policy.native_epic_id for s in self.subjects.values()}) != len(
            self.subjects
        ) or any(key != value.run_id or value.runtime is not runtime
                 for key, value in self.subjects.items()):
            raise CoordinatorError("COORDINATOR_REVIEW_CAPABILITY_UNREGISTERED")

    def _scope(self, actor, project_id, run_id, *, binding=True):
        self.runtime.connection_principal(actor)
        if project_id != self.runtime.spec.project_id or run_id not in self.subjects:
            raise CoordinatorError("COORDINATOR_REVIEW_SCOPE_DENIED")
        if review_capability([s.policy for s in self.subjects.values()]) != self.configuration_hash:
            raise CoordinatorError("COORDINATOR_REVIEW_CONFIGURATION_CHANGED")
        subject = self.subjects[run_id]
        parent = subject.original_start
        parent.connection_principal(parent.principal)
        epic = self.store.get_epic(run_id)
        root = self.store.get_coordinator(self.runtime.run_id)
        if epic.project_id != project_id or epic.codex_session_id == root.codex_session_id:
            raise CoordinatorError("COORDINATOR_REVIEW_PARENT_UNVERIFIED")
        if binding:
            claims = [o for o in self.store.project_operations(
                          self.runtime.run_id, kind="epic_claim")
                      if o.result.get("epic_run_id") == run_id
                      and o.status == "SUCCEEDED"
                      and o.result.get("stage") == "REGISTERED"]
            attachment = self.store.get_project_operation(
                project_id, "coordinator_review_attachment", run_id,
            )
            known = claims and len(claims) == 1 and (
                claims[0].result.get("start", {}).get("session_id") == epic.codex_session_id
            )
            if not known and not (
                attachment is not None and attachment.status == "SUCCEEDED"
                and attachment.coordinator_run_id == self.runtime.run_id
                and attachment.result.get("session_id") == epic.codex_session_id
                and attachment.result.get("policy_hash") == subject.plan.policy_hash
            ):
                raise CoordinatorError("COORDINATOR_REVIEW_PARENT_BINDING_MISSING")
        return subject

    async def attach_registered_product_run(self, actor, run_id):
        """Operator-only manual probe route, deliberately absent from MCP tools."""
        with self.runtime.lock():
            subject = self._scope(actor, self.runtime.spec.project_id, run_id, binding=False)
            epic = self.store.get_epic(run_id)
            start = self.store.get_operation(epic.project_id, "epic_start", run_id)
            if start is None or start.status != "SUCCEEDED" or (
                start.result.get("stage") != "REGISTERED"
                or start.result.get("actor") != actor.model_dump(mode="json")
                or any(t.worker_slot is not None for t in self.store.get_tasks(run_id))
                or subject.plan.operations()
            ):
                raise CoordinatorError("COORDINATOR_MANUAL_PRODUCT_ATTACHMENT_DENIED")
            subject.original_start.worktrees.verify_owned_worktree(epic)
            control, _, _, _ = subject.context()
            board = await control.scheduler.selection.reader.read_project()
            native = board.epic(subject.policy.native_epic_id)
            rows = board.epic_tasks(subject.policy.native_epic_id)
            if native is None or native.status != "InProgress" or (
                {r.id for r in rows} != set(subject.policy.native_task_ids)
                or any(r.execution_owner_kind != "User"
                       or r.responsible_user_id != subject.policy.user_id for r in rows)
            ):
                raise CoordinatorError("COORDINATOR_MANUAL_PRODUCT_BOARD_MISMATCH")
            with self.store.transaction():
                prior = self.store.get_project_operation(
                    epic.project_id, "coordinator_review_attachment", run_id,
                )
                result = dict(epic_run_id=run_id, session_id=epic.codex_session_id,
                              start_operation_id=start.id, policy_hash=subject.plan.policy_hash)
                if prior is not None:
                    if prior.result != result:
                        raise CoordinatorError("COORDINATOR_MANUAL_ATTACHMENT_CHANGED")
                    return prior
                op = ProjectOperation(
                    project_id=epic.project_id, coordinator_run_id=self.runtime.run_id,
                    kind="coordinator_review_attachment", idempotency_key=run_id,
                    status="SUCCEEDED", result=result,
                )
                self.store.add_project_operation(op)
                return op

    def _save(self, op, *, status=None, **result):
        with self.store.transaction():
            old = self.store.get_project_operation(op.project_id, op.kind, op.idempotency_key)
            if old is None or old.id != op.id:
                raise CoordinatorError("COORDINATOR_REVIEW_INTENT_MISSING")
            fixed = {"epic_run_id", "configuration_hash", "membership_hash", "handoff_id",
                     "handoff_hash", "source_commit", "target_commit", "context_id", "document",
                     "proposal_hash", "decision", "choices", "review_operation_id",
                     "epic_board_hash", "epic_board_scope_hash"}
            if any(k in old.result and k in result and old.result[k] != result[k] for k in fixed):
                raise CoordinatorError("COORDINATOR_REVIEW_HISTORY_IMMUTABLE")
            updated = old.model_copy(update={
                "result": old.result | result, "status": status or old.status,
                "updated_at": utc_now(),
            })
            self.store.update_project_operation(updated)
            return updated

    @staticmethod
    def _key(key):
        if not isinstance(key, str) or not key.strip() or len(key) > 128 or any(
            c in key for c in "\x00\r\n"
        ):
            raise CoordinatorError("COORDINATOR_REVIEW_KEY_INVALID")

    async def _handoff(self, subject):
        control, completion, manager, view = subject.context()
        rows = self.store.get_operations(subject.run_id, kind="epic_complete")
        if not rows:
            raise CoordinatorError("COORDINATOR_EPIC_HANDOFF_MISSING")
        latest = max(rows, key=lambda row: (row.created_at, row.id))
        receipt = await completion.require_current(control.actor, subject.run_id,
                                                   key=latest.idempotency_key)
        return receipt, control, completion, manager, view

    async def _fresh(self, subject, context):
        if context.coordinator_run_id != self.runtime.run_id or context.kind != self.CONTEXT or (
            context.status != "SUCCEEDED"
            or context.result.get("configuration_hash") != self.configuration_hash
            or context.result.get("epic_run_id") != subject.run_id
        ):
            raise CoordinatorError("COORDINATOR_REVIEW_CONTEXT_INVALID")
        receipt, control, _, manager, _ = await self._handoff(subject)
        _, _, membership = subject.plan.current()
        board_hash, board_scope = await subject.board_facts(
            control.scheduler.selection.reader.adapter,
        )
        facts = dict(
            handoff_id=receipt.id, handoff_hash=receipt.result["report_hash"],
            source_commit=receipt.result["snapshot"]["source_commit"],
            target_commit=receipt.result["snapshot"]["target_commit"], membership_hash=membership,
            epic_board_hash=board_hash, epic_board_scope_hash=board_scope,
        )
        if any(context.result.get(key) != value for key, value in facts.items()) or (
            digest(context.result["document"]) != context.result["context_id"]
        ):
            raise CoordinatorError("COORDINATOR_REVIEW_CONTEXT_STALE")
        return manager

    async def context(self, actor, project_id, run_id, key):
        self._key(key)
        subject = self._scope(actor, project_id, run_id)
        with self.runtime.lock():
            self._scope(actor, project_id, run_id)
            if any(o.status != "SUCCEEDED" or o.result.get("stage") != "DISPATCHED"
                   for o in subject.plan.operations()):
                raise CoordinatorError("COORDINATOR_CORRECTION_RECONCILIATION_REQUIRED")
            old = self.store.get_project_operation(project_id, self.CONTEXT, key)
            if old is not None and old.status == "SUCCEEDED":
                await self._fresh(subject, old)
                return self._context_summary(old)
            receipt, control, _, manager, view = await self._handoff(subject)
            _, _, membership = subject.plan.current()
            board_hash, board_scope = await subject.board_facts(
                control.scheduler.selection.reader.adapter,
            )
            fixed = dict(
                epic_run_id=run_id, configuration_hash=self.configuration_hash,
                membership_hash=membership, handoff_id=receipt.id,
                handoff_hash=receipt.result["report_hash"],
                source_commit=receipt.result["snapshot"]["source_commit"],
                target_commit=receipt.result["snapshot"]["target_commit"],
                epic_board_hash=board_hash, epic_board_scope_hash=board_scope,
            )
            if old is None:
                old = ProjectOperation(
                    project_id=project_id, coordinator_run_id=self.runtime.run_id,
                    kind=self.CONTEXT, idempotency_key=key, result=fixed | {"stage": "VERIFYING"},
                )
                self.store.add_project_operation(old)
            elif old.coordinator_run_id != self.runtime.run_id or any(
                old.result.get(k) != value for k, value in fixed.items()
            ):
                raise CoordinatorError("COORDINATOR_REVIEW_KEY_CONFLICT")
            verification = manager.verify_epic(actor, run_id, key="cr-verify-" + old.id)
            if not (verification.status == "SUCCEEDED" and verification.result.get("exit_code") == 0
                    or verification.status == "FAILED" and verification.error_code == "TEST_FAILED"
                    and type(verification.result.get("exit_code")) is int
                    and verification.result["exit_code"] > 0):
                raise CoordinatorError("COORDINATOR_REVIEW_VERIFICATION_UNKNOWN")
            fresh_receipt, _, _, _, _ = await self._handoff(subject)
            if fresh_receipt != receipt:
                raise CoordinatorError("COORDINATOR_REVIEW_HANDOFF_CHANGED")
            with manager._lock():
                evidence = manager.reviews.epic_review(
                    actor, run_id, expected_commit=fixed["source_commit"],
                    max_diff_bytes=4 * 1024 * 1024,
                )
                report = receipt.result["report"]
                patch = base64.b64decode(report["diff"]["patch"], validate=True)
                if not evidence.reviewable or evidence.target_commit != fixed["target_commit"] or (
                    patch != evidence.source.commit_diff.patch
                    or hashlib.sha256(patch).hexdigest() != report["diff"]["sha256"]
                ):
                    raise CoordinatorError("COORDINATOR_REVIEW_DIFF_UNVERIFIED")
                tasks = []
                for entry in report["manifest"]:
                    tasks.append({k: v for k, v in entry.items() if k not in {
                        "context", "approval", "run",
                    }} | dict(approval=entry["approval"]["decision"]))
                body = dict(
                    version=1, project_id=project_id, **fixed,
                    specification=report["specification"], sources=report["sources"],
                    current_plan=view.spec.model_dump(mode="json"), task_results=tasks,
                    diff=report["diff"], aggregate_steps=report["steps"],
                    independent_verification=verification.model_dump(mode="json"),
                    limitations=report["limitations"], review_policy=review_policy_text(),
                    correction_templates=[t.model_dump(mode="json")
                                          for t in subject.policy.templates],
                    patch_text=patch.decode("utf-8", errors="backslashreplace"),
                    patch_text_encoding="UTF-8; undecodable bytes shown as backslash escapes. "
                    "The complete base64 diff and SHA256 preserve the authoritative bytes.",
                )
                document = canonical_json(body)
                if len(document) > subject.policy.max_context_chars:
                    raise CoordinatorError("COORDINATOR_REVIEW_CONTEXT_TOO_LARGE")
                if subject.creator.adapter.redact_text(document) != document:
                    raise CoordinatorError("COORDINATOR_REVIEW_PRIVATE_VALUE_DENIED")
                saved = self._save(old, status="SUCCEEDED", stage="CURRENT", document=document,
                                   context_id=digest(document), verification_id=verification.id,
                                   verification_key=verification.idempotency_key)
            return self._context_summary(saved)

    def _context_summary(self, context):
        return dict(
            context_id=context.result["context_id"], chars=len(context.result["document"]),
            chunks=math.ceil(len(context.result["document"]) / self.CHUNK),
            chunk_chars=self.CHUNK, source_commit=context.result["source_commit"],
            target_commit=context.result["target_commit"],
        )

    def _find_context(self, run_id, context_id):
        rows = [o for o in self.store.project_operations(self.runtime.run_id, kind=self.CONTEXT)
                if o.result.get("epic_run_id") == run_id
                and o.result.get("context_id") == context_id]
        if len(rows) != 1:
            raise CoordinatorError("COORDINATOR_REVIEW_CONTEXT_AMBIGUOUS_OR_MISSING")
        return rows[0]

    async def read(self, actor, project_id, run_id, context_id, chunk_index):
        subject = self._scope(actor, project_id, run_id)
        with self.runtime.lock():
            self._scope(actor, project_id, run_id)
            context = self._find_context(run_id, context_id)
            await self._fresh(subject, context)
            document = context.result["document"]
            count = math.ceil(len(document) / self.CHUNK)
            if type(chunk_index) is not int or not 0 <= chunk_index < count:
                raise CoordinatorError("COORDINATOR_REVIEW_CHUNK_INVALID")
            chunk = document[chunk_index * self.CHUNK:(chunk_index + 1) * self.CHUNK]
            key = context_id + ":" + str(chunk_index)
            result = dict(context_id=context_id, epic_run_id=run_id, chunk_index=chunk_index,
                          chunk_hash=digest(chunk))
            old = self.store.get_project_operation(project_id, self.READ, key)
            if old is None:
                self.store.add_project_operation(ProjectOperation(
                    project_id=project_id, coordinator_run_id=self.runtime.run_id,
                    kind=self.READ, idempotency_key=key, status="SUCCEEDED", result=result,
                ))
            elif old.result != result or old.coordinator_run_id != self.runtime.run_id:
                raise CoordinatorError("COORDINATOR_REVIEW_CHUNK_RECEIPT_CHANGED")
            return dict(context_id=context_id, chunk_index=chunk_index, chunks=count,
                        text=chunk, chunk_hash=digest(chunk))

    def _all_read(self, project_id, context):
        document = context.result["document"]
        for index in range(math.ceil(len(document) / self.CHUNK)):
            row = self.store.get_project_operation(
                project_id, self.READ, context.result["context_id"] + ":" + str(index),
            )
            chunk = document[index * self.CHUNK:(index + 1) * self.CHUNK]
            if row is None or row.status != "SUCCEEDED" or (
                row.coordinator_run_id != self.runtime.run_id
                or row.result != dict(context_id=context.result["context_id"],
                                      epic_run_id=context.result["epic_run_id"],
                                      chunk_index=index, chunk_hash=digest(chunk))
            ):
                raise CoordinatorError("COORDINATOR_REVIEW_DOCUMENT_NOT_FULLY_READ")

    async def submit(self, actor, project_id, run_id, key, decision, choices=()):
        self._key(key)
        subject = self._scope(actor, project_id, run_id)
        if not isinstance(decision, (ApprovalDecision, ChangesDecision)):
            raise CoordinatorError("COORDINATOR_REVIEW_DECISION_INVALID")
        proposal_hash = digest(canonical_json(dict(
            decision=decision.model_dump(mode="json"),
            choices=[choice.model_dump(mode="json") for choice in choices],
        )))
        with self.runtime.lock():
            self._scope(actor, project_id, run_id)
            old = self.store.get_project_operation(project_id, self.REVIEW, key)
            if old is not None and (
                old.coordinator_run_id != self.runtime.run_id
                or old.result.get("epic_run_id") != run_id
                or old.result.get("proposal_hash") != proposal_hash
            ):
                raise CoordinatorError("COORDINATOR_REVIEW_DECISION_KEY_CONFLICT")
            if old is not None and old.status == "SUCCEEDED":
                return await self._resume_recorded(subject, old)
            if old is not None:
                recovered = self._recover_review_receipt(subject, old)
                if recovered is not None:
                    return await self._resume_recorded(subject, recovered)
            context = self._find_context(run_id, decision.context_id)
            manager = await self._fresh(subject, context)
            self._all_read(project_id, context)
            approved = isinstance(decision, ApprovalDecision)
            if approved and (
                decision.verified_criteria != subject.policy.original.acceptance_criteria or choices
            ):
                raise CoordinatorError("COORDINATOR_REVIEW_APPROVAL_CRITERIA_INVALID")
            if isinstance(decision, ChangesDecision) and any(
                not set(issue.acceptance_criteria) <= set(
                    subject.policy.original.acceptance_criteria)
                for issue in decision.issues
            ):
                raise CoordinatorError("COORDINATOR_REVIEW_FINDING_SCOPE_DENIED")
            if isinstance(decision, ChangesDecision) and choices:
                if len(choices) != len(decision.issues) or {
                    c.issue_number for c in choices
                } != {i.number for i in decision.issues}:
                    raise CoordinatorError("CORRECTION_ISSUE_MAPPING_INVALID")
                for choice in choices:
                    issue = next(i for i in decision.issues if i.number == choice.issue_number)
                    subject.plan.pending_entry(choice.template_id, issue, 1)
            serialized = canonical_json(decision.model_dump(mode="json"))
            if subject.creator.adapter.redact_text(serialized) != serialized:
                raise CoordinatorError("COORDINATOR_REVIEW_PRIVATE_VALUE_DENIED")
            verified = self.store.get_operation(
                project_id, "verify_epic", context.result["verification_key"],
            )
            if approved and (verified is None or verified.status != "SUCCEEDED"
                             or verified.result.get("exit_code") != 0):
                raise CoordinatorError("COORDINATOR_REVIEW_INDEPENDENT_TEST_FAILED")
            if old is None:
                old = ProjectOperation(
                    project_id=project_id, coordinator_run_id=self.runtime.run_id,
                    kind=self.REVIEW, idempotency_key=key, result=dict(
                        epic_run_id=run_id, context_id=decision.context_id,
                        proposal_hash=proposal_hash, decision=decision.model_dump(mode="json"),
                        choices=[choice.model_dump(mode="json") for choice in choices],
                        stage="REVIEW_PENDING", configuration_hash=self.configuration_hash,
                    ),
                )
                self.store.add_project_operation(old)
            feedback = decision.summary if approved else canonical_json(
                [issue.model_dump(mode="json") for issue in decision.issues]
            )
            self._scope(actor, project_id, run_id)
            recorded = manager.register_epic_review(
                actor, run_id, verification_key=context.result["verification_key"],
                key="cr-decision-" + old.id, approved=approved, feedback=feedback,
                verified_criteria=decision.verified_criteria if approved else None,
                allow_failed_rejection=not approved,
            )
            old = self._save(old, status="SUCCEEDED", stage="EPIC_APPROVED" if approved
                             else "EPIC_CHANGES_REQUESTED", review_operation_id=recorded.id)
            return await self._resume_recorded(subject, old)

    def _recover_review_receipt(self, subject, pending):
        """Reconcile the atomic F08 decision after a lost outer receipt write.

        This preserves the actual old decision, even if its commits are now stale;
        it never turns the saved handoff back into a new READY_FOR_REVIEW run.
        """
        recorded = self.store.get_operation(pending.project_id, "epic_review",
                                            "cr-decision-" + pending.id)
        if recorded is None:
            return None
        context = self._find_context(subject.run_id, pending.result["context_id"])
        self._all_read(pending.project_id, context)
        raw = pending.result["decision"]
        approved = raw["result"] == "APPROVED"
        feedback = raw["summary"] if approved else canonical_json(raw["issues"])
        expected = dict(
            verification_key=context.result["verification_key"], approved=approved,
            feedback_hash=digest(feedback), source_commit=context.result["source_commit"],
            target_commit=context.result["target_commit"],
        )
        expected.update(dict(verified_criteria=raw["verified_criteria"]) if approved else
                        dict(allow_failed_rejection=True))
        if recorded.status != "SUCCEEDED" or recorded.epic_run_id != subject.run_id or (
            recorded.task_run_id is not None
            or pending.result.get("configuration_hash") != self.configuration_hash
            or context.result.get("configuration_hash") != self.configuration_hash
            or digest(context.result["document"]) != context.result["context_id"]
            or any(recorded.result.get(name) != value for name, value in expected.items())
        ):
            raise CoordinatorError("COORDINATOR_REVIEW_RECEIPT_RECONCILIATION_DENIED")
        return self._save(pending, status="SUCCEEDED", stage="EPIC_APPROVED" if approved
                          else "EPIC_CHANGES_REQUESTED", review_operation_id=recorded.id)

    async def _resume_recorded(self, subject, review):
        if review.result["decision"]["result"] == "APPROVED":
            current = False
            try:
                context = self._find_context(subject.run_id, review.result["context_id"])
                # F36 requires READY_FOR_REVIEW; approved history is revalidated by F40's
                # actual approval/merge checks, never by pretending its old handoff is new.
                control, completion, manager, _ = subject.context()
                epic = self.store.get_epic(subject.run_id)
                source, target = manager._epic_pair(epic)
                manager._epic_approval(epic, source, target)
                receipts = [o for o in self.store.get_operations(
                                subject.run_id, kind="epic_complete")
                            if o.id == context.result["handoff_id"]]
                manifest = await completion._manifest(subject.original_start.principal, epic)
                current = len(receipts) == 1 and (
                    receipts[0].result["snapshot"]["manifest"] == manifest
                    and receipts[0].result["report_hash"] == context.result["handoff_hash"]
                    and subject.plan.current()[2] == context.result["membership_hash"]
                    and (source, target) == (context.result["source_commit"],
                                             context.result["target_commit"])
                    and self.runtime.main() == target
                    and await subject.board_stamp(control.scheduler.selection.reader.adapter)
                    == context.result["epic_board_hash"]
                )
            except Exception:
                current = False
            return dict(status="EPIC_APPROVED" if current else "HISTORICAL_APPROVAL",
                        review_id=review.id, current=current,
                        review_operation_id=review.result["review_operation_id"])
        from orchestrator.application.coordinator_correction_service import (
            CoordinatorCorrectionService,
        )
        return await CoordinatorCorrectionService(self).resume(subject, review)
