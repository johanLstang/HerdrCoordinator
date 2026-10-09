"""Persist verified blockers, mirror Attention, and park through actual F13 evidence."""

import fcntl
import os
from contextlib import contextmanager

from pydantic import ValidationError

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.runtime_lifecycle_service import (
    LifecycleError,
    RuntimeLifecycleService,
)
from orchestrator.application.state_service import StateService
from orchestrator.application.task_review_service import TaskReviewService
from orchestrator.application.worker_report_service import WorkerReportService
from orchestrator.domain.attention import BlockerDetails, ReviewBlockDecision
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import canonical_json


class AttentionError(RuntimeError):
    """Safe code; no untrusted report, decision, credentials or upstream exception."""


def attention_subject(task, start):
    return {
        "project_id": task.project_id,
        "epic_run_id": task.epic_run_id,
        "task_run_id": task.id,
        "task_id": task.task_id,
        "branch": task.branch,
        "worktree_path": task.worktree_path,
        "session_id": task.codex_session_id,
        "start_id": start.id,
        "generation": start.result.get("generation", start.id),
        "server_session": task.herdr_server_session,
        "agent_id": task.worker_agent_id,
        "binding": start.result["binding"],
        "base_commit": task.base_commit,
    }


ATTENTION_IMMUTABLE = (
    "actor",
    "subject",
    "details",
    "source_id",
    "source_kind",
    "source_hash",
    "reserved_slot",
    "block_event_id",
    "block_event_hash",
    "stop_key",
    "decision",
)


def attention_hash(result):
    return digest(canonical_json({k: result[k] for k in ATTENTION_IMMUTABLE if k in result}))


class TaskAttentionService:
    KIND = "task_attention"

    def __init__(
        self,
        settings,
        store,
        sync,
        herdr,
        codex=None,
        *,
        processes=None,
        worker_responsible_role="User",
    ):
        if (
            settings != sync.settings
            or sync.store is not store
            or worker_responsible_role not in {"User", "Integration", "Coordinator"}
        ):
            raise AttentionError("ATTENTION_CONFIGURATION_INVALID")
        self.settings, self.store, self.sync = settings, store, sync
        self.worker_responsible_role = worker_responsible_role
        self.reports = WorkerReportService(settings, store, herdr, codex)
        self.review = TaskReviewService(settings, store)
        self.lifecycle = RuntimeLifecycleService(
            settings, store, herdr, codex=codex, processes=processes
        )
        self.git = self.reports.integration.git

    @contextmanager
    def _lock(self, run_id):
        path = str(self.settings.sqlite_path) + ".attention-" + digest(run_id) + ".lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                raise AttentionError("ATTENTION_BUSY") from None
            except OSError:
                os.close(fd)
                raise
        except OSError:
            raise AttentionError("ATTENTION_LOCK_UNAVAILABLE") from None
        try:
            yield
        finally:
            os.close(fd)

    def _scope(self, actor, run_id):
        task = self.store.get_task(run_id)
        if (
            actor is None
            or actor.role != Role.INTEGRATION
            or task is None
            or actor.project_id != self.sync.project_id
        ):
            raise AttentionError("ATTENTION_SCOPE_DENIED")
        try:
            StateService.authorize_scope(actor, task)
            epic = self.store.get_epic(task.epic_run_id)
            if epic is None or epic.status != EpicState.ACTIVE or task.completed_at:
                raise ValueError
            self.reports.integration.worktrees.verify_owned_worktree(task)
            self.lifecycle._validate(actor, task.id, True)
        except Exception:
            raise AttentionError("ATTENTION_RUNTIME_UNVERIFIED") from None
        return task

    def _block(self, task):
        events = [
            e
            for e in self.store.events(task.project_id, task.epic_run_id, task.id)
            if e.request.get("target") == "BLOCKED"
        ]
        if not events:
            raise AttentionError("ATTENTION_BLOCK_SOURCE_MISSING")
        return events[-1]

    def _saved(self, actor, task, block):
        matches = [
            o
            for o in self.store.get_operations(task.epic_run_id, kind=self.KIND)
            if o.task_run_id == task.id and o.result.get("block_event_id") == block.id
        ]
        if len(matches) > 1:
            raise AttentionError("ATTENTION_MULTIPLE_OWNERS")
        if not matches:
            return None
        op = matches[0]
        start = self.store.get_operation(task.project_id, "start_runtime", task.id)
        if (
            op.result.get("intent_hash") != attention_hash(op.result)
            or op.result["actor"] != actor.model_dump(mode="json")
            or op.result["subject"] != attention_subject(task, start)
            or (
                task.internal_status == TaskState.BLOCKED
                and task.worker_slot != op.result["reserved_slot"]
            )
            or op.result["block_event_hash"]
            != digest(canonical_json(block.model_dump(mode="json")))
        ):
            raise AttentionError("ATTENTION_INTENT_CHANGED")
        return op

    def _save(self, op, *, stage=None, error=None, **updates):
        with self.store.transaction():
            current = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
            if current is None or current.id != op.id:
                raise AttentionError("ATTENTION_INTENT_CHANGED")
            result = current.result | updates
            if stage is not None:
                result["stage"] = stage
            value = current.model_copy(
                update={
                    "result": result,
                    "error_code": error,
                    "updated_at": utc_now(),
                    "status": "SUCCEEDED" if result["stage"] == "PARKED_AND_SYNCED" else "PENDING",
                }
            )
            self.store.update_operation(value)
            return value

    def _create(self, actor, task, source, details, *, review=False, decision=None):
        self.lifecycle.start._slot(task)
        start = self.sync.evidence._start(task)
        key = digest(canonical_json([task.id, source.id, review]))
        prior = self.store.get_operation(task.project_id, self.KIND, key)
        if prior is not None:
            raise AttentionError("ATTENTION_INTENT_RECONCILIATION_REQUIRED")
        op = Operation(
            project_id=task.project_id,
            epic_run_id=task.epic_run_id,
            task_run_id=task.id,
            kind=self.KIND,
            idempotency_key=key,
            result={
                "actor": actor.model_dump(mode="json"),
                "subject": attention_subject(task, start),
                "details": details.model_dump(mode="json"),
                "source_id": source.id,
                "source_kind": "review" if review else "worker",
                "stage": "RECORDED",
                "reserved_slot": task.worker_slot,
                "source_hash": digest(canonical_json(source.model_dump(mode="json"))),
                **({"decision": decision.model_dump(mode="json")} if decision else {}),
            },
        )
        self.store.add_operation(op)
        if review:
            task = StateService(self.store).transition_task(
                task.id,
                TaskState.BLOCKED,
                expected=TaskState.REVIEWING,
                event_id="attention-block:" + op.id,
                actor=actor,
                facts=VerifiedFacts(reason=details.message()),
            )
        block = self._block(task)
        op = self._save(
            op,
            block_event_id=block.id,
            block_event_hash=digest(canonical_json(block.model_dump(mode="json"))),
            stop_key="attention-park:" + op.id,
        )
        return self._save(op, intent_hash=attention_hash(op.result))

    async def park_blocked(self, actor, run_id):
        self._scope(actor, run_id)
        with self._lock(run_id):
            return await self._park_blocked(actor, run_id)

    async def _park_blocked(self, actor, run_id):
        task = self._scope(actor, run_id)
        if task.internal_status == TaskState.WORKING:
            # Read the actual correlated native report; no caller-supplied blocker text.
            self.reports.collect(actor, task.id, expected_status="BLOCKED")
            task = self._scope(actor, run_id)
        if task.internal_status not in {TaskState.BLOCKED, TaskState.PARKED}:
            raise AttentionError("ATTENTION_TASK_NOT_BLOCKED")
        with self.reports.integration._lock(), self.store.transaction():
            task = self._scope(actor, run_id)
            block = self._block(task)
            op = self._saved(actor, task, block)
            if op is None:
                if task.internal_status == TaskState.PARKED:
                    raise AttentionError("ATTENTION_PARKED_WITHOUT_JOURNAL")
                _, proof, _ = self.sync.evidence.task(task)
                sources = [
                    o
                    for o in self.store.get_operations(task.epic_run_id, kind="worker_report")
                    if o.id == proof.get("block_source_id") and o.task_run_id == task.id
                ]
                if len(sources) != 1 or sources[0].result.get("stage") != "BLOCKED":
                    raise AttentionError("ATTENTION_WORKER_SOURCE_UNVERIFIED")
                source = sources[0]
                details = BlockerDetails(
                    reason=source.result["report"]["reason"],
                    input_required=source.result["report"]["input_required"],
                    responsible_role=self.worker_responsible_role,
                )
                op = self._create(actor, task, source, details)
        return await self._perform(actor, op)

    async def block_review(self, actor, run_id, decision):
        self._scope(actor, run_id)
        with self._lock(run_id):
            return await self._block_review(actor, run_id, decision)

    async def _block_review(self, actor, run_id, decision):
        task = self._scope(actor, run_id)
        try:
            decision = ReviewBlockDecision.model_validate(decision)
        except ValidationError:
            raise AttentionError("ATTENTION_DECISION_INVALID") from None
        with self.reports.integration._lock(), self.store.transaction():
            task = self._scope(actor, run_id)
            if task.internal_status in {TaskState.BLOCKED, TaskState.PARKED}:
                op = self._saved(actor, task, self._block(task))
                if (
                    op is None
                    or op.result["source_kind"] != "review"
                    or op.result["decision"] != decision.model_dump(mode="json")
                ):
                    raise AttentionError("ATTENTION_DECISION_CHANGED")
            else:
                if task.internal_status != TaskState.REVIEWING:
                    raise AttentionError("ATTENTION_TASK_NOT_REVIEWING")
                packages = [
                    o
                    for o in self.store.get_operations(task.epic_run_id, kind=self.review.KIND)
                    if o.task_run_id == task.id
                ]
                if not packages:
                    raise AttentionError("ATTENTION_REVIEW_MISSING")
                package = max(packages, key=lambda o: o.created_at)
                if (
                    package.status != "SUCCEEDED"
                    or package.result["context"]["context_id"] != decision.context_id
                ):
                    raise AttentionError("ATTENTION_REVIEW_STALE")
                try:
                    self.review.current_context(actor, task.id, decision.context_id)
                    self.sync.evidence._ack(task)
                    if any(
                        o.task_run_id == task.id and o.status == "PENDING"
                        for kind in ("task_merge", "task_approve", "task_request_changes")
                        for o in self.store.get_operations(task.epic_run_id, kind=kind)
                    ):
                        raise ValueError
                except Exception:
                    raise AttentionError("ATTENTION_REVIEW_UNVERIFIED") from None
                op = self._create(
                    actor,
                    task,
                    package,
                    BlockerDetails(
                        **decision.model_dump(
                            include={
                                "reason",
                                "input_required",
                                "responsible_role",
                            }
                        )
                    ),
                    review=True,
                    decision=decision,
                )
        return await self._perform(actor, op)

    async def _mirror(self, actor, task, op, field):
        try:
            result = await self.sync.sync_task(actor, task.id)
        except TeamPlayerError as error:
            result = {"status": "PENDING", "reason": str(error)}
        op = self._save(op, **{field: result})
        return op, result["status"] in {"SYNCED", "EXISTING"}

    def _evidence(self, task):
        try:
            self.sync.evidence.task(task)
        except Exception:
            raise AttentionError("ATTENTION_SOURCE_UNVERIFIED") from None

    async def _perform(self, actor, op):
        task = self._scope(actor, op.task_run_id)
        op = self._saved(actor, task, self._block(task))
        if task.internal_status not in {TaskState.BLOCKED, TaskState.PARKED}:
            raise AttentionError("ATTENTION_PHASE_CHANGED")
        self._evidence(task)
        if task.internal_status == TaskState.BLOCKED:
            op, _ = await self._mirror(actor, task, op, "sync_before_park")
        task = self._scope(actor, task.id)
        op = self._saved(actor, task, self._block(task))
        if task.internal_status not in {TaskState.BLOCKED, TaskState.PARKED}:
            raise AttentionError("ATTENTION_PHASE_CHANGED")
        self._evidence(task)
        stop = self.store.get_operation(task.project_id, "stop_runtime", op.result["stop_key"])
        try:
            if stop is None or stop.status != "SUCCEEDED":
                stop = self.lifecycle.stop_task(
                    actor,
                    task.id,
                    key=op.result["stop_key"],
                    reason=BlockerDetails.model_validate(op.result["details"]).message(),
                )
            task = self._scope(actor, task.id)
            if (
                task.internal_status != TaskState.PARKED
                or task.worker_slot is not None
                or stop.status != "SUCCEEDED"
                or stop.result.get("stage") != "STOPPED"
                or stop.result.get("inactive") is not True
                or stop.task_run_id != task.id
                or stop.epic_run_id != task.epic_run_id
                or stop.result.get("reason")
                != BlockerDetails.model_validate(op.result["details"]).message()
                or self.store.latest_event(task.project_id, task.epic_run_id, task.id).id
                != "runtime-park:" + stop.id
                or stop.result.get("generation") != op.result["subject"]["generation"]
                or not self.lifecycle.confirm_task_inactive(actor, task.id)
            ):
                raise LifecycleError("ATTENTION_STOP_UNVERIFIED")
        except LifecycleError as error:
            stop = self.store.get_operation(task.project_id, "stop_runtime", op.result["stop_key"])
            op = self._save(
                op, stage="PARK_PENDING", error=str(error), stop_id=stop.id if stop else None
            )
            return self._result(op)
        op = self._save(op, stage="PARKED", stop_id=stop.id)
        op, synced = await self._mirror(actor, task, op, "sync_after_park")
        op = self._save(
            op,
            stage="PARKED_AND_SYNCED" if synced else "SYNC_PENDING",
            error=None
            if synced
            else op.result["sync_after_park"].get("reason", "ATTENTION_SYNC_PENDING"),
        )
        return self._result(op)

    @staticmethod
    def _result(op):
        return {
            "task_run_id": op.task_run_id,
            "blocker_id": op.id,
            "stage": op.result["stage"],
            "reason": op.error_code,
            "stop_id": op.result.get("stop_id"),
        }
