"""Protected manual product probe: real Coordinator, same Integration and board.

This host does not assert a positive D02 gate or create an F38 claim. The operator
attaches an actual F34-registered product run through the explicit F39 route.
"""

import asyncio
import shlex
import sys
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent
from native_f37_host import protected_config

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
from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.integration_control_service import IntegrationControlService
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_scheduler_service import TaskSchedulerService
from orchestrator.application.task_selection_service import TaskSelectionService
from orchestrator.application.teamplayer_reader import TeamPlayerReader
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.config import Settings
from orchestrator.domain.codex_mcp_launch import CodexMCPLaunch
from orchestrator.domain.coordinator_review import CoordinatorReviewPolicy
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.domain.worker_contracts import canonical_json
from orchestrator.event_log import EventLog
from orchestrator.mcp.principal import load_principal
from orchestrator.mcp.server import serve_stdio
from orchestrator.persistence.store import StateStore


def denied_factory(*args):
    raise RuntimeError("F39_PROBE_AUTOMATIC_CLAIM_DENIED")


@asynccontextmanager
async def composition(config_path):
    c = protected_config(config_path)
    settings = Settings.model_validate(c["settings"])
    coordinator = load_principal(Path(c["coordinator_profile"]), settings)
    integration = load_principal(Path(c["integration_profile"]), settings)
    policy = CoordinatorReviewPolicy.model_validate_json(canonical_json(c["policy"]))
    delivery_enabled = c.get("enable_delivery", False)
    policies = ([CoordinatorReviewPolicy.model_validate_json(canonical_json(p))
                 for p in c["review_policies"]] if delivery_enabled else [policy])
    root_settings = (settings.model_copy(update={"review_context": None})
                     if delivery_enabled else settings)
    native = tomllib.loads((Path.home() / ".codex/config.toml").read_text())["mcp_servers"][
        "teamplayer"
    ]
    headers = OperatorHeaders(command=tuple(shlex.split(native["http_headers_helper"])))
    async with teamplayer_connection(native["url"], headers, writable=True,
                                     timeout_seconds=30) as adapter:
        with StateStore(settings.sqlite_path) as store:
            codex = CodexAdapter()

            def herdr(role, sandbox):
                return HerdrAdapter(c["server"], sandbox=sandbox, mcp=CodexMCPLaunch(
                    command=c["python"], args=(c["host"], str(config_path), role),
                    disabled_servers=tuple(c["disabled_servers"]),
                    disabled_plugins=tuple(c["disabled_plugins"]),
                ))

            root_herdr = herdr("coordinator", "read-only")
            integration_herdr = herdr("integration", "read-only")
            workers = herdr("worker", "workspace-write")
            capabilities = {"coordinator_review": review_capability(policies)}
            if delivery_enabled:
                capabilities["coordinator_delivery"] = delivery_capability(
                    capabilities["coordinator_review"],
                )
            runtime = CoordinatorRuntimeService(
                root_settings, store, root_herdr, c["spec"], coordinator, c["run_id"], codex=codex,
                capabilities=capabilities,
            )
            # Real F34 creates and owns the Integration worktree/runtime. This sync
            # only covers its immutable original native bindings at registration.
            startup_sync = TeamPlayerSyncService(
                settings, store, adapter, project_id=policy.original.project_id,
                external_project_id=policy.native_project_id, user_id=policy.user_id,
                scopes={integration.epic_run_id: tuple(t.task.task_id
                                                      for t in policy.original.tasks)},
                codex=codex,
            )
            original = EpicStartService(
                settings, store, integration_herdr, policy.original, integration, codex,
                sync=startup_sync, external_epic_id=policy.native_epic_id,
            )
            log = EventLog(stream=sys.stderr)

            def factory(view):
                specs = {identity: entry.task.model_dump(mode="json")
                         for identity, entry in zip(view.native_task_ids, view.spec.tasks,
                                                    strict=True)}
                bindings = {policy.native_epic_id: LocalBoardBinding(
                    local_id=policy.original.epic_id,
                )} | {identity: LocalBoardBinding(local_id=spec["task_id"])
                      for identity, spec in specs.items()}
                reader = TeamPlayerReader(
                    adapter, project_id=policy.native_project_id, user_id=policy.user_id,
                    project_name="HerdrCoordinator", bindings=bindings,
                )
                selection = TaskSelectionService(
                    settings, store, reader, specs=specs, task_order=tuple(specs),
                    epic_prerequisites={policy.original.epic_id: ()}, codex=codex,
                )
                sync = TeamPlayerSyncService(
                    settings, store, adapter, project_id=policy.original.project_id,
                    external_project_id=policy.native_project_id, user_id=policy.user_id,
                    scopes={integration.epic_run_id: tuple(t["task_id"] for t in specs.values())},
                    codex=codex,
                )
                scheduler = TaskSchedulerService(settings, store, selection, sync, workers,
                                                  codex, review_provider=None, registration=view)
                completion = EpicCompletionService(
                    view, scheduler, completion_for(policy, view.spec),
                )
                return IntegrationControlService(view, scheduler, log, epic_completion=completion)

            creator = TeamPlayerCorrectionCreator(adapter, project_id=policy.native_project_id,
                                                  epic_id=policy.native_epic_id,
                                                  user_id=policy.user_id)
            subject = CoordinatorReviewSubject(runtime, original, policy, factory, creator)
            reviews = CoordinatorReviewService(runtime, {integration.epic_run_id: subject},
                                                policies=policies if delivery_enabled else None)
            control = subject.context()[0]
            claims_reader = control.scheduler.selection.reader
            if delivery_enabled:
                bindings = {p.native_epic_id: LocalBoardBinding(local_id=p.original.epic_id)
                            for p in policies}
                for p in policies:
                    bindings.update({identity: LocalBoardBinding(local_id=entry.task.task_id)
                                     for identity, entry in zip(p.native_task_ids,
                                                                p.original.tasks, strict=True)})
                spec, ids, _ = subject.plan.current()
                bindings.update({identity: LocalBoardBinding(local_id=entry.task.task_id)
                                 for identity, entry in zip(ids, spec.tasks, strict=True)})
                claims_reader = TeamPlayerReader(adapter, project_id=policy.native_project_id,
                                                  user_id=policy.user_id,
                                                  project_name="HerdrCoordinator",
                                                  bindings=bindings)
            claims = CoordinatorClaimService(
                runtime, claims_reader, denied_factory, runtime_gate=None,
                delivery_contexts={subject.run_id: settings},
                scopes={subject.run_id: tuple(t.task.task_id
                                             for t in subject.plan.current()[0].tasks)},
            )
            delivery = CoordinatorDeliveryService(reviews, claims) if delivery_enabled else None
            if delivery is not None:
                claims.completed_plan = delivery.completed_plan
            root_service = RuntimeService(
                store, coordinator, log,
                coordinator_control=CoordinatorControlService(claims, review=reviews,
                                                               delivery=delivery),
            )
            yield c, runtime, original, subject, reviews, root_service, control


class PerRequestRuntime:
    def __init__(self, metadata, path, role):
        self.metadata, self.path, self.role = metadata, path, role

    def __getattr__(self, name):
        return getattr(self.metadata, name)

    async def call_async(self, operation, arguments):
        # Reopen actual state and reconstruct the current verified membership;
        # cached pre-correction Integration views are intentionally invalidated.
        async with composition(self.path) as (_, _, _, _, _, root, integration):
            service = (root if self.role == "coordinator"
                       else integration.runtime(integration.delegate.log))
            return await service.call_async(operation, arguments)


async def worker_stdio():
    """Workers use their local shell only; no board or Coordinator MCP capability."""
    async def list_tools(ctx, params):
        return ListToolsResult(tools=[])

    async def call_tool(ctx, params):
        return CallToolResult(is_error=True, content=[TextContent(type="text", text="FORBIDDEN")])

    server = Server("F39WorkerNoControl", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def main():
    path, role = Path(sys.argv[1]), sys.argv[2]
    if role == "worker":
        await worker_stdio()
        return
    if role not in {"coordinator", "integration"}:
        raise RuntimeError("F39_HOST_ROLE_INVALID")
    async with composition(path) as (_, _, _, _, _, root, integration):
        metadata = root if role == "coordinator" else integration.runtime(integration.delegate.log)
    await serve_stdio(PerRequestRuntime(metadata, path, role))


if __name__ == "__main__":
    asyncio.run(main())
