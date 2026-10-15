"""Real task creation before additive activation; one original Integration dispatch."""

from orchestrator.adapters.herdr import HerdrError
from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.application.coordinator_runtime_service import CoordinatorError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.state_service import StateService
from orchestrator.domain.coordinator_review import CorrectionChoice
from orchestrator.domain.integration_contracts import IntegrationTask
from orchestrator.domain.policy import VerifiedFacts
from orchestrator.domain.review_contracts import ChangesDecision
from orchestrator.domain.states import EpicState
from orchestrator.domain.teamplayer import BoardTask
from orchestrator.domain.worker_contracts import canonical_json


class CoordinatorCorrectionService:
    def __init__(self, owner):
        self.owner, self.store = owner, owner.store

    def _scope(self, subject):
        return self.owner._scope(self.owner.runtime.principal,
                                 self.owner.runtime.spec.project_id, subject.run_id)

    async def _before_effect(self, subject, review):
        self._scope(subject)
        context = self.owner._find_context(subject.run_id, review.result["context_id"])
        epic = self.store.get_epic(subject.run_id)
        if epic.status != EpicState.CHANGES_REQUESTED or (
            subject.original_start.git.head(epic.branch) != context.result["source_commit"]
            or self.owner.runtime.main() != context.result["target_commit"]
        ):
            raise CoordinatorError("CORRECTION_REJECTED_BASE_CHANGED")
        _, scope_hash = await subject.board_facts(subject.creator.adapter)
        if scope_hash != context.result["epic_board_scope_hash"]:
            raise CoordinatorError("CORRECTION_EPIC_SCOPE_CHANGED")

    def _request(self, subject, correction):
        entry = IntegrationTask.model_validate(correction.result["entry"])
        spec, native, _ = subject.plan.current()
        identities = {entry.task.task_id: identity for entry, identity in zip(
            spec.tasks, native, strict=True,
        )}
        task = entry.task
        description = (
            "# Correction " + task.task_id + "\n\n" + task.goal + "\n\n"
            + "Review operation: " + correction.result["review_operation_id"] + "\n"
            + correction.result["marker"] + "\nEntry SHA256: "
            + correction.result["entry_hash"] + "\n\nScope:\n"
            + "\n".join("- " + item for item in task.scope) + "\n\nAcceptance:\n"
            + "\n".join("- " + item for item in task.acceptance_criteria)
            + "\n\nOut of scope:\n" + "\n".join("- " + item for item in task.out_of_scope)
            + "\n\nVerification:\n" + "\n".join("- " + item for item in task.verification_steps)
            + "\n\nSources:\n" + "\n".join("- " + item for item in task.sources)
        )
        return dict(
            projectId=subject.policy.native_project_id, epicId=subject.policy.native_epic_id,
            name=task.name, description=description, taskType="Task",
            priority={"P0": "Critical", "P1": "High", "P2": "Medium", "P3": "Low"}[
                entry.priority
            ], acceptanceCriteria=task.acceptance_criteria,
            dependsOnTaskIds=[identities[name] for name in task.dependencies],
            executionOwnerKind="User", responsibleUserId=subject.policy.user_id,
            validationOwnerKind="User", validationUserId=subject.policy.user_id,
            traceabilityArtifactIds=[],
        )

    @staticmethod
    def _validate_created(subject, row, request):
        task = BoardTask.model_validate(row)
        if task.project_id != subject.policy.native_project_id or (
            task.epic_id != subject.policy.native_epic_id
            or task.name != request["name"] or task.status != "Pending"
            or task.execution_owner_kind != "User"
            or task.responsible_user_id != subject.policy.user_id
            or list(task.acceptance_criteria) != request["acceptanceCriteria"]
            or set(task.dependencies) != set(request["dependsOnTaskIds"])
            or len(task.dependencies) != len(request["dependsOnTaskIds"])
            or int(task.priority) != {"Critical": 3, "High": 2, "Medium": 1, "Low": 0}[
                request["priority"]
            ]
            or not (task.description == request["description"]
                    or task.description.startswith(request["description"] + "\n"))
        ):
            raise CoordinatorError("CORRECTION_CREATED_TASK_SCOPE_MISMATCH")
        return task

    async def _reconcile(self, subject, correction, request):
        result = await subject.creator.adapter.read(
            "list_tasks", {"projectId": subject.policy.native_project_id},
        )
        if result.get("projectId") != subject.policy.native_project_id:
            raise CoordinatorError("CORRECTION_RECONCILIATION_PROJECT_CHANGED")
        matches = [row for row in result["tasks"]
                   if correction.result["marker"] in row.get("description", "").splitlines()]
        if len(matches) > 1:
            raise CoordinatorError("CORRECTION_RECONCILIATION_AMBIGUOUS")
        if not matches:
            return None
        row = await subject.creator.adapter.read("get_task", dict(
            projectId=subject.policy.native_project_id, taskId=matches[0]["taskId"],
        ))
        return self._validate_created(subject, row, request)

    async def _create(self, subject, review, correction):
        if correction.result["stage"] not in {"INTENT", "CREATING", "CREATED"}:
            return correction
        await self._before_effect(subject, review)
        request = self._request(subject, correction)
        if subject.creator.adapter.redact_text(canonical_json(request)) != canonical_json(request):
            raise CoordinatorError("CORRECTION_PRIVATE_VALUE_DENIED")
        if correction.result["stage"] == "INTENT":
            # Verify capability before declaring an external create attempt.
            await subject.creator.verify_capability()
            correction = subject.plan.save(correction, stage="CREATING", request=request)
            try:
                created = await subject.creator.create(request, correction.result["create_key"])
                row = await subject.creator.adapter.read("get_task", dict(
                    projectId=subject.policy.native_project_id, taskId=created["taskId"],
                ))
                task = self._validate_created(subject, row, request)
            except TeamPlayerError:
                task = await self._reconcile(subject, correction, request)
                if task is None:
                    raise CoordinatorError("CORRECTION_CREATE_OUTCOME_UNCONFIRMED") from None
        else:
            if correction.result.get("request") != request:
                raise CoordinatorError("CORRECTION_CREATE_REQUEST_CHANGED")
            task = await self._reconcile(subject, correction, request)
            if task is None:
                raise CoordinatorError("CORRECTION_CREATE_OUTCOME_UNCONFIRMED")
        return subject.plan.save(correction, stage="CREATED", native_task_id=task.id,
                                 native_created=task.model_dump(mode="json", by_alias=True))

    async def _dispatch(self, subject, review, corrections):
        self._scope(subject)
        plan = subject.plan
        first = self.store.get_project_operation(
            corrections[0].project_id, corrections[0].kind, corrections[0].idempotency_key,
        )
        original = subject.original_start
        epic = self.store.get_epic(subject.run_id)
        sid = epic.codex_session_id
        if first.result["stage"] == "ACTIVE":
            start = self.store.get_operation(epic.project_id, "epic_start", epic.id)
            _, _, facts = original._native(self.owner.runtime.principal, start)
            if not facts["ready"]:
                return "WAITING_FOR_INTEGRATION_IDLE"
            message = canonical_json(dict(
                version=1, type="EPIC_CORRECTION_PLAN_ACTIVE", epic_run_id=epic.id,
                review_id=review.id, decision=review.result["decision"],
                membership_hash=plan.current()[2],
                tasks=[dict(local_task_id=o.result["entry"]["task"]["task_id"],
                            native_task_id=o.result["native_task_id"]) for o in corrections],
                completion_key="after-review-" + review.id,
                instruction="Use current integration_overview and the verified existing task "
                "workflow. Deliver only unfinished correction tasks, then epic_complete with "
                "the supplied new key. If already complete, report the latest existing handoff. "
                "Keep this same session. Do not implement Done tasks again or merge main.",
            ))
            first = plan.save(first, stage="DISPATCH_PENDING", dispatch_text=message,
                              dispatch_hash=digest(message), dispatch_session_id=sid)
            try:
                original.herdr.prompt(epic.integration_agent_id, message, timeout_ms=45000)
            except HerdrError:
                pass
        if first.result["stage"] == "DISPATCH_PENDING":
            if first.result["dispatch_session_id"] != sid:
                raise CoordinatorError("CORRECTION_INTEGRATION_SESSION_CHANGED")
            thread = original.codex.read_thread(sid, epic.worktree_path)
            if thread["id"] != sid or thread["sessionId"] != sid or (
                thread["cwd"] != epic.worktree_path
            ):
                raise CoordinatorError("CORRECTION_DISPATCH_THREAD_CHANGED")
            matches = [(t["id"], item["id"]) for t in thread["turns"] for item in t["items"]
                       if item.get("type") == "userMessage" and any(
                           part.get("type") == "text"
                           and digest(part.get("text", "")) == first.result["dispatch_hash"]
                           for part in item.get("content", [])
                       )]
            if len(matches) != 1:
                raise CoordinatorError("CORRECTION_DISPATCH_OUTCOME_UNCONFIRMED")
            first = plan.save(first, stage="DISPATCHED", dispatch_turn=matches[0][0],
                              dispatch_item=matches[0][1])
        if first.result["stage"] == "DISPATCHED":
            # A crash after the first receipt must not strand the remaining members.
            for item in corrections[1:]:
                current = self.store.get_project_operation(
                    item.project_id, item.kind, item.idempotency_key,
                )
                if current.result["stage"] != "DISPATCHED":
                    plan.save(current, stage="DISPATCHED", dispatch_parent_id=first.id)
        return first.result["stage"]

    async def resume(self, subject, review):
        self._scope(subject)
        latest = [o for o in self.store.project_operations(self.owner.runtime.run_id,
                                                          kind=self.owner.REVIEW)
                  if o.result.get("epic_run_id") == subject.run_id and o.status == "SUCCEEDED"]
        if max(latest, key=lambda o: (o.created_at, o.id)).id != review.id:
            return dict(status="HISTORICAL_REJECTION", review_id=review.id, current=False)
        decision = ChangesDecision.model_validate(review.result["decision"])
        choices = [CorrectionChoice.model_validate(value) for value in review.result["choices"]]
        if not choices:
            return dict(status="EPIC_CHANGES_REQUESTED", review_id=review.id,
                        attention="CORRECTION_TEMPLATE_REQUIRED")
        with self.store.transaction():
            corrections = [subject.plan.intent(
                review, next(c for c in choices if c.issue_number == issue.number), issue,
            ) for issue in decision.issues]
        try:
            if any(o.status != "SUCCEEDED" for o in corrections):
                corrections = [await self._create(subject, review, op) for op in corrections]
                await self._before_effect(subject, review)
                with self.store.transaction():
                    StateService(self.store).transition_epic(
                        subject.run_id, EpicState.ACTIVE, expected=EpicState.CHANGES_REQUESTED,
                        event_id="correction-active:" + review.id,
                        actor=self.owner.runtime.principal,
                        facts=VerifiedFacts(
                            reason="Verified new correction tasks for " + review.id),
                    )
                    corrections = [subject.plan.save(o, status="SUCCEEDED", stage="ACTIVE")
                                   for o in corrections]
            dispatch = await self._dispatch(subject, review, corrections)
            return dict(status="EPIC_CHANGES_REQUESTED", review_id=review.id, dispatch=dispatch,
                        membership_hash=subject.plan.current()[2], tasks=[dict(
                            local_task_id=o.result["entry"]["task"]["task_id"],
                            native_task_id=o.result["native_task_id"], correction_id=o.id,
                        ) for o in corrections])
        except (CoordinatorError, TeamPlayerError) as error:
            return dict(status="EPIC_CHANGES_REQUESTED", review_id=review.id,
                        attention=str(error), preserve_existing_intents=True)
