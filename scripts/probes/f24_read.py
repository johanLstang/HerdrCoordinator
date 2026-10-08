"""Read-only operator native proof of F24 against the pinned E06 fixture."""

import asyncio
import json
import logging
import shlex
import sys
import tomllib
from pathlib import Path

from orchestrator.adapters.teamplayer_mcp import OperatorHeaders, teamplayer_connection
from orchestrator.application.teamplayer_reader import TeamPlayerReader
from orchestrator.domain.teamplayer import LocalBoardBinding

PROJECT = "d2ee4c75-7b80-465f-83ac-1750854a8e80"
USER = "105f26a7-0648-438d-94fd-3260ac3af4ee"
EPIC = "8051e9de-f4dc-4277-8f80-62b1c36c4690"
TASKS = {"f29251bf-a8c0-4c42-b3a4-1d5a426193db", "c650f6d0-ffff-4ca0-a772-16c59af8baf2"}


async def main():
    config = tomllib.loads((Path.home() / ".codex/config.toml").read_text())["mcp_servers"][
        "teamplayer"
    ]
    provider = OperatorHeaders(
        command=tuple(shlex.split(config.get("http_headers_helper", ""))),
        bearer_env=config.get("bearer_token_env_var"),
    )
    bindings = {
        EPIC: LocalBoardBinding(local_id="test-e06", sources=("docs/teamplayer/F-23-kontrakt.md",))
    }
    async with teamplayer_connection(config["url"], provider) as adapter:
        reader = TeamPlayerReader(
            adapter,
            project_id=PROJECT,
            user_id=USER,
            project_name="HerdrCoordinator",
            bindings=bindings,
        )
        first = await reader.read_project()
        second = await reader.read_project(previous=first)
        assert first == second
        tasks = first.epic_tasks(EPIC)
        assert {t.id for t in tasks} == TASKS
        for task in tasks:
            assert task == await reader.get_task(task.id)
        assert first.epic(EPIC).name.startswith("[TEST] E06 TeamPlayer MCP-kontrakt")
        proof = {
            "projectId": PROJECT,
            "userId": USER,
            "epicId": EPIC,
            "epicCount": len(first.epics),
            "taskCount": len(first.tasks),
            "repeatIdentical": True,
            "binding": first.epic(EPIC).binding.model_dump(),
            "tasks": [t.model_dump(mode="json", by_alias=True) for t in tasks],
            "fixtureIssues": [i.model_dump() for i in first.issues if i.task_id in TASKS],
        }
        path = Path(".herdr/probes/f24")
        path.mkdir(parents=True, exist_ok=True)
        (path / "proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(proof, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)  # Probe process only; no HTTP/SDK credential diagnostics.
    try:
        asyncio.run(main())
    except Exception:
        print("F24_READ_FAILED: inspect identity/contract; raw errors withheld", file=sys.stderr)
        raise SystemExit(1) from None
