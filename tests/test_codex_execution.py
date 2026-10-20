"""Explicit operator profile contract; real kernel measurement is separately opted in."""

import hashlib
import json
import os
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.adapters.herdr import HerdrAdapter, HerdrError
from orchestrator.domain.codex_execution import CodexExecutionPolicy, ExecutionPolicyError
from orchestrator.domain.codex_mcp_launch import CodexMCPLaunch


@pytest.fixture
def execution_policy(tmp_path):
    operator = tmp_path / "operator"
    operator.mkdir()
    binary = operator / "codex"
    binary.write_bytes(b"#!/bin/sh\nexit 0\n")
    binary.chmod(0o555)
    tools = operator / "tools"
    tools.mkdir(mode=0o700)
    helper = tools / "codex-linux-sandbox"
    helper.symlink_to(binary)
    return CodexExecutionPolicy(workspace_access="write", helper=helper, binary=binary,
                                binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())


def test_profile_is_scoped_and_legacy_argv_fingerprint_remain_identical(
    execution_policy, monkeypatch, tmp_path,
):
    monkeypatch.setenv("HERDR_ENV", "1")
    mcp = CodexMCPLaunch(command=sys.executable, args=("/operator/host.py",))
    legacy = HerdrAdapter("own", sandbox="workspace-write", mcp=mcp)
    assert legacy.mcp_fingerprint == mcp.fingerprint
    assert legacy._launch_args("/own") == [
        "--no-daemon", "--sandbox", "workspace-write", "--ask-for-approval", "on-request",
        "--cd", "/own", *mcp.cli_args(),
    ]
    work = tmp_path / "work"
    work.mkdir()
    profiled = HerdrAdapter("own", sandbox="workspace-write", mcp=mcp,
                            execution_policy=execution_policy)
    args = profiled._launch_args(str(work))
    assert "--sandbox" not in args and args[:3] == [
        "--no-daemon", "--ask-for-approval", "never",
    ]
    config = tomllib.loads("\n".join(execution_policy.cli_args()[1::2]))
    policy = config["permissions"][execution_policy.name]
    assert policy["filesystem"][":root"] == "deny"
    assert policy["filesystem"][":workspace_roots"] == {
        ".": "write", ".git": "read", ".codex": "read", ".agents": "read", ".aws": "read",
    }
    assert policy["network"]["enabled"] is False
    assert profiled.mcp_fingerprint != mcp.fingerprint
    seen = []
    monkeypatch.setattr(profiled, "call", lambda *a, **k: seen.append(a))
    profiled.start_agent("own", "w1:p1", str(work))
    assert list(seen[0][-len(args):]) == args
    execution_policy.binary.chmod(0o755)
    execution_policy.binary.write_bytes(b"changed\n")
    with pytest.raises(HerdrError, match="FILES_UNVERIFIED"):
        profiled.start_agent("own", "w1:p1", str(work))
    assert len(seen) == 1


def test_profile_rejects_writable_or_exposed_helper_and_inconsistent_role(
    execution_policy, tmp_path, monkeypatch,
):
    monkeypatch.setenv("HERDR_ENV", "1")
    with pytest.raises(HerdrError, match="CONFIGURATION"):
        HerdrAdapter("own", execution_policy=execution_policy)
    mcp = CodexMCPLaunch(command=sys.executable, args=("/host.py",))
    with pytest.raises(HerdrError, match="CONFIGURATION"):
        HerdrAdapter("own", mcp=mcp, execution_policy=execution_policy)
    with pytest.raises(ExecutionPolicyError):
        execution_policy.verify_files(execution_policy.helper.parent.parent)
    (execution_policy.helper.parent / "must-not-expose-config").write_text("fake only")
    with pytest.raises(ExecutionPolicyError):
        execution_policy.verify_files(tmp_path / "work")


@pytest.mark.parametrize("name", ["*", "with.dot", "bad\nname", "--option"])
def test_per_tool_approval_is_finite_and_not_a_global_switch(name):
    with pytest.raises(ValidationError):
        CodexMCPLaunch(command=sys.executable, args=("/host.py",), auto_approved_tools=(name,))


def test_own_tool_approval_roundtrip_changes_only_registered_server():
    base = CodexMCPLaunch(command=sys.executable, args=("/host.py",))
    scoped = base.model_copy(update={"auto_approved_tools": ("worker_commit",)})
    config = tomllib.loads("\n".join(scoped.cli_args()[1::2]))
    assert config["mcp_servers"]["herdr_coordinator"]["tools"] == {
        "worker_commit": {"approval_mode": "approve"},
    }
    assert scoped.fingerprint != base.fingerprint
    assert scoped.cli_args()[:-2] == base.cli_args()


def test_profile_verification_requires_pinned_native_binary_and_exact_full_argv(
    execution_policy, tmp_path, monkeypatch,
):
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setattr("orchestrator.adapters.herdr.shutil.which",
                        lambda name: str(execution_policy.binary) if name == "codex" else None)
    monkeypatch.setattr("orchestrator.adapters.herdr.process_stamp", lambda *a: "live-stamp")
    work = tmp_path / "work"
    work.mkdir()
    adapter = HerdrAdapter("own", sandbox="workspace-write", execution_policy=execution_policy,
                           mcp=CodexMCPLaunch(command=sys.executable, args=("/host.py",)))
    binding = dict(workspace_id="w1", tab_id="w1:t1", pane_id="w1:p1", terminal_id="term1")
    agent = binding | dict(name="own", agent="codex", cwd=str(work), foreground_cwd=str(work),
                            interactive_ready=True, agent_status="idle")
    args = ["codex", *adapter._launch_args(str(work))]
    monkeypatch.setattr(adapter, "process_info", lambda _: dict(
        pane_id=binding["pane_id"], foreground_processes=[dict(pid=123, argv=args, cwd=str(work))],
    ))
    assert adapter.verify_agent(agent, binding, str(work), "own")["ready"]
    args += ["-c", 'default_permissions="unverified"']
    with pytest.raises(HerdrError, match="RUNTIME_IDENTITY_MISMATCH"):
        adapter.verify_agent(agent, binding, str(work), "own")


@pytest.mark.skipif(not os.environ.get("HERDR_REAL_CODEX_BINARY"),
                    reason="explicit installed Codex binary required for real kernel audit")
def test_real_restricted_command_denies_host_state_git_socket_and_process(tmp_path):
    binary = Path(os.environ["HERDR_REAL_CODEX_BINARY"]).resolve()
    operator = tmp_path / "operator"
    operator.mkdir()
    tools = operator / "tools"
    tools.mkdir(mode=0o700)
    helper = tools / "codex-linux-sandbox"
    helper.symlink_to(binary)
    policy = CodexExecutionPolicy(workspace_access="write", helper=helper, binary=binary,
                                  binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()
    (work / ".git/marker").write_text("untouched\n")
    sentinel = operator / "state"
    sentinel.write_text("harmless host state\n")
    sock = socket.socket(socket.AF_UNIX)
    address = str(operator / "host.sock")
    sock.bind(address)
    sock.listen(1)
    script = """from pathlib import Path
import json,os,socket
r={}
(Path.cwd()/'own.txt').write_text('own output')
for key,path,mode in [('outside',SENTINEL,'r'),('git','.git/marker','w')]:
 try:
  Path(path).read_bytes() if mode=='r' else Path(path).write_text('must not persist')
  r[key]=True
 except OSError as e:r[key]=e.errno
try:
 s=socket.socket(socket.AF_UNIX);s.connect(ADDRESS);r['socket']=True
except OSError as e:r['socket']=e.errno
try:os.kill(PARENT,0);r['parent']=True
except OSError as e:r['parent']=e.errno
print(json.dumps(r))
""".replace("SENTINEL", repr(str(sentinel))).replace("ADDRESS", repr(address)).replace(
        "PARENT", str(os.getpid()),
    )
    (work / "probe.py").write_text(script)
    policy.verify_files(work)
    try:
        result = subprocess.run(
            ["codex", "sandbox", "-P", policy.name, *policy.cli_args(), "-C", str(work),
             "python3", "probe.py"], capture_output=True, text=True, timeout=30,
        )
    finally:
        sock.close()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"outside": 2, "git": 30, "socket": 1, "parent": 3}
    assert sentinel.read_text() == "harmless host state\n"
    assert (work / ".git/marker").read_text() == "untouched\n"
    assert (work / "own.txt").read_text() == "own output"


@pytest.mark.skipif(not os.environ.get("HERDR_REAL_CODEX_BINARY"),
                    reason="explicit installed binary required for composed boundary audit")
def test_real_all_role_profiles_and_exact_verification_process(tmp_path):
    from native_f41_gate import isolated_argv, measure

    binary = Path(os.environ["HERDR_REAL_CODEX_BINARY"]).resolve()
    tools = tmp_path / "tools"
    tools.mkdir(mode=0o700)
    helper = tools / "codex-linux-sandbox"
    helper.symlink_to(binary)
    policy = CodexExecutionPolicy(workspace_access="write", helper=helper, binary=binary,
                                  binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    read = policy.model_copy(update={"workspace_access": "read"})
    config = dict(profiles=dict(worker=policy.model_dump(mode="json"),
                                coordinator=read.model_dump(mode="json"),
                                integration=read.model_dump(mode="json")), settings=dict(
        worker_test_command=isolated_argv("/usr/bin/python3", "-B", "-m", "unittest",
                                          "discover", "-s", "tests", "-v"),
    ))
    result = measure(config)
    assert set(result) == {"worker", "coordinator", "integration", "test_process"}
    assert result["worker"]["own"] is True
    assert all(result[k]["own"] == 30 for k in ("coordinator", "integration", "test_process"))
