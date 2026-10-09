from orchestrator.domain.models import EpicRun, TaskRun, TransitionEvent, utc_now
from orchestrator.domain.policy import Actor, Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState, task_kanban
from orchestrator.persistence.store import StateStore

T = TaskState
E = EpicState
_TASK_EDGES = {
    T.PLANNED: {T.CLAIMED},
    T.CLAIMED: {T.STARTING, T.PLANNED},
    T.STARTING: {T.WORKING, T.CLAIMED, T.BLOCKED},
    T.WORKING: {T.READY_FOR_REVIEW, T.BLOCKED},
    T.READY_FOR_REVIEW: {T.REVIEWING, T.BLOCKED},
    T.REVIEWING: {T.CHANGES_REQUESTED, T.APPROVED, T.READY_FOR_REVIEW, T.BLOCKED},
    T.CHANGES_REQUESTED: {T.WORKING, T.BLOCKED},
    T.APPROVED: {T.MERGING, T.CHANGES_REQUESTED, T.BLOCKED, T.READY_FOR_REVIEW},
    T.MERGING: {T.DONE, T.APPROVED, T.MERGING, T.BLOCKED},
    T.BLOCKED: {T.PARKED},
    T.PARKED: {
        T.WORKING,
        T.READY_FOR_REVIEW,
        T.REVIEWING,
        T.CHANGES_REQUESTED,
        T.APPROVED,
        T.MERGING,
        T.STARTING,
    },
    T.DONE: set(),
}
_EPIC_EDGES = {
    E.PLANNED: {E.ACTIVE},
    E.ACTIVE: {E.READY_FOR_REVIEW},
    E.READY_FOR_REVIEW: {E.REVIEWING},
    E.REVIEWING: {E.APPROVED, E.CHANGES_REQUESTED, E.READY_FOR_REVIEW},
    E.CHANGES_REQUESTED: {E.ACTIVE},
    E.APPROVED: {E.MERGING, E.CHANGES_REQUESTED},
    E.MERGING: {E.DONE, E.MERGING, E.APPROVED},
    E.DONE: set(),
}


class StateError(RuntimeError):
    """Rejected transition; no persistent changes have been made."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise StateError(message)


class StateService:
    def __init__(self, store: StateStore):
        self.store = store

    @staticmethod
    def authorize_scope(actor: Actor, run: EpicRun | TaskRun) -> None:
        require(actor.project_id == run.project_id, "actor scope is not authorized")
        if actor.role != Role.COORDINATOR:
            epic_id = run.epic_run_id if isinstance(run, TaskRun) else run.id
            require(actor.epic_run_id == epic_id, "actor scope is not authorized")
        if actor.role == Role.WORKER:
            require(
                isinstance(run, TaskRun) and actor.task_run_id == run.id,
                "actor scope is not authorized",
            )

    def transition_task(
        self,
        run_id: str,
        target: T,
        *,
        expected: T,
        event_id: str,
        actor: Actor,
        facts: VerifiedFacts | None = None,
    ) -> TaskRun:
        facts = facts or VerifiedFacts()
        with self.store.transaction():
            task = self.store.get_task(run_id)
            require(task is not None, "task run not found")
            self.authorize_scope(actor, task)
            require(
                actor.role == Role.INTEGRATION
                or (actor.role == Role.WORKER and target in {T.READY_FOR_REVIEW, T.BLOCKED}),
                "role cannot perform this task transition",
            )
            request = dict(
                kind="task",
                run_id=run_id,
                target=target,
                expected=expected,
                actor=actor.model_dump(mode="json"),
                facts=facts.model_dump(mode="json"),
            )
            prior = self.store.get_event(task.project_id, event_id)
            if prior:
                require(prior.request == request, "event ID already belongs to another request")
                return TaskRun.model_validate(prior.result)
            require(task.internal_status == expected, "task state changed; reread before retry")
            require(target in _TASK_EDGES[expected], "task transition is not allowed")
            updates = {"internal_status": target, "kanban_status": task_kanban(target)}
            if target == T.CLAIMED and expected == T.PLANNED:
                require(facts.dependencies_ready, "dependencies are not verified")
            if target == T.STARTING or expected == T.PARKED:
                require(
                    facts.slot_reserved and task.worker_slot is not None,
                    "worker slot is not reserved",
                )
            if expected == T.PARKED:
                require(target == task.resume_state, "resume must restore the parked phase")
                require(
                    facts.start_confirmed and task.codex_session_id is not None,
                    "resume has not been confirmed",
                )
                updates["resume_state"] = None
            if target == T.WORKING and expected == T.STARTING:
                require(
                    facts.start_confirmed and task.codex_session_id is not None,
                    "worker start has not been confirmed",
                )
            if target == T.WORKING and expected == T.CHANGES_REQUESTED:
                require(
                    facts.start_confirmed
                    and facts.slot_reserved
                    and task.worker_slot is not None
                    and task.codex_session_id is not None,
                    "worker correction has not been confirmed",
                )
            if expected == T.STARTING and target == T.CLAIMED:
                require(facts.inactivity_confirmed, "failed start is not confirmed inactive")
            if target in {T.BLOCKED, T.CHANGES_REQUESTED}:
                require(bool(facts.reason.strip()), "transition requires a concrete reason")
            if target == T.BLOCKED:
                updates["resume_state"] = expected
            if target == T.PARKED:
                require(facts.inactivity_confirmed, "worker inactivity is not confirmed")
            if target == T.READY_FOR_REVIEW and expected == T.WORKING:
                require(
                    task.current_commit is not None
                    and facts.tests_passed
                    and facts.verification_commit == task.current_commit,
                    "task commit and test evidence are required",
                )
            if target == T.READY_FOR_REVIEW and expected == T.APPROVED:
                epic = self.store.get_epic(task.epic_run_id)
                require(
                    actor.role == Role.INTEGRATION
                    and epic is not None
                    and bool(facts.reason.strip())
                    and task.current_commit == task.approved_source_commit == facts.source_commit
                    and task.approved_source_commit is not None
                    and task.approved_target_commit is not None
                    and facts.target_commit == epic.current_commit
                    and facts.target_commit is not None
                    and facts.target_commit != task.approved_target_commit,
                    "requeue requires unchanged verified task and changed actual epic base",
                )
                updates.update(approved_source_commit=None, approved_target_commit=None)
            if target == T.APPROVED and expected == T.REVIEWING:
                epic = self.store.get_epic(task.epic_run_id)
                reviews = self.store.get_reviews(task.id)
                require(bool(reviews), "task review is missing")
                review = reviews[-1]
                require(
                    review.review_result == "APPROVED"
                    and task.current_commit is not None
                    and epic.current_commit is not None
                    and review.review_commit == task.current_commit
                    and review.epic_commit == epic.current_commit,
                    "approval does not match current task and epic commits",
                )
                updates.update(
                    approved_source_commit=task.current_commit,
                    approved_target_commit=epic.current_commit,
                )
            if target == T.CHANGES_REQUESTED:
                updates.update(approved_source_commit=None, approved_target_commit=None)
            if target == T.MERGING or (expected == T.MERGING and target == T.DONE):
                require(
                    task.approved_source_commit is not None
                    and task.approved_target_commit is not None
                    and task.approved_source_commit == task.current_commit
                    and facts.source_commit == task.approved_source_commit
                    and facts.target_commit == task.approved_target_commit,
                    "merge does not match reviewed commits",
                )
                reviews = self.store.get_reviews(task.id)
                require(
                    bool(reviews)
                    and reviews[-1].review_result == "APPROVED"
                    and reviews[-1].review_commit == task.approved_source_commit
                    and reviews[-1].epic_commit == task.approved_target_commit,
                    "latest review no longer approves this merge",
                )
                if target == T.MERGING and expected != T.MERGING:
                    require(
                        self.store.get_epic(task.epic_run_id).current_commit
                        == task.approved_target_commit,
                        "epic base changed after review",
                    )
            if facts.merge_commit is not None and expected == T.MERGING:
                require(
                    task.merge_commit in {None, facts.merge_commit},
                    "merge evidence conflicts with persisted result",
                )
                updates["merge_commit"] = facts.merge_commit
            if expected == T.MERGING and target == T.APPROVED:
                require(
                    task.merge_commit is None and facts.merge_commit is None,
                    "completed merge must not be repeated",
                )
            if target == T.DONE:
                require(
                    facts.merge_commit is not None
                    and facts.tests_passed
                    and facts.verification_commit == facts.merge_commit,
                    "Done requires merge and verification evidence",
                )
                require(
                    task.merge_commit in {None, facts.merge_commit},
                    "merge evidence conflicts with persisted result",
                )
                updates["completed_at"] = utc_now()
            result = TaskRun.model_validate(task.model_dump() | updates)
            self.store.record_transition(
                result,
                TransitionEvent(
                    id=event_id,
                    project_id=task.project_id,
                    epic_run_id=task.epic_run_id,
                    task_run_id=task.id,
                    request=request,
                    result=result.model_dump(mode="json"),
                ),
            )
            return result

    def transition_epic(
        self,
        run_id: str,
        target: E,
        *,
        expected: E,
        event_id: str,
        actor: Actor,
        facts: VerifiedFacts | None = None,
    ) -> EpicRun:
        facts = facts or VerifiedFacts()
        with self.store.transaction():
            epic = self.store.get_epic(run_id)
            require(epic is not None, "epic run not found")
            self.authorize_scope(actor, epic)
            require(
                actor.role == Role.COORDINATOR
                or (actor.role == Role.INTEGRATION and target in {E.READY_FOR_REVIEW, E.ACTIVE}),
                "role cannot perform this epic transition",
            )
            request = dict(
                kind="epic",
                run_id=run_id,
                target=target,
                expected=expected,
                actor=actor.model_dump(mode="json"),
                facts=facts.model_dump(mode="json"),
            )
            prior = self.store.get_event(epic.project_id, event_id)
            if prior:
                require(prior.request == request, "event ID already belongs to another request")
                return EpicRun.model_validate(prior.result)
            require(epic.status == expected, "epic state changed; reread before retry")
            require(target in _EPIC_EDGES[expected], "epic transition is not allowed")
            updates = {"status": target}
            if target == E.READY_FOR_REVIEW and expected == E.ACTIVE:
                tasks = self.store.get_tasks(epic.id)
                require(
                    bool(tasks)
                    and all(t.internal_status == T.DONE for t in tasks)
                    and facts.scope_complete
                    and facts.tests_passed
                    and facts.verification_commit == epic.current_commit,
                    "epic scope and aggregate verification are incomplete",
                )
            if target == E.APPROVED and expected == E.REVIEWING:
                require(
                    facts.review_approved
                    and facts.source_commit is not None
                    and facts.source_commit == epic.current_commit
                    and facts.target_commit is not None
                    and facts.tests_passed
                    and facts.verification_commit == epic.current_commit,
                    "final review must cover current epic and main commits",
                )
                updates.update(
                    approved_source_commit=facts.source_commit,
                    approved_target_commit=facts.target_commit,
                )
            if target == E.CHANGES_REQUESTED:
                require(bool(facts.reason.strip()), "transition requires a concrete reason")
                updates.update(approved_source_commit=None, approved_target_commit=None)
            if target == E.MERGING or (expected == E.MERGING and target == E.DONE):
                require(
                    epic.approved_source_commit is not None
                    and epic.approved_target_commit is not None
                    and facts.source_commit == epic.current_commit == epic.approved_source_commit
                    and facts.target_commit == epic.approved_target_commit,
                    "main merge does not match reviewed commits",
                )
            if facts.merge_commit is not None and expected == E.MERGING:
                require(
                    epic.merge_commit in {None, facts.merge_commit},
                    "merge evidence conflicts with persisted result",
                )
                updates["merge_commit"] = facts.merge_commit
            if expected == E.MERGING and target == E.APPROVED:
                require(
                    epic.merge_commit is None and facts.merge_commit is None,
                    "completed merge must not be repeated",
                )
            if target == E.DONE:
                require(
                    facts.merge_commit is not None
                    and facts.tests_passed
                    and facts.verification_commit == facts.merge_commit,
                    "epic Done requires main merge and final verification",
                )
                require(
                    epic.merge_commit in {None, facts.merge_commit},
                    "merge evidence conflicts with persisted result",
                )
                updates["completed_at"] = utc_now()
            result = EpicRun.model_validate(epic.model_dump() | updates)
            self.store.record_transition(
                result,
                TransitionEvent(
                    id=event_id,
                    project_id=epic.project_id,
                    epic_run_id=epic.id,
                    request=request,
                    result=result.model_dump(mode="json"),
                ),
            )
            return result
