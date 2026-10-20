"""Operator preparation/lifecycle only; native agents schedule/review/merge the scenario."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

from native_f37_host import protected_config
from native_f37_probe import save
from native_f41_gate import measure, native_preflight, verify_receipt
from native_f41_host import adapter_for, composition, fingerprint_files
from native_f41_setup import LOCAL, task

from orchestrator.application.runtime_lifecycle_service import RuntimeLifecycleService
from orchestrator.application.state_service import StateService
from orchestrator.application.task_start_service import TaskStartService
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.config import Settings
from orchestrator.domain.models import TaskRun, utc_now
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import EpicState
from orchestrator.persistence.store import StateStore


def require_approval(c):
    """Concrete external user decision; this command never creates one."""
    approval = protected_config(Path(c["approval"]))
    scope = Path(c["approval"]).parent / "scope.json"
    if approval.get("source") != "USER_EXPLICIT_F41_R1" or (
        approval.get("scope_sha256") != hashlib.sha256(scope.read_bytes()).hexdigest()
        or approval.get("boundary_hash") != fingerprint_files(c)
        or not approval.get("user_instruction") or not approval.get("received_at")
    ):
        raise RuntimeError("F41_EXPLICIT_SCOPED_USER_DECISION_REQUIRED")


def preflight_scope(c):
    settings = Settings.model_validate(c["settings"])
    coordinator = Actor(actor_id="f41-preflight-coordinator", role=Role.COORDINATOR,
                        project_id=LOCAL)
    integration = Actor(actor_id="f41-preflight-integration", role=Role.INTEGRATION,
                        project_id=LOCAL, epic_run_id="f41-native-preflight")
    return settings, coordinator, integration


def start_preflight(c, path):
    settings, coordinator, integration = preflight_scope(c)
    native = adapter_for(c, path, "worker")
    with StateStore(settings.sqlite_path) as store:
        epic = WorktreeService(settings, store).create_epic_worktree(
            coordinator, epic_id="PREFLIGHT", run_id=integration.epic_run_id,
        )
        StateService(store).transition_epic(
            epic.id, EpicState.ACTIVE, expected=EpicState.PLANNED, event_id="preflight-active",
            actor=coordinator,
        )
        spec = task("PREFLIGHT", "NATIVE", "F41 real restricted broker preflight",
                    "Implement answer() returning integer42 in answer.py and real unittest in "
                    "tests/test_answer.py. Run tests with python3 -B. Use worker_workspace and "
                    "worker_commit, stable key 'preflight-result', to make exactly one actual own "
                    "commit. Do not use shell git or any other service/host path. Reply with the "
                    "actual required final JSON. This is a separate registered product test, "
                    "not a development worktree or TeamPlayer implementation task.",
                    ["answer() returns42 and real unittest passes; own broker commit exists"],
                    ["answer.py", "tests/test_answer.py"])["task"]
        result = TaskStartService(settings, store, native).start(integration, epic.id, spec)
        save(Path(c["root"]) / "start.json", result)
        print(json.dumps(dict(status=result["status"], task_run_id=result["task"]["id"])))


async def main():
    path, command = Path(sys.argv[1]), sys.argv[2]
    c = protected_config(path)
    fingerprint_files(c)
    if command == "measure":
        result = measure(c)
        save(Path(c["root"]) / "standalone-measurement.json", result)
        print(json.dumps(dict(status="MEASURED_NOT_NATIVE_GATE", measurements=result)))
        return
    require_approval(c)
    if command == "start-preflight":
        start_preflight(c, path)
        return
    if command == "stop-preflight":
        settings, _, actor = preflight_scope(c)
        with StateStore(settings.sqlite_path) as store:
            tasks = [r for r in store.get_runs() if isinstance(r, TaskRun)]
            if len(tasks) != 1:
                raise RuntimeError("F41_PREFLIGHT_TASK_REQUIRED")
            lifecycle = RuntimeLifecycleService(settings, store, adapter_for(c, path, "worker"))
            result = lifecycle.stop_task(actor, tasks[0].id, key="f41-preflight-final-stop",
                                         reason="Actual native broker preflight complete")
            save(Path(c["root"]) / "stop.json", result.model_dump(mode="json"))
            print(json.dumps(dict(status=lifecycle.reconnect_task(actor, tasks[0].id)["status"])))
        return
    async with composition(path) as env:
        runtime = env.runtime
        if command == "start-coordinator":
            result = runtime.start(runtime.principal)
            save(Path(c["root"]) / "start.json", result.model_dump(mode="json"))
            print(json.dumps(dict(status=result.status, session_id=result.codex_session_id)))
        elif command == "refresh-gate":
            receipt = dict(version=1, boundary_hash=fingerprint_files(c),
                           configuration_hash=runtime.configuration_hash,
                           main_commit=runtime.main(), preflight=native_preflight(c),
                           measurements=measure(c), measured_at=utc_now().isoformat())
            verify_receipt(c, runtime, runtime.main(), receipt)
            save(Path(c["gate_receipt"]), receipt)
            print(json.dumps(dict(status="MEASURED_CURRENT_NATIVE_GATE",
                                  main_commit=receipt["main_commit"])))
        elif command == "send-coordinator":
            runtime.connection_principal(runtime.principal)
            run = env.store.get_coordinator(runtime.run_id)
            text = Path(sys.argv[3]).read_text()
            digest = hashlib.sha256(text.encode()).hexdigest()
            intent_path = Path(c["root"]) / ("operator-control-" + digest + ".json")
            if intent_path.exists():
                print(json.dumps(dict(stage="EXISTING_INTENT_NO_RESEND")))
                return
            thread = runtime.codex.read_thread(run.codex_session_id, run.worktree_path)
            intent = dict(id=str(uuid4()), text=text, text_hash=digest, stage="INTENT",
                          session_id=run.codex_session_id,
                          baseline_turn_ids=[t["id"] for t in thread["turns"]],
                          created_at=utc_now().isoformat())
            save(intent_path, intent, exclusive=True)
            try:
                runtime.herdr.prompt(run.coordinator_agent_id, text, timeout_ms=45000)
            except Exception:
                save(intent_path, intent | dict(stage="UNKNOWN"))
                print(json.dumps(dict(stage="UNKNOWN_NO_RESEND")))
                return
            save(intent_path, intent | dict(stage="RETURNED"))
            print(json.dumps(dict(stage="RETURNED")))
        elif command == "observe":
            root = env.store.get_coordinator(runtime.run_id)
            runs = ([root] if root else []) + env.store.get_runs()
            for run in runs:
                if run.codex_session_id:
                    save(Path(c["root"]) / (run.id + "-thread.json"),
                         runtime.codex.read_thread(run.codex_session_id, run.worktree_path))
            rows = env.store.db.execute("SELECT DISTINCT kind FROM project_operations").fetchall()
            operations = [o for (kind,) in rows for o in env.store.project_operations(
                runtime.run_id, kind=kind,
            )]
            save(Path(c["root"]) / "observed.json", dict(
                main=runtime.main(), runs=[r.model_dump(mode="json") for r in runs],
                project_operations=[o.model_dump(mode="json") for o in operations],
            ))
            print(json.dumps(dict(main=runtime.main(), runs=[dict(
                id=r.id, state=getattr(r, "internal_status", getattr(r, "status", None)),
                slot=getattr(r, "worker_slot", None), session_id=r.codex_session_id,
            ) for r in runs])))
        elif command == "stop-coordinator":
            result = runtime.stop(runtime.principal, "f41-r1-final-root-stop")
            save(Path(c["root"]) / "stop.json", result.model_dump(mode="json"))
            print(json.dumps(dict(status=result.status)))
        else:
            raise RuntimeError("F41_OPERATOR_COMMAND_INVALID")


if __name__ == "__main__":
    asyncio.run(main())
