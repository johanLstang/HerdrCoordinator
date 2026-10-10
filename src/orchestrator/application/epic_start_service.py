"""Coordinator startup of one operator-bound Integration session, without task work."""

import fcntl
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.runtime_lifecycle_service import RuntimeLifecycleService
from orchestrator.application.state_service import StateService
from orchestrator.application.worktree_service import slug
from orchestrator.domain.integration_contracts import EpicIntegrationSpec
from orchestrator.domain.models import EpicRun, Operation, utc_now
from orchestrator.domain.policy import Role
from orchestrator.domain.states import EpicState
from orchestrator.domain.worker_contracts import ContractError, canonical_json, object_from_json


class EpicStartError(RuntimeError):
    """Fixed codes only; preserve original resources and pending intents."""


def load_policy():
    resource = files("orchestrator").joinpath("prompts/integration-v1.md")
    try:
        return (
            resource.read_text(encoding="utf-8")
            if resource.is_file()
            else (Path(__file__).resolve().parents[3] / "prompts/integration-v1.md").read_text()
        )
    except OSError:
        raise EpicStartError("INTEGRATION_POLICY_UNAVAILABLE") from None


def intent_hash(result):
    return digest(canonical_json({
        k: result[k] for k in ("actor", "integration_principal", "spec", "configuration", "subject")
    }))


class EpicStartService:
    KIND = "epic_start"

    def __init__(
        self, settings, store, herdr, spec, integration_principal, codex=None,
        *, processes=None, sync=None, external_epic_id=None,
    ):
        self.spec = EpicIntegrationSpec.model_validate(spec)
        if (
            integration_principal.role != Role.INTEGRATION
            or integration_principal.project_id != self.spec.project_id
            or integration_principal.epic_run_id is None
            or integration_principal.task_run_id is not None
            or (sync is None) != (external_epic_id is None)
            or sync is not None and (sync.store is not store or sync.settings != settings)
        ):
            raise EpicStartError("EPIC_START_CONFIGURATION_INVALID")
        self.settings, self.store, self.herdr = settings, store, herdr
        # This object comes from F04/operator configuration, never a tool/agent argument.
        self.principal = integration_principal
        self.codex = codex or CodexAdapter()
        self.lifecycle = RuntimeLifecycleService(
            settings, store, herdr, codex=self.codex, processes=processes
        )
        self.runtime = self.lifecycle.start
        self.worktrees = self.runtime.worktrees
        self.git = self.worktrees.git
        self.policy = load_policy()
        self.sync, self.external_epic_id = sync, external_epic_id
        self.configuration = dict(
            settings=settings.model_dump(mode="json"),
            server_session=herdr.server_session,
            sandbox=herdr.sandbox,
            policy_hash=digest(self.policy),
            external_epic_id=external_epic_id,
        )
        if getattr(herdr, "mcp_fingerprint", None) is not None:
            self.configuration["mcp_configuration"] = herdr.mcp_fingerprint

    def _scope(self, actor, project_id, epic_id, run_id):
        if (
            actor is None or actor.role != Role.COORDINATOR
            or actor.project_id != project_id or project_id != self.spec.project_id
            or epic_id != self.spec.epic_id or run_id != self.principal.epic_run_id
            or actor.epic_run_id not in {None, run_id} or actor.task_run_id is not None
        ):
            raise EpicStartError("EPIC_START_SCOPE_DENIED")

    @contextmanager
    def _lock(self):
        key = digest(canonical_json([self.spec.project_id, slug(self.spec.epic_id)]))
        try:
            fd = os.open(
                str(self.settings.sqlite_path) + ".epic-start-" + key + ".lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
            )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                raise EpicStartError("EPIC_START_BUSY") from None
            except OSError:
                os.close(fd)
                raise
        except OSError:
            raise EpicStartError("EPIC_START_LOCK_UNAVAILABLE") from None
        try:
            yield
        finally:
            os.close(fd)

    def _journal(self, actor):
        run_id = self.principal.epic_run_id
        with self.store.transaction():
            op = self.store.get_operation(self.spec.project_id, self.KIND, run_id)
            if op is not None:
                self._verify(actor, op)
                return op
            if any(
                isinstance(run, EpicRun) and run.project_id == self.spec.project_id
                and slug(run.epic_id) == slug(self.spec.epic_id) and run.id != run_id
                for run in self.store.get_runs()
            ):
                raise EpicStartError("EPIC_START_ALREADY_OWNED")
            prior = self.store.get_epic(run_id)
            if prior is not None and (
                prior.status != EpicState.PLANNED or any(
                    getattr(prior, field) is not None for field in (
                        "integration_agent_id", "codex_session_id", "herdr_server_session",
                        "herdr_workspace_id", "herdr_tab_id", "herdr_pane_id", "herdr_terminal_id",
                    )
                ) or self.store.get_tasks(run_id)
                or self.store.get_operation(self.spec.project_id, "start_runtime", run_id)
            ):
                raise EpicStartError("EPIC_START_UNOWNED_RUNTIME")
            # F05 preparation and our configuration/owner commit together, before Git effects.
            epic = self.worktrees.create_epic_worktree(
                actor, epic_id=self.spec.epic_id, run_id=run_id, prepare_only=True
            )
            result = dict(
                actor=actor.model_dump(mode="json"),
                integration_principal=self.principal.model_dump(mode="json"),
                spec=self.spec.model_dump(mode="json"), configuration=self.configuration,
                subject={k: getattr(epic, k) for k in (
                    "id", "project_id", "epic_id", "branch", "worktree_path", "base_commit"
                )},
                stage="PREPARING",
            )
            op = Operation(
                project_id=epic.project_id, epic_run_id=epic.id, kind=self.KIND,
                idempotency_key=epic.id, result=result | {"intent_hash": intent_hash(result)},
            )
            self.store.add_operation(op)
            return op

    def _verify(self, actor, op):
        epic = self.store.get_epic(op.epic_run_id)
        data = op.result
        if (
            epic is None or op.task_run_id is not None
            or op.epic_run_id != self.principal.epic_run_id
            or op.project_id != self.spec.project_id or op.idempotency_key != epic.id
            or data["intent_hash"] != intent_hash(data)
            or data["actor"] != actor.model_dump(mode="json")
            or data["integration_principal"] != self.principal.model_dump(mode="json")
            or data["spec"] != self.spec.model_dump(mode="json")
            or data["configuration"] != self.configuration
            or any(getattr(epic, k) != v for k, v in data["subject"].items())
            or epic.completed_at is not None
        ):
            raise EpicStartError("EPIC_START_INTENT_CHANGED")
        return epic

    def _save(self, op, *, stage=None, status=None, error=None, **updates):
        with self.store.transaction():
            current = self.store.get_operation(op.project_id, self.KIND, op.idempotency_key)
            if (
                current is None or current.id != op.id
                or current.result["intent_hash"] != intent_hash(current.result)
            ):
                raise EpicStartError("EPIC_START_INTENT_CHANGED")
            saved = current.model_copy(update={
                "result": current.result | updates | ({"stage": stage} if stage else {}),
                "updated_at": utc_now(), "error_code": error,
                "status": status or current.status,
            })
            self.store.update_operation(saved)
            return saved

    def _native(self, actor, op):
        epic = self._verify(actor, op)
        run, start = self.lifecycle._validate(actor, epic.id, False)
        facts = self.lifecycle._live(run, start)
        if facts is None:
            raise EpicStartError("INTEGRATION_RUNTIME_MISSING")
        return run, start, facts

    @staticmethod
    def outcome(op, epic):
        return dict(
            operation_id=op.id, epic_run_id=epic.id, project_id=epic.project_id,
            epic_id=epic.epic_id, branch=epic.branch, worktree_path=epic.worktree_path,
            session_id=epic.codex_session_id, integration_agent_id=epic.integration_agent_id,
            stage=op.result["stage"], reason=op.error_code,
        )

    async def start(self, actor, project_id, epic_id, run_id):
        self._scope(actor, project_id, epic_id, run_id)
        with self._lock():
            op = self._journal(actor)
            try:
                return await self._drive(actor, op)
            except EpicStartError as error:
                self._save(op, error=str(error))
                raise
            except Exception:
                self._save(op, error="EPIC_START_UNVERIFIED")
                raise EpicStartError("EPIC_START_UNVERIFIED") from None

    async def _drive(self, actor, op):
        epic = self._verify(actor, op)
        if op.result["stage"] == "PREPARING":
            epic = self.worktrees.create_epic_worktree(
                actor, epic_id=self.spec.epic_id, run_id=epic.id
            )
            with self.store.transaction():
                event_id = "epic-start:" + op.id
                epic = StateService(self.store).transition_epic(
                    epic.id, EpicState.ACTIVE, expected=EpicState.PLANNED,
                    event_id=event_id, actor=actor,
                )
                op = self._save(op, stage="START_PENDING", active_event_id=event_id)
        self.worktrees.verify_owned_worktree(epic)
        if op.result["stage"] == "START_PENDING":
            if self.sync is not None:
                await self.sync.bind_epic(actor, epic.id, self.external_epic_id)
                result = await self.sync.sync_epic(actor, epic.id)
                op = self._save(op, initial_board_sync=result)
                if result["status"] not in {"SYNCED", "EXISTING"}:
                    return self.outcome(self._save(op, error="EPIC_START_SYNC_PENDING"), epic)
            epic = self.runtime.start_epic(actor, epic.id)
            op = self._save(op, stage="RUNTIME_READY")
        if op.result["stage"] == "ACK_TIMEOUT":
            raise EpicStartError("INTEGRATION_ACK_TIMEOUT")
        if op.result["stage"] == "RUNTIME_READY":
            op = self._dispatch(actor, op)
        return self._observe(actor, op)

    def _dispatch(self, actor, op):
        epic, start, facts = self._native(actor, op)
        if not facts["ready"]:
            raise EpicStartError("INTEGRATION_RUNTIME_NOT_IDLE")
        head = self.git.inspect(Path(epic.worktree_path), epic.branch, clean=True)
        if self.git.in_progress(Path(epic.worktree_path)) or self.git.unsafe_index_paths(
            Path(epic.worktree_path)
        ):
            raise EpicStartError("INTEGRATION_GIT_UNVERIFIED")
        context = dict(
            project_id=epic.project_id, epic_id=epic.epic_id, epic_run_id=epic.id,
            branch=epic.branch, worktree_path=epic.worktree_path, base_commit=epic.base_commit,
            current_commit=head, integration_principal=self.principal.model_dump(mode="json"),
        )
        context_hash = digest(canonical_json(context | {"spec": op.result["spec"]}))
        correlation = str(uuid4())
        expected = dict(
            version=1, status="INTEGRATION_READY", project_id=epic.project_id,
            epic_id=epic.epic_id, epic_run_id=epic.id, correlation_id=correlation,
            assignment_id=op.id, context_hash=context_hash,
        )
        prompt = canonical_json(dict(
            type="HERDR_EPIC_ASSIGNMENT", version=1, policy_version=1,
            policy=self.policy, context=context, spec=op.result["spec"], confirmation=expected,
        ))
        if len(prompt.encode()) > 65536:
            raise EpicStartError("INTEGRATION_PROMPT_TOO_LARGE")
        sid = facts["session_id"]
        baseline = self.codex.read_thread(sid, epic.worktree_path) if sid else None
        if baseline is not None and (
            baseline["id"] != sid or baseline["sessionId"] != sid
            or Path(baseline["cwd"]) != Path(epic.worktree_path)
        ):
            raise EpicStartError("INTEGRATION_SESSION_CHANGED")
        op = self._save(
            op, stage="DISPATCH_REQUESTED", prompt=prompt, prompt_hash=digest(prompt),
            context=context, context_hash=context_hash, expected_ack=expected,
            baseline_session_id=sid, baseline_turns=[t["id"] for t in baseline["turns"]]
            if baseline else [], start_operation_id=start.id,
            deadline=(utc_now() + timedelta(seconds=45)).isoformat(),
        )
        epic, current_start, facts = self._native(actor, op)
        if current_start.id != start.id or not facts["ready"] or self.git.head(epic.branch) != head:
            raise EpicStartError("INTEGRATION_DISPATCH_CHANGED")
        try:
            self.herdr.prompt(epic.integration_agent_id, prompt, timeout_ms=45000)
            transport = "RETURNED"
        except HerdrError:
            transport = "UNKNOWN"
        return self._save(op, transport=transport)

    def _assignment(self, epic, op, start):
        data = op.result
        prompt = object_from_json(data["prompt"])
        context = data["context"]
        expected = dict(
            version=1, status="INTEGRATION_READY", project_id=epic.project_id,
            epic_id=epic.epic_id, epic_run_id=epic.id,
            correlation_id=data["expected_ack"]["correlation_id"],
            assignment_id=op.id, context_hash=data["context_hash"],
        )
        if (
            data["prompt_hash"] != digest(data["prompt"])
            or data["context_hash"] != digest(canonical_json(context | {"spec": data["spec"]}))
            or data["expected_ack"] != expected or data["start_operation_id"] != start.id
            or prompt != dict(
                type="HERDR_EPIC_ASSIGNMENT", version=1, policy_version=1,
                policy=self.policy, context=context, spec=data["spec"], confirmation=expected,
            )
            or any(context[k] != getattr(epic, k) for k in (
                "project_id", "epic_id", "branch", "worktree_path", "base_commit"
            ))
            or context["epic_run_id"] != epic.id
            or context["integration_principal"] != self.principal.model_dump(mode="json")
            or data["baseline_session_id"] not in {None, epic.codex_session_id}
        ):
            raise EpicStartError("INTEGRATION_ASSIGNMENT_CHANGED")
        head = self.git.head(epic.branch)
        if op.status != "SUCCEEDED" and head != context["current_commit"]:
            raise EpicStartError("INTEGRATION_EPIC_HEAD_CHANGED")
        if op.status == "SUCCEEDED" and not self.git.contains_commit(
            epic.branch, context["current_commit"]
        ):
            raise EpicStartError("INTEGRATION_EPIC_BASE_LOST")
        return expected

    @staticmethod
    def _match(op, thread, expected, status):
        allowed = {"completed", "inProgress"} | ({"interrupted"} if status == "working" else set())
        for turn in thread["turns"]:
            if turn["id"] in op.result["baseline_turns"] or turn["status"] not in allowed:
                continue
            delivered = any(
                item["type"] == "userMessage" and any(
                    part.get("type") == "text"
                    and digest(part.get("text", "")) == op.result["prompt_hash"]
                    for part in item.get("content", [])
                ) for item in turn["items"]
            )
            if not delivered:
                continue
            for item in turn["items"]:
                if item["type"] != "agentMessage":
                    continue
                try:
                    ack = object_from_json(item["text"])
                except ContractError:
                    continue
                # JSON equality alone equates bool/int; require the exact v1 integer too.
                if type(ack.get("version")) is int and ack == expected:
                    return dict(
                        turn_id=turn["id"], item_id=item["id"],
                        message_hash=digest(item["text"]),
                    )
        return None

    def _observe(self, actor, op):
        epic, start, facts = self._native(actor, op)
        sid = facts["session_id"]
        if sid and epic.codex_session_id is None:
            with self.store.transaction():
                current = self._verify(actor, op)
                epic = current.model_copy(update={"codex_session_id": sid})
                self.store.update_runtime_metadata(epic)
        expected = self._assignment(epic, op, start)
        if sid is None:
            proof = None
        else:
            thread = self.codex.read_thread(sid, epic.worktree_path)
            if (
                thread["id"] != sid or thread["sessionId"] != sid
                or Path(thread["cwd"]) != Path(epic.worktree_path)
            ):
                raise EpicStartError("INTEGRATION_SESSION_CHANGED")
            proof = self._match(op, thread, expected, facts["status"])
        if op.status == "SUCCEEDED":
            if proof is None or op.result["ack"] != proof | {"session_id": sid}:
                raise EpicStartError("INTEGRATION_REGISTERED_ACK_CHANGED")
            return self.outcome(op, epic)
        if utc_now() > datetime.fromisoformat(op.result["deadline"]):
            self._save(op, stage="ACK_TIMEOUT", error="INTEGRATION_ACK_TIMEOUT")
            raise EpicStartError("INTEGRATION_ACK_TIMEOUT")
        if proof is None:
            return self.outcome(op, epic)
        with self.store.transaction():
            self._verify(actor, op)
            op = self._save(op, stage="REGISTERED", status="SUCCEEDED", ack=proof | {
                "session_id": sid,
            })
        return self.outcome(op, epic)

    def connection_principal(self, actor):
        """Trusted F04 launcher factory: validate registration before enabling its connection."""
        if actor != self.principal:
            raise EpicStartError("INTEGRATION_CONNECTION_SCOPE_DENIED")
        op = self.store.get_operation(self.spec.project_id, self.KIND, actor.epic_run_id)
        if op is None or op.status != "SUCCEEDED" or op.result["stage"] != "REGISTERED":
            raise EpicStartError("INTEGRATION_CONNECTION_NOT_REGISTERED")
        coordinator = self._coordinator(op)
        self._observe(coordinator, op)
        return self.principal

    @staticmethod
    def _coordinator(op):
        # This is a replay of the private service's original registered owner, not agent input.
        from orchestrator.domain.policy import Actor

        return Actor.model_validate(op.result["actor"])
