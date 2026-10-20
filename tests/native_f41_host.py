"""Protected F41 composition: genuine F38 claims, F39 review and F40 delivery.

No command here schedules, approves or merges on the operator's behalf. Runtime
principals come from persisted claims and actual native process/cwd identities.
The measured gate is deliberately absent until the registered preflight exists.
"""

import asyncio
import hashlib
import os
import shlex
import sys
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from native_f37_host import protected_config
from native_f37_probe import save

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.herdr import HerdrAdapter
from orchestrator.adapters.teamplayer_correction import TeamPlayerCorrectionCreator
from orchestrator.adapters.teamplayer_mcp import OperatorHeaders, teamplayer_connection
from orchestrator.application.coordinator_claim_service import CoordinatorClaimService
from orchestrator.application.coordinator_control_service import CoordinatorControlService
from orchestrator.application.coordinator_delivery_service import (
    CoordinatorDeliveryService,
    delivery_capability,
)
from orchestrator.application.coordinator_review_service import (
    CoordinatorReviewService,
    review_capability,
)
from orchestrator.application.coordinator_review_subject import (
    CoordinatorReviewSubject,
    completion_for,
)
from orchestrator.application.coordinator_runtime_service import CoordinatorRuntimeService
from orchestrator.application.epic_completion_service import EpicCompletionService
from orchestrator.application.epic_start_service import EpicStartError, EpicStartService
from orchestrator.application.integration_control_service import IntegrationControlService
from orchestrator.application.runtime_lifecycle_service import RuntimeLifecycleService
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_scheduler_service import TaskSchedulerService
from orchestrator.application.task_selection_service import TaskSelectionService
from orchestrator.application.teamplayer_reader import TeamPlayerReader
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.application.worker_commit_service import WorkerCommitService
from orchestrator.config import Settings
from orchestrator.domain.codex_execution import CodexExecutionPolicy
from orchestrator.domain.codex_mcp_launch import CodexMCPLaunch
from orchestrator.domain.coordinator_contracts import RuntimeGateEvidence
from orchestrator.domain.coordinator_review import CoordinatorReviewPolicy
from orchestrator.domain.models import EpicRun, TaskRun, utc_now
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.domain.worker_contracts import canonical_json
from orchestrator.event_log import EventLog
from orchestrator.mcp.contracts import ToolResponse
from orchestrator.mcp.server import serve_stdio
from orchestrator.persistence.store import StateStore

ROOT_TOOLS = (
    "coordinator_overview", "epic_claim_next",
    "coordinator_review_context", "coordinator_review_read", "coordinator_review_submit",
    "coordinator_epic_deliver",
)
INTEGRATION_TOOLS = (
    "runtime_status", "policy_check", "integration_overview", "task_schedule", "task_get_next",
    "task_start", "task_review_request", "task_request_changes", "task_approve", "task_merge",
    "task_park_blocked", "task_block_review", "resume_task", "worker_resume", "epic_complete",
    "set_task_status",
)
WORKER_TOOLS = ("worker_workspace", "worker_commit")


def fingerprint_files(c):
    """Pins are checked before connecting to native/board services, not merely logged."""
    for name, digest in c["source_pins"].items():
        path = Path(c["implementation"]) / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("F41_FROZEN_SOURCE_CHANGED")
    return hashlib.sha256(canonical_json(dict(
        source_pins=c["source_pins"], profiles=c["profiles"],
        test_command=c["settings"]["worker_test_command"],
        tools=dict(coordinator=ROOT_TOOLS, integration=INTEGRATION_TOOLS, worker=WORKER_TOOLS),
    )).encode()).hexdigest()


def adapter_for(c, config_path, role):
    tools = {"coordinator": ROOT_TOOLS, "integration": INTEGRATION_TOOLS,
             "worker": WORKER_TOOLS}[role]
    return HerdrAdapter(
        c["server"], sandbox="workspace-write" if role == "worker" else "read-only",
        mcp=CodexMCPLaunch(
            command=c["python"], args=(c["host"], str(config_path), role),
            disabled_servers=tuple(c["disabled_servers"]),
            disabled_plugins=tuple(c["disabled_plugins"]), auto_approved_tools=tools,
        ), execution_policy=CodexExecutionPolicy.model_validate(c["profiles"][role]),
    )


def child_settings(settings, spec):
    return settings.model_copy(update=dict(review_context=EpicReviewSpec(
        version=1, project_id=spec.project_id, epic_id=spec.epic_id,
        requirements=spec.requirements, acceptance_criteria=spec.acceptance_criteria,
        sources=spec.sources,
    )))


def integration_actor(project_id, run_id):
    return Actor(actor_id="f41-integration-" + run_id, role=Role.INTEGRATION,
                 project_id=project_id, epic_run_id=run_id)


@asynccontextmanager
async def composition(config_path):
    c = protected_config(config_path)
    boundary_hash = fingerprint_files(c)
    settings = Settings.model_validate(c["settings"])
    coordinator = Actor.model_validate(c["coordinator"])
    policies = [CoordinatorReviewPolicy.model_validate_json(canonical_json(p))
                for p in c["policies"]]
    native = tomllib.loads((Path.home() / ".codex/config.toml").read_text())["mcp_servers"][
        "teamplayer"
    ]
    headers = OperatorHeaders(command=tuple(shlex.split(native["http_headers_helper"])))
    async with teamplayer_connection(native["url"], headers, writable=True,
                                     timeout_seconds=30) as board:
        with StateStore(settings.sqlite_path) as store:
            codex, log = CodexAdapter(), EventLog(stream=sys.stderr)
            root_herdr = adapter_for(c, config_path, "coordinator")
            integration_herdr = adapter_for(c, config_path, "integration")
            workers = adapter_for(c, config_path, "worker")
            review_hash = review_capability(policies)
            runtime = CoordinatorRuntimeService(
                settings, store, root_herdr, c["spec"], coordinator, c["run_id"], codex=codex,
                capabilities=dict(coordinator_review=review_hash,
                                  coordinator_delivery=delivery_capability(review_hash),
                                  execution_boundary=boundary_hash),
            )
            parents, subjects = {}, {}

            def bindings():
                result = {}
                for policy in policies:
                    result[policy.native_epic_id] = LocalBoardBinding(
                        local_id=policy.original.epic_id,
                    )
                    subject = next((s for s in subjects.values()
                                    if s.policy.native_epic_id == policy.native_epic_id), None)
                    spec, ids = (subject.plan.current()[:2] if subject else
                                 (policy.original, policy.native_task_ids))
                    result.update({identity: LocalBoardBinding(local_id=entry.task.task_id)
                                   for identity, entry in zip(ids, spec.tasks, strict=True)})
                return result

            def reader():
                return TeamPlayerReader(board, project_id=c["native_project"],
                                        user_id=c["user"], project_name="HerdrCoordinator",
                                        bindings=bindings())

            def scopes():
                return {identity: tuple(e.task.task_id for e in s.plan.current()[0].tasks)
                        for identity, s in subjects.items()}

            def sync(child):
                return TeamPlayerSyncService(
                    child, store, board, project_id=runtime.spec.project_id,
                    external_project_id=c["native_project"], user_id=c["user"],
                    scopes=scopes(), codex=codex,
                )

            def start_factory(configured, run_id):
                if run_id not in parents:
                    spec = configured.spec
                    child = child_settings(settings, spec)
                    startup_sync = TeamPlayerSyncService(
                        child, store, board, project_id=spec.project_id,
                        external_project_id=c["native_project"], user_id=c["user"],
                        scopes={run_id: tuple(t.task.task_id for t in spec.tasks)}, codex=codex,
                    )
                    parents[run_id] = EpicStartService(
                        child, store, integration_herdr, spec,
                        integration_actor(spec.project_id, run_id), codex,
                        sync=startup_sync, external_epic_id=configured.native_epic_id,
                    )
                return parents[run_id]

            def context_factory(view):
                policy = next(p for p in policies if p.original.epic_id == view.spec.epic_id)
                specs = {identity: entry.task.model_dump(mode="json")
                         for identity, entry in zip(view.native_task_ids, view.spec.tasks,
                                                    strict=True)}
                contexts = {identity: s.original_start.settings for identity, s in subjects.items()
                            if identity != view.principal.epic_run_id}
                previous_scopes = {k: v for k, v in scopes().items() if k in contexts}
                prerequisites = tuple(p.native_epic_id for p in policies
                                      if p.original.epic_id in view.spec.dependencies)
                selection = TaskSelectionService(
                    view.settings, store, reader(), specs=specs, task_order=tuple(specs),
                    epic_prerequisites={view.spec.epic_id: prerequisites},
                    delivery_contexts=contexts, scopes=previous_scopes, codex=codex,
                )
                scheduler = TaskSchedulerService(view.settings, store, selection,
                                                  sync(view.settings), workers, codex,
                                                  registration=view)
                completion = EpicCompletionService(
                    view, scheduler, completion_for(policy, view.spec),
                )
                return IntegrationControlService(view, scheduler, log, epic_completion=completion)

            # Reconstruct only real persisted F38 reservations, including a partial
            # claim before F34 native registration. No invented placeholder run.
            for claim in store.project_operations(runtime.run_id, kind="epic_claim"):
                if "epic_run_id" not in claim.result:
                    continue
                configured = next(e for e in runtime.spec.epics
                                  if e.spec.epic_id == claim.result["epic_id"])
                parent = start_factory(configured, claim.result["epic_run_id"])
                policy = next(p for p in policies if p.native_epic_id == configured.native_epic_id)
                subjects[parent.principal.epic_run_id] = CoordinatorReviewSubject(
                    runtime, parent, policy, context_factory,
                    TeamPlayerCorrectionCreator(board, project_id=c["native_project"],
                                                epic_id=policy.native_epic_id, user_id=c["user"]),
                )

            def gate(root, main):
                # This operator receipt is produced only by the separate measured
                # preflight verifier. Missing/old/mismatched receipt keeps WAIT.
                receipt = protected_config(Path(c["gate_receipt"]))
                if receipt["boundary_hash"] != boundary_hash or (
                    receipt["configuration_hash"] != root.configuration_hash
                ):
                    raise RuntimeError("F41_CURRENT_MEASURED_PREFLIGHT_REQUIRED")
                # Revalidate actual proofs; an arbitrary positive JSON is never
                # itself an attestation. No fallback if the verifier is absent.
                from native_f41_gate import verify_receipt
                # The same registered boundary may follow verified delivery to a
                # new main. Re-measure now; no manual scheduling/approval or copied
                # old-main attestation is needed for autonomous continuation.
                evidence = verify_receipt(c, root, main, receipt)
                if receipt["main_commit"] != main:
                    save(Path(c["gate_receipt"]), receipt | dict(
                        main_commit=main, measured_at=utc_now().isoformat(),
                    ))
                return RuntimeGateEvidence.model_validate(evidence)

            claims = CoordinatorClaimService(
                runtime, reader(), start_factory, runtime_gate=gate,
                delivery_contexts={k: s.original_start.settings for k, s in subjects.items()},
                scopes=scopes(),
            )
            reviews = CoordinatorReviewService(runtime, subjects, policies=policies)
            delivery = CoordinatorDeliveryService(reviews, claims)
            claims.completed_plan = delivery.completed_plan
            root = RuntimeService(store, coordinator, log, coordinator_control=
                                  CoordinatorControlService(claims, review=reviews,
                                                            delivery=delivery))
            yield SimpleNamespace(config=c, settings=settings, store=store, runtime=runtime,
                                  claims=claims, reviews=reviews, delivery=delivery, root=root,
                                  subjects=subjects, workers=workers, log=log)


def scoped_service(env, role, cwd, parent_pid, *, metadata_only=False):
    if role == "coordinator":
        env.runtime.connection_principal(env.runtime.principal)
        run = env.store.get_coordinator(env.runtime.run_id)
        op = env.store.get_project_operation(run.project_id, env.runtime.KIND, run.id)
        facts = env.runtime._native(run, op)
        service = env.root
    elif role == "integration":
        runs = [r for r in env.store.get_runs() if isinstance(r, EpicRun)
                and r.worktree_path == cwd and r.id in env.subjects]
        if len(runs) != 1:
            raise RuntimeError("F41_NATIVE_INTEGRATION_SCOPE_UNVERIFIED")
        subject = env.subjects[runs[0].id]
        if metadata_only:
            return subject.context()[0].runtime(env.log)
        subject.original_start.connection_principal(subject.original_start.principal)
        control = subject.context()[0]
        start = env.store.get_operation(runs[0].project_id, "start_runtime", runs[0].id)
        facts = subject.original_start.lifecycle._live(runs[0], start)
        service = control.runtime(env.log)
    elif role == "worker":
        runs = [r for r in env.store.get_runs() if isinstance(r, TaskRun)
                and r.worktree_path == cwd]
        if len(runs) != 1:
            raise RuntimeError("F41_NATIVE_WORKER_SCOPE_UNVERIFIED")
        run = runs[0]
        actor = Actor(actor_id="f41-worker-" + run.id, role=Role.WORKER,
                      project_id=run.project_id, epic_run_id=run.epic_run_id, task_run_id=run.id)
        service = RuntimeService(env.store, actor, env.log, worker_commit=WorkerCommitService(
            env.settings, env.store, env.workers, actor, client_parent_pid=parent_pid,
        ))
        if metadata_only:
            return service
        # Metadata may be listed before the assignment ACK. Every mutation still
        # performs the complete registration/ACK/process checks in the broker.
        start = env.store.get_operation(run.project_id, "start_runtime", run.id)
        facts = RuntimeLifecycleService(env.settings, env.store, env.workers)._live(run, start)
    else:
        raise RuntimeError("F41_NATIVE_ROLE_INVALID")
    if facts is None or parent_pid not in {p["pid"] for p in facts["processes"]}:
        raise RuntimeError("F41_NATIVE_STDIO_PARENT_UNVERIFIED")
    return service


class PerRequestRuntime:
    def __init__(self, metadata, path, role, cwd, parent_pid):
        self.metadata, self.path, self.role = metadata, path, role
        self.cwd, self.parent_pid = cwd, parent_pid

    def __getattr__(self, name):
        return getattr(self.metadata, name)

    async def call_async(self, operation, arguments):
        async with composition(self.path) as env:
            try:
                service = scoped_service(env, self.role, self.cwd, self.parent_pid)
            except EpicStartError as error:
                if str(error) != "INTEGRATION_CONNECTION_NOT_REGISTERED":
                    raise
                return ToolResponse(ok=False, code=str(error),
                                    message="wait for the original startup ACK to be registered")
            return await service.call_async(operation, arguments)


async def main():
    path, role = Path(sys.argv[1]), sys.argv[2]
    cwd, parent_pid = str(Path.cwd().resolve()), os.getppid()
    async with composition(path) as env:
        # Catalogs must be available while Codex starts, before its assignment ACK.
        # Scoped mutation calls recheck the actual identity after registration.
        if role == "coordinator":
            metadata = env.root
        else:
            metadata = scoped_service(env, role, cwd, parent_pid, metadata_only=True)
    await serve_stdio(PerRequestRuntime(metadata, path, role, cwd, parent_pid))


if __name__ == "__main__":
    asyncio.run(main())
