import asyncio
import io
import json
import subprocess
import sys
from unittest.mock import Mock

import pytest
from mcp import Client, StdioServerParameters

from orchestrator.application.runtime_service import RuntimeService
from orchestrator.config import load_settings
from orchestrator.domain.models import EpicRun, Review, TaskRun
from orchestrator.domain.policy import Actor, Role
from orchestrator.event_log import EventLog
from orchestrator.mcp.principal import PrincipalError, load_principal
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


def seed(store):
    store.add_epic(
        EpicRun(
            id="epic",
            project_id="p",
            epic_id="e",
            branch="feature/e",
            worktree_path="/tmp/test/epic",
        )
    )
    for run_id in ["own", "other"]:
        store.add_task(
            TaskRun(
                id=run_id,
                project_id="p",
                epic_run_id="epic",
                task_id=run_id,
                branch=f"task/e/{run_id}",
                worktree_path=f"/tmp/test/{run_id}",
            )
        )
    store.add_review(
        Review(
            task_run_id="own",
            review_number=1,
            review_result="CHANGES_REQUESTED",
            review_commit="a" * 40,
            epic_commit="b" * 40,
            feedback="fix this",
        )
    )
    store.add_review(
        Review(
            task_run_id="own",
            review_number=2,
            review_result="APPROVED",
            review_commit="a" * 40,
            epic_commit="b" * 40,
        )
    )


def worker():
    return Actor(
        actor_id="worker", role=Role.WORKER, project_id="p", epic_run_id="epic", task_run_id="own"
    )


@pytest.fixture
def runtime(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        seed(store)
        log_stream = io.StringIO()
        yield store, RuntimeService(store, worker(), EventLog(stream=log_stream)), log_stream


def test_registered_worker_reads_own_runtime_and_cannot_impersonate(runtime):
    store, service, logs = runtime

    async def exercise():
        async with Client(create_server(service)) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {"runtime_status", "policy_check"}
            good = await client.call_tool(
                "runtime_status", {"project_id": "p", "task_run_id": "own"}
            )
            assert good.structured_content["data"]["task_id"] == "own"
            assert not good.is_error
            bad = await client.call_tool(
                "runtime_status",
                {
                    "project_id": "p",
                    "task_run_id": "own",
                    "role": "Coordinator",
                    "secret": "planted-secret-123",
                },
            )
            assert bad.is_error
            assert bad.structured_content["code"] == "INVALID_ARGUMENT"
            assert "planted-secret-123" not in str(bad)
            policy = await client.call_tool(
                "policy_check",
                {
                    "project_id": "p",
                    "task_run_id": "own",
                    "operation": "epic_merge",
                },
            )
            assert policy.structured_content["code"] == "FORBIDDEN"
            unknown = await client.call_tool("task_merge", {"project_id": "p"})
            assert unknown.structured_content["code"] == "UNKNOWN_OPERATION"

    changes = store.db.total_changes
    asyncio.run(asyncio.wait_for(exercise(), timeout=15))
    assert store.db.total_changes == changes
    assert service.actor.role == Role.WORKER
    assert "planted-secret-123" not in logs.getvalue()


@pytest.mark.parametrize(
    "arguments",
    [
        {"project_id": "other", "task_run_id": "own"},
        {"project_id": "p", "task_run_id": "other"},
        {"project_id": "p", "epic_run_id": "epic"},
    ],
)
def test_wrong_scope_returns_no_data_or_database_changes(runtime, arguments):
    store, service, _ = runtime
    changes = store.db.total_changes
    result = service.call("runtime_status", arguments)
    assert result.code == "FORBIDDEN"
    assert result.data == {}
    assert store.db.total_changes == changes


def test_unregistered_connection_is_denied_before_store_access(runtime, monkeypatch):
    store, _, _ = runtime
    lookup = Mock(wraps=store.get_task)
    monkeypatch.setattr(store, "get_task", lookup)
    service = RuntimeService(store, None, EventLog(stream=io.StringIO()))
    changes = store.db.total_changes
    result = service.call("runtime_status", {"project_id": "p", "task_run_id": "own"})
    assert result.code == "UNAUTHENTICATED"
    lookup.assert_not_called()
    assert store.db.total_changes == changes


def test_authorized_operation_is_unavailable_until_service_exists(runtime):
    store, _, _ = runtime
    service = RuntimeService(
        store,
        Actor(actor_id="integration", role=Role.INTEGRATION, project_id="p", epic_run_id="epic"),
        EventLog(stream=io.StringIO()),
    )
    result = service.call(
        "policy_check",
        {
            "project_id": "p",
            "epic_run_id": "epic",
            "operation": "task_merge",
        },
    )
    assert result.code == "NOT_IMPLEMENTED"
    assert not result.ok


@pytest.fixture
def launch(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    config = tmp_path / "config.toml"
    config.write_text('repository="repo"\nworktree_root="trees"\nsqlite_path="state.db"\n')
    principal = tmp_path / "principal.json"
    principal.write_text(worker().model_dump_json())
    principal.chmod(0o600)
    with StateStore(tmp_path / "state.db") as store:
        seed(store)
    return config, principal


def test_principal_file_is_protected_operator_input(launch):
    config, path = launch
    assert load_principal(path, load_settings(config)).role == Role.WORKER
    path.chmod(0o644)
    with pytest.raises(PrincipalError, match="owner-only"):
        load_principal(path, load_settings(config))


def test_principal_in_repository_is_rejected(launch):
    config, path = launch
    in_repo = config.parent / "repo/principal.json"
    in_repo.write_text(path.read_text())
    in_repo.chmod(0o600)
    with pytest.raises(PrincipalError, match="outside"):
        load_principal(in_repo, load_settings(config))


def test_stdio_mcp_restart_preserves_runtime_and_review_history(launch):
    config, principal = launch
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "orchestrator",
            "--config",
            str(config),
            "--mcp",
            "--principal",
            str(principal),
        ],
    )

    async def exercise():
        for _ in range(2):
            async with Client(parameters) as client:
                result = await client.call_tool(
                    "runtime_status",
                    {
                        "project_id": "p",
                        "task_run_id": "own",
                    },
                )
                assert result.structured_content["data"]["branch"] == "task/e/own"
                assert result.structured_content["data"]["internal_status"] == "PLANNED"
                bad = await client.call_tool(
                    "runtime_status",
                    {
                        "project_id": "p",
                        "task_run_id": "other",
                    },
                )
                assert bad.structured_content["code"] == "FORBIDDEN"

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
    with StateStore(config.parent / "state.db") as store:
        assert store.get_epic("epic").epic_id == "e"
        assert [review.review_result for review in store.get_reviews("own")] == [
            "CHANGES_REQUESTED",
            "APPROVED",
        ]
        assert store.get_task("other").branch == "task/e/other"


def test_stdio_unregistered_connection_has_documented_error(launch):
    config, _ = launch

    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "orchestrator",
                "--config",
                str(config),
                "--mcp",
            ],
        )
        async with Client(parameters) as client:
            result = await client.call_tool(
                "runtime_status",
                {
                    "project_id": "p",
                    "task_run_id": "own",
                },
            )
            assert result.structured_content["code"] == "UNAUTHENTICATED"

    asyncio.run(asyncio.wait_for(exercise(), timeout=15))


def test_principal_validation_does_not_echo_secret(launch):
    config, path = launch
    path.write_text(json.dumps({"role": "planted-secret-123"}))
    with pytest.raises(PrincipalError) as error:
        load_principal(path, load_settings(config))
    assert "planted-secret-123" not in str(error.value)
