"""Re-measured OS boundaries plus actual native broker/stop proof, never a permit."""

import hashlib
import json
import os
import socket
import subprocess
import tempfile
from pathlib import Path

from native_f37_host import protected_config

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.git import GitAdapter
from orchestrator.application.runtime_lifecycle_service import RuntimeLifecycleService
from orchestrator.config import Settings
from orchestrator.domain.codex_execution import CodexExecutionPolicy
from orchestrator.domain.models import TaskRun
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.worker_contracts import canonical_json
from orchestrator.persistence.store import StateStore


def isolated_argv(*command):
    argv = ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib"]
    if Path("/lib64").exists():
        argv += ["--ro-bind", "/lib64", "/lib64"]
    return (*argv, "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--ro-bind", ".", "/work", "--chdir", "/work", "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--", *command)


def measure(c):
    """Actual child effects; only newly created harmless files/sockets are targeted."""
    results = {}
    with tempfile.TemporaryDirectory(prefix="herdr-f41-boundary-") as temporary:
        root = Path(temporary)
        work = root / "work"
        work.mkdir()
        (work / ".git").mkdir()
        (work / ".git/marker").write_text("unchanged")
        state = root / "protected-state"
        state.write_text("harmless boundary sentinel")
        outside = root / "outside"
        outside.write_text("harmless outside sentinel")
        address = str(root / "host.sock")
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(address)
        sock.listen(1)
        tcp = socket.socket()
        tcp.bind(("127.0.0.1", 0))
        tcp.listen(1)
        script = '''import json,os,socket
from pathlib import Path
r={}
for key,path,write in CASES:
 try:
  Path(path).write_text('probe') if write else Path(path).read_bytes()
  r[key]=True
 except OSError as e:r[key]=e.errno
try:
 s=socket.socket(socket.AF_UNIX);s.connect(ADDRESS);r['socket']=True
except OSError as e:r['socket']=e.errno
try:
 s=socket.socket();s.bind(('127.0.0.1',0));r['network']=True
except OSError as e:r['network']=e.errno
try:
 s=socket.socket();r['host_network']=s.connect_ex(('127.0.0.1',HOST_PORT))
except OSError as e:r['host_network']=e.errno
try:os.kill(PARENT,0);r['parent']=True
except OSError as e:r['parent']=e.errno
print(json.dumps(r,sort_keys=True))
'''.replace("CASES", repr([
            ("outside", str(outside), False), ("state", str(state), False),
            ("git", ".git/marker", True), ("own", "own.txt", True),
        ])).replace("ADDRESS", repr(address)).replace("PARENT", str(os.getpid())).replace(
            "HOST_PORT", str(tcp.getsockname()[1]),
        )
        (work / "probe.py").write_text(script)
        env = {k: os.environ[k] for k in ("PATH", "HOME", "USER") if k in os.environ}
        try:
            for role in ("worker", "coordinator", "integration"):
                policy = CodexExecutionPolicy.model_validate(c["profiles"][role])
                policy.verify_files(work)
                result = subprocess.run(
                    [str(policy.binary), "sandbox", "-P", policy.name, *policy.cli_args(),
                     "-C", str(work), "/usr/bin/python3", "-B", "probe.py"],
                    capture_output=True, text=True, timeout=30, env=env,
                )
                if result.returncode:
                    raise RuntimeError("F41_PROFILE_MEASUREMENT_FAILED")
                facts = json.loads(result.stdout)
                expected = dict(outside=2, state=2, git=30, own=True if role == "worker" else 30,
                                socket=1, network=1, host_network=1, parent=3)
                if facts != expected:
                    raise RuntimeError("F41_PROFILE_BOUNDARY_NOT_ENFORCED")
                results[role] = facts
                (work / "own.txt").unlink(missing_ok=True)
            prefix = isolated_argv("/usr/bin/python3", "-B")
            if tuple(c["settings"]["worker_test_command"]) != (*prefix, "-m", "unittest",
                                                               "discover", "-s", "tests", "-v"):
                raise RuntimeError("F41_VERIFICATION_ARGV_CHANGED")
            result = subprocess.run([*prefix, "probe.py"], cwd=work,
                                    capture_output=True, text=True, timeout=30, env=env)
            if result.returncode:
                raise RuntimeError("F41_TEST_ISOLATION_MEASUREMENT_FAILED")
            facts = json.loads(result.stdout)
            if facts != dict(outside=2, state=2, git=30, own=30, socket=2, network=True,
                             host_network=111, parent=3):
                raise RuntimeError("F41_TEST_BOUNDARY_NOT_ENFORCED")
            results["test_process"] = facts
        finally:
            sock.close()
            tcp.close()
        if state.read_text() != "harmless boundary sentinel" or (
            outside.read_text() != "harmless outside sentinel"
            or (work / ".git/marker").read_text() != "unchanged"
        ):
            raise RuntimeError("F41_HOST_SENTINEL_CHANGED")
    return results


def native_preflight(c):
    """Actual registered native Worker, own commit and confirmed physical stop."""
    from native_f41_host import adapter_for, fingerprint_files
    path = Path(c["preflight_config"])
    preflight = protected_config(path)
    if fingerprint_files(preflight) != fingerprint_files(c):
        raise RuntimeError("F41_PREFLIGHT_COMPOSITION_CHANGED")
    settings = Settings.model_validate(preflight["settings"])
    native = adapter_for(preflight, path, "worker")
    with StateStore(settings.sqlite_path) as store:
        tasks = [r for r in store.get_runs() if isinstance(r, TaskRun)]
        if len(tasks) != 1:
            raise RuntimeError("F41_NATIVE_PREFLIGHT_REQUIRED")
        task = tasks[0]
        assignment = store.get_operation(task.project_id, "task_start", task.task_id)
        start = store.get_operation(task.project_id, "start_runtime", task.id)
        commits = [o for o in store.get_operations(task.epic_run_id, kind="worker_commit")
                   if o.task_run_id == task.id and o.status == "SUCCEEDED"
                   and o.result.get("stage") == "COMMITTED"]
        if assignment is None or assignment.status != "SUCCEEDED" or (
            assignment.result.get("stage") != "WORKING" or start is None
            or start.result.get("mcp_configuration") != native.mcp_fingerprint
            or not task.codex_session_id or len(commits) != 1
            or commits[0].result.get("session_id") != task.codex_session_id
        ):
            raise RuntimeError("F41_NATIVE_BROKER_PROOF_REQUIRED")
        actor = Actor(actor_id="f41-preflight-integration", role=Role.INTEGRATION,
                      project_id=task.project_id, epic_run_id=task.epic_run_id)
        lifecycle = RuntimeLifecycleService(settings, store, native)
        if lifecycle.reconnect_task(actor, task.id)["status"] != "STOPPED":
            raise RuntimeError("F41_PREFLIGHT_PHYSICAL_STOP_REQUIRED")
        git = GitAdapter(settings.repository)
        commit = commits[0].result["commit"]
        if git.inspect(Path(task.worktree_path), task.branch, clean=True) != commit or (
            git.parents(commit) != (commits[0].result["parent"],)
            or "herdr-worker-commit:" + commits[0].id not in git.run(
                "show", "-s", "--format=%B", commit,
            ).splitlines()
        ):
            raise RuntimeError("F41_NATIVE_PREFLIGHT_COMMIT_CHANGED")
        thread = CodexAdapter().read_thread(task.codex_session_id, task.worktree_path)
        calls = [i for t in thread["turns"] for i in t["items"] if i["type"] == "mcpToolCall"
                 and i.get("server") == "herdr_coordinator" and i.get("tool") == "worker_commit"]
        if not calls or not any(i.get("status") == "completed" and not i.get("error")
                                for i in calls):
            raise RuntimeError("F41_ACTUAL_NATIVE_MCP_CALL_REQUIRED")
        return dict(task_run_id=task.id, session_id=task.codex_session_id,
                    commit=commit, operation_id=commits[0].id,
                    thread_sha256=hashlib.sha256(canonical_json(thread).encode()).hexdigest())


def verify_receipt(c, runtime, main, receipt):
    preflight = native_preflight(c)
    if receipt["preflight"] != preflight or runtime.main() != main:
        raise RuntimeError("F41_PREFLIGHT_RECEIPT_CHANGED")
    actual = measure(c)
    if receipt["measurements"] != actual:
        raise RuntimeError("F41_CURRENT_BOUNDARY_CHANGED")
    # Registration only succeeded after real native ACK and interactive readiness.
    runtime.connection_principal(runtime.principal)
    run = runtime.store.get_coordinator(runtime.run_id)
    start = runtime.store.get_project_operation(run.project_id, runtime.KIND, run.id)
    if start.status != "SUCCEEDED" or start.result.get("stage") != "REGISTERED":
        raise RuntimeError("F41_NATIVE_ROOT_INPUT_UNVERIFIED")
    return dict(version=1, configuration_hash=runtime.configuration_hash, main_commit=main,
                probe_source="F41 measured profiles/tests and registered native broker/stop",
                outside_read_denied=all(v["outside"] == 2 for v in actual.values()),
                protected_state_denied=all(v["state"] == 2 for v in actual.values()),
                git_role_boundaries_verified=bool(preflight["operation_id"])
                and all(v["git"] == 30 for v in actual.values()),
                verification_process_isolated=actual["test_process"]["parent"] == 3
                and actual["test_process"]["host_network"] == 111,
                input_readiness_verified=run.codex_session_id is not None)
