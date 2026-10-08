import subprocess
from pathlib import Path

import pytest

from orchestrator.adapters.git import GitError
from orchestrator.adapters.herdr import HerdrAdapter, HerdrError
from orchestrator.application.runtime_start_service import RuntimeStartError, RuntimeStartService
from orchestrator.application.state_service import StateService
from orchestrator.application.worktree_service import WorktreeError, WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.policy import Actor, Role, VerifiedFacts
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.persistence.store import StateStore


class FakeHerdr:
    """Controlled CLI facts only. Real Git/SQLite below; live CLI proof is recorded separately."""

    server_session = "test-session"
    sandbox = "read-only"

    def __init__(self):
        self.workspaces, self.agents, self.starts = {}, {}, 0
        self.fail_create = False
        self.start_failure = None
        self.blocked = False
        self.identity_failure = False

    def create_workspace(self, cwd, label):
        n = len(self.workspaces) + 1
        b = {
            "workspace_id": f"w{n}",
            "tab_id": f"w{n}:t1",
            "pane_id": f"w{n}:p1",
            "terminal_id": f"term_{n}",
        }
        self.workspaces[b["pane_id"]] = b | {"cwd": cwd}
        if self.fail_create:
            raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")
        return b

    def pane(self, pane_id):
        return self.workspaces[pane_id]

    def process_info(self, pane_id):
        return {"shell_pid": 10, "foreground_processes": [{"pid": 10}], "pane_id": pane_id}

    def get_agent(self, name):
        return self.agents.get(name)

    def start_agent(self, name, pane_id, cwd):
        self.starts += 1
        self.agents[name] = self.workspaces[pane_id] | {"name": name}
        if self.start_failure:
            raise HerdrError(self.start_failure)

    def verify_agent(self, agent, binding, cwd, name):
        if self.identity_failure or agent["cwd"] != cwd:
            raise HerdrError("RUNTIME_IDENTITY_MISMATCH")
        return {
            "ready": not self.blocked,
            "status": "blocked" if self.blocked else "idle",
            "session_id": None,
            "processes": [{"pid": 20, "start_time": "123"}],
        }


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
    (repo / "README.md").write_text("Harmless runtime fixture\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True
    )
    settings = Settings(
        repository=repo, worktree_root=tmp_path / "trees", sqlite_path=tmp_path / "state.db"
    )
    with StateStore(settings.sqlite_path) as store:
        c = Actor(actor_id="c", role=Role.COORDINATOR, project_id="p")
        i = Actor(actor_id="i", role=Role.INTEGRATION, project_id="p", epic_run_id="e")
        w = WorktreeService(settings, store)
        w.create_epic_worktree(c, epic_id="E-01", run_id="e")
        StateService(store).transition_epic(
            "e", EpicState.ACTIVE, expected=EpicState.PLANNED, event_id="active", actor=c
        )
        for n in range(1, 4):
            task = w.create_task_worktree(i, epic_run_id="e", task_id=f"F-0{n}", run_id=f"t{n}")
            StateService(store).transition_task(
                task.id,
                TaskState.CLAIMED,
                expected=TaskState.PLANNED,
                event_id=f"claim-{n}",
                actor=i,
                facts=VerifiedFacts(dependencies_ready=True),
            )
        h = FakeHerdr()
        yield RuntimeStartService(settings, store, h), c, i, h


def test_start_persists_bindings_slot_and_starting_without_prompt_or_working(setup):
    s, _, i, h = setup
    t = s.start_task(i, "t1")
    assert t.internal_status == TaskState.STARTING and t.worker_slot == 1
    assert t.herdr_server_session == h.server_session and t.herdr_pane_id == "w1:p1"
    assert t.herdr_terminal_id == "term_1" and t.herdr_tab_id == "w1:t1"
    assert t.codex_session_id is None
    assert s.store.get_operation("p", s.KIND, t.id).status == "SUCCEEDED"
    assert len(s.store.get_references("e")) == 4
    assert h.starts == 1


def test_repeat_and_database_reopen_reuse_owned_runtime(setup):
    s, _, i, h = setup
    t = s.start_task(i, "t1")
    assert s.start_task(i, "t1") == t
    with StateStore(s.settings.sqlite_path) as store:
        reopened = RuntimeStartService(s.settings, store, h)
        assert reopened.start_task(i, "t1") == t
    assert len(h.workspaces) == h.starts == 1


@pytest.mark.parametrize(
    "role,project,epic",
    [
        (Role.WORKER, "p", "e"),
        (Role.COORDINATOR, "p", None),
        (Role.INTEGRATION, "other", "e"),
        (Role.INTEGRATION, "p", "other"),
    ],
)
def test_wrong_principal_has_no_effects(setup, role, project, epic):
    s, _, _, h = setup
    changes = s.store.db.total_changes
    a = Actor(
        actor_id="unauthorized", role=role, project_id=project, epic_run_id=epic, task_run_id="t1"
    )
    with pytest.raises(RuntimeStartError):
        s.start_task(a, "t1")
    assert s.store.db.total_changes == changes
    assert not h.workspaces and not h.starts


def test_two_slots_reserved_before_third_attempt_has_no_effects(setup):
    s, _, i, h = setup
    assert s.start_task(i, "t1").worker_slot == 1
    assert s.start_task(i, "t2").worker_slot == 2
    changes = s.store.db.total_changes
    with pytest.raises(RuntimeStartError, match="CAPACITY"):
        s.start_task(i, "t3")
    assert s.store.db.total_changes == changes and h.starts == len(h.workspaces) == 2


def test_lost_workspace_response_is_not_retried_or_adopted(setup):
    s, _, i, h = setup
    h.fail_create = True
    with pytest.raises(RuntimeStartError, match="UNKNOWN_RUNTIME"):
        s.start_task(i, "t1")
    assert s.store.get_task("t1").internal_status == TaskState.STARTING
    assert s.store.get_operation("p", s.KIND, "t1").result["stage"] == "WORKSPACE_CREATE_REQUESTED"
    h.fail_create = False
    with pytest.raises(RuntimeStartError, match="WORKSPACE_OUTCOME_UNKNOWN"):
        s.start_task(i, "t1")
    assert len(h.workspaces) == 1 and not h.starts


def test_checkpointed_workspace_survives_failure_and_reopen(setup, monkeypatch):
    s, _, i, h = setup
    save = s._save

    def fail(run, op, phase, changes, runtime_updates=None):
        if phase == "WORKSPACE_CREATED":
            raise RuntimeError("simulated crash before start intent")
        return save(run, op, phase, changes, runtime_updates)

    monkeypatch.setattr(s, "_save", fail)
    with pytest.raises(RuntimeError):
        s.start_task(i, "t1")
    t = s.store.get_task("t1")
    assert t.herdr_pane_id == "w1:p1" and not h.starts
    with StateStore(s.settings.sqlite_path) as store:
        t = RuntimeStartService(s.settings, store, h).start_task(i, "t1")
    assert t.herdr_pane_id == "w1:p1" and h.starts == len(h.workspaces) == 1


def test_lost_start_response_is_reconciled_from_actual_agent_without_extra_start(setup):
    s, _, i, h = setup
    h.start_failure = "UNKNOWN_RUNTIME_OUTCOME"
    t = s.start_task(i, "t1")
    assert s.start_task(i, "t1") == t
    assert h.starts == 1


def test_trust_blocker_retains_binding_and_slot_without_false_readiness(setup):
    s, _, i, h = setup
    h.blocked = True
    h.start_failure = "AGENT_NOT_READY"
    with pytest.raises(RuntimeStartError, match="RUNTIME_BLOCKED"):
        s.start_task(i, "t1")
    op = s.store.get_operation("p", s.KIND, "t1")
    assert op.status == "PENDING"
    t = s.store.get_task("t1")
    assert t.herdr_pane_id == "w1:p1" and t.worker_slot == 1
    h.blocked = False
    assert s.start_task(i, "t1").internal_status == TaskState.STARTING
    assert h.starts == 1


def test_missing_agent_after_start_intent_is_unknown_not_new_start(setup, monkeypatch):
    s, _, i, h = setup

    def crash(*args):
        raise RuntimeError("simulated crash before external call")

    monkeypatch.setattr(h, "start_agent", crash)
    with pytest.raises(RuntimeError):
        s.start_task(i, "t1")
    with pytest.raises(RuntimeStartError, match="START_OUTCOME_UNKNOWN"):
        s.start_task(i, "t1")
    assert not h.starts and len(h.workspaces) == 1


def test_foreign_cwd_after_workspace_checkpoint_has_no_agent_start(setup):
    s, _, i, h = setup
    original = h.create_workspace

    def corrupt(cwd, label):
        b = original(cwd, label)
        h.workspaces[b["pane_id"]]["cwd"] = "foreign"
        return b

    h.create_workspace = corrupt
    with pytest.raises(RuntimeStartError, match="PANE_NOT_IDLE_OR_OWNED"):
        s.start_task(i, "t1")
    assert s.store.get_task("t1").herdr_pane_id == "w1:p1" and not h.starts


def test_changed_agent_process_or_binding_is_rejected_without_duplicate(setup):
    s, _, i, h = setup
    s.start_task(i, "t1")
    h.identity_failure = True
    with pytest.raises(RuntimeStartError, match="IDENTITY_MISMATCH"):
        s.start_task(i, "t1")
    assert h.starts == 1


def test_wrong_git_branch_and_missing_ownership_rejected_before_effect(setup):
    s, _, i, h = setup
    t = s.store.get_task("t1")
    g = s.worktrees.git
    g.run("symbolic-ref", "HEAD", "refs/heads/main", cwd=Path(t.worktree_path))
    with pytest.raises((WorktreeError, GitError)):
        s.start_task(i, "t1")
    assert not h.workspaces and not h.starts


def test_epic_start_requires_coordinator_and_has_no_worker_slot(setup):
    s, c, i, h = setup
    with pytest.raises(RuntimeStartError):
        s.start_epic(i, "e")
    e = s.start_epic(c, "e")
    assert e.herdr_pane_id == "w1:p1" and e.integration_agent_id
    assert s.start_epic(c, "e") == e and h.starts == 1


def test_adapter_argv_timeout_and_unknown_response_are_bounded(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    seen = []

    def runner(argv, **kwargs):
        seen.append((argv, kwargs))
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", runner)
    a = HerdrAdapter("isolated-server", sandbox="workspace-write")
    with pytest.raises(HerdrError, match="UNKNOWN_RUNTIME"):
        a.start_agent("name", "w1:p1", "/safe/path")
    args, options = seen[0]
    assert args[:3] == ["herdr", "--session", "isolated-server"]
    assert args[-7:] == [
        "--no-daemon",
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "on-request",
        "--cd",
        "/safe/path",
    ]
    assert options["timeout"] == 35 and "shell" not in options


def test_adapter_checks_exit_and_rejects_malformed_json_without_raw_logs(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, "TOKEN_SECRET", "")
    )
    with pytest.raises(HerdrError) as e:
        HerdrAdapter("isolated").get_agent("name")
    assert "TOKEN_SECRET" not in str(e.value)


def test_unmanaged_context_and_dangerous_sandbox_configuration_rejected(monkeypatch):
    monkeypatch.delenv("HERDR_ENV", raising=False)
    with pytest.raises(HerdrError, match="CONTEXT_REQUIRED"):
        HerdrAdapter("isolated")
    monkeypatch.setenv("HERDR_ENV", "1")
    with pytest.raises(HerdrError, match="INVALID_RUNTIME_CONFIGURATION"):
        HerdrAdapter("isolated", sandbox="danger-full-access")


def test_concurrent_same_run_creates_only_one_workspace(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    s, _, i, h = setup
    entered, release = Event(), Event()
    create = h.create_workspace

    def pause(cwd, label):
        binding = create(cwd, label)
        entered.set()
        assert release.wait(5)
        return binding

    monkeypatch.setattr(h, "create_workspace", pause)

    def start():
        with StateStore(s.settings.sqlite_path) as store:
            return RuntimeStartService(s.settings, store, h).start_task(i, "t1")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(start)
        assert entered.wait(5)
        try:
            with pytest.raises(RuntimeStartError, match="WORKSPACE_OUTCOME_UNKNOWN"):
                s.start_task(i, "t1")
        finally:
            release.set()
        result = future.result(timeout=10)
    assert s.start_task(i, "t1") == result
    assert len(h.workspaces) == h.starts == 1


def test_adapter_confirms_native_session_against_recorded_codex_cwd(monkeypatch):
    from orchestrator.adapters.codex import CodexError

    monkeypatch.setenv("HERDR_ENV", "1")
    a = HerdrAdapter("isolated")
    b = {"workspace_id": "w1", "tab_id": "w1:t1", "pane_id": "w1:p1", "terminal_id": "term_1"}
    sid = "00000000-0000-4000-8000-000000000001"
    agent = b | {
        "name": "owned",
        "agent": "codex",
        "cwd": "/safe",
        "foreground_cwd": "/safe",
        "interactive_ready": True,
        "agent_status": "idle",
        "agent_session": {"source": "herdr:codex", "kind": "id", "value": sid},
    }
    args = [
        "codex",
        "--no-daemon",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "on-request",
        "--cd",
        "/safe",
    ]
    monkeypatch.setattr(
        a,
        "process_info",
        lambda _: {
            "pane_id": "w1:p1",
            "foreground_processes": [{"pid": 123, "argv": args, "cwd": "/safe"}],
        },
    )
    monkeypatch.setattr("orchestrator.adapters.herdr.process_stamp", lambda *a: "456")
    monkeypatch.setattr(
        "orchestrator.adapters.herdr.CodexAdapter.read_session", lambda *a: {"session_id": sid}
    )
    assert a.verify_agent(agent, b, "/safe", "owned")["session_id"] == sid

    def foreign(*args):
        raise CodexError("foreign session cwd")

    monkeypatch.setattr("orchestrator.adapters.herdr.CodexAdapter.read_session", foreign)
    with pytest.raises(HerdrError, match="CODEX_SESSION_UNVERIFIED"):
        a.verify_agent(agent, b, "/safe", "owned")
    agent["interactive_ready"] = "true"
    agent.pop("agent_session")
    assert not a.verify_agent(agent, b, "/safe", "owned")["ready"]


def test_changed_runtime_identity_is_rejected_before_extra_side_effect(setup):
    s, _, i, h = setup
    task = s.start_task(i, "t1")
    s.store.update_runtime_metadata(task.model_copy(update={"worker_agent_id": "foreign"}))
    with pytest.raises(RuntimeStartError, match="RUNTIME_BINDING_CHANGED"):
        s.start_task(i, "t1")
    assert len(h.workspaces) == h.starts == 1


def test_runtime_metadata_cannot_mutate_domain_state_or_git(setup):
    from orchestrator.persistence.store import StoreError

    s, _, _, _ = setup
    task = s.store.get_task("t1")
    with pytest.raises(StoreError):
        s.store.update_runtime_metadata(task.model_copy(update={"current_commit": "a" * 40}))
    assert s.store.get_task("t1") == task


def test_cli_json_error_on_stderr_is_recognized_without_unknown_start(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            [],
            1,
            "",
            '{"id":"cli:agent:get","error":{"code":"agent_not_found","message":"secret"}}',
        ),
    )
    assert HerdrAdapter("isolated").get_agent("owned") is None


def test_git_branch_changed_after_workspace_is_rechecked_before_agent_start(setup):
    s, _, i, h = setup
    create = h.create_workspace
    task = s.store.get_task("t1")

    def branch_change(cwd, label):
        result = create(cwd, label)
        s.worktrees.git.run("symbolic-ref", "HEAD", "refs/heads/main", cwd=Path(cwd))
        return result

    h.create_workspace = branch_change
    with pytest.raises(RuntimeStartError, match="WORKTREE_UNVERIFIED"):
        s.start_task(i, "t1")
    assert h.starts == 0 and s.store.get_task(task.id).herdr_pane_id == "w1:p1"


def test_known_workspace_ids_survive_cli_response_with_wrong_cwd(setup, monkeypatch):
    import json

    s, _, i, _ = setup
    real_run = subprocess.run
    seen = []
    b = {
        "workspace_id": "w42",
        "tab_id": "w42:t1",
        "pane_id": "w42:p1",
        "terminal_id": "term_42",
        "cwd": "/foreign",
    }

    def response(argv, **kwargs):
        if argv[0] != "herdr":
            return real_run(argv, **kwargs)
        seen.append(argv[3:])
        if argv[3:5] == ["workspace", "create"]:
            result = {
                "workspace": {"workspace_id": "w42"},
                "tab": {"workspace_id": "w42", "tab_id": "w42:t1"},
                "root_pane": b,
            }
        elif argv[3:5] == ["pane", "get"]:
            result = {"pane": b}
        else:
            result = {
                "process_info": {
                    "shell_pid": 42,
                    "pane_id": "w42:p1",
                    "foreground_processes": [{"pid": 42}],
                }
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps({"result": result}), "")

    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setattr(subprocess, "run", response)
    service = RuntimeStartService(s.settings, s.store, HerdrAdapter("isolated"))
    with pytest.raises(RuntimeStartError, match="PANE_NOT_IDLE_OR_OWNED"):
        service.start_task(i, "t1")
    assert s.store.get_task("t1").herdr_workspace_id == "w42"
    assert len(s.store.get_references("e")) == 4
    assert not any(args[:2] == ["agent", "start"] for args in seen)
