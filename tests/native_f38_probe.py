"""Actual operator start/observe/send/stop; Coordinator itself must call scoped tools."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

from native_f37_probe import save
from native_f38_host import composition

from orchestrator.application.coordinator_runtime_service import CoordinatorError
from orchestrator.domain.models import utc_now


async def main():
    path, command = Path(sys.argv[1]), sys.argv[2]
    async with composition(path) as (config, runtime, _, _):
        root, actor = Path(config["root"]), runtime.principal
        if command == "start":
            for _ in range(20):
                try:
                    run = runtime.start(actor)
                    save(root / "start.json", run.model_dump(mode="json"))
                    print(json.dumps(dict(status=run.status, session_id=run.codex_session_id)),
                          flush=True)
                    return
                except CoordinatorError as error:
                    if str(error) not in {
                        "COORDINATOR_ACK_PENDING", "COORDINATOR_ACK_UNVERIFIED",
                        "COORDINATOR_INPUT_NOT_READY",
                    }:
                        raise
                    print(json.dumps(dict(status=str(error))), flush=True)
                    await asyncio.sleep(0.5)
            raise RuntimeError("F38_NATIVE_START_NOT_CONFIRMED")
        elif command == "observe":
            run = runtime.store.get_coordinator(runtime.run_id)
            if run is None:
                raise RuntimeError("F38_NATIVE_RUN_MISSING")
            thread = runtime.codex.read_thread(run.codex_session_id, run.worktree_path)
            save(root / "thread.json", thread)
            save(root / "observed.json", dict(
                coordinator=run.model_dump(mode="json"), main=runtime.main(),
                epic_tasks=[r.model_dump(mode="json") for r in runtime.store.get_runs()],
                claims=[o.model_dump(mode="json") for o in runtime.store.project_operations(
                    runtime.run_id, kind="epic_claim",
                )],
            ))
            print(json.dumps(dict(
                status=run.status, session_id=run.codex_session_id,
                turns=len(thread["turns"]), epic_tasks=len(runtime.store.get_runs()),
            )))
        elif command == "send":
            runtime.connection_principal(actor)
            run = runtime.store.get_coordinator(runtime.run_id)
            text = Path(sys.argv[3]).read_text()
            intent_path = root / ("control-" + hashlib.sha256(text.encode()).hexdigest() + ".json")
            if intent_path.exists():
                print(json.dumps(dict(status="EXISTING_INTENT_NO_RESEND")))
                return
            thread = runtime.codex.read_thread(run.codex_session_id, run.worktree_path)
            intent = dict(
                id=str(uuid4()), stage="INTENT", text=text,
                text_hash=hashlib.sha256(text.encode()).hexdigest(),
                session_id=run.codex_session_id,
                baseline_turn_ids=[t["id"] for t in thread["turns"]],
                created_at=utc_now().isoformat(),
            )
            save(intent_path, intent, exclusive=True)
            try:
                runtime.herdr.prompt(run.coordinator_agent_id, text, timeout_ms=45000)
            except Exception:
                save(intent_path, intent | {"stage": "UNKNOWN"})
                print(json.dumps(dict(status="UNKNOWN_NO_RESEND")))
                return
            save(intent_path, intent | {"stage": "RETURNED"})
            print(json.dumps(dict(status="PROMPT_RETURNED")))
        elif command == "stop":
            stopped = runtime.stop(actor, "f38-r1-final-stop")
            save(root / "stop.json", stopped.model_dump(mode="json"))
            print(json.dumps(dict(status=stopped.status, session_id=stopped.codex_session_id)))
        else:
            raise RuntimeError("F38_NATIVE_COMMAND_INVALID")


if __name__ == "__main__":
    asyncio.run(main())
