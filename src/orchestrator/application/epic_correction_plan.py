"""Additive verified plan projection; the actual F34 registration never changes."""

from copy import deepcopy

from orchestrator.application.coordinator_runtime_service import CoordinatorError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.domain.coordinator import ProjectOperation
from orchestrator.domain.integration_contracts import EpicIntegrationSpec, IntegrationTask
from orchestrator.domain.models import utc_now
from orchestrator.domain.review_contracts import ChangesDecision
from orchestrator.domain.teamplayer import BoardTask
from orchestrator.domain.worker_contracts import canonical_json


class CorrectionPlanError(CoordinatorError):
    pass


class EpicCorrectionPlan:
    KIND = "coordinator_epic_correction"

    def __init__(self, runtime, original_start, policy):
        self.runtime, self.original_start, self.store = runtime, original_start, runtime.store
        self._policy_json = canonical_json(policy.model_dump(mode="json"))
        self.policy_hash = digest(self._policy_json)
        self.policy = deepcopy(policy)
        if original_start.spec != policy.original or original_start.store is not self.store:
            raise CorrectionPlanError("CORRECTION_ORIGINAL_REGISTRATION_MISMATCH")
        self.run_id = original_start.principal.epic_run_id

    def operations(self):
        return [o for o in self.store.project_operations(self.runtime.run_id, kind=self.KIND)
                if o.result.get("epic_run_id") == self.run_id]

    def current(self):
        if canonical_json(self.policy.model_dump(mode="json")) != self._policy_json:
            raise CorrectionPlanError("CORRECTION_POLICY_CHANGED")
        entries = list(self.policy.original.tasks)
        native = list(self.policy.native_task_ids)
        records = []
        for op in sorted(self.operations(), key=lambda o: o.result["revision"]):
            if op.status != "SUCCEEDED" or op.result.get("stage") not in {
                "ACTIVE", "DISPATCH_PENDING", "DISPATCHED",
            }:
                continue
            result = op.result
            review = self.store.get_project_operation(
                op.project_id, "coordinator_epic_review", result["review_key"]
            )
            proof = (self.store.get_operation(op.project_id, "epic_review",
                                             "cr-decision-" + review.id)
                     if review is not None else None)
            if result["policy_hash"] != self.policy_hash or review is None or (
                review.status != "SUCCEEDED"
                or review.coordinator_run_id != self.runtime.run_id
                or review.result.get("epic_run_id") != self.run_id
                or review.result.get("decision", {}).get("result") != "CHANGES_REQUESTED"
                or review.result.get("review_operation_id") != result["review_operation_id"]
                or proof is None or proof.status != "SUCCEEDED"
                or proof.id != result["review_operation_id"]
                or proof.epic_run_id != self.run_id or proof.result.get("approved") is not False
            ):
                raise CorrectionPlanError("CORRECTION_REVIEW_LINK_UNVERIFIED")
            entry = IntegrationTask.model_validate(result["entry"])
            choice_number = int(op.idempotency_key.rsplit(":", 1)[1])
            decision = ChangesDecision.model_validate(review.result["decision"])
            issue = next((i for i in decision.issues if i.number == choice_number), None)
            created = BoardTask.model_validate(result["native_created"])
            if digest(canonical_json(result["entry"])) != result["entry_hash"] or (
                entry.task.project_id != self.policy.original.project_id
                or entry.task.epic_id != self.policy.original.epic_id
                or entry.task.task_id in {e.task.task_id for e in entries}
                or result["native_task_id"] in native
                or issue is None
                or self.pending_entry(result["template_id"], issue, result["revision"]) != entry
                or created.id != result["native_task_id"]
                or created.project_id != self.policy.native_project_id
                or created.epic_id != self.policy.native_epic_id
                or created.execution_owner_kind != "User"
                or created.responsible_user_id != self.policy.user_id
                or list(created.acceptance_criteria) != entry.task.acceptance_criteria
            ):
                raise CorrectionPlanError("CORRECTION_MEMBERSHIP_CHANGED")
            entries.append(entry)
            native.append(result["native_task_id"])
            records.append(dict(id=op.id, entry_hash=result["entry_hash"],
                                native_task_id=result["native_task_id"]))
        spec = EpicIntegrationSpec.model_validate(
            self.policy.original.model_dump() | {"tasks": entries}
        )
        return spec, native, digest(canonical_json(records))

    def pending_entry(self, template_id, issue, revision):
        template = next((t for t in self.policy.templates if t.template_id == template_id), None)
        if template is None or not set(issue.acceptance_criteria) <= set(template.criteria):
            raise CorrectionPlanError("CORRECTION_TEMPLATE_SCOPE_DENIED")
        local_id = template.entry.task.task_id + "-r" + str(revision)
        task = template.entry.task.model_dump()
        task.update(task_id=local_id, name=local_id + " " + task["name"],
                    goal=task["goal"] + "\nReview finding: " + issue.problem
                    + "\nRequested correction: " + issue.requested_change)
        return IntegrationTask.model_validate(template.entry.model_dump() | {"task": task})

    def intent(self, review, choice, issue):
        key = review.id + ":" + str(issue.number)
        prior = self.store.get_project_operation(self.runtime.spec.project_id, self.KIND, key)
        if prior is not None:
            if prior.result.get("template_id") != choice.template_id:
                raise CorrectionPlanError("CORRECTION_KEY_CONFLICT")
            return prior
        revision = max([o.result["revision"] for o in self.operations()] or [0]) + 1
        if revision > 128:
            raise CorrectionPlanError("CORRECTION_REVISION_LIMIT")
        entry = self.pending_entry(choice.template_id, issue, revision)
        spec, _, _ = self.current()
        reserved = [IntegrationTask.model_validate(o.result["entry"]).task.task_id
                    for o in self.operations()]
        project_ids = [entry.task.task_id for epic in self.runtime.spec.epics
                       for entry in epic.spec.tasks]
        project_ids += [o.result["entry"]["task"]["task_id"]
                        for o in self.store.project_operations(self.runtime.run_id, kind=self.KIND)]
        if len(self.policy.original.tasks) + len(reserved) >= 128 or (
            entry.task.task_id.lower() in {
                identity.lower() for identity in [
                    *(t.task.task_id for t in spec.tasks), *reserved, *project_ids,
                ]
            }
        ):
            raise CorrectionPlanError("CORRECTION_TASK_SCOPE_LIMIT_OR_COLLISION")
        op = ProjectOperation(
            project_id=self.runtime.spec.project_id, coordinator_run_id=self.runtime.run_id,
            kind=self.KIND, idempotency_key=key, result=dict(
                stage="INTENT", epic_run_id=self.run_id, revision=revision,
                policy_hash=self.policy_hash, template_id=choice.template_id,
                review_key=review.idempotency_key,
                review_operation_id=review.result["review_operation_id"],
                entry=entry.model_dump(mode="json"),
                entry_hash=digest(canonical_json(entry.model_dump(mode="json"))),
                marker="herdr-correction:" + key, create_key="hc-correction-" + digest(key),
            ),
        )
        self.store.add_project_operation(op)
        return op

    def save(self, op, *, status=None, **result):
        with self.store.transaction():
            current = self.store.get_project_operation(op.project_id, op.kind, op.idempotency_key)
            if current is None or current.id != op.id:
                raise CorrectionPlanError("CORRECTION_INTENT_MISSING")
            fixed = {"epic_run_id", "revision", "policy_hash", "template_id", "review_key",
                     "review_operation_id", "entry", "entry_hash", "marker", "create_key"}
            if "native_task_id" in current.result:
                fixed.add("native_task_id")
            if any(name in result and result[name] != current.result[name] for name in fixed):
                raise CorrectionPlanError("CORRECTION_IMMUTABLE_INTENT_CHANGED")
            updated = current.model_copy(update={
                "status": status or current.status, "result": current.result | result,
                "updated_at": utc_now(),
            })
            self.store.update_project_operation(updated)
            return updated

    def registered_view(self):
        return RegisteredIntegrationPlan(self)


class RegisteredIntegrationPlan:
    """Real original principal/session plus an explicit registered effective scope."""

    def __init__(self, plan):
        self.plan = plan
        self.original = plan.original_start
        spec, native, self.membership_hash = plan.current()
        self._spec_json = canonical_json(spec.model_dump(mode="json"))
        self.native_task_ids = tuple(native)
        for name in ("store", "settings", "principal", "codex", "herdr", "configuration"):
            setattr(self, name, getattr(self.original, name))

    @property
    def spec(self):
        return EpicIntegrationSpec.model_validate_json(self._spec_json)

    def scheduler_identity(self, configuration):
        """Preserve the original owner only for this verified additive membership.

        Other scheduler configuration remains in the digest. No stored owner or
        original task claim is rewritten to make a new scheduler appear valid.
        """
        expected = {identity: entry.task.model_dump(mode="json")
                    for identity, entry in zip(self.native_task_ids, self.spec.tasks, strict=True)}
        if configuration["specs"] != expected or (
            configuration["order"] != list(self.native_task_ids)
            or any(configuration["bindings"].get(identity, {}).get("local_id")
                   != entry.task.task_id for identity, entry in zip(
                       self.native_task_ids, self.spec.tasks, strict=True))
        ):
            raise CorrectionPlanError("CORRECTION_SCHEDULER_MEMBERSHIP_INVALID")
        original_ids = set(self.plan.policy.native_task_ids)
        added = set(self.native_task_ids) - original_ids
        base = deepcopy(configuration)
        for name in ("specs", "bindings"):
            base[name] = {key: value for key, value in base[name].items() if key not in added}
        base["order"] = [identity for identity in base["order"] if identity not in added]
        return digest(canonical_json(base))

    def connection_principal(self, actor):
        self.original.connection_principal(actor)
        spec, native, stamp = self.plan.current()
        if stamp != self.membership_hash or tuple(native) != self.native_task_ids or (
            canonical_json(spec.model_dump(mode="json")) != self._spec_json
        ):
            raise CorrectionPlanError("CORRECTION_EFFECTIVE_PLAN_STALE")
        return self.principal
