"""Explicit F32 operator probe: actual F31 parked run, never a replacement session."""

import argparse
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

import f30_native as shared
import f31_native as prior

from orchestrator.application.task_attention_service import TaskAttentionService
from orchestrator.application.task_resume_service import ResumeError, TaskResumeService
from orchestrator.config import Settings
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.states import TaskState
from orchestrator.persistence.store import StateStore

BASE = Path.cwd() / ".herdr/probes/f32"


def save(name, value):
    (BASE / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def configure(source):
    prior.BASE = source.resolve(strict=True)
    prior.PROJECT, prior.EPIC, prior.RUN = "f31-probe-r2", "f31-epic-r2", "f31-epic-run-r2"
    prior.INTEGRATION = Actor(
        actor_id="f31-integration-r2",
        role=Role.INTEGRATION,
        project_id=prior.PROJECT,
        epic_run_id=prior.RUN,
    )
    shared.BASE = BASE
    settings = Settings.model_validate(prior.load("settings"))
    if (
        os.environ.get("HERDR_ENV") != "1"
        or settings.sqlite_path != prior.BASE / "state.sqlite"
        or prior.load("operator")["server"] != "hc-f31-r2-20261009"
    ):
        raise RuntimeError("F32 exact preserved F31 fixture required")
    return settings


async def operate(phase, source):
    settings = configure(source)
    BASE.mkdir(parents=True, exist_ok=True)
    with StateStore(settings.sqlite_path) as db:
        tasks = db.get_tasks(prior.RUN)
        assert len(tasks) == 1
        task = tasks[0]
        async with shared.connection() as adapter:
            sync, h, c = prior.composition(settings, db, adapter)
            service = TaskResumeService(TaskAttentionService(settings, db, sync, h, c))
            if phase == "prepare":
                assert not (BASE / "decision.json").exists()
                assert task.internal_status == TaskState.PARKED and task.worker_slot is None
                assert service.lifecycle.confirm_task_inactive(prior.INTEGRATION, task.id)
                attention = db.get_operations(prior.RUN, kind="task_attention")
                assert (
                    len(attention) == 1
                    and attention[0].id == "71d62a8a-0374-40a2-83fb-d8c1fe3c6106"
                )
                assert task.codex_session_id == "01a1212b-1132-7f13-aa40-e3af437b129e"
                save("before", task.model_dump(mode="json"))
                save(
                    "decision",
                    dict(
                        version=1,
                        input_id=str(uuid4()),
                        blocker_id=attention[0].id,
                        answer="Explicit operator fixture decision: RETENTION_DAYS=7. "
                        "Use strict positive integer 7 for f31_retention.retention_days() and own "
                        "tests/test_f31_retention.py. This is harmless test input within "
                        "the original "
                        "scope. Historical greeting files remain unchanged.",
                    ),
                )
                save(
                    "source",
                    dict(
                        path=str(prior.BASE),
                        settings_database=str(settings.sqlite_path),
                        server=h.server_session,
                        fixture=prior.load("fixture"),
                    ),
                )
                print("Explicit bounded fixture input saved; no resume effect yet")
                return
            decision = json.loads((BASE / "decision.json").read_text())
            if phase == "resume":
                deadline = time.monotonic() + 90
                while True:
                    try:
                        result = await service.resume(prior.INTEGRATION, task.id, decision)
                        save("result", result)
                        if result["stage"] == "ACTIVE_AND_SYNCED":
                            save("active", db.get_task(task.id).model_dump(mode="json"))
                            print(json.dumps(result))
                            return
                    except ResumeError as error:
                        save("last-error", dict(code=str(error)))
                        if str(error) not in {
                            "RESUME_UNCONFIRMED",
                            "RESUME_OUTCOME_UNKNOWN",
                            "INPUT_SYNC_PENDING",
                        }:
                            raise
                    if time.monotonic() > deadline:
                        raise RuntimeError(
                            "Bounded native observation ended; known journals preserved"
                        )
                    await asyncio.sleep(0.5)
            assert phase == "export"
            before = json.loads((BASE / "before.json").read_text())
            active = db.get_task(task.id)
            assert active.kanban_status == "Active" and active.worker_slot in {1, 2}
            assert all(
                getattr(active, k) == before[k]
                for k in ("id", "branch", "worktree_path", "codex_session_id", "base_commit")
            )
            input_op = db.get_operation(task.project_id, service.KIND, decision["input_id"])
            assert input_op.status == "SUCCEEDED"
            counts = {
                k: len(db.get_operations(prior.RUN, kind=k))
                for k in (
                    "start_runtime",
                    "dispatch_assignment",
                    "resume_runtime",
                    "task_resume",
                    "task_attention",
                    "stop_runtime",
                )
            }
            replay = await service.resume(prior.INTEGRATION, task.id, decision)
            assert replay == json.loads((BASE / "result.json").read_text())
            assert counts == {k: len(db.get_operations(prior.RUN, kind=k)) for k in counts}
            assert (
                counts["start_runtime"]
                == counts["dispatch_assignment"]
                == counts["resume_runtime"]
                == counts["task_resume"]
                == 1
            )
            actual = await adapter.read(
                "get_task",
                {
                    "projectId": prior.EXTERNAL_PROJECT,
                    "taskId": prior.load("fixture")["tasks"]["A"],
                },
            )
            assert (
                actual["status"] == "InProgress"
                and actual["executionOwnerKind"] == "User"
                and actual["responsibleUserId"] == prior.USER
            )
            resume = db.get_operation(
                task.project_id, "resume_runtime", input_op.result["resume_key"]
            )
            assert (
                resume.result["input_operation_id"] == input_op.id
                and resume.result["session_id"] == before["codex_session_id"]
            )
            start = db.get_operation(task.project_id, "start_runtime", task.id)
            assert start.result["generation"] == resume.id
            assert (
                service.lifecycle._live(active, start)["session_id"] == before["codex_session_id"]
            )
            assert service.git.head("main") == prior.load("before-git")["main"]
            assert all(
                service.git.run("rev-parse", "--verify", r).strip() == sha
                for r, sha in prior.load("before-git")["refs"].items()
            )
            assert all(
                Path(p) in service.git.worktrees() for p in prior.load("before-git")["worktrees"]
            )
            save(
                "proof",
                dict(
                    acceptance_pass=True,
                    source=prior.load("fixture"),
                    before=before,
                    active=active.model_dump(mode="json"),
                    input_operation=input_op.model_dump(mode="json"),
                    native_resume=resume.model_dump(mode="json"),
                    actual_native_status=actual["status"],
                    native_version=actual["version"],
                    replay=replay,
                    operation_counts=counts,
                    same_resources=True,
                    old_refs_and_worktrees_preserved=True,
                    limitation="One actual same-session resume; two-worker Attention is F33. "
                    "No main merge or fixture Done claimed.",
                ),
            )
            print(
                json.dumps(
                    dict(
                        F32_native="PASS",
                        session=active.codex_session_id,
                        input=input_op.id,
                        resume=resume.id,
                        replay_no_effects=True,
                    )
                )
            )


def main():
    logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "resume", "export"))
    parser.add_argument("--f31-state-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(operate(args.phase, args.f31_state_dir))


if __name__ == "__main__":
    main()
