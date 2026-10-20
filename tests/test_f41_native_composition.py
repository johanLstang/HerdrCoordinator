"""Composition contract checks; controlled native facts are not runtime acceptance."""

import asyncio
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from mcp import Client
from native_f37_host import protected_config
from native_f41_host import WORKER_TOOLS, PerRequestRuntime, scoped_service
from native_f41_probe import require_approval
from native_f41_setup import prepare
from test_codex_execution import execution_policy  # noqa: F401
from test_epic_start import setup as epic_setup  # noqa: F401
from test_task_start import setup as startup_setup  # noqa: F401
from test_worker_commit import broker  # noqa: F401

from orchestrator.config import Settings
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server


def test_worker_catalog_handshakes_before_ack_but_call_rechecks_actual_runtime(broker):  # noqa: F811
    b, s = broker, broker.service
    env = SimpleNamespace(store=s.store, settings=s.settings, workers=b.native,
                          log=EventLog(stream=io.StringIO()))
    pending = s.store.get_operation(b.task.project_id, "task_start", b.task.task_id)
    s.store.update_operation(pending.model_copy(update={"status": "PENDING"}))
    metadata = scoped_service(env, "worker", str(b.path), 20, metadata_only=True)

    async def catalog():
        async with Client(create_server(metadata)) as client:
            assert {t.name for t in (await client.list_tools()).tools} == set(WORKER_TOOLS)

    asyncio.run(catalog())
    result = metadata.call("worker_commit", dict(request_key="first", message="own result"))
    assert not result.ok and result.code == "WORKER_COMMIT_ASSIGNMENT_UNVERIFIED"
    assert not s.store.get_operations(b.task.epic_run_id, kind=s.KIND)
    with pytest.raises(RuntimeError, match="STDIO_PARENT_UNVERIFIED"):
        scoped_service(env, "worker", str(b.path), 999999)


def test_setup_is_closed_idempotence_safe_and_cannot_start_without_user_decision(
    execution_policy, tmp_path,  # noqa: F811
):  # noqa: F811
    root = tmp_path / "bounded fixture"
    ids = dict(epics=[str(uuid4()), str(uuid4())],
               tasks=[[str(uuid4()) for _ in range(3)], [str(uuid4())]])
    path = prepare(root, ids, execution_policy.binary)
    config = protected_config(path)
    assert not Path(config["approval"]).exists() and not Path(config["gate_receipt"]).exists()
    settings = Settings.model_validate(config["settings"])
    assert settings.max_workers == 2 and not settings.sqlite_path.exists()
    assert len(config["source_pins"]) > 70
    assert json.loads((root / "board-ids.json").read_text()) == ids
    original = path.read_bytes()
    with pytest.raises(FileNotFoundError):
        require_approval(config)
    with pytest.raises(RuntimeError, match="NO_OVERWRITE"):
        prepare(root, ids, execution_policy.binary)
    assert path.read_bytes() == original


def test_wrapped_native_ack_is_denied_with_retryable_transport_without_scheduling(
    epic_setup, monkeypatch,  # noqa: F811
):
    start, coordinator, integration, native = epic_setup
    prompt = native.prompt

    def wrapped(*args, **kwargs):
        prompt(*args, **kwargs)
        item = native.turns[-1]["items"][-1]
        item["text"] = json.dumps(dict(confirmation=json.loads(item["text"])))

    monkeypatch.setattr(native, "prompt", wrapped)
    result = asyncio.run(start.start(coordinator, "p", "E-34", "e"))
    assert result["stage"] == "DISPATCH_REQUESTED"
    run = start.store.get_epic(integration.epic_run_id)
    env = SimpleNamespace(store=start.store,
                          subjects={run.id: SimpleNamespace(original_start=start)})

    @asynccontextmanager
    async def composed(_):
        yield env

    monkeypatch.setattr("native_f41_host.composition", composed)
    transport = PerRequestRuntime(None, Path("/operator/config"), "integration",
                                  run.worktree_path, 20)
    changes = start.store.db.total_changes
    response = asyncio.run(transport.call_async("task_schedule", dict(epic_run_id=run.id)))
    assert not response.ok and response.code == "INTEGRATION_CONNECTION_NOT_REGISTERED"
    assert start.store.db.total_changes == changes and start.store.get_tasks(run.id) == []
    assert native.starts == len(native.prompts) == 1
