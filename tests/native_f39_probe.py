"""Operator lifecycle/observation only. Native agents own all task/review decisions."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

from native_f37_probe import save
from native_f39_host import composition

from orchestrator.domain.models import utc_now


async def main():
    path, command = Path(sys.argv[1]), sys.argv[2]
    async with composition(path) as (c, runtime, original, subject, review, _, control):
        root, actor = Path(c["root"]), runtime.principal
        run_id = original.principal.epic_run_id
        if command == "start-coordinator":
            run = runtime.start(actor)
            save(root / "coordinator-start.json", run.model_dump(mode="json"))
            print(json.dumps(dict(status=run.status, session_id=run.codex_session_id)))
        elif command == "start-integration":
            runtime.connection_principal(actor)
            result = await original.start(actor, runtime.spec.project_id,
                                           original.spec.epic_id, run_id)
            save(root / "integration-start.json", result)
            print(json.dumps(dict(stage=result["stage"], session_id=result.get("session_id"))))
        elif command == "attach":
            result = await review.attach_registered_product_run(actor, run_id)
            save(root / "actual-product-attachment.json", result.model_dump(mode="json"))
            print(json.dumps(dict(status=result.status, id=result.id)))
        elif command in {"send-coordinator", "send-integration"}:
            runtime.connection_principal(actor)
            role = command.removeprefix("send-")
            if role == "coordinator":
                run = runtime.store.get_coordinator(runtime.run_id)
                native, name = runtime.herdr, run.coordinator_agent_id
            else:
                original.connection_principal(original.principal)
                run = runtime.store.get_epic(run_id)
                native, name = original.herdr, run.integration_agent_id
            text = Path(sys.argv[3]).read_text()
            digest = hashlib.sha256(text.encode()).hexdigest()
            intent_path = root / (role + "-control-" + digest + ".json")
            if intent_path.exists():
                print(json.dumps(dict(stage="EXISTING_INTENT_NO_RESEND")))
                return
            thread = runtime.codex.read_thread(run.codex_session_id, run.worktree_path)
            intent = dict(id=str(uuid4()), role=role, text=text, sha256=digest,
                          stage="INTENT", session_id=run.codex_session_id,
                          baseline_turn_ids=[t["id"] for t in thread["turns"]],
                          created_at=utc_now().isoformat())
            save(intent_path, intent, exclusive=True)
            try:
                native.prompt(name, text, timeout_ms=45000)
            except Exception:
                save(intent_path, intent | dict(stage="UNKNOWN"))
                print(json.dumps(dict(stage="UNKNOWN_NO_RESEND")))
                return
            save(intent_path, intent | dict(stage="RETURNED"))
            print(json.dumps(dict(stage="RETURNED", session_id=run.codex_session_id)))
        elif command == "observe":
            coordinator = runtime.store.get_coordinator(runtime.run_id)
            epic = runtime.store.get_epic(run_id)
            runs = [coordinator, epic, *runtime.store.get_tasks(run_id)]
            for run in runs:
                if run is not None and run.codex_session_id:
                    thread = runtime.codex.read_thread(run.codex_session_id, run.worktree_path)
                    save(root / (run.id + "-thread.json"), thread)
            operations = runtime.store.project_operations(runtime.run_id)
            save(root / "observed.json", dict(
                runs=[r.model_dump(mode="json") for r in runs if r is not None],
                project_operations=[o.model_dump(mode="json") for o in operations],
                epic_operations=[o.model_dump(mode="json")
                                 for o in runtime.store.get_operations(run_id)] if epic else [],
                main=runtime.main(),
                effective_spec=subject.plan.current()[0].model_dump(mode="json"),
            ))
            print(json.dumps(dict(
                coordinator=coordinator.status if coordinator else None,
                epic=epic.status if epic else None,
                tasks=[dict(id=t.task_id, status=t.internal_status, slot=t.worker_slot,
                            session_id=t.codex_session_id)
                       for t in runtime.store.get_tasks(run_id)],
                decisions=[dict(id=o.id, stage=o.result.get("stage"))
                           for o in operations if o.kind == review.REVIEW],
            )))
        elif command == "stop-integration":
            runtime.connection_principal(actor)
            result = original.lifecycle.stop_epic(actor, run_id, key="f39-r1-integration-stop")
            save(root / "integration-stop.json", result.model_dump(mode="json"))
            actual = original.lifecycle.reconnect_epic(actor, run_id)
            save(root / "integration-stop-proof.json", actual)
            print(json.dumps(dict(status=actual["status"])))
        elif command == "stop-coordinator":
            result = runtime.stop(actor, "f39-r1-coordinator-stop")
            save(root / "coordinator-stop.json", result.model_dump(mode="json"))
            print(json.dumps(dict(status=result.status)))
        else:
            raise RuntimeError("F39_NATIVE_COMMAND_INVALID")


if __name__ == "__main__":
    asyncio.run(main())
