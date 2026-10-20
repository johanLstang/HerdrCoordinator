"""Operator host: actual Coordinator and board, absent D02 gate; no start factory effect."""

import asyncio
import shlex
import sys
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from native_f37_host import protected_config

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.herdr import HerdrAdapter
from orchestrator.adapters.teamplayer_mcp import OperatorHeaders, teamplayer_connection
from orchestrator.application.coordinator_claim_service import CoordinatorClaimService
from orchestrator.application.coordinator_control_service import CoordinatorControlService
from orchestrator.application.coordinator_runtime_service import CoordinatorRuntimeService
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.teamplayer_reader import TeamPlayerReader
from orchestrator.config import Settings
from orchestrator.domain.codex_mcp_launch import CodexMCPLaunch
from orchestrator.domain.coordinator_contracts import CoordinatorSpec
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.event_log import EventLog
from orchestrator.mcp.principal import load_principal
from orchestrator.mcp.server import serve_stdio
from orchestrator.persistence.store import StateStore


def denied_start_factory(*args):
    raise RuntimeError("F38_NATIVE_UNEXPECTED_START_FACTORY")


@asynccontextmanager
async def composition(config_path):
    config = protected_config(config_path)
    settings = Settings.model_validate(config["settings"])
    principal = load_principal(Path(config["profile"]), settings)
    spec = CoordinatorSpec.model_validate(config["spec"])
    native = tomllib.loads((Path.home() / ".codex/config.toml").read_text())["mcp_servers"][
        "teamplayer"
    ]
    headers = OperatorHeaders(command=tuple(shlex.split(native["http_headers_helper"])))
    async with teamplayer_connection(
        native["url"], headers, writable=True, timeout_seconds=30,
    ) as adapter:
        with StateStore(settings.sqlite_path) as store:
            launch = CodexMCPLaunch(
                command=config["python"], args=(config["host"], str(config_path)),
                disabled_servers=tuple(config["disabled_servers"]),
                disabled_plugins=tuple(config["disabled_plugins"]),
            )
            herdr = HerdrAdapter(config["server"], sandbox="read-only", mcp=launch)
            runtime = CoordinatorRuntimeService(
                settings, store, herdr, spec, principal, config["run_id"], codex=CodexAdapter(),
            )
            bindings = {}
            for epic in spec.epics:
                bindings[epic.native_epic_id] = LocalBoardBinding(local_id=epic.spec.epic_id)
                for identity, entry in zip(epic.native_task_ids, epic.spec.tasks, strict=True):
                    bindings[identity] = LocalBoardBinding(local_id=entry.task.task_id)
            reader = TeamPlayerReader(
                adapter, project_id=spec.native_project_id, user_id=config["user"],
                project_name="HerdrCoordinator", bindings=bindings,
            )
            # No operator assertion of a secure OS: the actual default denial is under test.
            claims = CoordinatorClaimService(
                runtime, reader, denied_start_factory, runtime_gate=None
            )
            service = RuntimeService(
                store, principal, EventLog(stream=sys.stderr),
                coordinator_control=CoordinatorControlService(claims),
            )
            yield config, runtime, claims, service


class PerRequestRuntime:
    def __init__(self, metadata, path):
        self.metadata, self.path = metadata, path

    def __getattr__(self, name):
        return getattr(self.metadata, name)

    async def call_async(self, operation, arguments):
        async with composition(self.path) as (_, _, _, runtime):
            return await runtime.call_async(operation, arguments)


async def main():
    path = Path(sys.argv[1])
    async with composition(path) as (_, _, _, service):
        metadata = service
    await serve_stdio(PerRequestRuntime(metadata, path))


if __name__ == "__main__":
    asyncio.run(main())
