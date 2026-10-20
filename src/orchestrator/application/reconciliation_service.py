"""Read-only startup inventory; reports are advice, never reusable mutation authority."""

import json
from copy import deepcopy
from pathlib import Path

from orchestrator.adapters.git import GitAdapter, GitError
from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.runtime_lifecycle_service import LifecycleError
from orchestrator.application.task_resume_intent import verified_input
from orchestrator.application.teamplayer_evidence import TeamPlayerEvidence
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.domain.coordinator import CoordinatorRun
from orchestrator.domain.models import EpicRun, TaskRun
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import canonical_json


class ReconciliationError(RuntimeError):
    """Safe error code, not external exception text."""


def bind_selection(report, board):
    """Fresh observations must describe the same board used for this pending claim."""
    if report.get("board_hash") != digest(canonical_json(board.model_dump(mode="json"))):
        report["scheduling_allowed"] = False
        report["classification"] = "UNCERTAIN"
        report["findings"].append(
            dict(
                classification="UNCERTAIN",
                code="BOARD_CHANGED_SINCE_SELECTION",
                next_action="REPEAT_SELECTION_AND_RECONCILIATION",
            )
        )
    return report


def integration_report(report, epic_run_id):
    """Project audit is internal; Integration only receives its own resource identities."""
    result = deepcopy(report)
    owned = {r["run_id"] for r in report["runs"] if r.get("epic_run_id") == epic_run_id}
    result["runs"] = [r for r in result["runs"] if r["run_id"] in owned]
    result["operations"] = [r for r in result["operations"] if r["run_id"] in owned]
    result["registrations"] = [
        r for r in result["registrations"] if r["epic_run_id"] == epic_run_id
    ]
    result["references"] = [r for r in result["references"] if r["epic_run_id"] == epic_run_id]
    result["reservations"] = [r for r in result["reservations"] if r["run_id"] in owned]
    result["foreign_reserved_workers"] = len(report["reservations"]) - len(result["reservations"])
    result["foreign_blockers"] = sorted(
        {
            f["code"]
            for r in (*report["runs"], *report["operations"])
            if r["relevant"] and r["blocks_new_work"] and r["run_id"] not in owned
            for f in r["findings"]
        }
    )
    return result


class LifecycleObserver:
    """Operator supplies the original F13 service and its registered owning principal."""

    def __init__(self, lifecycle, principal):
        self.lifecycle, self.principal = lifecycle, principal

    def __call__(self, run):
        # _reconnect is the read-only implementation; do not use effectful stop/resume.
        result = self.lifecycle._reconnect(self.principal, run.id, isinstance(run, TaskRun))
        sid = result["session_id"]
        if not sid:
            raise LifecycleError("CODEX_SESSION_UNREGISTERED")
        self.lifecycle.codex.read_session(sid, run.worktree_path)
        return dict(
            status=result["status"], session_id=sid, runtime_status=result.get("runtime_status")
        )


class CoordinatorObserver:
    def __init__(self, runtime):
        self.runtime = runtime

    def __call__(self, run):
        service = self.runtime
        op = service.store.get_project_operation(run.project_id, service.KIND, run.id)
        if op is None or op.status != "SUCCEEDED" or op.result.get("stage") != "REGISTERED":
            raise ReconciliationError("COORDINATOR_REGISTRATION_INCOMPLETE")
        facts = service._native(run, op)
        if not run.codex_session_id or facts["session_id"] != run.codex_session_id:
            raise ReconciliationError("COORDINATOR_SESSION_CHANGED")
        service.codex.read_session(run.codex_session_id, run.worktree_path)
        return dict(status="LIVE", session_id=run.codex_session_id, runtime_status=facts["status"])


class IntegrationObserver:
    """F34's original persisted Coordinator authorizes this Integration observation."""

    def __init__(self, startup):
        from orchestrator.application.epic_correction_plan import RegisteredIntegrationPlan

        self.view = startup if isinstance(startup, RegisteredIntegrationPlan) else None
        self.startup = self.view.original if self.view is not None else startup

    @property
    def principal(self):
        s = self.startup
        op = s.store.get_operation(s.spec.project_id, s.KIND, s.principal.epic_run_id)
        if op is None:
            raise ReconciliationError("INTEGRATION_REGISTRATION_MISSING")
        return s._coordinator(op)

    def __call__(self, run):
        if self.view is not None:
            # Verify the additive plan while observing its original registration,
            # not a made-up runtime for the effective review/task catalogue.
            self.view.verify_membership()
        s = self.startup
        op = s.store.get_operation(run.project_id, s.KIND, run.id)
        if op is None or op.status != "SUCCEEDED" or op.result.get("stage") != "REGISTERED":
            raise ReconciliationError("INTEGRATION_REGISTRATION_INCOMPLETE")
        observed = s._verify(self.principal, op)
        if observed != run:
            raise ReconciliationError("INTEGRATION_REGISTRATION_CHANGED")
        result = s.lifecycle._reconnect(self.principal, run.id, False)
        s.codex.read_session(run.codex_session_id, run.worktree_path)
        return dict(
            status=result["status"],
            session_id=result["session_id"],
            runtime_status=result.get("runtime_status"),
        )


class ReconciliationService:
    """Fixed project/reader and operator observers. No MCP-controlled scope or callbacks."""

    def __init__(
        self,
        settings,
        store,
        reader,
        *,
        project_id,
        observers=None,
        evidence_contexts=None,
        scopes=None,
        codex=None,
        processes=None,
    ):
        self.settings, self.store, self.reader = settings, store, reader
        self.project_id = project_id
        databases = store.db.execute("PRAGMA database_list").fetchall()
        if not any(
            row[1] == "main" and Path(row[2]).resolve() == settings.sqlite_path for row in databases
        ):
            raise ReconciliationError("RECONCILIATION_DATABASE_MISMATCH")
        self.observers = observers or {}
        self.contexts, self.scopes = dict(evidence_contexts or {}), dict(scopes or {})
        self.codex, self.processes = codex, processes
        self.git = GitAdapter(settings.repository)

    def _observer(self, run):
        return self.observers(run) if callable(self.observers) else self.observers.get(run.id)

    @staticmethod
    def _fingerprint(inventory):
        return digest(
            canonical_json(
                {k: [v.model_dump(mode="json") for v in rows] for k, rows in inventory.items()}
            )
        )

    @staticmethod
    def _issue(row, status, code, action):
        row["findings"].append(dict(classification=status, code=code, next_action=action))

    def _evidence(self, run):
        epic_id = run.epic_run_id if isinstance(run, TaskRun) else run.id
        settings = self.contexts.get(epic_id, self.settings)
        if settings.repository != self.settings.repository or (
            settings.sqlite_path != self.settings.sqlite_path
        ):
            raise ReconciliationError("RECOVERY_CONTEXT_MISMATCH")
        return TeamPlayerEvidence(
            settings, self.store, scopes=self.scopes, codex=self.codex, processes=self.processes
        )

    def _known_head(self, run, actual, inventory):
        expected = (
            run.initial_commit
            if isinstance(run, CoordinatorRun)
            else run.current_commit or run.base_commit
        )
        # A Worker can have committed before READY_FOR_REVIEW updated current_commit.
        if isinstance(run, TaskRun):
            commits = [
                o
                for o in inventory["operations"]
                if o.kind == "worker_commit"
                and o.task_run_id == run.id
                and o.epic_run_id == run.epic_run_id
                and o.status == "SUCCEEDED"
                and o.result.get("stage") == "COMMITTED"
            ]
            known = {expected}
            parents = set()
            start = self.store.get_operation(run.project_id, "start_runtime", run.id)
            for _ in commits:
                for op in commits:
                    r = op.result
                    sha = r.get("commit")
                    actor = r.get("actor", {})
                    if (
                        r.get("parent") not in known
                        or not sha
                        or start is None
                        or (
                            start.id != r.get("start_operation_id")
                            or start.result.get("mcp_configuration") != r.get("configuration")
                            or actor.get("role") != "Worker"
                            or actor.get("project_id") != run.project_id
                            or actor.get("epic_run_id") != run.epic_run_id
                            or actor.get("task_run_id") != run.id
                        )
                    ):
                        continue
                    if self.git.parents(sha) == (r["parent"],) and (
                        self.git.run("rev-parse", sha + "^{tree}").strip() == r.get("tree")
                        and "herdr-worker-commit:" + op.id
                        in self.git.run("show", "-s", "--format=%B", sha).splitlines()
                        and r.get("session_id") == run.codex_session_id
                    ):
                        known.add(sha)
                        parents.add(r["parent"])
            tips = known - parents
            if len(tips) != 1:
                raise ReconciliationError("COMMIT_HISTORY_AMBIGUOUS")
            return tips.pop()
        # initial_commit is an immutable startup anchor, not the last main tip.
        if isinstance(run, CoordinatorRun):
            for epic in inventory["epics"]:
                if epic.merge_commit and not self.git.contains_commit("main", epic.merge_commit):
                    return epic.merge_commit
            if self.git.contains_commit("main", run.initial_commit):
                return actual
        return expected

    def _git(self, run, row, inventory):
        path = Path(run.worktree_path)
        if not path.is_dir() or path.is_symlink():
            self._issue(row, "DRIFT", "WORKTREE_MISSING_OR_REPLACED", "RESTORE_OR_REVIEW_RESOURCE")
            return
        try:
            actual = self.git.inspect(path, run.branch, clean=False)
            row["observed_commit"] = actual
            if not isinstance(run, CoordinatorRun):
                WorktreeService(self.settings, self.store).verify_owned_worktree(run)
            expected = self._known_head(run, actual, inventory)
            row["expected_commit"] = expected
            if actual != expected:
                self._issue(row, "DRIFT", "HEAD_CHANGED", "REVIEW_SAVED_AND_OBSERVED_COMMITS")
            if self.git.in_progress(path) or self.git.unsafe_index_paths(path):
                self._issue(row, "DRIFT", "GIT_OPERATION_OR_INDEX_UNSAFE", "REVIEW_GIT_STATE")
            # git status can execute a repository-configured clean/process filter.
            # Reject before any content/status read, even in a nominally read-only audit.
            if self.git.run("config", "--get-regexp", r"^filter\.", missing=True):
                self._issue(row, "DRIFT", "GIT_FILTER_CONFIGURATION_UNSAFE", "REVIEW_GIT_CONFIG")
                return
            row["uncommitted_changes"] = bool(self.git.working_changes(path))
            if row["uncommitted_changes"] and not isinstance(run, TaskRun):
                self._issue(row, "DRIFT", "INTEGRATION_WORKTREE_DIRTY", "REVIEW_GIT_STATE")
        except Exception:
            self._issue(row, "DRIFT", "GIT_OWNERSHIP_UNVERIFIED", "REVIEW_GIT_RESOURCE")

    def _board(self, run, row, board):
        if isinstance(run, CoordinatorRun):
            return
        task = isinstance(run, TaskRun)
        local = run.task_id if task else run.epic_id
        matches = [
            r
            for r in (board.tasks if task else board.epics)
            if r.binding is not None and r.binding.local_id == local
        ]
        if len(matches) != 1:
            self._issue(row, "DRIFT", "BOARD_BINDING_MISSING_OR_AMBIGUOUS", "REVIEW_BOARD_BINDING")
            return
        native = matches[0]
        row.update(board_id=native.id, board_status=native.status, board_version=native.version)
        epic_id = run.epic_run_id if task else run.id
        refs = [
            r
            for r in self.store.get_references(epic_id)
            if r.provider == "teamplayer"
            and r.kind == ("task" if task else "epic")
            and r.task_run_id == (run.id if task else None)
        ]
        if refs and (len(refs) != 1 or refs[0].external_id != native.id):
            self._issue(row, "DRIFT", "BOARD_REFERENCE_CHANGED", "REVIEW_BOARD_BINDING")
        if any(i.task_id == native.id for i in board.issues):
            self._issue(row, "DRIFT", "BOARD_ITEM_INVALID", "REVIEW_BOARD_ITEM")
        if task:
            epic = self.store.get_epic(run.epic_run_id)
            bound = board.epic(native.epic_id)
            if (
                epic is None
                or bound is None
                or bound.binding is None
                or (
                    bound.binding.local_id != epic.epic_id
                    or native.execution_owner_kind != "User"
                    or native.responsible_user_id != self.reader.user_id
                )
            ):
                self._issue(
                    row, "DRIFT", "BOARD_OWNER_OR_EPIC_CHANGED", "RESTORE_VERIFIED_ASSIGNMENT"
                )
        state = run.internal_status if task else run.status
        if native.status == "Done" or state == "DONE":
            try:
                proof = self._evidence(run)
                if task:
                    status, _, _ = proof.task(run)
                    if status != "Done":
                        raise ValueError
                else:
                    # The evidence API authorizes this role internally; reuse the
                    # original registered observer's principal, never invent one.
                    observer = self._observer(run)
                    principal = getattr(observer, "principal", None)
                    if principal is None:
                        raise ValueError
                    status, _, _ = proof.epic(principal, run)
                    if status != "Done":
                        raise ValueError
                if native.status != "Done" or state != "DONE":
                    self._issue(row, "RESUMABLE", "DONE_SYNC_PENDING", "RECONCILE_ORIGINAL_SYNC")
            except Exception:
                self._issue(row, "DRIFT", "DONE_WITHOUT_DELIVERY_PROOF", "REVIEW_ORIGINAL_DELIVERY")
        else:
            expected = (
                run.kanban_status if task else ("Planned" if state == "PLANNED" else "Active")
            )
            if task:
                from orchestrator.application.session_recovery_record import task_record

                try:
                    recovery = task_record(self.store, run)
                    if recovery is not None and recovery.result["stage"] == "ATTENTION":
                        expected = "Attention"
                except Exception:
                    self._issue(row, "DRIFT", "RECOVERY_OBSERVATION_CHANGED",
                                "REPEAT_RECOVERY_VERIFICATION")
            if native.kanban != expected:
                self._issue(row, "RESUMABLE", "BOARD_STATE_MISMATCH", "RECONCILE_ORIGINAL_SYNC")

    def _runtime(self, run, row):
        try:
            observer = self._observer(run)
        except Exception:
            observer = None
        if observer is None:
            self._issue(
                row, "UNCERTAIN", "RUNTIME_OBSERVER_UNAVAILABLE", "RESTORE_ORIGINAL_OBSERVER"
            )
            return
        try:
            result = observer(run)
            if result.get("session_id") != run.codex_session_id or not run.codex_session_id:
                self._issue(
                    row, "DRIFT", "SESSION_CHANGED_OR_UNREGISTERED", "REVIEW_ORIGINAL_SESSION"
                )
            elif result.get("status") == "LIVE":
                row.update(runtime_status=result.get("runtime_status"), runtime_observation="LIVE")
                if result.get("runtime_status") not in {"working", "idle", "done", "blocked"}:
                    self._issue(
                        row, "UNCERTAIN", "RUNTIME_STATUS_UNCERTAIN", "RETRY_READ_ONLY_OBSERVATION"
                    )
            elif result.get("status") == "STOPPED":
                row["runtime_observation"] = "STOPPED"
                self._issue(
                    row,
                    "RESUMABLE",
                    "ORIGINAL_RUNTIME_STOPPED",
                    "RESUME_SAME_SESSION_WITH_SLOT"
                    if isinstance(run, TaskRun)
                    else "RECONCILE_ORIGINAL_EPIC_OPERATION"
                    if run.status == EpicState.MERGING
                    else "RECONNECT_ORIGINAL_INTEGRATION",
                )
            else:
                raise ValueError
        except HerdrError as error:
            if str(error) == "RUNTIME_IDENTITY_MISMATCH":
                self._issue(row, "DRIFT", "RUNTIME_IDENTITY_MISMATCH", "REVIEW_ORIGINAL_RUNTIME")
            else:
                self._issue(row, "UNCERTAIN", "HERDR_UNAVAILABLE", "RETRY_READ_ONLY_OBSERVATION")
        except LifecycleError as error:
            code = str(error)
            if code not in {
                "RUNTIME_BINDING_UNVERIFIED",
                "RUNTIME_PANE_CHANGED",
                "RUNTIME_PROCESS_CHANGED",
                "CODEX_SESSION_CHANGED",
                "RUNTIME_MISSING_UNCONFIRMED",
                "CODEX_SESSION_UNREGISTERED",
            }:
                code = "RUNTIME_REGISTRATION_UNVERIFIED"
            self._issue(row, "DRIFT", code, "REVIEW_ORIGINAL_RUNTIME")
        except Exception:
            self._issue(
                row, "UNCERTAIN", "RUNTIME_OBSERVATION_UNAVAILABLE", "RETRY_READ_ONLY_OBSERVATION"
            )

    @staticmethod
    def _pending(op):
        if op.status != "PENDING" and not op.result.get("requires_reconciliation"):
            return False
        # These are durable ownership/watch loops, not unfinished external effects.
        if op.status == "PENDING" and (
            op.kind == "epic_schedule"
            or op.kind == "epic_claim"
            and op.result.get("stage") == "REGISTERED"
            or op.kind == "task_schedule"
            and op.result.get("phase") == "WAITING_INPUT"
            or op.kind == "task_schedule"
            and op.result.get("phase") in {"OBSERVING", "AWAITING_REVIEW", "DONE"}
            and not op.result.get("reason")
        ):
            return False
        return True

    def _passive_input(self, op, inventory, rows):
        """Known same-session input wait cannot consume a second, unrelated slot."""
        task = next((t for t in inventory["tasks"] if t.id == op.task_run_id), None)
        observed = next((r for r in rows if task is not None and r["run_id"] == task.id), None)
        if task is None or task.internal_status != TaskState.PARKED or task.worker_slot is None:
            return False
        if (
            observed is None
            or observed["findings"]
            or observed.get("runtime_observation") != "LIVE"
        ):
            return False
        if op.kind == "task_schedule":
            inputs = [
                o
                for o in inventory["operations"]
                if o.task_run_id == task.id and o.kind == "task_resume" and o.status == "PENDING"
            ]
            return (
                op.result.get("phase") == "DISPATCH_REQUESTED"
                and len(inputs) == 1
                and self._passive_input(inputs[0], inventory, rows)
            )
        if op.kind != "task_resume" or op.result.get("stage") != "DISPATCH_REQUESTED":
            return False
        try:
            start, _, _ = verified_input(self.store, task, op)
            resumed = self.store.get_operation(
                task.project_id, "resume_runtime", op.result["resume_key"]
            )
            prompt = json.loads(op.result["prompt"])
            return bool(
                resumed
                and resumed.status == "SUCCEEDED"
                and resumed.result.get("input_operation_id") == op.id
                and resumed.id == op.result["resume_id"] == start.result.get("generation")
                and resumed.result.get("session_id") == task.codex_session_id
                and resumed.result.get("worker_slot") == task.worker_slot
                and self.git.head(task.branch) == op.result["resource_commit"]
                and digest(op.result["prompt"]) == op.result["prompt_hash"]
                and prompt["type"] == "HERDR_INPUT"
                and prompt["answer"] == op.result["decision"]["answer"]
                and prompt["branch"] == task.branch
                and prompt["worktree_path"] == task.worktree_path
                and prompt["assignment"]["task_run_id"] == task.id
                and prompt["assignment"]["input_id"] == op.idempotency_key
            )
        except Exception:
            return False

    def _continuation_wait(self, op, inventory, rows, board, key):
        if op.kind != "coordinator_epic_continuation" or key is None or board is None:
            return False
        if op.result.get("claim_key") != key or op.result.get("stage") != "INTENT":
            return False
        try:
            root = next(r for r in inventory["coordinators"] if r.id == op.coordinator_run_id)
            observed = next(r for r in rows if r["run_id"] == root.id)
            observer = self._observer(root)
            if (
                observed["findings"]
                or observed.get("runtime_observation") != "LIVE"
                or (
                    not isinstance(observer, CoordinatorObserver)
                    or observer.runtime.store is not self.store
                )
            ):
                return False
            delivery = next(
                o
                for o in inventory["project_operations"]
                if o.id == op.idempotency_key == op.result["delivery_id"]
                and o.kind == "coordinator_epic_delivery"
                and o.coordinator_run_id == root.id
            )
            if delivery.status != "SUCCEEDED" or delivery.result.get("stage") != "RELEASED":
                return False
            epic = next(
                e
                for e in inventory["epics"]
                if e.id == delivery.result["epic_run_id"] and e.status == EpicState.DONE
            )
            bound = [e for e in board.epics if e.binding and e.binding.local_id == epic.epic_id]
            if len(bound) != 1 or bound[0].status != "Done":
                return False
            for task in self.store.get_tasks(epic.id):
                native = [
                    t
                    for t in board.epic_tasks(bound[0].id)
                    if t.binding and t.binding.local_id == task.task_id
                ]
                if (
                    len(native) != 1
                    or native[0].status != "Done"
                    or (
                        native[0].execution_owner_kind != "User"
                        or native[0].responsible_user_id != self.reader.user_id
                        or any(i.task_id == native[0].id for i in board.issues)
                    )
                ):
                    return False
            status, proof, _ = self._evidence(epic).epic(observer.runtime.principal, epic)
            child = self._observer(epic)
            if (
                status != "Done"
                or not isinstance(child, IntegrationObserver)
                or (
                    proof["merge_commit"] != delivery.result["merge_commit"]
                    or proof["final_test_id"] != delivery.result["final_verification_id"]
                )
            ):
                return False
            lifecycle = child.startup.lifecycle
            run, start = lifecycle._validate(child.principal, epic.id, False)
            stop = next(
                o
                for o in inventory["operations"]
                if o.id == delivery.result["stop_operation_id"]
                and o.epic_run_id == epic.id
                and o.task_run_id is None
                and o.kind == "stop_runtime"
                and o.status == "SUCCEEDED"
                and o.result.get("stage") == "STOPPED"
                and o.result.get("inactive") is True
                and o.result.get("generation") == start.result.get("generation", start.id)
            )
            return lifecycle._inactive(run, start, stop)
        except Exception:
            return False

    async def inspect(self, *, epic_run_id=None, continuation_key=None):
        inventory = self.store.project_inventory(self.project_id)
        before = self._fingerprint(inventory)
        report = dict(
            project_id=self.project_id,
            epic_run_id=epic_run_id,
            snapshot_hash=before,
            runs=[],
            operations=[],
            registrations=[],
            reservations=[],
            findings=[],
            references=[
                dict(
                    reference_id=r.id,
                    provider=r.provider,
                    kind=r.kind,
                    external_id=r.external_id,
                    epic_run_id=r.epic_run_id,
                    task_run_id=r.task_run_id,
                )
                for r in inventory["references"]
            ],
        )
        try:
            board = await self.reader.read_project()
            if board.project_id != self.reader.project_id or (
                board.authenticated_user_id != self.reader.user_id
            ):
                raise ValueError
        except Exception:
            board = None
            self._issue(
                report, "UNCERTAIN", "TEAMPLAYER_UNAVAILABLE", "RETRY_READ_ONLY_OBSERVATION"
            )
        report["board_hash"] = (
            digest(canonical_json(board.model_dump(mode="json"))) if board is not None else None
        )
        runs = (*inventory["coordinators"], *inventory["epics"], *inventory["tasks"])
        ids = {r.id for r in runs}
        pending = [
            o
            for o in (*inventory["operations"], *inventory["project_operations"])
            if self._pending(o)
        ]
        pending_keys = {o.id for o in pending}
        for op in (*inventory["operations"], *inventory["project_operations"]):
            if op.status != "PENDING" or op.id in pending_keys:
                continue
            report["registrations"].append(
                dict(
                    operation_id=op.id,
                    kind=op.kind,
                    idempotency_key=op.idempotency_key,
                    stage=op.result.get("stage"),
                    phase=op.result.get("phase"),
                    epic_run_id=getattr(op, "epic_run_id", op.result.get("epic_run_id")),
                    task_run_id=getattr(op, "task_run_id", None),
                    coordinator_run_id=getattr(op, "coordinator_run_id", None),
                )
            )
            if op.kind == "epic_claim":
                owned = next(
                    (e for e in inventory["epics"] if e.id == op.result.get("epic_run_id")), None
                )
                if owned is None or owned.epic_id != op.result.get("epic_id"):
                    self._issue(
                        report,
                        "DRIFT",
                        "EPIC_RESERVATION_OWNER_MISSING",
                        "REVIEW_ORIGINAL_RESERVATION",
                    )
                elif owned.status == EpicState.DONE:
                    self._issue(
                        report,
                        "RESUMABLE",
                        "EPIC_RELEASE_PENDING",
                        "RECONCILE_ORIGINAL_EPIC_RELEASE",
                    )
        pending_ids = {
            getattr(o, "task_run_id", None)
            or getattr(o, "epic_run_id", None)
            or o.coordinator_run_id
            for o in pending
        }
        for run in runs:
            done = (
                run.status == "STOPPED"
                if isinstance(run, CoordinatorRun)
                else run.internal_status == TaskState.DONE
                if isinstance(run, TaskRun)
                else run.status == EpicState.DONE
            )
            if done and run.id not in pending_ids and not getattr(run, "worker_slot", None):
                continue
            row = dict(
                run_id=run.id,
                project_id=run.project_id,
                epic_run_id=getattr(
                    run, "epic_run_id", run.id if isinstance(run, EpicRun) else None
                ),
                task_id=getattr(run, "task_id", None),
                epic_id=getattr(run, "epic_id", None),
                kind=type(run).__name__,
                branch=run.branch,
                worktree_path=run.worktree_path,
                session_id=run.codex_session_id,
                state=getattr(run, "internal_status", getattr(run, "status", None)),
                worker_slot=getattr(run, "worker_slot", None),
                saved_commit=getattr(run, "current_commit", getattr(run, "initial_commit", None)),
                herdr_server_session=run.herdr_server_session,
                herdr_binding={
                    k: getattr(run, "herdr_" + k)
                    for k in ("workspace_id", "tab_id", "pane_id", "terminal_id")
                },
                findings=[],
                relevant=epic_run_id is None
                or isinstance(run, TaskRun)
                or isinstance(run, EpicRun)
                and run.id == epic_run_id,
            )
            self._git(run, row, inventory)
            if board is not None:
                self._board(run, row, board)
            if not done:
                self._runtime(run, row)
            if isinstance(run, TaskRun) and run.worker_slot is not None:
                report["reservations"].append(dict(run_id=run.id, slot=run.worker_slot))
                if done:
                    self._issue(
                        row, "DRIFT", "COMPLETED_RUN_RETAINS_SLOT", "REVIEW_ORIGINAL_RESERVATION"
                    )
            report["runs"].append(row)
        for op in pending:
            owner = getattr(op, "task_run_id", None) or getattr(op, "epic_run_id", None)
            owner = owner or getattr(op, "coordinator_run_id", None)
            relevant = epic_run_id is None or getattr(op, "epic_run_id", None) == epic_run_id
            row = dict(
                operation_id=op.id,
                kind=op.kind,
                idempotency_key=op.idempotency_key,
                run_id=owner,
                status=op.status,
                stage=op.result.get("stage"),
                relevant=relevant,
                findings=[],
            )
            self._issue(
                row,
                "RESUMABLE" if owner in ids else "DRIFT",
                "OPERATION_PENDING" if owner in ids else "OPERATION_OWNER_MISSING",
                "RECONCILE_ORIGINAL_OPERATION",
            )
            report["operations"].append(row)
        slots = report["reservations"]
        if (
            len(
                [
                    e
                    for e in inventory["epics"]
                    if e.status not in {EpicState.PLANNED, EpicState.DONE}
                ]
            )
            > 1
        ):
            self._issue(report, "DRIFT", "MULTIPLE_ACTIVE_EPICS", "REVIEW_ORIGINAL_RESERVATIONS")
        if len({r["slot"] for r in slots}) != len(slots) or any(
            r["slot"] > self.settings.max_workers for r in slots
        ):
            self._issue(
                report, "DRIFT", "WORKER_RESERVATIONS_CONFLICT", "REVIEW_ORIGINAL_RESERVATIONS"
            )
        rank = {"VERIFIED": 0, "RESUMABLE": 1, "DRIFT": 2, "UNCERTAIN": 3}
        for row in (*report["runs"], *report["operations"]):
            row["classification"] = max(
                (f["classification"] for f in row["findings"]), key=rank.get, default="VERIFIED"
            )
            parked = next((r for r in inventory["tasks"] if r.id == row["run_id"]), None)
            row["blocks_new_work"] = row["classification"] != "VERIFIED" and not (
                parked is not None
                and parked.internal_status == TaskState.PARKED
                and parked.worker_slot is None
                and row.get("runtime_observation") == "STOPPED"
                and {f["code"] for f in row["findings"]} == {"ORIGINAL_RUNTIME_STOPPED"}
            )
            if "operation_id" in row:
                operation = next(o for o in pending if o.id == row["operation_id"])
                if hasattr(operation, "task_run_id") and self._passive_input(
                    operation, inventory, report["runs"]
                ):
                    row["blocks_new_work"] = False
                    row["findings"][0]["next_action"] = "OBSERVE_ORIGINAL_INPUT_ACK"
                if self._continuation_wait(
                    operation, inventory, report["runs"], board, continuation_key
                ):
                    row["blocks_new_work"] = False
                    row["findings"][0]["next_action"] = "CONTINUE_ORIGINAL_EPIC_CLAIM"
        # Repeat the local inventory and observed Git heads; never authorize from a
        # report that raced a writer. Existing operation-specific guards still apply.
        if board is not None:
            try:
                if await self.reader.read_project(previous=board) != board:
                    self._issue(
                        report,
                        "UNCERTAIN",
                        "BOARD_CHANGED_DURING_RECONCILIATION",
                        "RETRY_READ_ONLY_OBSERVATION",
                    )
            except Exception:
                self._issue(
                    report, "UNCERTAIN", "TEAMPLAYER_UNAVAILABLE", "RETRY_READ_ONLY_OBSERVATION"
                )
        if self._fingerprint(self.store.project_inventory(self.project_id)) != before:
            self._issue(
                report,
                "UNCERTAIN",
                "STATE_CHANGED_DURING_RECONCILIATION",
                "RETRY_READ_ONLY_OBSERVATION",
            )
        for row in report["runs"]:
            if "observed_commit" in row:
                try:
                    if (
                        self.git.inspect(Path(row["worktree_path"]), row["branch"], clean=False)
                        != row["observed_commit"]
                    ):
                        raise GitError
                except Exception:
                    self._issue(
                        row,
                        "UNCERTAIN",
                        "GIT_CHANGED_DURING_RECONCILIATION",
                        "RETRY_READ_ONLY_OBSERVATION",
                    )
                    row["classification"], row["blocks_new_work"] = "UNCERTAIN", True
                    self._issue(
                        report,
                        "UNCERTAIN",
                        "GIT_CHANGED_DURING_RECONCILIATION",
                        "RETRY_READ_ONLY_OBSERVATION",
                    )
        report["scheduling_allowed"] = not report["findings"] and not any(
            row["relevant"] and row["blocks_new_work"]
            for row in (*report["runs"], *report["operations"])
        )
        report["classification"] = max(
            [f["classification"] for f in report["findings"]]
            + [
                r["classification"]
                for r in (*report["runs"], *report["operations"])
                if r["relevant"]
            ],
            key=rank.get,
            default="VERIFIED",
        )
        return report

    async def require_scheduling(self, *, epic_run_id=None):
        report = await self.inspect(epic_run_id=epic_run_id)
        if not report["scheduling_allowed"]:
            raise ReconciliationError("STARTUP_RECONCILIATION_REQUIRED")
        return report

    def current(self, report):
        return report.get("snapshot_hash") == self._fingerprint(
            self.store.project_inventory(self.project_id)
        )
