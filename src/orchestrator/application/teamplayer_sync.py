"""Role-bound durable external sync. Runtime and Git success are never repeated here."""

import fcntl
import os
from contextlib import contextmanager

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.state_service import StateService
from orchestrator.application.teamplayer_evidence import TeamPlayerEvidence
from orchestrator.application.teamplayer_reader import TeamPlayerReader
from orchestrator.domain.models import ExternalReference, Operation, utc_now
from orchestrator.domain.policy import Role
from orchestrator.domain.teamplayer import BoardEpic, BoardTask, external_id
from orchestrator.domain.worker_contracts import canonical_json


class TeamPlayerSyncService:
    KIND = "teamplayer_sync"

    def __init__(
        self,
        settings,
        store,
        adapter,
        *,
        project_id,
        external_project_id,
        user_id,
        scopes=None,
        codex=None,
        processes=None,
    ):
        self.settings, self.store, self.adapter = settings, store, adapter
        self.project_id = project_id
        try:
            self.external_project_id, self.user_id = (
                external_id(external_project_id),
                external_id(user_id),
            )
        except Exception:
            raise TeamPlayerError("TEAMPLAYER_SYNC_CONFIG_INVALID") from None
        self.evidence = TeamPlayerEvidence(
            settings, store, scopes=scopes, codex=codex, processes=processes
        )

    def _run(self, actor, run_id, task):
        expected = Role.INTEGRATION if task else Role.COORDINATOR
        if actor.role != expected or actor.project_id != self.project_id:
            raise TeamPlayerError("TEAMPLAYER_SYNC_ROLE_DENIED")
        run = self.store.get_task(run_id) if task else self.store.get_epic(run_id)
        if run is None or run.project_id != self.project_id:
            raise TeamPlayerError("TEAMPLAYER_SYNC_SCOPE_DENIED")
        try:
            StateService.authorize_scope(actor, run)
        except Exception:
            raise TeamPlayerError("TEAMPLAYER_SYNC_SCOPE_DENIED") from None
        return run

    @contextmanager
    def _lock(self):
        path = str(self.settings.sqlite_path) + ".teamplayer.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                raise TeamPlayerError("TEAMPLAYER_SYNC_BUSY") from None
            except OSError:
                os.close(fd)
                raise
        except OSError:
            raise TeamPlayerError("TEAMPLAYER_SYNC_LOCK_UNAVAILABLE") from None
        try:
            yield
        finally:
            os.close(fd)

    async def _identity(self):
        me = await self.adapter.read("get_me", {})
        projects = await self.adapter.read("list_projects", {})
        matches = [p for p in projects if p.get("projectId") == self.external_project_id]
        if (
            me.get("userId") != self.user_id
            or len(matches) != 1
            or matches[0].get("access") != "Write"
        ):
            raise TeamPlayerError("TEAMPLAYER_SYNC_IDENTITY_OR_GRANT_DENIED")

    def _reference(self, run, task):
        epic_id = run.epic_run_id if task else run.id
        matches = [
            r
            for r in self.store.get_references(epic_id)
            if r.provider == "teamplayer"
            and r.kind == ("task" if task else "epic")
            and r.task_run_id == (run.id if task else None)
        ]
        if len(matches) != 1 or matches[0].project_id != self.project_id:
            raise TeamPlayerError("TEAMPLAYER_REFERENCE_UNVERIFIED")
        return external_id(matches[0].external_id)

    async def _external(self, run, task, identity):
        if task:
            value = await self.adapter.read(
                "get_task", {"projectId": self.external_project_id, "taskId": identity}
            )
            node = BoardTask.model_validate(value)
            epic = self.store.get_epic(run.epic_run_id)
            spec = self.evidence.spec(run)
            if (
                node.id != identity
                or node.project_id != self.external_project_id
                or node.epic_id != self._reference(epic, False)
                or node.execution_owner_kind != "User"
                or node.responsible_user_id != self.user_id
                or node.task_type == "Epic"
                or node.name != spec.name
                or node.acceptance_criteria != tuple(spec.acceptance_criteria)
            ):
                raise TeamPlayerError("TEAMPLAYER_EXTERNAL_TASK_SCOPE_CHANGED")
            dependencies = []
            for dependency in spec.dependencies:
                matches = [t for t in self.store.get_tasks(epic.id) if t.task_id == dependency]
                if len(matches) != 1:
                    raise TeamPlayerError("TEAMPLAYER_EXTERNAL_DEPENDENCY_UNBOUND")
                dependencies.append(self._reference(matches[0], True))
            if tuple(dependencies) != node.dependencies:
                raise TeamPlayerError("TEAMPLAYER_EXTERNAL_DEPENDENCIES_CHANGED")
            return value
        values = await self.adapter.read("list_epics", {"projectId": self.external_project_id})
        matches = [
            e
            for e in values
            if e.get("id") == identity and e.get("projectId") == self.external_project_id
        ]
        if len(matches) != 1 or type(matches[0].get("version")) is not int:
            raise TeamPlayerError("TEAMPLAYER_EXTERNAL_EPIC_UNVERIFIED")
        BoardEpic.model_validate(matches[0])
        return matches[0]

    async def bind_epic(self, actor, run_id, external_epic_id):
        return await self._bind(actor, run_id, external_epic_id, False)

    async def bind_task(self, actor, run_id, external_task_id):
        return await self._bind(actor, run_id, external_task_id, True)

    async def _bind(self, actor, run_id, identity, task):
        run = self._run(actor, run_id, task)
        try:
            external_id(identity)
            self.evidence.worktrees.verify_owned_worktree(run)
            await self._identity()
            await self._external(run, task, identity)
            with self.store.transaction():
                matches = [
                    r
                    for r in self.store.get_references(run.epic_run_id if task else run.id)
                    if r.provider == "teamplayer"
                    and r.kind == ("task" if task else "epic")
                    and r.task_run_id == (run.id if task else None)
                ]
                if matches:
                    if len(matches) != 1 or matches[0].external_id != identity:
                        raise TeamPlayerError("TEAMPLAYER_REFERENCE_CONFLICT")
                    return matches[0]
                ref = ExternalReference(
                    project_id=self.project_id,
                    epic_run_id=run.epic_run_id if task else run.id,
                    task_run_id=run.id if task else None,
                    provider="teamplayer",
                    kind="task" if task else "epic",
                    external_id=identity,
                )
                self.store.add_reference(ref)
                return ref
        except TeamPlayerError:
            raise
        except Exception:
            raise TeamPlayerError("TEAMPLAYER_BINDING_UNVERIFIED") from None

    def _source(self, actor, run, task):
        try:
            status, proof, reason = (
                self.evidence.task(run) if task else self.evidence.epic(actor, run)
            )
            text = f"Herdr {run.id}: {status}.\n" + reason + "\nEvidence: " + canonical_json(proof)
            for name in self.settings.credential_env:
                if value := os.environ.get(name):
                    text = text.replace(value, "[REDACTED]")
            if hasattr(self.adapter, "redact_text"):
                text = self.adapter.redact_text(text)
            return status, proof, text
        except Exception:
            raise TeamPlayerError("TEAMPLAYER_SOURCE_UNVERIFIED") from None

    def _intent(self, actor, run, task, identity, source):
        status, proof, text = source
        key = digest(
            canonical_json({"run_id": run.id, "task": task, "event_id": proof["event_id"]})
        )
        request = {
            "target_id": identity,
            "external_project_id": self.external_project_id,
            "user_id": self.user_id,
            "task": task,
            "status": status,
            "proof": proof,
            "text": text,
            "actor": actor.model_dump(mode="json"),
        }
        with self.store.transaction():
            prior = self.store.get_operation(self.project_id, self.KIND, key)
            if prior:
                if prior.result["intent"] != request:
                    raise TeamPlayerError("TEAMPLAYER_SYNC_INTENT_CHANGED")
                return prior
            # A newer durable local event replaces unfinished mirror work. Keep its
            # attempts for audit, but never replay its older desired status.
            for old in self.store.get_operations(
                run.epic_run_id if task else run.id, kind=self.KIND
            ):
                if old.task_run_id == (run.id if task else None) and old.status == "PENDING":
                    self.store.update_operation(
                        old.model_copy(
                            update={
                                "status": "SUPERSEDED",
                                "updated_at": utc_now(),
                                "error_code": "TEAMPLAYER_NEWER_LOCAL_EVENT",
                            }
                        )
                    )
            op = Operation(
                project_id=self.project_id,
                epic_run_id=run.epic_run_id if task else run.id,
                task_run_id=run.id if task else None,
                kind=self.KIND,
                idempotency_key=key,
                result={"intent": request, "stage": "QUEUED"},
            )
            self.store.add_operation(op)
            return op

    def _save(self, op, *, status="PENDING", error=None, **fields):
        with self.store.transaction():
            current = self.store.get_operation(self.project_id, self.KIND, op.idempotency_key)
            if current.id != op.id or current.result["intent"] != op.result["intent"]:
                raise TeamPlayerError("TEAMPLAYER_SYNC_INTENT_CHANGED")
            saved = current.model_copy(
                update={
                    "status": status,
                    "error_code": error,
                    "updated_at": utc_now(),
                    "result": current.result | fields,
                }
            )
            self.store.update_operation(saved)
            return saved

    @staticmethod
    def _block(op):
        text = op.result["intent"]["text"]
        head = f"<!-- herdr-event:{op.id};sha256:{digest(text)} -->"
        tail = f"<!-- /herdr-event:{op.id} -->"
        return head + "\n" + text + "\n" + tail

    def _has_block(self, description, op):
        prefix = f"<!-- herdr-event:{op.id};"
        if prefix not in description:
            return False
        if description.count(prefix) != 1 or self._block(op) not in description:
            raise TeamPlayerError("TEAMPLAYER_HISTORY_EVENT_CONFLICT")
        return True

    async def sync_task(self, actor, run_id):
        return await self._sync(actor, run_id, True)

    async def sync_epic(self, actor, run_id):
        return await self._sync(actor, run_id, False)

    async def _write_attempt(self, op, field, name, arguments):
        """Unknown outcomes retain their original CAS baseline even after a rejection.

        A fresh version_conflict proves this first attempt was rejected. A stale
        CAS retry after a timeout cannot prove that the earlier write never applied.
        """
        attempt = op.result[field]
        uncertain_before = attempt.get("uncertain", False)
        op = self._save(op, **{field: attempt | {"uncertain": True}})
        try:
            await self.adapter.write(name, arguments)
        except TeamPlayerError as error:
            if str(error) == "TEAMPLAYER_VERSION_CONFLICT" and not uncertain_before:
                self._save(op, **{field: attempt | {"uncertain": False, "rejected": True}})
            raise
        return self._save(op, **{field: attempt | {"uncertain": False, "accepted": True}})

    async def _sync(self, actor, run_id, task):
        run = self._run(actor, run_id, task)  # Deny Worker before lock/journal/network.
        with self._lock():
            identity = self._reference(run, task)
            source = self._source(actor, run, task)
            op = self._intent(actor, run, task, identity, source)  # Durable before external I/O.
            try:
                await self._identity()
                current = await self._external(run, task, identity)
                if not task and source[0] == "Done":
                    snapshot = await TeamPlayerReader(
                        self.adapter, project_id=self.external_project_id, user_id=self.user_id
                    ).read_project()
                    targets = {self._reference(t, True) for t in self.store.get_tasks(run.id)}
                    tasks = snapshot.epic_tasks(identity)
                    actual = {t.id for t in tasks}
                    if targets != actual or any(
                        t.status != "Done"
                        or t.execution_owner_kind != "User"
                        or t.responsible_user_id != self.user_id
                        for t in tasks
                    ):
                        raise TeamPlayerError("TEAMPLAYER_EPIC_TASK_SCOPE_UNVERIFIED")
                desired = source[0]
                if current["status"] in {"Done", "Cancelled"} and current["status"] != desired:
                    raise TeamPlayerError("TEAMPLAYER_EXTERNAL_REOPEN_UNVERIFIED")
                if not task and desired == "Pending" and current["status"] != "Pending":
                    raise TeamPlayerError("TEAMPLAYER_MANUAL_STATUS_DIVERGED")
                if op.status == "SUCCEEDED":
                    if current["status"] != desired or (
                        task and not self._has_block(current["description"], op)
                    ):
                        raise TeamPlayerError("TEAMPLAYER_COMPLETED_SYNC_DIVERGED")
                    return {"status": "EXISTING", "operation_id": op.id}
                if current["status"] != desired:
                    if task:
                        allowed = {
                            "Pending": {"Pending"},
                            "InProgress": {"Pending", "InProgress"},
                            "NeedsInput": {"Pending", "InProgress", "Testing", "NeedsInput"},
                            "Done": {
                                "Pending",
                                "InProgress",
                                "Testing",
                                "NeedsInput",
                                "Blocked",
                                "Done",
                            },
                        }[desired]
                        if "resume_id" in source[1] and desired == "InProgress":
                            allowed |= {"NeedsInput", "Blocked"}
                        if current["status"] not in allowed:
                            raise TeamPlayerError("TEAMPLAYER_MANUAL_STATUS_DIVERGED")
                    if "status_attempt" not in op.result or op.result["status_attempt"].get(
                        "rejected"
                    ):
                        attempt = {
                            "version": current["version"],
                            "before": current["status"],
                            "desired": desired,
                        }
                        op = self._save(op, stage="STATUS_INTENT", status_attempt=attempt)
                    attempt = op.result["status_attempt"]
                    if (
                        current["version"] != attempt["version"]
                        or current["status"] != attempt["before"]
                    ):
                        raise TeamPlayerError("TEAMPLAYER_STATUS_OUTCOME_DIVERGED")
                    if self._source(actor, self._run(actor, run_id, task), task) != source:
                        raise TeamPlayerError("TEAMPLAYER_LOCAL_STATE_CHANGED")
                    base = {
                        "projectId": self.external_project_id,
                        "version": attempt["version"],
                        "status": desired,
                    }
                    if task:
                        args = {
                            "request": base
                            | {"taskId": identity, "statusReason": op.result["intent"]["text"]}
                        }
                        tool = "update_task_status"
                    else:
                        args = base | {"epicId": identity}
                        tool = "update_epic_status"
                    op = await self._write_attempt(op, "status_attempt", tool, args)
                    current = await self._external(run, task, identity)
                if current["status"] != desired:
                    raise TeamPlayerError("TEAMPLAYER_STATUS_READBACK_MISMATCH")
                op = self._save(op, stage="STATUS_CONFIRMED", status_version=current["version"])
                if task:
                    if not self._has_block(current["description"], op):
                        if "history_attempt" in op.result and not op.result["history_attempt"].get(
                            "rejected"
                        ):
                            attempt = op.result["history_attempt"]
                            if (
                                current["version"] != attempt["version"]
                                or current["description"] != attempt["before"]
                            ):
                                raise TeamPlayerError("TEAMPLAYER_HISTORY_OUTCOME_DIVERGED")
                        else:
                            attempt = {
                                "version": current["version"],
                                "before": current["description"],
                                "desired": current["description"] + "\n\n" + self._block(op),
                            }
                            op = self._save(op, stage="HISTORY_INTENT", history_attempt=attempt)
                        if self._source(actor, self._run(actor, run_id, task), task) != source:
                            raise TeamPlayerError("TEAMPLAYER_LOCAL_STATE_CHANGED")
                        op = await self._write_attempt(
                            op,
                            "history_attempt",
                            "update_task_details",
                            {
                                "request": {
                                    "projectId": self.external_project_id,
                                    "taskId": identity,
                                    "version": attempt["version"],
                                    "description": attempt["desired"],
                                    "changeReason": "Herdr event " + op.id,
                                }
                            },
                        )
                        current = await self._external(run, task, identity)
                    if (
                        not self._has_block(current["description"], op)
                        or current["status"] != desired
                    ):
                        raise TeamPlayerError("TEAMPLAYER_HISTORY_READBACK_MISMATCH")
                if self._source(actor, self._run(actor, run_id, task), task) != source:
                    raise TeamPlayerError("TEAMPLAYER_LOCAL_STATE_CHANGED")
                op = self._save(
                    op, status="SUCCEEDED", stage="SYNCED", external_version=current["version"]
                )
                return {"status": "SYNCED", "operation_id": op.id}
            except TeamPlayerError as error:
                op = self._save(op, error=str(error))
                return {"status": "PENDING", "operation_id": op.id, "reason": op.error_code}
            except Exception:
                op = self._save(op, error="TEAMPLAYER_SYNC_OUTCOME_UNKNOWN")
                return {"status": "PENDING", "operation_id": op.id, "reason": op.error_code}
