"""F34: real Git/SQLite, controlled native boundary; actual CLI proof is separate."""

import asyncio
import io
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier, Event

import pytest
from mcp import Client
from pydantic import ValidationError
from test_runtime_start import FakeHerdr

from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.epic_start_service import EpicStartError, EpicStartService
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.config import Settings
from orchestrator.domain.integration_contracts import EpicIntegrationSpec
from orchestrator.domain.models import utc_now
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import EpicState
from orchestrator.domain.worker_contracts import canonical_json
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore

SID = "00000000-0000-4000-8000-000000000034"


def spec():
    task = dict(
        version=1, project_id="p", epic_id="E-34", task_id="F-01", name="One",
        goal="Deliver one harmless fixture", requirements=["Stay scoped"], scope=["README"],
        out_of_scope=["main merge"], acceptance_criteria=["Verified"], sources=["README.md"],
        verification_steps=["Read README"],
    )
    return dict(
        version=1, project_id="p", epic_id="E-34", title="Fixture", goal="Scoped integration",
        requirements=["Exactly one Integration"], acceptance_criteria=["Native receipt"],
        sources=["README.md"], project_instructions=["Use services"], dependencies=["E-33"],
        external_prerequisites=["No implementation in this start probe"],
        tasks=[dict(version=1, priority="P0", task=task), dict(
            version=1, priority="P1", task=task | {"task_id": "F-02", "dependencies": ["F-01"]}
        )],
    )


class Native(FakeHerdr):
    def __init__(self):
        super().__init__()
        self.sid, self.turns, self.prompts = None, [], []
        self.ack, self.unknown, self.process_changed = True, False, False

    def verify_agent(self, *args):
        return super().verify_agent(*args) | {
            "session_id": self.sid,
            "processes": [{"pid": 20, "start_time": "changed" if self.process_changed else "123"}],
        }

    def prompt(self, name, text, *, timeout_ms):
        assert name in self.agents and timeout_ms == 45000
        self.prompts.append(text)
        self.sid = SID
        self.turns.append(dict(id="turn-1", status="completed", items=[
            dict(id="user-1", type="userMessage", content=[dict(type="text", text=text)]),
            dict(id="agent-1", type="agentMessage", text=canonical_json(
                json.loads(text)["confirmation"] if self.ack else {"status": "wrong"}
            )),
        ]))
        if self.unknown:
            raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

    def read_thread(self, sid, cwd):
        return dict(id=sid, sessionId=sid, cwd=cwd, turns=deepcopy(self.turns))


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    for argv in (["init", "-b", "main"], ["config", "user.name", "Test"],
                 ["config", "user.email", "test@example.invalid"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True)
    (repo / "README.md").write_text("Harmless fixture\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"],
                   check=True, capture_output=True)
    settings = Settings(repository=repo, worktree_root=tmp_path / "trees",
                        sqlite_path=tmp_path / "state.db", max_workers=2)
    c = Actor(actor_id="c", role=Role.COORDINATOR, project_id="p")
    i = Actor(actor_id="i", role=Role.INTEGRATION, project_id="p", epic_run_id="e")
    h = Native()
    with StateStore(settings.sqlite_path) as store:
        yield EpicStartService(settings, store, h, spec(), i, codex=h), c, i, h


def start(s, c):
    return asyncio.run(s.start(c, "p", "E-34", "e"))


def test_complete_context_native_receipt_single_owner_and_reopen(setup):
    s, c, i, h = setup
    result = start(s, c)
    assert result["stage"] == "REGISTERED" and result["session_id"] == SID
    assert s.connection_principal(i) == i
    epic = s.store.get_epic("e")
    assert epic.status == EpicState.ACTIVE and epic.completed_at is None
    assert s.store.get_tasks("e") == []
    payload = json.loads(h.prompts[0])
    assert payload["spec"] == EpicIntegrationSpec.model_validate(spec()).model_dump(mode="json")
    assert payload["context"]["current_commit"] == s.git.head(epic.branch)
    assert payload["context"]["branch"] == epic.branch
    assert payload["context"]["worktree_path"] == epic.worktree_path
    assert payload["context"]["integration_principal"] == i.model_dump(mode="json")
    assert "Never start another epic" in payload["policy"]
    with StateStore(s.settings.sqlite_path) as db:
        reopened = EpicStartService(s.settings, db, h, spec(), i, codex=h)
        assert start(reopened, c) == result
        assert reopened.connection_principal(i) == i
    assert start(s, c) == result
    assert h.starts == len(h.workspaces) == len(h.prompts) == 1
    assert len(s.store.get_operations("e", kind=s.KIND)) == 1
    assert s.store.db.execute(
        "SELECT COUNT(*) FROM task_runs WHERE json_extract(payload, '$.worker_slot') IS NOT NULL"
    ).fetchone()[0] == 0


@pytest.mark.parametrize("role,project,epic,run", [
    (Role.INTEGRATION, "p", "E-34", "e"), (Role.WORKER, "p", "E-34", "e"),
    (Role.COORDINATOR, "other", "E-34", "e"),
    (Role.COORDINATOR, "p", "other", "e"), (Role.COORDINATOR, "p", "E-34", "other"),
])
def test_wrong_scope_before_any_effect(setup, role, project, epic, run):
    s, _, _, h = setup
    a = Actor(actor_id="bad", role=role, project_id=project)
    changes = s.store.db.total_changes
    with pytest.raises(EpicStartError, match="SCOPE_DENIED"):
        asyncio.run(s.start(a, project, epic, run))
    assert s.store.db.total_changes == changes
    assert not h.workspaces and not h.starts and not h.prompts


def test_unknown_prompt_response_reconciles_without_redispatch(setup):
    s, c, _, h = setup
    h.unknown = True
    assert start(s, c)["stage"] == "REGISTERED"
    assert start(s, c)["stage"] == "REGISTERED"
    assert len(h.prompts) == 1
    assert s.store.get_operation("p", s.KIND, "e").result["transport"] == "UNKNOWN"


def test_missing_ack_keeps_original_deadline_and_never_resends(setup):
    s, c, i, h = setup
    h.ack = False
    assert start(s, c)["stage"] == "DISPATCH_REQUESTED"
    op = s.store.get_operation("p", s.KIND, "e")
    with pytest.raises(EpicStartError, match="NOT_REGISTERED"):
        s.connection_principal(i)
    assert start(s, c)["stage"] == "DISPATCH_REQUESTED"
    assert s.store.get_operation("p", s.KIND, "e").result["deadline"] == op.result["deadline"]
    expired = op.model_copy(update={"result": op.result | {
        "deadline": (utc_now() - timedelta(seconds=1)).isoformat()
    }})
    s.store.update_operation(expired)
    with pytest.raises(EpicStartError, match="ACK_TIMEOUT"):
        start(s, c)
    with pytest.raises(EpicStartError, match="ACK_TIMEOUT"):
        start(s, c)
    assert h.starts == len(h.prompts) == 1
    assert s.store.get_operation("p", s.KIND, "e").result["stage"] == "ACK_TIMEOUT"


@pytest.mark.parametrize("change", ["duplicate", "boolean", "foreign", "baseline", "no-user"])
def test_native_ack_must_be_strict_correlated_new_and_delivered(setup, change):
    s, c, _, h = setup
    h.ack = False
    start(s, c)
    op = s.store.get_operation("p", s.KIND, "e")
    text = canonical_json(op.result["expected_ack"])
    if change == "duplicate":
        text = text[:-1] + ',"version":1}'
    if change == "boolean":
        text = text.replace('"version":1', '"version":true')
    if change == "foreign":
        text = text.replace('"epic_run_id":"e"', '"epic_run_id":"other"')
    if change == "baseline":
        s.store.update_operation(op.model_copy(update={"result": op.result | {
            "baseline_turns": ["turn-1"]
        }}))
    if change == "no-user":
        h.turns[0]["items"][0]["content"][0]["text"] = "different prompt"
    h.turns[0]["items"][1]["text"] = text
    assert start(s, c)["stage"] == "DISPATCH_REQUESTED"
    assert len(h.prompts) == 1


@pytest.mark.parametrize("field", ["goal", "principal", "server", "sandbox", "actor"])
def test_changed_configuration_owner_cannot_adopt_existing_session(setup, field):
    s, c, i, h = setup
    start(s, c)
    cfg, principal = spec(), i
    if field == "goal":
        cfg["goal"] = "different scope"
    if field == "principal":
        principal = i.model_copy(update={"actor_id": "another owner"})
    if field == "server":
        h.server_session = "foreign-session"
    if field == "sandbox":
        h.sandbox = "workspace-write"
    if field == "actor":
        c = c.model_copy(update={"actor_id": "different coordinator"})
    other = EpicStartService(s.settings, s.store, h, cfg, principal, codex=h)
    with pytest.raises(EpicStartError, match="INTENT_CHANGED"):
        start(other, c)
    assert h.starts == len(h.prompts) == 1


@pytest.mark.parametrize("change", ["sid", "process", "prompt", "ack", "head"])
def test_changed_runtime_or_assignment_is_not_registration(setup, change):
    s, c, _, h = setup
    start(s, c)
    if change == "sid":
        h.sid = "00000000-0000-4000-8000-000000000099"
    if change == "process":
        h.process_changed = True
    if change == "prompt":
        op = s.store.get_operation("p", s.KIND, "e")
        s.store.update_operation(op.model_copy(update={"result": op.result | {"prompt": "{}"}}))
    if change == "ack":
        h.turns[0]["items"][1]["text"] = "{}"
    if change == "head":
        epic = s.store.get_epic("e")
        subprocess.run(["git", "-C", epic.worktree_path, "checkout", "--orphan", "orphan"],
                       check=True, capture_output=True)
    with pytest.raises(EpicStartError):
        start(s, c)
    assert h.starts == len(h.prompts) == 1


@pytest.mark.parametrize("stage", ["PREPARING", "START_PENDING", "RUNTIME_READY",
                                   "DISPATCH_REQUESTED", "REGISTERED"])
def test_crash_after_durable_stage_reuses_known_resources(setup, monkeypatch, stage):
    s, c, _, h = setup
    original = s._save
    fired = False

    def save(*args, **kwargs):
        nonlocal fired
        result = original(*args, **kwargs)
        if kwargs.get("stage") == stage and not fired:
            fired = True
            # BaseException models process death without application's error handler.
            raise KeyboardInterrupt
        return result

    if stage == "PREPARING":
        original_journal = s._journal

        def journal(*args):
            original_journal(*args)
            raise KeyboardInterrupt

        monkeypatch.setattr(s, "_journal", journal)
    else:
        monkeypatch.setattr(s, "_save", save)
    with pytest.raises(KeyboardInterrupt):
        start(s, c)
    with StateStore(s.settings.sqlite_path) as db:
        reopened = EpicStartService(s.settings, db, h, spec(), s.principal, codex=h)
        result = start(reopened, c)
        if stage == "DISPATCH_REQUESTED":
            assert result["stage"] == "DISPATCH_REQUESTED" and not h.prompts
        else:
            assert result["stage"] == "REGISTERED"
    assert h.starts <= 1 and len(h.workspaces) <= 1 and len(h.prompts) <= 1


def test_competing_start_locked_before_native_or_git_effect(setup):
    s, c, _, h = setup
    with s._lock():
        with StateStore(s.settings.sqlite_path) as db:
            competing = EpicStartService(s.settings, db, h, spec(), s.principal, codex=h)
            with pytest.raises(EpicStartError, match="BUSY"):
                start(competing, c)
    assert not h.starts and not h.workspaces and s.store.get_epic("e") is None
    assert start(s, c)["stage"] == "REGISTERED"


def test_sdk_registration_scope_and_role_arguments(setup):
    s, c, i, h = setup
    s.worktrees.create_epic_worktree(c, epic_id="E-other", run_id="other")
    logs = io.StringIO()
    runtime = RuntimeService(s.store, c, EventLog(stream=logs), epic_start=s)

    async def exercise():
        args = dict(project_id="p", epic_id="E-34", epic_run_id="e")
        async with Client(create_server(runtime)) as client:
            assert "epic_start" in {t.name for t in (await client.list_tools()).tools}
            invalid = await client.call_tool("epic_start", args | {"role": "Coordinator"})
            assert invalid.structured_content["code"] == "INVALID_ARGUMENT"
            assert not h.starts
            good = await client.call_tool("epic_start", args)
            assert good.structured_content["ok"] and not good.is_error
        integration = RuntimeService(
            s.store, s.connection_principal(i), EventLog(stream=logs), epic_start=s
        )
        changes = s.store.db.total_changes
        async with Client(create_server(integration)) as client:
            own = await client.call_tool("runtime_status", dict(project_id="p", epic_run_id="e"))
            assert own.structured_content["ok"]
            other = await client.call_tool(
                "runtime_status", dict(project_id="p", epic_run_id="other")
            )
            assert other.structured_content["code"] == "FORBIDDEN"
            for operation in ("epic_start", "epic_merge"):
                policy = await client.call_tool("policy_check", dict(
                    project_id="p", epic_run_id="e", operation=operation
                ))
                assert policy.structured_content["code"] == "FORBIDDEN"
            denied = await client.call_tool("epic_start", args)
            assert denied.structured_content["code"] == "FORBIDDEN"
        assert s.store.db.total_changes == changes

    asyncio.run(asyncio.wait_for(exercise(), timeout=20))
    assert h.starts == len(h.prompts) == 1


def test_two_concurrent_start_calls_have_one_owner(setup):
    s, c, i, h = setup
    barrier, prepared, contender_done = Barrier(2), Event(), Event()

    def invoke(owner):
        with StateStore(s.settings.sqlite_path) as db:
            service = EpicStartService(s.settings, db, h, spec(), i, codex=h)
            if owner:
                original = service._journal

                def journal(actor):
                    op = original(actor)
                    prepared.set()
                    assert contender_done.wait(10)
                    return op

                service._journal = journal
            barrier.wait(timeout=10)
            if owner:
                return start(service, c)
            assert prepared.wait(10)
            try:
                with pytest.raises(EpicStartError, match="BUSY"):
                    start(service, c)
            finally:
                contender_done.set()
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(invoke, True), pool.submit(invoke, False)
        assert first.result(timeout=20)["stage"] == "REGISTERED"
        assert second.result(timeout=20) == "denied"
    assert h.starts == len(h.workspaces) == len(h.prompts) == 1
    assert len(s.store.get_operations("e", kind=s.KIND)) == 1


@pytest.mark.parametrize("change", ["version", "unknown", "source", "foreign-task", "duplicate"])
def test_operator_contract_rejects_invalid_context(change):
    data = spec()
    if change == "version":
        data["version"] = True
    if change == "unknown":
        data["role"] = "Integration"
    if change == "source":
        data["sources"] = ["../credentials"]
    if change == "foreign-task":
        data["tasks"][0]["task"]["epic_id"] = "foreign"
    if change == "duplicate":
        data["tasks"][1]["task"]["task_id"] = "F-01"
    with pytest.raises(ValidationError):
        EpicIntegrationSpec.model_validate(data)


def test_second_principal_run_cannot_create_another_epic_owner(setup):
    s, c, i, h = setup
    start(s, c)
    changes = s.store.db.total_changes
    other = EpicStartService(s.settings, s.store, h, spec(),
                             i.model_copy(update={"epic_run_id": "different-run"}), codex=h)
    with pytest.raises(EpicStartError, match="ALREADY_OWNED"):
        asyncio.run(other.start(c, "p", "E-34", "different-run"))
    assert s.store.get_epic("different-run") is None
    assert s.store.db.total_changes == changes and h.starts == len(h.prompts) == 1


def test_journal_committed_before_first_git_side_effect(setup, monkeypatch):
    s, c, _, _ = setup
    original = s.git.add_worktree

    def add(*args, **kwargs):
        with sqlite3.connect(f"file:{s.settings.sqlite_path}?mode=ro", uri=True) as db:
            row = db.execute("SELECT payload FROM operations WHERE kind='epic_start'").fetchone()
            assert row is not None and json.loads(row[0])["result"]["stage"] == "PREPARING"
            status = db.execute("SELECT status FROM epic_runs WHERE id='e'").fetchone()[0]
            assert status == "PLANNED"
        return original(*args, **kwargs)

    monkeypatch.setattr(s.git, "add_worktree", add)
    assert start(s, c)["stage"] == "REGISTERED"


def test_unknown_workspace_response_preserves_single_intent_and_no_agent(setup):
    s, c, _, h = setup
    h.fail_create = True
    with pytest.raises(EpicStartError):
        start(s, c)
    with pytest.raises(EpicStartError):
        start(s, c)
    assert len(h.workspaces) == 1 and h.starts == 0 and not h.prompts
    assert s.store.get_operation("p", s.KIND, "e").result["stage"] == "START_PENDING"


def test_lost_runtime_ready_persistence_reobserves_original_agent(setup, monkeypatch):
    s, c, _, h = setup
    original = s._save

    def save(*args, **kwargs):
        if kwargs.get("stage") == "RUNTIME_READY":
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    monkeypatch.setattr(s, "_save", save)
    with pytest.raises(KeyboardInterrupt):
        start(s, c)
    assert h.starts == 1 and not h.prompts
    with StateStore(s.settings.sqlite_path) as db:
        other = EpicStartService(s.settings, db, h, spec(), s.principal, codex=h)
        assert start(other, c)["stage"] == "REGISTERED"
    assert h.starts == len(h.prompts) == 1


def test_lost_prompt_response_persistence_reads_native_ack(setup, monkeypatch):
    s, c, _, h = setup
    original = s._save

    def save(*args, **kwargs):
        if "transport" in kwargs:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    monkeypatch.setattr(s, "_save", save)
    with pytest.raises(KeyboardInterrupt):
        start(s, c)
    with StateStore(s.settings.sqlite_path) as db:
        other = EpicStartService(s.settings, db, h, spec(), s.principal, codex=h)
        assert start(other, c)["stage"] == "REGISTERED"
    assert h.starts == len(h.prompts) == 1


def test_pending_initial_board_sync_prevents_runtime_and_retries_only_sync(setup):
    s, c, i, h = setup

    class Sync:
        def __init__(self):
            self.store, self.settings = s.store, s.settings
            self.ready, self.calls = False, []

        async def bind_epic(self, actor, run, external_id):
            self.calls.append(("bind", actor.actor_id, run, external_id))

        async def sync_epic(self, actor, run):
            self.calls.append(("sync", actor.actor_id, run))
            return {"status": "SYNCED" if self.ready else "PENDING", "reason": "WAIT"}

    sync = Sync()
    service = EpicStartService(s.settings, s.store, h, spec(), i, codex=h,
                               sync=sync, external_epic_id="board-epic")
    pending = start(service, c)
    assert pending["stage"] == "START_PENDING" and pending["reason"] == "EPIC_START_SYNC_PENDING"
    assert s.store.get_epic("e").status == EpicState.ACTIVE
    assert not h.starts and not h.workspaces and not h.prompts
    sync.ready = True
    assert start(service, c)["stage"] == "REGISTERED"
    assert h.starts == len(h.prompts) == 1
    count = len(sync.calls)
    assert start(service, c)["stage"] == "REGISTERED"
    assert len(sync.calls) == count


def test_foreign_connection_principal_cannot_rebind_registration(setup):
    s, c, i, h = setup
    start(s, c)
    changes = s.store.db.total_changes
    with pytest.raises(EpicStartError, match="CONNECTION_SCOPE_DENIED"):
        s.connection_principal(i.model_copy(update={"epic_run_id": "other"}))
    assert s.store.db.total_changes == changes and len(h.prompts) == 1
