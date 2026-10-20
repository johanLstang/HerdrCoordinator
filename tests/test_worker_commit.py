"""Actual Git/SQLite/claim/ACK; controlled native facts, never a kernel attestation."""

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client
from test_codex_execution import execution_policy  # noqa: F401
from test_task_start import setup as startup_setup  # noqa: F401

from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.worker_commit_service import WorkerCommitError, WorkerCommitService
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import TaskState
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server


@pytest.fixture
def broker(startup_setup, execution_policy):  # noqa: F811
    start, integration, spec, native, _ = startup_setup
    native.sandbox = "workspace-write"
    native.execution_policy = execution_policy
    native.mcp_fingerprint = execution_policy.fingerprint
    result = start.start(integration, integration.epic_run_id, spec)
    assert result["status"] == "CONFIRMED"
    task = start.store.get_task(result["task"]["id"])
    worker = Actor(actor_id="registered-worker", role=Role.WORKER, project_id=task.project_id,
                   epic_run_id=task.epic_run_id, task_run_id=task.id)
    service = WorkerCommitService(start.settings, start.store, native, worker, client_parent_pid=20)
    return SimpleNamespace(service=service, task=task, worker=worker, integration=integration,
                           start=start, native=native, path=Path(task.worktree_path))


def test_own_commit_and_repeat_preserve_other_refs_and_native_ownership(broker):
    b, s = broker, broker.service
    before = s.git.head("main"), s.git.head("feature/epic-e")
    (b.path / "result.py").write_bytes(b"answer = 42\n")
    result = s.commit(b.worker, "first", "Implement own result")
    assert result["status"] == "COMMITTED" and result["clean"]
    assert s.git.parents(result["commit"]) == (b.task.base_commit,)
    assert s.git.run("show", result["commit"] + ":result.py") == "answer = 42\n"
    changes = s.store.db.total_changes
    assert s.commit(b.worker, "first", "Implement own result") == result
    assert s.store.db.total_changes == changes
    assert (s.git.head("main"), s.git.head("feature/epic-e")) == before
    actual = s.store.get_task(b.task.id)
    assert actual.codex_session_id == b.task.codex_session_id and actual.worker_slot == 1
    assert str(actual.internal_status) == "WORKING" and actual.merge_commit is None
    assert s.status(b.worker)["commit"] == result["commit"]


@pytest.mark.parametrize("boundary", ["object", "ref", "index"])
def test_lost_response_recovers_same_commit_without_second_ref_effect(
    broker, monkeypatch, boundary,
):
    b, s = broker, broker.service
    (b.path / "result.txt").write_text("actual result\n")
    names = {"object": "worker_commit_object", "ref": "worker_update_ref",
             "index": "worker_sync_index"}
    name = names[boundary]
    original = getattr(s.git, name)
    calls, objects = [], []

    def lose(*args):
        value = original(*args)
        calls.append(args)
        if boundary == "object":
            objects.append(value)
        raise RuntimeError("Controlled response loss after actual Git effect")

    with monkeypatch.context() as patch:
        patch.setattr(s.git, name, lose)
        with pytest.raises(RuntimeError, match="response loss"):
            s.commit(b.worker, "stable", "Commit exact result")
    with pytest.raises(WorkerCommitError, match="ORIGINAL_KEY"):
        s.commit(b.worker, "replacement", "Commit exact result")
    result = s.commit(b.worker, "stable", "Commit exact result")
    assert result["clean"] and len(calls) == 1
    if objects:
        assert result["commit"] == objects[0]
    assert s.git.parents(result["commit"]) == (b.task.base_commit,)
    history = s.git.run("log", "--oneline", b.task.base_commit + ".." + result["commit"])
    assert len(history.splitlines()) == 1
    assert len(s.store.get_operations(b.task.epic_run_id, kind=s.KIND)) == 1


def test_drift_before_ref_does_not_commit_changed_files_or_discard_them(broker, monkeypatch):
    b, s = broker, broker.service
    result = b.path / "result.txt"
    result.write_text("reviewable original\n")
    original = s.git.worker_commit_object

    def change(*args):
        value = original(*args)
        result.write_text("concurrent new work\n")
        return value

    with monkeypatch.context() as patch:
        patch.setattr(s.git, "worker_commit_object", change)
        with pytest.raises(WorkerCommitError, match="INPUT_CHANGED"):
            s.commit(b.worker, "stable", "Own result")
    assert s.git.head(b.task.branch) == b.task.base_commit
    assert result.read_text() == "concurrent new work\n"
    result.write_text("reviewable original\n")
    assert s.commit(b.worker, "stable", "Own result")["clean"]


@pytest.mark.parametrize("kind", ["file_symlink", "directory_symlink", "metadata", "staged"])
def test_unsafe_input_is_rejected_without_ref_or_state_changes(broker, tmp_path, kind):
    b, s = broker, broker.service
    sentinel = tmp_path / "harmless-secret-sentinel"
    sentinel.write_text("must not be copied\n")
    if kind == "file_symlink":
        (b.path / "link").symlink_to(sentinel)
    elif kind == "directory_symlink":
        (b.path / "link").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "metadata":
        (b.path / ".codex").mkdir()
        (b.path / ".codex/config.toml").write_text("fake only\n")
    else:
        (b.path / "result.txt").write_text("already staged by somebody\n")
        s.git.run("add", "result.txt", cwd=b.path)
    changes = s.store.db.total_changes
    with pytest.raises(WorkerCommitError):
        s.commit(b.worker, "first", "Own result")
    assert s.store.db.total_changes == changes
    assert s.git.head(b.task.branch) == b.task.base_commit
    assert sentinel.read_text() == "must not be copied\n"
    assert not s.store.get_operations(b.task.epic_run_id, kind=s.KIND)


@pytest.mark.parametrize("kind", ["foreign_role", "foreign_task", "parent_pid", "fingerprint"])
def test_registered_role_process_and_configuration_are_required(broker, kind):
    b, s = broker, broker.service
    actor = b.worker
    if kind == "foreign_role":
        actor = b.integration
    elif kind == "foreign_task":
        actor = actor.model_copy(update={"task_run_id": "other"})
    elif kind == "parent_pid":
        s.client_parent_pid = 999999
    else:
        b.native.mcp_fingerprint = "0" * 64
    (b.path / "result.txt").write_text("own result\n")
    changes = s.store.db.total_changes
    with pytest.raises(WorkerCommitError):
        s.commit(actor, "stable", "Own result")
    assert s.store.db.total_changes == changes
    assert s.git.head(b.task.branch) == b.task.base_commit


def test_sdk_is_dedicated_to_own_workspace_and_commit_without_scope_arguments(broker):
    b, s = broker, broker.service
    runtime = RuntimeService(s.store, b.worker, EventLog(stream=io.StringIO()), worker_commit=s)
    (b.path / "result.txt").write_text("actual own result\n")

    async def run():
        async with Client(create_server(runtime)) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert set(tools) == {"worker_workspace", "worker_commit"}
            assert set(tools["worker_commit"].input_schema["properties"]) == {
                "request_key", "message",
            }
            changes = s.store.db.total_changes
            denied = await client.call_tool("worker_commit", dict(
                request_key="first", message="Own result", role="Coordinator", branch="main",
            ))
            assert denied.is_error and s.store.db.total_changes == changes
            committed = await client.call_tool("worker_commit", dict(
                request_key="first", message="Own result",
            ))
            assert not committed.is_error
            assert s.git.head(b.task.branch) != b.task.base_commit
            assert not s.git.working_changes(b.path)

    asyncio.run(run())


def test_next_commit_preserves_deletions_modes_bytes_and_never_invokes_filters(broker):
    b, s = broker, broker.service
    hook_marker = b.path.parent / "hook-must-not-run"
    hooks = s.git.common_dir / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\ntouch '" + str(hook_marker) + "'\nexit 9\n")
    hook.chmod(0o755)
    (b.path / ".gitattributes").write_text("*.txt filter=forbidden\n")
    (b.path / "--name,with space.txt").write_bytes(b"raw\r\n\x00bytes\n")
    first = s.commit(b.worker, "first", "Exact bytes")
    assert first["clean"]
    (b.path / "README.md").unlink()
    executable = b.path / "own.sh"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    second = s.commit(b.worker, "second", "Delete and add executable")
    assert second["clean"] and s.git.parents(second["commit"]) == (first["commit"],)
    assert "100755" in s.git.run("ls-tree", second["commit"], "own.sh")
    assert not s.git.run("ls-tree", second["commit"], "README.md")
    assert not hook_marker.exists()
    assert (b.path / "--name,with space.txt").read_bytes() == b"raw\r\n\x00bytes\n"


def test_configured_filter_is_denied_before_git_status_can_execute_it(broker):
    b, s = broker, broker.service
    marker = b.path.parent / "filter-must-not-run"
    s.git.run("config", "filter.forbidden.clean", "touch '" + str(marker) + "'")
    (b.path / ".gitattributes").write_text("*.txt filter=forbidden\n")
    (b.path / "result.txt").write_text("uncommitted original\n")
    changes = s.store.db.total_changes
    with pytest.raises(WorkerCommitError, match="FILTER_CONFIGURATION_DENIED"):
        s.commit(b.worker, "first", "Must be denied")
    assert s.store.db.total_changes == changes and not marker.exists()
    assert s.git.head(b.task.branch) == b.task.base_commit
    assert (b.path / "result.txt").read_text() == "uncommitted original\n"


def test_pending_commit_cannot_finish_after_worker_reported_ready(broker, monkeypatch):
    b, s = broker, broker.service
    (b.path / "result.txt").write_text("own result\n")
    original = s.git.worker_commit_object

    def lose(*args):
        original(*args)
        raise RuntimeError("lost response")

    with monkeypatch.context() as patch:
        patch.setattr(s.git, "worker_commit_object", lose)
        with pytest.raises(RuntimeError, match="lost response"):
            s.commit(b.worker, "first", "Own result")
    changed = s.store.get_task(b.task.id).model_copy(update={
        "internal_status": TaskState.READY_FOR_REVIEW,
    })
    s.store.db.execute("UPDATE task_runs SET internal_status=?, payload=? WHERE id=?",
                       (str(changed.internal_status), changed.model_dump_json(), b.task.id))
    changes = s.store.db.total_changes
    with pytest.raises(WorkerCommitError, match="NOT_WORKING"):
        s.commit(b.worker, "first", "Own result")
    assert s.store.db.total_changes == changes
    assert s.git.head(b.task.branch) == b.task.base_commit
    assert (b.path / "result.txt").read_text() == "own result\n"
