"""A real project session: no placeholder epic, Worker slot, adoption or prompt replay."""

import fcntl
import os
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.git import GitAdapter
from orchestrator.adapters.herdr import HerdrError
from orchestrator.adapters.processes import ProcessObserver
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.domain.coordinator import CoordinatorRun, ProjectOperation
from orchestrator.domain.coordinator_contracts import CoordinatorSpec
from orchestrator.domain.models import utc_now
from orchestrator.domain.policy import Role
from orchestrator.domain.worker_contracts import ContractError, canonical_json, object_from_json


class CoordinatorError(RuntimeError):
    """Safe codes only; preserve actual identities and indeterminate effects."""


def load_policy():
    resource = files("orchestrator").joinpath("prompts/coordinator-v1.md")
    fallback = Path(__file__).resolve().parents[3] / "prompts/coordinator-v1.md"
    try:
        return (resource if resource.is_file() else fallback).read_text(encoding="utf-8")
    except OSError:
        raise CoordinatorError("COORDINATOR_POLICY_UNAVAILABLE") from None


class CoordinatorRuntimeService:
    KIND = "coordinator_start"

    def __init__(
        self, settings, store, herdr, spec, principal, run_id, *, codex=None, processes=None
    ):
        self.spec = CoordinatorSpec.model_validate(deepcopy(spec))
        if principal.role != Role.COORDINATOR or principal.project_id != self.spec.project_id or (
            principal.epic_run_id is not None or principal.task_run_id is not None
        ):
            raise CoordinatorError("COORDINATOR_CONFIGURATION_INVALID")
        self.settings, self.store, self.herdr = settings, store, herdr
        self.principal, self.run_id = principal, run_id
        self.codex, self.processes = codex or CodexAdapter(), processes or ProcessObserver()
        self.git = GitAdapter(settings.repository)
        self.policy = load_policy()
        self.configuration_hash = self._configuration_hash()

    def _configuration_hash(self):
        return digest(canonical_json(dict(
            settings=self.settings.model_dump(mode="json"), spec=self.spec.model_dump(mode="json"),
            principal=self.principal.model_dump(mode="json"), policy_hash=digest(self.policy),
            server_session=self.herdr.server_session, sandbox=self.herdr.sandbox,
            mcp_configuration=getattr(self.herdr, "mcp_fingerprint", None),
        )))

    @contextmanager
    def lock(self):
        try:
            fd = os.open(
                str(self.settings.sqlite_path) + ".coordinator-" + digest(self.spec.project_id)
                + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
            )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(fd)
                raise
        except BlockingIOError:
            raise CoordinatorError("COORDINATOR_BUSY") from None
        except OSError:
            raise CoordinatorError("COORDINATOR_LOCK_UNAVAILABLE") from None
        try:
            yield
        finally:
            os.close(fd)

    def main(self):
        head = self.git.inspect(self.settings.repository, "main", clean=True)
        if self.git.in_progress(self.settings.repository) or self.git.unsafe_index_paths(
            self.settings.repository
        ):
            raise CoordinatorError("COORDINATOR_MAIN_GIT_UNVERIFIED")
        return head

    def scope(self, actor):
        if actor != self.principal:
            raise CoordinatorError("COORDINATOR_SCOPE_DENIED")
        if self._configuration_hash() != self.configuration_hash:
            raise CoordinatorError("COORDINATOR_CONFIGURATION_CHANGED")

    def _save(self, op, **result):
        with self.store.transaction():
            current = self.store.get_project_operation(op.project_id, op.kind, op.idempotency_key)
            if current is None or current.id != op.id:
                raise CoordinatorError("COORDINATOR_INTENT_MISSING")
            saved = current.model_copy(update={
                "result": current.result | result, "updated_at": utc_now(),
            })
            self.store.update_project_operation(saved)
            return saved

    def _native(self, run, op):
        if run is None or run.completed_at is not None or (
            run.id != self.run_id or run.project_id != self.spec.project_id
            or run.actor_id != self.principal.actor_id
            or run.configuration_hash != self.configuration_hash
            or run.worktree_path != str(self.settings.repository)
            or run.herdr_server_session != self.herdr.server_session
            or run.coordinator_agent_id != op.result["name"]
        ):
            raise CoordinatorError("COORDINATOR_IDENTITY_MISMATCH")
        binding = op.result.get("binding")
        if not binding or any(getattr(run, "herdr_" + k) != v for k, v in binding.items()):
            raise CoordinatorError("COORDINATOR_BINDING_MISMATCH")
        agent = self.herdr.get_agent(run.coordinator_agent_id)
        if agent is None:
            raise CoordinatorError("COORDINATOR_PROCESS_MISSING")
        facts = self.herdr.verify_agent(agent, binding, run.worktree_path, run.coordinator_agent_id)
        if op.result.get("processes") != facts["processes"]:
            raise CoordinatorError("COORDINATOR_PROCESS_CHANGED")
        if run.codex_session_id is not None and facts["session_id"] != run.codex_session_id:
            raise CoordinatorError("COORDINATOR_SESSION_CHANGED")
        return facts

    def start(self, actor):
        self.scope(actor)
        with self.lock():
            self.main()
            with self.store.transaction():
                run = self.store.get_coordinator(self.run_id)
                op = self.store.get_project_operation(self.spec.project_id, self.KIND, self.run_id)
                if run is None and op is None:
                    run = CoordinatorRun(
                        id=self.run_id, project_id=self.spec.project_id, actor_id=actor.actor_id,
                        worktree_path=str(self.settings.repository), initial_commit=self.main(),
                        configuration_hash=self.configuration_hash,
                        coordinator_agent_id="hc-c-" + uuid4().hex[:24],
                        herdr_server_session=self.herdr.server_session,
                    )
                    op = ProjectOperation(
                        project_id=run.project_id, coordinator_run_id=run.id, kind=self.KIND,
                        idempotency_key=run.id,
                        result=dict(stage="INTENT", name=run.coordinator_agent_id),
                    )
                    self.store.add_coordinator(run)
                    self.store.add_project_operation(op)
                if run is None or op is None or run.completed_at is not None or (
                    run.configuration_hash != self.configuration_hash
                    or run.actor_id != actor.actor_id
                ):
                    raise CoordinatorError("COORDINATOR_START_INTENT_MISMATCH")
            if op.result["stage"] != "REGISTERED" and self.main() != run.initial_commit:
                raise CoordinatorError("COORDINATOR_START_MAIN_CHANGED")
            if op.result["stage"] == "INTENT":
                op = self._save(op, stage="WORKSPACE_PENDING")
                binding = self.herdr.create_workspace(run.worktree_path, "hc-c-" + op.id)
                with self.store.transaction():
                    run = CoordinatorRun.model_validate(run.model_dump() | {
                        "herdr_" + k: v for k, v in binding.items()
                    })
                    self.store.update_coordinator(run)
                    op = self._save(op, stage="WORKSPACE_READY", binding=binding)
            if op.result["stage"] == "WORKSPACE_PENDING":
                raise CoordinatorError("COORDINATOR_WORKSPACE_OUTCOME_UNKNOWN")
            if op.result["stage"] == "WORKSPACE_READY":
                op = self._save(op, stage="AGENT_PENDING")
                self.herdr.start_agent(
                    run.coordinator_agent_id, run.herdr_pane_id, run.worktree_path
                )
            if op.result["stage"] == "AGENT_PENDING":
                agent = self.herdr.get_agent(run.coordinator_agent_id)
                if agent is None:
                    raise CoordinatorError("COORDINATOR_AGENT_OUTCOME_UNKNOWN")
                facts = self.herdr.verify_agent(
                    agent, op.result["binding"], run.worktree_path, run.coordinator_agent_id
                )
                if not facts["ready"]:
                    raise CoordinatorError("COORDINATOR_INPUT_NOT_READY")
                op = self._save(op, stage="RUNTIME_READY", processes=facts["processes"])
            if op.result["stage"] == "RUNTIME_READY":
                self._native(run, op)
                confirmation = dict(
                    version=1, status="COORDINATOR_READY", project_id=run.project_id,
                    coordinator_run_id=run.id, correlation_id=op.id,
                    configuration_hash=run.configuration_hash, initial_commit=run.initial_commit,
                )
                prompt = canonical_json(dict(
                    version=1, policy=self.policy,
                    context=dict(project_id=run.project_id, coordinator_run_id=run.id,
                                 branch="main", worktree_path=run.worktree_path,
                                 initial_commit=run.initial_commit),
                    plan=dict(title=self.spec.title, sources=self.spec.sources, epics=[
                        dict(epic_id=e.spec.epic_id, title=e.spec.title, priority=e.priority,
                             dependencies=e.spec.dependencies) for e in self.spec.epics
                    ]), confirmation=confirmation,
                ))
                if len(prompt.encode()) > 65536:
                    raise CoordinatorError("COORDINATOR_ASSIGNMENT_TOO_LARGE")
                op = self._save(
                    op, stage="DISPATCH_PENDING", prompt_hash=digest(prompt),
                    confirmation=confirmation,
                    deadline=(utc_now() + timedelta(seconds=45)).isoformat(),
                )
                try:
                    self.herdr.prompt(run.coordinator_agent_id, prompt, timeout_ms=45000)
                except HerdrError:
                    # Persisted intent is observed, never blindly dispatched twice.
                    pass
            return self._observe(run, op)

    def _observe(self, run, op):
        facts = self._native(run, op)
        if op.result["stage"] not in {"DISPATCH_PENDING", "REGISTERED"}:
            raise CoordinatorError("COORDINATOR_NOT_REGISTERED")
        sid = facts["session_id"]
        if sid is None:
            raise CoordinatorError("COORDINATOR_ACK_PENDING")
        if run.codex_session_id is None:
            with self.store.transaction():
                run = CoordinatorRun.model_validate(run.model_dump() | dict(codex_session_id=sid))
                self.store.update_coordinator(run)
        expected = dict(
            version=1, status="COORDINATOR_READY", project_id=run.project_id,
            coordinator_run_id=run.id, correlation_id=op.id,
            configuration_hash=run.configuration_hash, initial_commit=run.initial_commit,
        )
        if op.result["confirmation"] != expected or type(
            op.result["confirmation"].get("version")
        ) is not int:
            raise CoordinatorError("COORDINATOR_ASSIGNMENT_CHANGED")
        thread = self.codex.read_thread(sid, run.worktree_path)
        if thread["id"] != sid or thread["sessionId"] != sid or thread["cwd"] != run.worktree_path:
            raise CoordinatorError("COORDINATOR_THREAD_MISMATCH")
        matches = []
        for turn in thread["turns"]:
            if not any(
                item.get("type") == "userMessage" and any(
                    part.get("type") == "text" and digest(part.get("text", ""))
                    == op.result["prompt_hash"] for part in item.get("content", [])
                ) for item in turn["items"]
            ):
                continue
            for item in turn["items"]:
                if item.get("type") != "agentMessage":
                    continue
                try:
                    ack = object_from_json(item["text"])
                    if type(ack.get("version")) is not int or ack != expected:
                        continue
                except ContractError:
                    continue
                observed = item.get("nativeObservedAt")
                if observed is None:
                    continue
                try:
                    observed = datetime.fromisoformat(observed)
                    if observed.tzinfo is None or observed > datetime.fromisoformat(
                        op.result["deadline"]
                    ):
                        continue
                except (ValueError, TypeError):
                    continue
                matches.append(dict(session_id=sid, turn_id=turn["id"], item_id=item["id"],
                                    message_hash=digest(item["text"])))
        if len(matches) != 1:
            if utc_now() > datetime.fromisoformat(op.result["deadline"]):
                raise CoordinatorError("COORDINATOR_ACK_TIMEOUT")
            raise CoordinatorError("COORDINATOR_ACK_UNVERIFIED")
        if op.result["stage"] == "REGISTERED":
            if run.status != "ACTIVE" or op.result.get("ack") != matches[0]:
                raise CoordinatorError("COORDINATOR_REGISTRATION_CHANGED")
            return run
        with self.store.transaction():
            registered = CoordinatorRun.model_validate(run.model_dump() | dict(
                status="ACTIVE", codex_session_id=sid,
            ))
            self.store.update_coordinator(registered)
            op = self._save(op, stage="REGISTERED", ack=matches[0])
            self.store.update_project_operation(op.model_copy(update={"status": "SUCCEEDED"}))
        return registered

    def connection_principal(self, actor):
        self.scope(actor)
        run = self.store.get_coordinator(self.run_id)
        op = self.store.get_project_operation(self.spec.project_id, self.KIND, self.run_id)
        if run is None or op is None or op.status != "SUCCEEDED":
            raise CoordinatorError("COORDINATOR_CONNECTION_NOT_REGISTERED")
        self.main()
        self._observe(run, op)
        return self.principal

    def _stopped(self, run, op):
        info = self.herdr.process_info(run.herdr_pane_id)
        foreground = info.get("foreground_processes", [])
        return (
            self.herdr.get_agent(run.coordinator_agent_id) is None
            and info.get("pane_id") == run.herdr_pane_id
            and len(foreground) == 1 and foreground[0]["pid"] == info["shell_pid"]
            and self.processes.inactive(op.result["process_proof"])
        )

    def stop(self, actor, key):
        """Operator lifecycle only, never exposed as a Coordinator mutation tool."""
        self.scope(actor)
        with self.lock():
            run = self.store.get_coordinator(self.run_id)
            start = self.store.get_project_operation(self.spec.project_id, self.KIND, self.run_id)
            op = self.store.get_project_operation(self.spec.project_id, "coordinator_stop", key)
            if op is not None and op.coordinator_run_id != self.run_id:
                raise CoordinatorError("COORDINATOR_STOP_KEY_CONFLICT")
            if op is None:
                pending = [o for o in self.store.project_operations(
                    self.run_id, kind="coordinator_stop"
                ) if o.status == "PENDING"]
                if pending:
                    raise CoordinatorError("COORDINATOR_STOP_ALREADY_PENDING")
                facts = self._native(run, start)
                info = self.herdr.process_info(run.herdr_pane_id)
                proof = self.processes.capture(facts["processes"], info["shell_pid"])
                op = ProjectOperation(
                    project_id=self.spec.project_id, coordinator_run_id=self.run_id,
                    kind="coordinator_stop", idempotency_key=key,
                    result=dict(stage="INTERRUPT_PENDING", process_proof=proof,
                                start_operation_id=start.id),
                )
                self.store.add_project_operation(op)
                self.herdr.interrupt(run.coordinator_agent_id)
            if op.result["stage"] == "INTERRUPT_PENDING":
                facts = self._native(run, start)
                if not facts["ready"]:
                    raise CoordinatorError("COORDINATOR_STOP_NOT_IDLE")
                op = self._save(op, stage="EXIT_PENDING")
                self.herdr.exit_agent(run.coordinator_agent_id)
            if not self._stopped(run, op):
                raise CoordinatorError("COORDINATOR_STOP_UNCONFIRMED")
            with self.store.transaction():
                if run.completed_at is None:
                    run = CoordinatorRun.model_validate(run.model_dump() | dict(
                        status="STOPPED", completed_at=utc_now(),
                    ))
                    self.store.update_coordinator(run)
                op = self._save(op, stage="STOPPED", inactive=True)
                self.store.update_project_operation(op.model_copy(update={"status": "SUCCEEDED"}))
            return run
