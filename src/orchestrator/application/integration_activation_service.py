"""Optional post-registration control delivery; the epic-start lock owns dispatch."""

from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.epic_start_service import EpicStartError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.domain.models import Operation, utc_now
from orchestrator.domain.states import EpicState
from orchestrator.domain.worker_contracts import canonical_json

ACTIVATION_POLICY = (
    "Your exact startup ACK is verified and this Integration connection is registered. "
    "Execute the complete existing epic task loop now, using only its registered services and "
    "the original assignment's order, dependencies, scope and criteria. Use task_schedule and "
    "fresh integration_overview. A PAUSED record is not replayed by another tick: inspect its "
    "actual reason and next_operations, and choose an existing guarded task_start/recovery "
    "action only when fresh checks permit it. Preserve original tasks, resources and sessions; "
    "never clear state or bypass a denied check. Keep up to two eligible Workers working. "
    "Read complete current review contexts, request concrete corrections to the same Worker, "
    "approve only actual current criteria and integrate through task_merge. Continue after "
    "each verified Done, waiting boundedly for active Workers, until all registered tasks are "
    "Done; then epic_complete and actual final handoff. Stay in this same Integration session "
    "for later Coordinator corrections. Do not implement task code, merge in shell, write "
    "TeamPlayer directly, claim success from transport, or invent runtime/test/review facts."
)


class IntegrationActivationService:
    KIND = "integration_activate"

    def __init__(self, parent):
        self.parent, self.store = parent, parent.store

    def _save(self, op, *, stage, **values):
        with self.store.transaction():
            current = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
            if current is None or current.id != op.id:
                raise EpicStartError("INTEGRATION_ACTIVATION_INTENT_CHANGED")
            if any(current.result[key] != op.result[key] for key in (
                "binding", "message", "message_hash", "baseline_turn_ids",
            )):
                raise EpicStartError("INTEGRATION_ACTIVATION_HISTORY_CHANGED")
            result = current.model_copy(update=dict(
                status="SUCCEEDED" if stage == "DELIVERED" else current.status,
                result=current.result | values | dict(stage=stage), updated_at=utc_now(),
            ))
            self.store.update_operation(result)
            return result

    def dispatch_under_start_lock(self, actor):
        """Internal to EpicStartService.start, never exposed as an agent tool."""
        p = self.parent
        run_id = p.principal.epic_run_id
        p._scope(actor, p.spec.project_id, p.spec.epic_id, run_id)
        if getattr(p, "activate_registered", False) is not True or (
            p.configuration.get("activation_policy_hash") != digest(ACTIVATION_POLICY)
        ):
            raise EpicStartError("INTEGRATION_ACTIVATION_NOT_CONFIGURED")
        original = self.store.get_operation(p.spec.project_id, p.KIND, run_id)
        if original is None or original.status != "SUCCEEDED" or (
            original.result.get("stage") != "REGISTERED"
        ):
            raise EpicStartError("INTEGRATION_ACTIVATION_REQUIRES_REGISTERED_ACK")
        p._verify(actor, original)
        p.connection_principal(p.principal)
        epic, start, facts = p._native(actor, original)
        if not epic.codex_session_id or facts["session_id"] != epic.codex_session_id:
            raise EpicStartError("INTEGRATION_ACTIVATION_SESSION_UNVERIFIED")
        binding = dict(
            project_id=epic.project_id, epic_id=epic.epic_id, epic_run_id=epic.id,
            branch=epic.branch, worktree_path=epic.worktree_path,
            session_id=epic.codex_session_id, actor=actor.model_dump(mode="json"),
            epic_start_id=original.id, runtime_start_id=start.id,
            generation=start.result.get("generation", start.id),
            configuration_hash=digest(canonical_json(p.configuration)),
            spec_hash=digest(canonical_json(p.spec.model_dump(mode="json"))),
        )
        message = canonical_json(dict(type="HERDR_INTEGRATION_ACTIVATE", version=1,
                                      context=binding, instruction=ACTIVATION_POLICY))
        if len(message.encode()) > 32768:
            raise EpicStartError("INTEGRATION_ACTIVATION_MESSAGE_TOO_LARGE")
        op = self.store.get_operation(epic.project_id, self.KIND, epic.id)
        thread = p.codex.read_thread(epic.codex_session_id, epic.worktree_path)
        if op is None:
            if epic.status != EpicState.ACTIVE or self.store.get_tasks(epic.id):
                raise EpicStartError("INTEGRATION_ACTIVATION_WORK_ALREADY_STARTED")
            if not facts["ready"]:
                return dict(status="WAITING", stage="WAITING_FOR_IDLE", operation_id=None)
            op = Operation(project_id=epic.project_id, epic_run_id=epic.id, kind=self.KIND,
                           idempotency_key=epic.id, result=dict(
                               stage="PREPARED", binding=binding, message=message,
                               message_hash=digest(message),
                               baseline_turn_ids=[t["id"] for t in thread["turns"]],
                           ))
            self.store.add_operation(op)
        if op.epic_run_id != epic.id or op.task_run_id is not None or (
            op.result["binding"] != binding or op.result["message"] != message
            or op.result["message_hash"] != digest(message)
        ):
            raise EpicStartError("INTEGRATION_ACTIVATION_BINDING_CHANGED")
        if op.result["stage"] == "PREPARED":
            # No external dispatch was attempted in PREPARED. Once the request is
            # saved, every retry only observes, including a lost transport reply.
            if not p._native(actor, original)[2]["ready"]:
                return dict(status="WAITING", stage="PREPARED", operation_id=op.id)
            op = self._save(op, stage="DISPATCH_REQUESTED")
            try:
                p.herdr.prompt(epic.integration_agent_id, message, timeout_ms=45000)
            except HerdrError as error:
                op = self._save(op, stage="DISPATCH_REQUESTED", transport="UNKNOWN",
                                transport_error=error.code)
            else:
                op = self._save(op, stage="DISPATCH_REQUESTED", transport="RETURNED")
            thread = p.codex.read_thread(epic.codex_session_id, epic.worktree_path)
        # Recheck the actual runtime after the external transport effect.
        p.connection_principal(p.principal)
        current, current_start, current_facts = p._native(actor, original)
        if current.codex_session_id != binding["session_id"] or (
            current_facts["session_id"] != binding["session_id"]
            or current_start.id != binding["runtime_start_id"]
            or current_start.result.get("generation", current_start.id) != binding["generation"]
        ):
            raise EpicStartError("INTEGRATION_ACTIVATION_RUNTIME_CHANGED")
        matches = []
        for turn in thread["turns"]:
            if turn["id"] in op.result["baseline_turn_ids"] or turn["status"] not in {
                "completed", "inProgress", "interrupted",
            }:
                continue
            for item in turn["items"]:
                if item["type"] == "userMessage" and any(
                    part.get("type") == "text" and digest(part.get("text", ""))
                    == op.result["message_hash"] for part in item.get("content", [])
                ):
                    matches.append(dict(session_id=epic.codex_session_id, turn_id=turn["id"],
                                        item_id=item["id"], message_hash=op.result["message_hash"]))
        if len(matches) > 1:
            raise EpicStartError("INTEGRATION_ACTIVATION_DELIVERY_AMBIGUOUS")
        if op.status == "SUCCEEDED":
            if matches != [op.result["delivery"]]:
                raise EpicStartError("INTEGRATION_ACTIVATION_DELIVERY_CHANGED")
        elif matches:
            op = self._save(op, stage="DELIVERED", delivery=matches[0])
        return dict(status="DELIVERED" if op.status == "SUCCEEDED" else "WAITING",
                    stage=op.result["stage"], operation_id=op.id,
                    session_id=epic.codex_session_id)
