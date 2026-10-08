"""One durable assignment; only a correlated native agent message confirms WORKING."""

import hashlib
import json
from datetime import datetime, timedelta
from uuid import uuid4

from orchestrator.adapters.codex import CodexAdapter, CodexError
from orchestrator.adapters.git import GitError
from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.runtime_start_service import RuntimeStartError, RuntimeStartService
from orchestrator.application.state_service import StateError, StateService
from orchestrator.application.worktree_service import WorktreeError
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.policy import Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class AssignmentError(RuntimeError):
    """Safe error; the dispatch journal and reserved slot are retained."""


class RuntimeAssignmentService:
    KIND = "dispatch_assignment"

    def __init__(self, settings, store, herdr, codex=None):
        self.settings, self.store, self.herdr = settings, store, herdr
        self.codex = codex or CodexAdapter()
        self.start = RuntimeStartService(settings, store, herdr)

    def _validate(self, actor, run_id):
        run = self.store.get_task(run_id)
        if run is None:
            raise AssignmentError("RUN_NOT_FOUND")
        StateService.authorize_scope(actor, run)
        if actor.role != Role.INTEGRATION:
            raise AssignmentError("ASSIGNMENT_ROLE_DENIED")
        epic = self.store.get_epic(run.epic_run_id)
        if (
            epic is None
            or epic.status != EpicState.ACTIVE
            or epic.completed_at is not None
            or run.completed_at is not None
        ):
            raise AssignmentError("EPIC_NOT_ACTIVE")
        self.start.worktrees.verify_owned_worktree(run)
        self.start._slot(run)
        start = self.store.get_operation(run.project_id, self.start.KIND, run.id)
        if (
            start is None
            or start.status != "SUCCEEDED"
            or start.result.get("stage") != "READY"
            or start.epic_run_id != run.epic_run_id
            or start.task_run_id != run.id
            or start.result.get("cwd") != run.worktree_path
            or start.result.get("branch") != run.branch
            or start.result.get("server_session") != self.herdr.server_session
            or start.result.get("sandbox") != self.herdr.sandbox
            or run.herdr_server_session != self.herdr.server_session
            or start.result.get("name") != run.worker_agent_id
            or any(getattr(run, "herdr_" + k) != v for k, v in start.result["binding"].items())
        ):
            raise AssignmentError("START_BINDING_UNVERIFIED")
        return run, start

    def _runtime(self, run, start):
        binding = start.result["binding"]
        pane = self.herdr.pane(binding["pane_id"])
        if (
            any(pane.get(k) != v for k, v in binding.items())
            or pane.get("cwd") != run.worktree_path
        ):
            raise AssignmentError("RUNTIME_PANE_CHANGED")
        agent = self.herdr.get_agent(run.worker_agent_id)
        if agent is None:
            raise AssignmentError("RUNTIME_NOT_FOUND")
        facts = self.herdr.verify_agent(agent, binding, run.worktree_path, run.worker_agent_id)
        if facts["processes"] != start.result["processes"]:
            raise AssignmentError("RUNTIME_PROCESS_CHANGED")
        if run.codex_session_id not in {None, facts["session_id"]}:
            raise AssignmentError("CODEX_SESSION_CHANGED")
        return facts

    @staticmethod
    def _ack(run, correlation):
        return dict(
            status="WORKING",
            project_id=run.project_id,
            epic_run_id=run.epic_run_id,
            task_run_id=run.id,
            task_id=run.task_id,
            correlation_id=correlation,
        )

    @classmethod
    def _prompt(cls, run, correlation, instruction):
        return json.dumps(
            {
                "type": "HERDR_ASSIGNMENT",
                "version": 1,
                "assignment": cls._ack(run, correlation),
                "instruction": instruction,
                "start_confirmation": "Reply with exactly the assignment JSON object "
                "as your WORKING acknowledgement before carrying out the instruction.",
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def dispatch(self, actor, run_id, instruction: str, *, timeout_seconds: int = 45):
        if (
            not isinstance(instruction, str)
            or not instruction.strip()
            or len(instruction.encode()) > 32768
            or "\x00" in instruction
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 45
        ):
            raise AssignmentError("INVALID_ASSIGNMENT")
        try:
            with self.store.transaction():
                run, start = self._validate(actor, run_id)
                op = self.store.get_operation(run.project_id, self.KIND, run.id)
                if op is not None:
                    if (
                        op.result["instruction_hash"] != digest(instruction)
                        or op.result["timeout_seconds"] != timeout_seconds
                    ):
                        raise AssignmentError("ASSIGNMENT_INTENT_MISMATCH")
                    fresh = False
                else:
                    if run.internal_status != TaskState.STARTING:
                        raise AssignmentError("TASK_NOT_STARTING")
                    facts = self._runtime(run, start)
                    if not facts["ready"]:
                        raise AssignmentError("RUNTIME_NOT_IDLE")
                    sid = facts["session_id"]
                    baseline = self.codex.read_thread(sid, run.worktree_path) if sid else None
                    correlation = str(uuid4())
                    prompt = self._prompt(run, correlation, instruction)
                    op = Operation(
                        project_id=run.project_id,
                        epic_run_id=run.epic_run_id,
                        task_run_id=run.id,
                        kind=self.KIND,
                        idempotency_key=run.id,
                        result={
                            "stage": "DISPATCH_REQUESTED",
                            "correlation_id": correlation,
                            "instruction_hash": digest(instruction),
                            "prompt_hash": digest(prompt),
                            "timeout_seconds": timeout_seconds,
                            "deadline": (
                                utc_now() + timedelta(seconds=timeout_seconds)
                            ).isoformat(),
                            "start_operation_id": start.id,
                            "baseline_session_id": sid,
                            "baseline_turns": [t["id"] for t in baseline["turns"]] if sid else [],
                        },
                    )
                    self.store.add_operation(op)
                    fresh = True
            if fresh:
                # Intent is durable first. A retry only observes; it never resends.
                run, start = self._validate(actor, run_id)
                if run.internal_status != TaskState.STARTING:
                    raise AssignmentError("TASK_NOT_STARTING")
                if utc_now() > datetime.fromisoformat(op.result["deadline"]):
                    return self.observe(actor, run_id)
                if not self._runtime(run, start)["ready"]:
                    raise AssignmentError("RUNTIME_NOT_IDLE")
                try:
                    self.herdr.prompt(
                        run.worker_agent_id, prompt, timeout_ms=timeout_seconds * 1000
                    )
                    self._transport(run, op, None)
                except HerdrError as e:
                    self._transport(run, op, e.code)
            return self.observe(actor, run_id)
        except (
            HerdrError,
            CodexError,
            WorktreeError,
            GitError,
            StateError,
            RuntimeStartError,
            KeyError,
            TypeError,
            ValueError,
        ) as e:
            raise AssignmentError(
                e.code if isinstance(e, HerdrError) else "ASSIGNMENT_UNVERIFIED"
            ) from None

    def _transport(self, run, op, error):
        with self.store.transaction():
            current = self.store.get_operation(run.project_id, self.KIND, run.id)
            if current.id != op.id:
                raise AssignmentError("ASSIGNMENT_CHANGED")
            # An observer may already have confirmed the ACK while transport was waiting.
            self.store.update_operation(
                current.model_copy(
                    update={
                        "result": current.result
                        | {"transport": "UNKNOWN" if error else "RETURNED"},
                        "error_code": error if current.status == "PENDING" else current.error_code,
                        "updated_at": utc_now(),
                    }
                )
            )

    def _confirmed(self, run, op, start, facts=None):
        facts = facts or self._runtime(run, start)
        return {
            "status": "CONFIRMED",
            "event_id": op.result["event_id"],
            "ack": op.result["ack"],
            "runtime_status": facts["status"],
            "task": run.model_dump(mode="json"),
        }

    def observe(self, actor, run_id):
        try:
            run, start = self._validate(actor, run_id)
            op = self.store.get_operation(run.project_id, self.KIND, run.id)
            if op is None or op.result["start_operation_id"] != start.id:
                raise AssignmentError("ASSIGNMENT_NOT_FOUND")
            if op.status == "SUCCEEDED":
                return self._confirmed(run, op, start)
            if op.status == "TIMED_OUT":
                raise AssignmentError("ASSIGNMENT_ACK_TIMEOUT")
            if run.internal_status != TaskState.STARTING:
                raise AssignmentError("TASK_NOT_STARTING")
            facts = self._runtime(run, start)
            sid = facts["session_id"]
            if op.result["baseline_session_id"] not in {None, sid}:
                raise AssignmentError("CODEX_SESSION_CHANGED")
            thread = self.codex.read_thread(sid, run.worktree_path) if sid else None
            proof = self._match(run, op, thread) if thread else None
            with self.store.transaction():
                run, current_start = self._validate(actor, run_id)
                current = self.store.get_operation(run.project_id, self.KIND, run.id)
                if current.id != op.id or current_start.id != start.id:
                    raise AssignmentError("ASSIGNMENT_CHANGED")
                if current.status == "SUCCEEDED":
                    return self._confirmed(run, current, current_start, facts)
                if sid and run.codex_session_id is None:
                    run = run.model_copy(update={"codex_session_id": sid})
                    self.store.update_runtime_metadata(run)
                if current.status == "TIMED_OUT" or utc_now() > datetime.fromisoformat(
                    current.result["deadline"]
                ):
                    self.store.update_operation(
                        current.model_copy(
                            update={
                                "status": "TIMED_OUT",
                                "error_code": "ASSIGNMENT_ACK_TIMEOUT",
                                "updated_at": utc_now(),
                            }
                        )
                    )
                    timed_out = True
                elif proof:
                    eid = "assignment-ack:" + op.id
                    run = StateService(self.store).transition_task(
                        run.id,
                        TaskState.WORKING,
                        expected=TaskState.STARTING,
                        event_id=eid,
                        actor=actor,
                        facts=VerifiedFacts(
                            start_confirmed=True,
                            slot_reserved=True,
                            reason="Correlated native Codex agent message",
                        ),
                    )
                    ack = proof | {"session_id": sid, "correlation_id": op.result["correlation_id"]}
                    self.store.update_operation(
                        current.model_copy(
                            update={
                                "status": "SUCCEEDED",
                                "error_code": None,
                                "updated_at": utc_now(),
                                "result": current.result
                                | {"stage": "CONFIRMED", "ack": ack, "event_id": eid},
                            }
                        )
                    )
                    return {
                        "status": "CONFIRMED",
                        "event_id": eid,
                        "ack": ack,
                        "runtime_status": facts["status"],
                        "task": run.model_dump(mode="json"),
                    }
                else:
                    timed_out = False
            if timed_out:
                raise AssignmentError("ASSIGNMENT_ACK_TIMEOUT")
            if facts["status"] == "blocked":
                raise AssignmentError("RUNTIME_BLOCKED")
            return {
                "status": "WAITING",
                "runtime_status": facts["status"],
                "correlation_id": op.result["correlation_id"],
                "task": run.model_dump(mode="json"),
            }
        except (
            HerdrError,
            CodexError,
            WorktreeError,
            GitError,
            StateError,
            RuntimeStartError,
            KeyError,
            TypeError,
            ValueError,
        ) as e:
            raise AssignmentError(
                e.code if isinstance(e, HerdrError) else "ASSIGNMENT_UNVERIFIED"
            ) from None

    def _match(self, run, op, thread):
        expected = self._ack(run, op.result["correlation_id"])
        for turn in thread["turns"]:
            if turn["id"] in op.result["baseline_turns"] or turn["status"] not in {
                "completed",
                "inProgress",
            }:
                continue
            # Verify delivered user prompt in that same new turn; no screen scraping.
            delivered = any(
                item["type"] == "userMessage"
                and any(
                    part.get("type") == "text"
                    and digest(part.get("text", "")) == op.result["prompt_hash"]
                    for part in item.get("content", [])
                )
                for item in turn["items"]
            )
            if not delivered:
                continue
            for item in turn["items"]:
                if item["type"] != "agentMessage":
                    continue
                try:
                    ack = json.loads(item["text"])
                except (ValueError, TypeError):
                    continue
                if ack == expected:
                    return {
                        "turn_id": turn["id"],
                        "item_id": item["id"],
                        "message_hash": digest(item["text"]),
                    }
        return None
