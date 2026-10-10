"""Operator start/control/read/stop only; native Integration owns every task decision."""

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from native_f37_host import composition


def save(path, value, *, exclusive=False):
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


async def main():
    config_path, command = Path(sys.argv[1]), sys.argv[2]
    async with composition(config_path) as (c, _, store, control, h, coordinator):
        root, run = Path(c["root"]), control.actor.epic_run_id
        if command == "start":
            # Reuse F34's original durable intent; no independent task control.
            for _ in range(20):
                result = await control.epic_start.start(
                    coordinator, c["spec"]["project_id"], c["spec"]["epic_id"], run
                )
                save(root / "start.json", result)
                print(json.dumps({"stage": result["stage"]}), flush=True)
                if result["stage"] == "REGISTERED":
                    break
                await asyncio.sleep(0.3)
        elif command == "send":
            epic = store.get_epic(run)
            control.epic_start.connection_principal(control.actor)
            text = Path(sys.argv[3]).read_text()
            intent_path = root / ("control-" + hashlib.sha256(text.encode()).hexdigest() + ".json")
            thread = control.epic_start.codex.read_thread(epic.codex_session_id, epic.worktree_path)
            if intent_path.exists():
                # Never repeat a prompt with unknown or known outcome to repair observation.
                print(json.dumps({"stage": "EXISTING_CONTROL_INTENT", "path": str(intent_path)}))
                return
            intent = dict(
                request_id=str(uuid4()),
                stage="INTENT",
                text=text,
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                run_id=run,
                session_id=epic.codex_session_id,
                baseline_turn_ids=[t["id"] for t in thread["turns"]],
                created_at=datetime.now(UTC).isoformat(),
            )
            save(intent_path, intent, exclusive=True)  # BEFORE external prompt.
            try:
                h.prompt(epic.integration_agent_id, text, timeout_ms=45000)
            except Exception:
                save(intent_path, intent | {"stage": "OUTCOME_UNKNOWN"})
                print(
                    json.dumps(
                        {"stage": "OUTCOME_UNKNOWN", "next": "Read original thread; no resend"}
                    )
                )
                return
            save(intent_path, intent | {"stage": "PROMPT_RETURNED"})
            print(json.dumps({"stage": "PROMPT_RETURNED", "session_id": epic.codex_session_id}))
        elif command == "observe":
            epic = store.get_epic(run)
            thread = control.epic_start.codex.read_thread(epic.codex_session_id, epic.worktree_path)
            overview = await control.overview(control.actor)
            save(root / "integration-thread.json", thread)
            save(root / "overview.json", overview)
            tasks = store.get_tasks(run)
            for task in tasks:
                if task.codex_session_id:
                    save(
                        root / (task.task_id + "-thread.json"),
                        control.epic_start.codex.read_thread(
                            task.codex_session_id, task.worktree_path
                        ),
                    )
            print(
                json.dumps(
                    {
                        "epic_run_id": run,
                        "session_id": epic.codex_session_id,
                        "state": epic.status.value,
                        "tasks": [
                            dict(
                                task_id=t.task_id,
                                run_id=t.id,
                                state=t.internal_status.value,
                                session_id=t.codex_session_id,
                                slot=t.worker_slot,
                                merge_commit=t.merge_commit,
                            )
                            for t in tasks
                        ],
                        "turns": len(thread["turns"]),
                    }
                ),
                flush=True,
            )
        elif command == "handoff":
            # Independent read: never create/replay epic_complete or task decisions.
            receipt = await control.epic_completion.require_current(
                control.actor, run, key=sys.argv[3]
            )
            save(root / "confirmed-handoff.json", receipt.model_dump(mode="json"))
            print(
                json.dumps(
                    {
                        "stage": receipt.result["stage"],
                        "id": receipt.id,
                        "source_commit": receipt.result["snapshot"]["source_commit"],
                    }
                )
            )
        elif command == "stop":
            op = control.epic_start.lifecycle.stop_epic(coordinator, run, key="f37-final-stop")
            save(root / "stop.json", op.model_dump(mode="json"))
            actual = control.epic_start.lifecycle.reconnect_epic(coordinator, run)
            if actual["status"] != "STOPPED":
                raise RuntimeError("PROBE_STOP_NOT_CONFIRMED")
            save(root / "confirmed-stop.json", actual)
            print(json.dumps({"stage": "STOPPED", "operation_id": op.id}))
        else:
            raise RuntimeError("PROBE_COMMAND_UNKNOWN")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(
            json.dumps({"stage": "OPERATOR_STEP_FAILED", "type": type(error).__name__}),
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2) from None
