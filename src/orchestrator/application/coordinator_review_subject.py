"""Trusted composition: original native registration and verified effective plan."""

from copy import deepcopy

from orchestrator.application.epic_correction_plan import CorrectionPlanError, EpicCorrectionPlan
from orchestrator.application.epic_integration_service import EpicIntegrationService
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.domain.epic_completion import EpicCompletionSpec
from orchestrator.domain.teamplayer import BoardEpic
from orchestrator.domain.worker_contracts import LocalTaskSpec, canonical_json


def completion_for(policy, spec):
    ids = tuple(entry.task.task_id for entry in spec.tasks)
    original_ids = tuple(entry.task.task_id for entry in policy.original.tasks)
    base = policy.completion if ids == original_ids else policy.correction_completion
    if base is None:
        raise CorrectionPlanError("CORRECTION_COMPLETION_CONFIGURATION_MISSING")
    data = base.model_dump(mode="json") | {"task_ids": list(ids)}
    for name in ("build", "test", "acceptance"):
        data[name]["argv"] = [arg.replace("{task_count}", str(len(ids)))
                              for arg in data[name]["argv"]]
    return EpicCompletionSpec.model_validate_json(canonical_json(data))


class CoordinatorReviewSubject:
    def __init__(self, runtime, original_start, policy, context_factory, creator):
        self.runtime, self.original_start = runtime, original_start
        self.policy = deepcopy(policy)
        self.factory, self.creator = context_factory, creator
        self.plan = EpicCorrectionPlan(runtime, original_start, policy)
        self.run_id = original_start.principal.epic_run_id
        configured = next((e for e in runtime.spec.epics
                           if e.native_epic_id == policy.native_epic_id), None)
        if configured is None or configured.spec != policy.original or (
            configured.native_task_ids != policy.native_task_ids
            or runtime.spec.native_project_id != policy.native_project_id
            or runtime.spec.project_id != policy.original.project_id
            or runtime.store is not original_start.store
            or runtime.settings != original_start.settings
            or original_start.external_epic_id not in {None, policy.native_epic_id}
            or creator.project_id != policy.native_project_id
            or creator.epic_id != policy.native_epic_id or creator.user_id != policy.user_id
        ):
            raise CorrectionPlanError("COORDINATOR_REVIEW_SUBJECT_CONFIGURATION_INVALID")

    async def board_facts(self, adapter):
        rows = await adapter.read("list_epics", {"projectId": self.policy.native_project_id})
        matches = [row for row in rows if row.get("id") == self.policy.native_epic_id]
        if len(matches) != 1:
            raise CorrectionPlanError("COORDINATOR_REVIEW_BOARD_EPIC_MISSING")
        row = BoardEpic.model_validate(matches[0])
        if row.project_id != self.policy.native_project_id or row.status != "InProgress" or (
            row.name != self.policy.original.title
        ):
            raise CorrectionPlanError("COORDINATOR_REVIEW_BOARD_EPIC_CHANGED")
        return (digest(canonical_json(row.model_dump(mode="json"))),
                digest(canonical_json(row.model_dump(mode="json", exclude={"version"}))))

    async def board_stamp(self, adapter):
        return (await self.board_facts(adapter))[0]

    def context(self):
        view = self.plan.registered_view()
        control = self.factory(view)
        completion = control.epic_completion
        spec = view.spec
        selection = control.scheduler.selection
        expected = {identity: entry.task for identity, entry in zip(
            view.native_task_ids, spec.tasks, strict=True,
        )}
        actual = {identity: LocalTaskSpec.model_validate(value)
                  for identity, value in selection.specs.items()}
        if control.epic_start is not view or control.store is not self.runtime.store or (
            control.settings != self.runtime.settings or completion is None
            or completion.spec != completion_for(self.policy, spec)
            or completion.epic_start is not view or completion.scheduler is not control.scheduler
            or control.scheduler.registration is not view
            or selection.reader.project_id != self.policy.native_project_id
            or selection.reader.user_id != self.policy.user_id or actual != expected
        ):
            raise CorrectionPlanError("COORDINATOR_REVIEW_EFFECTIVE_COMPOSITION_INVALID")
        configured = self.policy.verification
        manager = EpicIntegrationService(
            control.settings, control.store,
            expected_task_ids=tuple(entry.task.task_id for entry in spec.tasks),
            test_command=tuple(arg.replace("{task_count}", str(len(spec.tasks)))
                               for arg in configured.argv),
            test_timeout=configured.timeout,
        )
        return control, completion, manager, view
