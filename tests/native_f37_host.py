"""Protected operator factory for the F37 real native proof (not a pytest fixture)."""

import asyncio
import json
import os
import shlex
import stat
import sys
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.herdr import HerdrAdapter
from orchestrator.adapters.teamplayer_mcp import OperatorHeaders, teamplayer_connection
from orchestrator.application.epic_completion_service import EpicCompletionService
from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.integration_control_service import IntegrationControlService
from orchestrator.application.task_scheduler_service import TaskSchedulerService
from orchestrator.application.task_selection_service import TaskSelectionService
from orchestrator.application.teamplayer_reader import TeamPlayerReader
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.config import Settings
from orchestrator.domain.codex_mcp_launch import CodexMCPLaunch
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.event_log import EventLog
from orchestrator.mcp.principal import load_principal
from orchestrator.mcp.server import serve_stdio
from orchestrator.persistence.store import StateStore


def protected_config(path):
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeError("PROBE_CONFIG_PATH_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > 1 << 20
        ):
            raise RuntimeError("PROBE_CONFIG_PROTECTION_INVALID")
        with os.fdopen(os.dup(fd)) as stream:
            return json.load(stream)
    finally:
        os.close(fd)


@asynccontextmanager
async def composition(config_path):
    c = protected_config(config_path)
    settings = Settings.model_validate(c["settings"])
    integration = load_principal(Path(c["profile"]), settings)
    native = tomllib.loads((Path.home() / ".codex/config.toml").read_text())["mcp_servers"][
        "teamplayer"
    ]
    helper = native["http_headers_helper"]
    if not isinstance(helper, str):
        raise RuntimeError("PROBE_AUTH_HELPER_INVALID")
    headers = OperatorHeaders(command=tuple(shlex.split(helper)))
    async with teamplayer_connection(
        native["url"], headers, writable=True, timeout_seconds=30
    ) as board:
        with StateStore(settings.sqlite_path) as store:
            codex = CodexAdapter()
            local_project, local_epic = c["spec"]["project_id"], c["spec"]["epic_id"]
            bindings = {c["native_epic"]: LocalBoardBinding(local_id=local_epic)} | {
                k: LocalBoardBinding(local_id=s["task_id"]) for k, s in c["specs"].items()
            }
            reader = TeamPlayerReader(
                board,
                project_id=c["native_project"],
                user_id=c["user"],
                project_name="HerdrCoordinator",
                bindings=bindings,
            )
            sync = TeamPlayerSyncService(
                settings,
                store,
                board,
                project_id=local_project,
                external_project_id=c["native_project"],
                user_id=c["user"],
                scopes={integration.epic_run_id: tuple(s["task_id"] for s in c["specs"].values())},
                codex=codex,
            )
            h = HerdrAdapter(
                c["server"],
                sandbox="read-only",
                mcp=CodexMCPLaunch(command=c["python"], args=(c["host"], str(config_path))),
            )
            workers = HerdrAdapter(c["server"], sandbox="workspace-write")
            epic = EpicStartService(
                settings,
                store,
                h,
                c["spec"],
                integration,
                codex,
                sync=sync,
                external_epic_id=c["native_epic"],
            )
            selection = TaskSelectionService(
                settings,
                store,
                reader,
                specs=c["specs"],
                task_order=tuple(c["specs"]),
                epic_prerequisites={local_epic: ()},
                codex=codex,
            )
            scheduler = TaskSchedulerService(
                settings, store, selection, sync, workers, codex, review_provider=None
            )
            completion = EpicCompletionService(epic, scheduler, c["completion"])
            log = EventLog(stream=sys.stderr)
            control = IntegrationControlService(epic, scheduler, log, epic_completion=completion)
            coordinator = Actor(
                actor_id=c["coordinator"], role=Role.COORDINATOR, project_id=local_project
            )
            yield c, settings, store, control, h, coordinator


class PerRequestRuntime:
    """Only transport is refreshed: actual scopes/run/SID/config are revalidated every call."""

    def __init__(self, metadata, config_path):
        self.metadata, self.config_path = metadata, config_path

    def __getattr__(self, name):
        return getattr(self.metadata, name)

    async def call_async(self, operation, arguments):
        async with composition(self.config_path) as (_, _, _, control, _, _):
            return await control.runtime(EventLog(stream=sys.stderr)).call_async(
                operation, arguments
            )


async def main():
    path = Path(sys.argv[1])
    async with composition(path) as (_, _, _, control, _, _):
        metadata = control.runtime(EventLog(stream=sys.stderr))
    await serve_stdio(PerRequestRuntime(metadata, path))


if __name__ == "__main__":
    asyncio.run(main())
