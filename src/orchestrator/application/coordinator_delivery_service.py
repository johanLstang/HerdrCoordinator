"""Current approval -> one actual merge -> stopped parent -> tested/synced delivery."""

import json
from importlib.resources import files
from pathlib import Path

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.application.coordinator_runtime_service import CoordinatorError
from orchestrator.application.epic_start_service import intent_hash
from orchestrator.application.git_integration_service import IntegrationError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.runtime_lifecycle_service import LifecycleError
from orchestrator.application.state_service import StateService
from orchestrator.domain.coordinator import ProjectOperation
from orchestrator.domain.coordinator_contracts import CoordinatorEpic
from orchestrator.domain.models import utc_now
from orchestrator.domain.policy import VerifiedFacts
from orchestrator.domain.states import EpicState
from orchestrator.domain.worker_contracts import canonical_json


def delivery_policy_text():
    resource = files("orchestrator").joinpath("prompts/coordinator-delivery-v1.md")
    fallback = Path(__file__).resolve().parents[3] / "prompts/coordinator-delivery-v1.md"
    return (resource if resource.is_file() else fallback).read_text(encoding="utf-8")


def delivery_capability(review_hash):
    return digest(canonical_json(dict(version=1, review=review_hash,
                                     policy_hash=digest(delivery_policy_text()))))


class CoordinatorDeliveryService:
    KIND = "coordinator_epic_delivery"
    PERMIT = "coordinator_bounded_delivery_authorization"
    CONTINUE = "coordinator_epic_continuation"

    def __init__(self, review, claims):
        self.review, self.claims = review, claims
        self.runtime, self.store = review.runtime, review.store
        self.configuration_hash = delivery_capability(review.configuration_hash)
        if claims.runtime is not self.runtime or self.runtime.capabilities.get(
            "coordinator_delivery"
        ) != self.configuration_hash:
            raise CoordinatorError("COORDINATOR_DELIVERY_CAPABILITY_UNREGISTERED")
        for subject in review.subjects.values():
            claims.register_epic_observer(subject.original_start)

    def _scope(self, actor, project_id, run_id):
        self.runtime.connection_principal(actor)
        if project_id != self.runtime.spec.project_id or run_id not in self.review.subjects:
            raise CoordinatorError("COORDINATOR_DELIVERY_SCOPE_DENIED")
        self.review.verify_configuration()
        if delivery_capability(self.review.configuration_hash) != self.configuration_hash:
            raise CoordinatorError("COORDINATOR_DELIVERY_CONFIGURATION_CHANGED")
        return self.review.subjects[run_id]

    def _save(self, op, *, status=None, **changes):
        with self.store.transaction():
            old = self.store.get_project_operation(op.project_id, op.kind, op.idempotency_key)
            if old is None or old.id != op.id or old.coordinator_run_id != self.runtime.run_id:
                raise CoordinatorError("COORDINATOR_DELIVERY_INTENT_CHANGED")
            fixed = {"epic_run_id", "review_id", "configuration_hash", "parent_registration_hash",
                     "authorization", "context_id", "source_commit", "target_commit",
                     "membership_hash", "merge_key", "stop_key", "merge_commit",
                     "merge_operation_id", "stop_operation_id", "final_verification_id",
                     "final_verification_key", "delivery_id", "claim_key"}
            if any(k in old.result and k in changes and old.result[k] != changes[k] for k in fixed):
                raise CoordinatorError("COORDINATOR_DELIVERY_HISTORY_IMMUTABLE")
            updated = old.model_copy(update=dict(
                result=old.result | changes, status=status or old.status,
                updated_at=utc_now(),
            ))
            self.store.update_project_operation(updated)
            return updated

    def _latest_review(self, subject):
        rows = [o for o in self.store.project_operations(self.runtime.run_id,
                                                        kind=self.review.REVIEW)
                if o.status == "SUCCEEDED" and o.result.get("epic_run_id") == subject.run_id]
        if not rows:
            raise CoordinatorError("COORDINATOR_DELIVERY_APPROVAL_MISSING")
        review = max(rows, key=lambda o: (o.created_at, o.id))
        if review.result.get("decision", {}).get("result") != "APPROVED":
            raise CoordinatorError("COORDINATOR_DELIVERY_APPROVAL_MISSING")
        return review

    def _rows(self):
        return [ProjectOperation.model_validate_json(row[0]) for row in self.store.db.execute(
            "SELECT payload FROM project_operations WHERE project_id=? AND kind=?",
            (self.runtime.spec.project_id, self.KIND),
        )]

    def _registered(self, subject):
        parent = subject.original_start
        epic = self.store.get_epic(subject.run_id)
        start = self.store.get_operation(epic.project_id, "epic_start", epic.id)
        if start is None or start.status != "SUCCEEDED" or (
            start.result.get("stage") != "REGISTERED"
            or start.result.get("intent_hash") != intent_hash(start.result)
            or start.result.get("actor") != self.runtime.principal.model_dump(mode="json")
            or start.result.get("integration_principal") != parent.principal.model_dump(mode="json")
            or start.result.get("spec") != subject.policy.original.model_dump(mode="json")
            or start.result.get("configuration") != parent.configuration
            or any(getattr(epic, k) != v for k, v in start.result["subject"].items())
            or start.result.get("ack", {}).get("session_id") != epic.codex_session_id
        ):
            raise CoordinatorError("COORDINATOR_DELIVERY_PARENT_REGISTRATION_CHANGED")
        return digest(canonical_json(start.model_dump(mode="json")))

    async def authorize_bounded_product_probe(self, actor, run_id, review_id, *, reason):
        """Operator-only. No MCP route; exact current reviewed fixture, never a gate."""
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2048:
            raise CoordinatorError("COORDINATOR_DELIVERY_AUTHORIZATION_REASON_REQUIRED")
        with self.runtime.lock():
            subject = self._scope(actor, self.runtime.spec.project_id, run_id)
            self.review._scope(actor, self.runtime.spec.project_id, run_id)
            attachment = self.store.get_project_operation(self.runtime.spec.project_id,
                                                           "coordinator_review_attachment", run_id)
            if attachment is None or attachment.status != "SUCCEEDED" or (
                attachment.coordinator_run_id != self.runtime.run_id
                or attachment.result.get("policy_hash") != subject.plan.policy_hash
                or self.claims._active_claims()
            ):
                raise CoordinatorError("COORDINATOR_DELIVERY_NOT_A_MANUAL_PRODUCT_PROBE")
            review = self._latest_review(subject)
            if review.id != review_id or not (await self.review._resume_recorded(
                subject, review
            ))["current"]:
                raise CoordinatorError("COORDINATOR_DELIVERY_AUTHORIZATION_STALE")
            root = self.store.get_coordinator(self.runtime.run_id)
            context = self.review._find_context(run_id, review.result["context_id"])
            payload = dict(
                stage="AUTHORIZED", epic_run_id=run_id, review_id=review.id,
                context_id=context.result["context_id"],
                source_commit=context.result["source_commit"],
                target_commit=context.result["target_commit"],
                membership_hash=context.result["membership_hash"],
                coordinator_session_id=root.codex_session_id,
                configuration_hash=self.configuration_hash,
                repository=str(self.runtime.settings.repository),
                reason=reason.strip(), parent_registration_hash=self._registered(subject),
            )
            old = self.store.get_project_operation(self.runtime.spec.project_id,
                                                   self.PERMIT, review.id)
            if old is not None:
                if old.coordinator_run_id != self.runtime.run_id or old.result != payload:
                    raise CoordinatorError("COORDINATOR_DELIVERY_AUTHORIZATION_CHANGED")
                return old
            op = ProjectOperation(project_id=self.runtime.spec.project_id,
                                  coordinator_run_id=self.runtime.run_id, kind=self.PERMIT,
                                  idempotency_key=review.id, status="SUCCEEDED", result=payload)
            self.store.add_project_operation(op)
            return op

    def _authorization(self, subject, review, context):
        try:
            return dict(kind="runtime_gate", evidence=self.claims._gate(self.runtime.main()))
        except CoordinatorError:
            pass
        permit = self.store.get_project_operation(self.runtime.spec.project_id,
                                                  self.PERMIT, review.id)
        expected = dict(
            stage="AUTHORIZED", epic_run_id=subject.run_id, review_id=review.id,
            context_id=context.result["context_id"],
            source_commit=context.result["source_commit"],
            target_commit=context.result["target_commit"],
            membership_hash=context.result["membership_hash"],
            coordinator_session_id=self.store.get_coordinator(self.runtime.run_id).codex_session_id,
            configuration_hash=self.configuration_hash,
            repository=str(self.runtime.settings.repository),
            parent_registration_hash=self._registered(subject),
        )
        if permit is None or permit.status != "SUCCEEDED" or (
            permit.coordinator_run_id != self.runtime.run_id
            or not permit.result.get("reason")
            or any(permit.result.get(k) != v for k, v in expected.items())
        ):
            raise CoordinatorError("RUNTIME_SECURITY_GATE_UNVERIFIED")
        return dict(kind="bounded_operator_probe", operation_id=permit.id,
                    evidence_hash=digest(canonical_json(permit.result)))

    def _bound(self, subject, op):
        review = self._latest_review(subject)
        context = self.review._find_context(subject.run_id, op.result["context_id"])
        if op.coordinator_run_id != self.runtime.run_id or (
            op.result["configuration_hash"] != self.configuration_hash
            or op.result["parent_registration_hash"] != self._registered(subject)
            or op.result["review_id"] != review.id
            or op.result["membership_hash"] != subject.plan.current()[2]
            or digest(context.result["document"]) != context.result["context_id"]
            or context.result["context_id"] != review.result["context_id"]
            or any(op.result[k] != context.result[k] for k in (
                "source_commit", "target_commit", "membership_hash"))
        ):
            raise CoordinatorError("COORDINATOR_DELIVERY_REVIEW_SCOPE_CHANGED")
        return context

    async def _board(self, subject, context, control, *, done=False):
        board = await control.scheduler.selection.reader.read_project()
        projects = await control.scheduler.selection.reader.adapter.read("list_projects", {})
        matches = [p for p in projects if p.get("projectId") == subject.policy.native_project_id]
        if len(matches) != 1 or matches[0].get("access") != "Write":
            raise CoordinatorError("COORDINATOR_BOARD_WRITE_DENIED")
        epic = board.epic(subject.policy.native_epic_id)
        if epic is None or epic.status not in ({"InProgress", "Done"} if done else {"InProgress"}):
            raise CoordinatorError("COORDINATOR_DELIVERY_BOARD_EPIC_CHANGED")
        scope = epic.model_dump(mode="json", exclude={"version"})
        scope["binding"] = None
        scope["status"] = "InProgress"
        if digest(canonical_json(scope)) != context.result["epic_board_scope_hash"]:
            raise CoordinatorError("COORDINATOR_DELIVERY_BOARD_EPIC_CHANGED")
        document = json.loads(context.result["document"])
        versions = {t["board_task_id"]: t["board_version"] for t in document["task_results"]}
        rows = board.epic_tasks(epic.id)
        spec, identities, _ = subject.plan.current()
        if set(versions) != set(identities) or {r.id for r in rows} != set(identities) or (
            len(rows) != len(identities)
            or any(i.task_id in {epic.id, *identities} for i in board.issues)
        ):
            raise CoordinatorError("COORDINATOR_DELIVERY_TASK_SCOPE_CHANGED")
        local = self.store.get_epic(subject.run_id)
        for row in rows:
            entry = spec.tasks[identities.index(row.id)]
            if row.version != versions[row.id] or row.status != "Done" or (
                row.execution_owner_kind != "User"
                or row.responsible_user_id != subject.policy.user_id
                or control.scheduler.selection._spec(row, local, board) != entry.task
            ):
                raise CoordinatorError("COORDINATOR_DELIVERY_TASK_SCOPE_CHANGED")
        return board

    def _git_proof(self, subject, op, manager, *, final=False):
        epic = self.store.get_epic(subject.run_id)
        merge = self.store.get_operation(epic.project_id, "merge_epic_to_main",
                                          op.result["merge_key"])
        if merge is None or merge.status != "SUCCEEDED" or (
            merge.epic_run_id != epic.id or merge.result.get("requires_reconciliation")
            or merge.result.get("source_commit") != op.result["source_commit"]
            or merge.result.get("target_commit") != op.result["target_commit"]
            or merge.result.get("manifest_hash") != manager._manifest_hash(manager._manifest(epic))
            or epic.merge_commit != merge.result.get("merge_commit")
            or self.runtime.main() != epic.merge_commit
            or manager.git.head(epic.branch) != op.result["source_commit"]
            or manager.git.find_operation_merge("main", merge.id, op.result["target_commit"],
                                                op.result["source_commit"]) != epic.merge_commit
            or manager.git.parents(epic.merge_commit) != (
                op.result["target_commit"], op.result["source_commit"])
        ):
            raise CoordinatorError("COORDINATOR_DELIVERY_MERGE_UNVERIFIED")
        if final:
            test = self.store.get_operation(epic.project_id, "verify_main_merge",
                                            op.result["final_verification_key"])
            if test is None or test.status != "SUCCEEDED" or test.epic_run_id != epic.id or (
                test.result.get("exit_code") != 0
                or test.result.get("merge_key") != merge.idempotency_key
                or test.result.get("merge_commit") != epic.merge_commit
                or test.result.get("source_commit") != op.result["source_commit"]
                or test.result.get("target_commit") != epic.merge_commit
                or test.result.get("command_hash") != manager.command_hash
                or test.result.get("manifest_hash") != merge.result["manifest_hash"]
            ):
                raise CoordinatorError("COORDINATOR_DELIVERY_FINAL_TEST_UNVERIFIED")
        return merge

    async def deliver(self, actor, project_id, run_id, key, verification_key):
        self.review._key(key)
        self.review._key(verification_key)
        subject = self._scope(actor, project_id, run_id)
        with self.runtime.lock():
            subject = self._scope(actor, project_id, run_id)
            named = self.store.get_project_operation(project_id, self.KIND, key)
            rows = [o for o in self._rows() if o.result.get("epic_run_id") == run_id]
            if len(rows) > 1 or named is not None and named not in rows:
                raise CoordinatorError("COORDINATOR_DELIVERY_KEY_CONFLICT")
            op = rows[0] if rows else None
            if op is None:
                self.review._scope(actor, project_id, run_id)
                review = self._latest_review(subject)
                if not (await self.review._resume_recorded(subject, review))["current"]:
                    raise CoordinatorError("COORDINATOR_DELIVERY_APPROVAL_STALE")
                context = self.review._find_context(run_id, review.result["context_id"])
                try:
                    authorization = self._authorization(subject, review, context)
                except CoordinatorError as error:
                    if str(error) == "RUNTIME_SECURITY_GATE_UNVERIFIED":
                        return dict(status="WAIT", reason=str(error))
                    raise
                op = ProjectOperation(project_id=project_id, coordinator_run_id=self.runtime.run_id,
                                      kind=self.KIND, idempotency_key=key, result=dict(
                    stage="INTENT", epic_run_id=run_id, review_id=review.id,
                    configuration_hash=self.configuration_hash,
                    parent_registration_hash=self._registered(subject),
                    authorization=authorization,
                    **{k: context.result[k] for k in (
                        "context_id", "source_commit", "target_commit", "membership_hash")},
                ))
                op = op.model_copy(update={"result": op.result | dict(
                    merge_key="cd-merge-" + op.id, stop_key="cd-stop-" + op.id,
                )})
                self.store.add_project_operation(op)
            try:
                context = self._bound(subject, op)
                control, _, manager, _ = subject.context()
                if op.result["stage"] == "RELEASED":
                    original = next(e for e in self.runtime.spec.epics
                                    if e.native_epic_id == subject.policy.native_epic_id)
                    self.completed_plan(original)
                    await self._board(subject, context, control, done=True)
                else:
                    await self._board(subject, context, control,
                                      done=self.store.get_epic(run_id).status == EpicState.DONE)
                    op = await self._drive(subject, op, context, control, manager, verification_key)
                    if op.result["stage"] != "RELEASED":
                        return dict(status="ATTENTION", delivery_id=op.id, **op.result)
            except (CoordinatorError, IntegrationError, LifecycleError, TeamPlayerError) as error:
                code = (str(error) if isinstance(error, CoordinatorError) else
                        "COORDINATOR_DELIVERY_RECONCILIATION_REQUIRED")
                current = self.store.get_project_operation(project_id, self.KIND,
                                                           op.idempotency_key)
                known = self.store.get_operation(project_id, "merge_epic_to_main",
                                                  current.result["merge_key"])
                observation = {}
                if known is not None:
                    actual = self.runtime.git.find_operation_merge(
                        "main", known.id, current.result["target_commit"],
                        current.result["source_commit"],
                    )
                    if actual is not None:
                        observation = dict(observed_merge_commit=actual,
                                           observed_merge_operation_id=known.id)
                op = self._save(current, attention=code, **observation)
                return dict(status="ATTENTION", delivery_id=op.id, **op.result)
        return await self._continue(actor, subject, op)

    async def _drive(self, subject, op, context, control, manager, verification_key):
        actor = self.runtime.principal
        run_id = subject.run_id
        if op.result["stage"] == "INTENT":
            # Fresh authorization until there is an actual merge; recovery after merge
            # uses its preserved exact authorization and real Git operation receipt.
            known = self.store.get_operation(op.project_id, "merge_epic_to_main",
                                              op.result["merge_key"])
            actual = known and manager.git.find_operation_merge(
                "main", known.id, op.result["target_commit"], op.result["source_commit"],
            )
            if not actual:
                self._authorization(subject, self._latest_review(subject), context)
            merged = manager.merge_epic_to_main(actor, run_id, key=op.result["merge_key"])
            if merged.status != "SUCCEEDED":
                return self._save(op, attention="COORDINATOR_DELIVERY_MERGE_RECONCILIATION",
                                  merge_operation_id=merged.id)
            self._git_proof(subject, op, manager)
            op = self._save(op, stage="MERGED", merge_commit=merged.result["merge_commit"],
                            merge_operation_id=merged.id)
        self._git_proof(subject, op, manager)
        lifecycle = subject.original_start.lifecycle
        if op.result["stage"] == "MERGED":
            stop = lifecycle.stop_epic(actor, run_id, key=op.result["stop_key"])
            if stop.status != "SUCCEEDED":
                return self._save(op, attention="COORDINATOR_DELIVERY_STOP_UNCONFIRMED")
            op = self._save(op, stage="STOPPED", stop_operation_id=stop.id)
        if lifecycle.reconnect_epic(actor, run_id)["status"] != "STOPPED":
            raise CoordinatorError("COORDINATOR_DELIVERY_PARENT_NOT_STOPPED")
        if op.result["stage"] == "STOPPED":
            test_key = "cd-final-" + digest(op.id + ":" + verification_key)
            try:
                test = manager.verify_main_merge(actor, run_id, merge_key=op.result["merge_key"],
                                                  key=test_key)
            except IntegrationError:
                pending = self.store.get_operation(op.project_id, "verify_main_merge", test_key)
                if pending is not None and pending.status == "PENDING":
                    return self._save(op, attention="COORDINATOR_DELIVERY_FINAL_TEST_UNKNOWN",
                                      last_verification_id=pending.id,
                                      last_verification_key=test_key)
                raise
            if test.status != "SUCCEEDED" or test.result.get("exit_code") != 0:
                return self._save(op, attention="COORDINATOR_DELIVERY_FINAL_TEST_FAILED",
                                  last_verification_id=test.id,
                                  last_verification_key=test_key,
                                  final_test_exit_code=test.result.get("exit_code"),
                                  final_test_error=test.error_code)
            op = self._save(op, stage="VERIFIED", final_verification_id=test.id,
                            final_verification_key=test_key, attention=None)
        self._git_proof(subject, op, manager, final=True)
        await self._board(subject, context, control,
                          done=self.store.get_epic(run_id).status == EpicState.DONE)
        if op.result["stage"] == "VERIFIED":
            with self.store.transaction():
                epic = self.store.get_epic(run_id)
                if epic.status == EpicState.MERGING:
                    StateService(self.store).transition_epic(
                        run_id, EpicState.DONE, expected=EpicState.MERGING,
                        event_id="coordinator-delivered:" + op.id, actor=actor,
                        facts=VerifiedFacts(source_commit=op.result["source_commit"],
                                            target_commit=op.result["target_commit"],
                                            merge_commit=op.result["merge_commit"],
                                            verification_commit=op.result["merge_commit"],
                                            tests_passed=True),
                    )
                op = self._save(op, stage="LOCAL_DONE")
        sync = await control.scheduler.sync.sync_epic(actor, run_id)
        if sync["status"] not in {"SYNCED", "EXISTING"}:
            return self._save(op, attention="COORDINATOR_DELIVERY_BOARD_SYNC_PENDING", sync=sync)
        board = await self._board(subject, context, control, done=True)
        if board.epic(subject.policy.native_epic_id).status != "Done":
            raise CoordinatorError("COORDINATOR_DELIVERY_BOARD_DONE_UNCONFIRMED")
        self._git_proof(subject, op, manager, final=True)
        if lifecycle.reconnect_epic(actor, run_id)["status"] != "STOPPED":
            raise CoordinatorError("COORDINATOR_DELIVERY_PARENT_NOT_STOPPED")
        with self.store.transaction():
            active = self.claims._active_claims()
            own = [c for c in active if c.coordinator_run_id == self.runtime.run_id
                   and c.result.get("epic_run_id") == run_id]
            if active and (len(active) != 1 or len(own) != 1):
                raise CoordinatorError("COORDINATOR_DELIVERY_RESERVATION_CHANGED")
            if own:
                claim = own[0]
                self.store.update_project_operation(claim.model_copy(update=dict(
                    result=claim.result | dict(stage="RELEASED", delivery_id=op.id,
                                               merge_commit=op.result["merge_commit"],
                                               sync_operation_id=sync["operation_id"],
                                               stop_operation_id=op.result["stop_operation_id"]),
                    updated_at=utc_now(),
                )))
            op = self._save(op, stage="RELEASED", status="SUCCEEDED", attention=None,
                            sync_operation_id=sync["operation_id"],
                            released_claim_id=own[0].id if own else None)
        return op

    async def _continue(self, actor, subject, delivery):
        """Outside the delivery lock: claim takes that same lock itself."""
        project = delivery.project_id
        old = self.store.get_project_operation(project, self.CONTINUE, delivery.id)
        if old is not None and old.result.get("stage") == "REGISTERED":
            return dict(status="DONE", delivery_id=delivery.id, **delivery.result,
                        continuation=old.result["outcome"])
        with self.runtime.lock():
            self._scope(actor, project, subject.run_id)
            old = self.store.get_project_operation(project, self.CONTINUE, delivery.id)
            if old is None:
                old = ProjectOperation(project_id=project, coordinator_run_id=self.runtime.run_id,
                                       kind=self.CONTINUE, idempotency_key=delivery.id, result=dict(
                    stage="INTENT", delivery_id=delivery.id,
                    claim_key="after-delivery-" + delivery.id,
                ))
                self.store.add_project_operation(old)
        outcome = await self.claims.claim(actor, project, old.result["claim_key"])
        with self.runtime.lock():
            self._scope(actor, project, subject.run_id)
            self._save(old, status="SUCCEEDED" if outcome["status"] in {"REGISTERED", "WAIT"}
                       else "PENDING", stage=outcome["status"], outcome=outcome)
        return dict(status="DONE", delivery_id=delivery.id, **delivery.result,
                    continuation=outcome)

    def completed_plan(self, configured):
        """Trusted effective dependency view; original project/F34 specs never change."""
        subjects = [s for s in self.review.subjects.values()
                    if s.policy.native_epic_id == configured.native_epic_id]
        if not subjects:
            return configured
        subject = subjects[0]
        rows = [o for o in self._rows() if o.result.get("epic_run_id") == subject.run_id]
        if len(rows) != 1 or rows[0].status != "SUCCEEDED" or (
            rows[0].result.get("stage") != "RELEASED"
        ):
            raise CoordinatorError("COORDINATOR_COMPLETED_DELIVERY_UNVERIFIED")
        op = rows[0]
        self._bound(subject, op)
        control, _, _, view = subject.context()
        status, proof, _ = control.scheduler.sync.evidence.epic(
            self.runtime.principal, self.store.get_epic(subject.run_id),
        )
        if status != "Done" or proof["merge_commit"] != op.result["merge_commit"] or (
            subject.original_start.lifecycle.reconnect_epic(
                self.runtime.principal, subject.run_id)["status"] != "STOPPED"
        ):
            raise CoordinatorError("COORDINATOR_COMPLETED_DELIVERY_UNVERIFIED")
        return CoordinatorEpic.model_validate(configured.model_dump() | dict(
            spec=view.spec, native_task_ids=list(view.native_task_ids),
        ))
