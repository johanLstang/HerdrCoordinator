import asyncio
import io
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from mcp import Client
from test_runtime_assignment import SID, History
from test_runtime_start import FakeHerdr

from orchestrator.adapters.git import GitError
from orchestrator.adapters.herdr import HerdrError
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.state_service import StateService
from orchestrator.application.task_start_service import TaskStartError, TaskStartService
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    for args in [
        ["init", "-b", "main"],
        ["config", "user.name", "Test"],
        ["config", "user.email", "test@example.invalid"],
    ]:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "README.md").write_text("Harmless task start fixture\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True
    )
    settings = Settings(
        repository=repo,
        worktree_root=tmp_path / "trees",
        sqlite_path=tmp_path / "state.db",
        max_workers=1,
    )
    with StateStore(settings.sqlite_path) as store:
        coordinator = Actor(actor_id="c", role=Role.COORDINATOR, project_id="p")
        actor = Actor(actor_id="i", role=Role.INTEGRATION, project_id="p", epic_run_id="epic-run")
        WorktreeService(settings, store).create_epic_worktree(
            coordinator, epic_id="E", run_id="epic-run"
        )
        StateService(store).transition_epic(
            "epic-run",
            EpicState.ACTIVE,
            expected=EpicState.PLANNED,
            event_id="active",
            actor=coordinator,
        )
        h, history = FakeHerdr(), History()
        h.sent, h.session_id, h.status = [], None, "idle"
        verify = h.verify_agent

        def observe(*args):
            facts = verify(*args)
            return facts | {
                "session_id": h.session_id,
                "status": "blocked" if h.blocked else h.status,
                "ready": facts["ready"] and h.status in {"idle", "done"},
            }

        h.verify_agent = observe

        def prompt(name, text, *, timeout_ms):
            assert name in h.agents
            h.sent.append(text)
            h.session_id = SID
            history.reply(text)

        h.prompt = prompt
        spec = dict(
            version=1,
            project_id="p",
            epic_id="E",
            task_id="T",
            name="Task",
            goal="Implement bounded result",
            requirements=["Persist result"],
            scope=["Storage"],
            out_of_scope=["Merges"],
            acceptance_criteria=["Result survives reopening"],
            sources=["README.md"],
            verification_steps=["Run documented tests"],
        )
        yield TaskStartService(settings, store, h, history), actor, spec, h, history


def test_complete_pipeline_reserves_before_git_and_confirms_only_native_ack(setup, monkeypatch):
    s, actor, spec, h, _ = setup
    original = s.worktrees.git.add_worktree

    def add(*args, **kwargs):
        task = s.store.get_tasks(actor.epic_run_id)[0]
        assert task.internal_status == TaskState.CLAIMED and task.worker_slot == 1
        op = s.store.get_operation("p", s.KIND, "T")
        assert op and op.result["instruction"] and op.result["stage"] == "CLAIMED"
        return original(*args, **kwargs)

    monkeypatch.setattr(s.worktrees.git, "add_worktree", add)
    result = s.start(actor, actor.epic_run_id, spec)
    assert result["status"] == "CONFIRMED"
    task = s.store.get_task(result["task"]["id"])
    assert task.internal_status == TaskState.WORKING and task.codex_session_id == SID
    assert task.base_commit == s.worktrees.git.head("main")
    assert task.branch == "task/e-t" and Path(task.worktree_path).is_dir()
    assert h.starts == len(h.workspaces) == len(h.sent) == 1
    instruction = json.loads(h.sent[0])["instruction"]
    assert task.id in instruction and task.base_commit in instruction
    assert s.store.get_operation("p", s.KIND, "T").status == "SUCCEEDED"
    assert s.worktrees.git.head("main") == task.base_commit


def test_repeat_and_reopen_return_same_run_without_resources_or_prompt(setup):
    s, actor, spec, h, history = setup
    first = s.start(actor, actor.epic_run_id, spec)
    with StateStore(s.settings.sqlite_path) as store:
        repeated = TaskStartService(s.settings, store, h, history).start(
            actor, actor.epic_run_id, spec
        )
        assert repeated["status"] == "EXISTING" and repeated["task"]["id"] == first["task"]["id"]
        assert len(store.get_tasks(actor.epic_run_id)) == 1
    assert h.starts == len(h.workspaces) == len(h.sent) == 1


def test_phase_four_one_slot_blocks_second_task_before_any_resources(setup):
    s, actor, spec, h, _ = setup
    s.start(actor, actor.epic_run_id, spec)
    with pytest.raises(TaskStartError, match="CAPACITY"):
        s.start(actor, actor.epic_run_id, spec | {"task_id": "T2"})
    assert len(s.store.get_tasks(actor.epic_run_id)) == 1
    assert s.worktrees.git.head("task/e-t2") is None
    assert h.starts == len(h.workspaces) == 1 and s.settings.max_workers == 1


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"acceptance_criteria": []},
        {"epic_id": "foreign"},
        {"project_id": "foreign"},
        {"dependencies": ["unknown"]},
        {"external_prerequisites": ["Unavailable external service"]},
        {"goal": "x" * 32768},
        {"task_id": "../unsafe"},
    ],
)
def test_invalid_scope_dependencies_prerequisites_and_oversized_prompt_have_no_effects(
    setup, change
):
    s, actor, spec, h, _ = setup
    trees = s.worktrees.git.worktrees()
    with pytest.raises(TaskStartError):
        s.start(actor, actor.epic_run_id, spec | change)
    assert s.store.get_tasks(actor.epic_run_id) == []
    assert s.store.get_operations(actor.epic_run_id, kind=s.KIND) == []
    assert s.worktrees.git.worktrees() == trees and not h.workspaces


@pytest.mark.parametrize(
    "role,project,epic",
    [
        (Role.WORKER, "p", "epic-run"),
        (Role.COORDINATOR, "p", None),
        (Role.INTEGRATION, "other", "epic-run"),
        (Role.INTEGRATION, "p", "other"),
    ],
)
def test_wrong_principal_never_claims_or_creates(setup, role, project, epic):
    s, _, spec, h, _ = setup
    bad = Actor(actor_id="bad", role=role, project_id=project, epic_run_id=epic)
    with pytest.raises(TaskStartError, match="SCOPE"):
        s.start(bad, "epic-run", spec)
    assert not s.store.get_tasks("epic-run") and h.starts == 0


def test_changed_spec_or_runtime_binding_is_not_new_assignment(setup):
    s, actor, spec, h, _ = setup
    s.start(actor, actor.epic_run_id, spec)
    with pytest.raises(TaskStartError, match="INTENT_MISMATCH"):
        s.start(actor, actor.epic_run_id, spec | {"goal": "Different task"})
    h.server_session = "foreign"
    with pytest.raises(TaskStartError, match="INTENT_MISMATCH"):
        s.start(actor, actor.epic_run_id, spec)
    assert len(h.sent) == h.starts == 1


@pytest.mark.parametrize(
    "when", ["before_git", "after_git", "after_workspace", "after_agent", "after_prompt"]
)
def test_interruption_retains_claim_and_known_steps_without_duplicate_effects(
    setup, monkeypatch, when
):
    s, actor, spec, h, history = setup
    if when in {"before_git", "after_git"}:
        original = s.worktrees.git.add_worktree

        def broken(*args, **kwargs):
            if when == "after_git":
                original(*args, **kwargs)
            raise GitError("fixture interruption")

        monkeypatch.setattr(s.worktrees.git, "add_worktree", broken)
    elif when == "after_workspace":
        h.fail_create = True
    elif when == "after_agent":
        h.start_failure = "UNKNOWN_RUNTIME_OUTCOME"
        h.blocked = True
    else:
        original_prompt = h.prompt

        def broken_prompt(*args, **kwargs):
            original_prompt(*args, **kwargs)
            raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

        h.prompt = broken_prompt
    if when == "after_prompt":
        first = s.start(actor, actor.epic_run_id, spec)
        assert first["status"] == "CONFIRMED"
    else:
        with pytest.raises(TaskStartError):
            s.start(actor, actor.epic_run_id, spec)
    task = s.store.get_tasks(actor.epic_run_id)[0]
    assert task.worker_slot == (None if when in {"before_git", "after_git"} else 1)
    if when != "after_prompt":
        assert task.internal_status != TaskState.WORKING
    if when not in {"before_git", "after_git"}:
        with pytest.raises(TaskStartError, match="CAPACITY"):
            s.start(actor, actor.epic_run_id, spec | {"task_id": "T2"})
    else:
        assert s.store.get_operation("p", s.KIND, "T").result["reservation_released"] is True
    if when in {"before_git", "after_git"}:
        monkeypatch.setattr(s.worktrees.git, "add_worktree", original)
    if when == "after_agent":
        h.blocked = False
    if when == "after_workspace":
        with pytest.raises(TaskStartError):
            s.start(actor, actor.epic_run_id, spec)
        assert len(h.workspaces) == 1 and h.starts == 0
    else:
        repeat = s.start(actor, actor.epic_run_id, spec)
        assert repeat["task"]["id"] == task.id
        assert h.starts == len(h.workspaces) == len(h.sent) == 1
    assert len(s.store.get_tasks(actor.epic_run_id)) == 1


def test_concurrent_starts_share_one_owner_and_runtime(setup):
    s, actor, spec, h, history = setup
    barrier = Barrier(2)

    def run():
        with StateStore(s.settings.sqlite_path) as store:
            service = TaskStartService(s.settings, store, h, history)
            barrier.wait(timeout=5)
            try:
                return service.start(actor, actor.epic_run_id, spec)
            except TaskStartError:
                # An overlapping observer can see an incomplete external intent; retry observes it.
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert any(r and r["status"] == "CONFIRMED" for r in results)
    final = s.start(actor, actor.epic_run_id, spec)
    assert final["task"]["internal_status"] == "WORKING"
    assert (
        len(s.store.get_tasks(actor.epic_run_id))
        == h.starts
        == len(h.workspaces)
        == len(h.sent)
        == 1
    )


def test_mcp_exposes_start_only_with_operator_service_and_rejects_worker_impersonation(setup):
    s, actor, spec, h, _ = setup
    service = RuntimeService(s.store, actor, EventLog(stream=io.StringIO()), task_start=s)

    async def exercise():
        async with Client(create_server(service)) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools.tools if t.name == "task_start")
            assert tool.annotations.read_only_hint is False
            bad = await client.call_tool(
                "task_start",
                {"project_id": "p", "epic_run_id": "epic-run", "task": spec, "role": "Coordinator"},
            )
            assert bad.is_error and bad.structured_content["code"] == "INVALID_ARGUMENT"
            good = await client.call_tool(
                "task_start", {"project_id": "p", "epic_run_id": "epic-run", "task": spec}
            )
            assert not good.is_error and good.structured_content["data"]["status"] == "CONFIRMED"

    asyncio.run(asyncio.wait_for(exercise(), timeout=15))
    worker = actor.model_copy(update={"role": Role.WORKER})
    denied = RuntimeService(s.store, worker, EventLog(stream=io.StringIO()), task_start=s).call(
        "task_start", {"project_id": "p", "epic_run_id": "epic-run", "task": spec}
    )
    assert denied.code == "FORBIDDEN" and h.starts == 1


def test_unconfirmed_prompt_remains_starting_and_is_only_observed_on_retry(setup):
    s, actor, spec, h, _ = setup
    h.prompt = lambda name, text, **kwargs: h.sent.append(text)
    first = s.start(actor, actor.epic_run_id, spec)
    assert first["status"] == "WAITING"
    again = s.start(actor, actor.epic_run_id, spec)
    assert again["status"] == "WAITING" and first["task"]["id"] == again["task"]["id"]
    task = s.store.get_task(first["task"]["id"])
    assert task.internal_status == TaskState.STARTING and task.worker_slot == 1
    assert len(h.sent) == h.starts == 1


def test_existing_independently_registered_task_is_not_adopted(setup):
    s, actor, spec, h, _ = setup
    WorktreeService(s.settings, s.store).create_task_worktree(
        actor, epic_run_id=actor.epic_run_id, task_id="T", run_id="independent"
    )
    with pytest.raises(TaskStartError, match="ALREADY_OWNED"):
        s.start(actor, actor.epic_run_id, spec)
    assert s.store.get_task("independent").worker_slot is None and not h.workspaces


def test_trusted_prerequisite_probe_must_return_exact_true(setup):
    s, actor, spec, _, _ = setup
    spec = spec | {"external_prerequisites": ["Operator-confirmed fixture"]}
    s.prerequisite_probe = lambda _: "yes"
    with pytest.raises(TaskStartError, match="PREREQUISITE"):
        s.start(actor, actor.epic_run_id, spec)
    s.prerequisite_probe = lambda _: True
    assert s.start(actor, actor.epic_run_id, spec)["status"] == "CONFIRMED"


def test_retry_observes_native_ack_while_worker_is_busy_instead_of_restarting(setup):
    s, actor, spec, h, history = setup
    h.prompt = lambda name, text, **kwargs: h.sent.append(text)
    assert s.start(actor, actor.epic_run_id, spec)["status"] == "WAITING"
    h.session_id, h.status = SID, "processing"
    history.reply(h.sent[0])
    assert s.start(actor, actor.epic_run_id, spec)["status"] == "CONFIRMED"
    assert len(h.sent) == h.starts == 1


def test_lost_parent_checkpoint_after_verified_ack_reuses_existing_confirmation(setup, monkeypatch):
    s, actor, spec, h, _ = setup
    checkpoint = s._checkpoint

    def crash(op, stage, **kwargs):
        if stage == "WORKING":
            raise RuntimeError("simulated crash after ACK commit")
        return checkpoint(op, stage, **kwargs)

    monkeypatch.setattr(s, "_checkpoint", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        s.start(actor, actor.epic_run_id, spec)
    assert s.store.get_tasks(actor.epic_run_id)[0].internal_status == TaskState.WORKING
    monkeypatch.setattr(s, "_checkpoint", checkpoint)
    assert s.start(actor, actor.epic_run_id, spec)["status"] == "CONFIRMED"
    assert len(h.sent) == h.starts == 1


def test_immutable_base_change_after_claim_requires_reconciliation(setup, monkeypatch):
    s, actor, spec, h, _ = setup
    original = s.worktrees.git.add_worktree
    monkeypatch.setattr(
        s.worktrees.git, "add_worktree", lambda *a, **k: (_ for _ in ()).throw(GitError("crash"))
    )
    with pytest.raises(TaskStartError):
        s.start(actor, actor.epic_run_id, spec)
    epic = s.store.get_epic(actor.epic_run_id)
    path = Path(epic.worktree_path)
    (path / "changed.txt").write_text("Epic moved after saved task base\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "new base"], check=True, capture_output=True
    )
    monkeypatch.setattr(s.worktrees.git, "add_worktree", original)
    with pytest.raises(TaskStartError):
        s.start(actor, actor.epic_run_id, spec)
    assert not h.workspaces and s.worktrees.git.head("task/e-t") is None
    assert s.store.get_tasks(actor.epic_run_id)[0].worker_slot is None
