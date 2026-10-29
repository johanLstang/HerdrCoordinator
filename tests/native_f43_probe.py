"""Operator-only bounded F43 proof; actual Herdr/Codex/Git/SQLite/TeamPlayer.

No implementation delegation, fabricated registration, replacement runtime or merge.
Each invocation reopens connections/state. Do not repeat an unknown prompt dispatch.
"""

import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from native_f37_host import protected_config

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.git import GitAdapter
from orchestrator.adapters.herdr import HerdrAdapter
from orchestrator.adapters.teamplayer_mcp import OperatorHeaders, teamplayer_connection
from orchestrator.application.coordinator_runtime_service import CoordinatorRuntimeService
from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.session_recovery_service import SessionRecoveryService
from orchestrator.application.task_attention_service import TaskAttentionService
from orchestrator.application.task_resume_service import TaskResumeService
from orchestrator.application.task_start_service import TaskStartService
from orchestrator.application.teamplayer_reader import TeamPlayerReader
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.config import Settings
from orchestrator.domain.codex_mcp_launch import CodexMCPLaunch
from orchestrator.domain.coordinator_contracts import CoordinatorSpec
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.review_contracts import EpicReviewSpec
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.mcp.principal import load_principal
from orchestrator.persistence.store import StateStore


def save(path, data):
    temporary = path.with_suffix(path.suffix + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def prepare(root, attempt="r1", date="20261020"):
    if not re.fullmatch(r"r[1-9][0-9]{0,2}", attempt) or not re.fullmatch(r"[0-9]{8}", date):
        raise RuntimeError("INVALID_EXPLICIT_PROBE_ATTEMPT")
    if (root / "config.json").exists():
        raise RuntimeError("EXISTING_PROBE_CONFIG_PRESERVED")
    root.chmod(0o700)
    ids = protected_config(root / "board-ids.json")
    repo = root / "repo"
    repo.mkdir(exist_ok=True)
    if any(repo.iterdir()):
        raise RuntimeError("EXISTING_PROBE_REPOSITORY_PRESERVED")
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    g = GitAdapter(repo)
    g.run("config", "user.name", "Herdr F43 isolated probe")
    g.run("config", "user.email", "probe@example.invalid")
    (repo / "README.md").write_text("# F43 fixed JSON recovery probe\nNo tools or file edits.\n")
    (repo / "AGENTS.md").write_text(
        "This is an operator-owned fixed JSON runtime probe. Use no tools, commands, file "
        "changes, delegation or external messages. First honor the exact startup/WORKING "
        "JSON acknowledgement. Integration and Coordinator end that turn and wait. Workers "
        "then issue a version-1 BLOCKED final report with original assignment IDs and branch, "
        "summary 'Fixed JSON recovery probe', reason 'Probe input required', input_required "
        "'Vilket provvärde?'. After a saved operator input, first send its exact WORKING "
        "acknowledgement, then report BLOCKED with reason 'Probe receipt completed' and "
        "input_required 'Operator: stop this bounded probe'. Never invent a commit or Done.\n"
    )
    g.run("add", "README.md", "AGENTS.md")
    g.run("commit", "-m", "Seed harmless F43 fixed JSON fixture")
    project, epic = "f43-" + attempt, "F43" + attempt.upper()
    co_run, epic_run = str(uuid4()), str(uuid4())
    criteria = [
        "Samma registrerade session och worktree återfinns efter förlorad controlleranslutning.",
        "Parkering, sparat svar och återupptagning bevarar identiteter och högst två aktiva "
        "Worker-slots.",
    ]
    tasks = []
    for letter in "ABC":
        tasks.append(
            dict(
                version=1,
                priority="P1",
                task=dict(
                    version=1,
                    project_id=project,
                    epic_id=epic,
                    task_id=letter,
                    name="PROV F43-" + attempt + " " + letter + " — fast sessions- och inputprob",
                    goal="Fixed JSON recovery probe. No tools, commands, code or file edits. "
                    "After the required WORKING acknowledgement, report version-1 BLOCKED with "
                    "original IDs, branch, summary 'Fixed JSON recovery probe', "
                    "reason 'Probe input "
                    "required', input_required 'Vilket provvärde?'. After saved input, ACK then "
                    "report BLOCKED asking the operator to stop this bounded probe.",
                    requirements=criteria,
                    scope=["Native acknowledgement and BLOCKED JSON only"],
                    out_of_scope=[
                        "All tools, implementation, file changes, merges and external messages"
                    ],
                    acceptance_criteria=criteria,
                    sources=["README.md", "AGENTS.md"],
                    dependencies=[],
                    verification_steps=[
                        "Operator verifies native thread, session and process identities"
                    ],
                ),
            )
        )
    spec = dict(
        version=1,
        project_id=project,
        epic_id=epic,
        title="F43 fixed JSON probe",
        goal="Reconnect original bounded sessions without new runs or lost answers",
        requirements=criteria,
        acceptance_criteria=criteria,
        sources=["README.md", "AGENTS.md"],
        project_instructions=[
            "No tools or code. Exact startup acknowledgement, then wait for operator."
        ],
        tasks=tasks,
        dependencies=[],
    )
    settings = Settings(
        repository=repo,
        worktree_root=root / "trees",
        sqlite_path=root / "state.db",
        worker_test_command=("python3", "-c", "from pathlib import Path; "
                             "assert Path('README.md').read_text().startswith('# F43 fixed JSON')"),
        review_context=EpicReviewSpec(
            version=1,
            project_id=project,
            epic_id=epic,
            requirements=criteria,
            acceptance_criteria=criteria,
            sources=["README.md"],
        ),
    )
    for role, name in [(Role.COORDINATOR, "coordinator"), (Role.INTEGRATION, "integration")]:
        actor = Actor(
            actor_id="f43-" + attempt + "-" + name,
            role=role,
            project_id=project,
            epic_run_id=epic_run if role == Role.INTEGRATION else None,
        )
        save(root / (name + ".json"), actor.model_dump(mode="json"))
    native = tomllib.loads((Path.home() / ".codex/config.toml").read_text())
    original_source = Path(__file__).resolve().parents[1]
    source = root / "frozen-source"
    source.mkdir()
    shutil.copytree(original_source / "src", source / "src", ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc"
    ))
    shutil.copytree(original_source / "prompts", source / "prompts")
    (source / "tests").mkdir()
    for name in ("native_f43_probe.py", "native_f43_host.py", "native_f37_host.py"):
        shutil.copy2(original_source / "tests" / name, source / "tests" / name)
    pins = {
        str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            *source.glob("src/orchestrator/**/*.py"),
            *source.glob("prompts/*.md"),
            source / "tests/native_f43_probe.py",
            source / "tests/native_f43_host.py",
            source / "tests/native_f37_host.py",
        ]
    }
    config = dict(
        root=str(root),
        source=str(source),
        ids=ids,
        settings=settings.model_dump(mode="json"),
        spec=spec,
        co_run=co_run,
        epic_run=epic_run,
        pins=pins,
        attempt=attempt,
        server="hc-f43-" + attempt + "-" + date,
        mcp_host=str(source / "tests/native_f43_host.py"),
        python=sys.executable,
        disabled_servers=list(native.get("mcp_servers", {})),
        disabled_plugins=list(native.get("plugins", {})),
    )
    save(root / "config.json", config)
    print(
        json.dumps(dict(prepared=str(root), source_pins=len(pins), initial_commit=g.head("main")))
    )


@asynccontextmanager
async def composition(root):
    c = protected_config(root / "config.json")
    source = Path(c["source"])
    if any(
        hashlib.sha256((source / p).read_bytes()).hexdigest() != h for p, h in c["pins"].items()
    ):
        raise RuntimeError("NATIVE_PROBE_SOURCE_CHANGED")
    settings = Settings.model_validate(c["settings"])
    coordinator = load_principal(root / "coordinator.json", settings)
    integration = load_principal(root / "integration.json", settings)
    native = tomllib.loads((Path.home() / ".codex/config.toml").read_text())["mcp_servers"][
        "teamplayer"
    ]
    headers = OperatorHeaders(command=tuple(shlex.split(native["http_headers_helper"])))
    async with teamplayer_connection(
        native["url"], headers, writable=True, timeout_seconds=30
    ) as board:
        with StateStore(settings.sqlite_path) as store:
            launch = CodexMCPLaunch(
                command=c["python"],
                args=(c.get("mcp_host", str(source / "tests/native_f43_host.py")),),
                disabled_servers=tuple(c["disabled_servers"]),
                disabled_plugins=tuple(c["disabled_plugins"]),
            )
            herdr = HerdrAdapter(c["server"], sandbox="read-only", mcp=launch)
            codex = CodexAdapter()
            ids = c["ids"]
            sync = TeamPlayerSyncService(
                settings,
                store,
                board,
                project_id=coordinator.project_id,
                external_project_id=ids["projectId"],
                user_id=ids["userId"],
                codex=codex,
            )
            epic = EpicStartService(
                settings,
                store,
                herdr,
                c["spec"],
                integration,
                codex,
                sync=sync,
                external_epic_id=ids["epicId"],
            )
            plan = CoordinatorSpec(
                version=1,
                project_id=coordinator.project_id,
                native_project_id=ids["projectId"],
                title="F43 recovery probe",
                sources=["README.md", "AGENTS.md"],
                epics=[
                    dict(
                        version=1,
                        native_epic_id=ids["epicId"],
                        native_task_ids=ids["taskIds"],
                        priority="P1",
                        spec=c["spec"],
                    )
                ],
            )
            runtime = CoordinatorRuntimeService(
                settings, store, herdr, plan, coordinator, c["co_run"], codex=codex
            )
            bindings = {ids["epicId"]: LocalBoardBinding(local_id=c["spec"]["epic_id"])}
            bindings |= {
                identity: LocalBoardBinding(local_id=entry["task"]["task_id"])
                for identity, entry in zip(ids["taskIds"], c["spec"]["tasks"], strict=True)
            }
            reader = TeamPlayerReader(
                board,
                project_id=ids["projectId"],
                user_id=ids["userId"],
                project_name="HerdrCoordinator",
                bindings=bindings,
            )
            attention = TaskAttentionService(settings, store, sync, herdr, codex)
            resume = TaskResumeService(attention, ack_wait_seconds=5)
            recovery = SessionRecoveryService(
                settings,
                store,
                reader,
                project_id=coordinator.project_id,
                coordinator=runtime,
                integrations=[epic],
                resumers={integration.epic_run_id: resume},
            )
            yield c, runtime, epic, recovery, resume


async def main():
    root, command = Path(sys.argv[1]).resolve(), sys.argv[2]
    if command == "prepare":
        prepare(root, *(sys.argv[3:5]))
        return
    async with composition(root) as (c, runtime, epic, recovery, resume):
        actor = epic.principal
        store = runtime.store
        if command == "start-root":
            result = runtime.start(runtime.principal).model_dump(mode="json")
        elif command == "start-epic":
            result = await epic.start(
                runtime.principal, actor.project_id, epic.spec.epic_id, actor.epic_run_id
            )
        elif command == "start-task":
            letter = sys.argv[3]
            index = "ABC".index(letter)
            spec = c["spec"]["tasks"][index]["task"]
            snapshot = await recovery.audit.reader.read_project()
            native_task = snapshot.task(c["ids"]["taskIds"][index])
            if native_task is None or (
                native_task.execution_owner_kind != "User"
                or native_task.responsible_user_id != c["ids"]["userId"]
                or native_task.epic_id != c["ids"]["epicId"]
                or native_task.name != spec["name"]
                or native_task.acceptance_criteria != tuple(spec["acceptance_criteria"])
                or native_task.status not in {"Pending", "InProgress", "NeedsInput"}
            ):
                raise RuntimeError("NATIVE_TASK_SCOPE_CHANGED")
            start = TaskStartService(
                runtime.settings, store, epic.herdr, epic.codex, ack_wait_seconds=5
            )
            started = start.start(actor, actor.epic_run_id, spec, timeout_seconds=45)
            task = store.get_task(started["task"]["id"])
            if started["status"] not in {"CONFIRMED", "EXISTING"}:
                save(root / "start-task-latest.json", started)
                print(json.dumps(started, ensure_ascii=False), flush=True)
                return
            await resume.sync.bind_task(actor, task.id, c["ids"]["taskIds"][index])
            result = dict(
                task=task.model_dump(mode="json"), sync=await resume.sync.sync_task(actor, task.id)
            )
        elif command in {"poll", "watch"}:
            result = await recovery.poll()
            result["controller_pid"] = os.getpid()
            save(root / ("poll-" + str(os.getpid()) + ".json"), result)
            if command == "watch":
                save(root / "watch-ready.json", result)
                print(json.dumps(dict(stage="WATCHING", controller_pid=os.getpid())), flush=True)
                while True:
                    await asyncio.sleep(2)
                    await recovery.poll()
        elif command == "park":
            task = next(t for t in store.get_tasks(actor.epic_run_id) if t.task_id == sys.argv[3])
            result = await resume.attention.park_blocked(actor, task.id)
        elif command == "save-input":
            task = next(t for t in store.get_tasks(actor.epic_run_id) if t.task_id == "A")
            blockers = [
                o
                for o in store.get_operations(actor.epic_run_id, kind="task_attention")
                if o.task_run_id == task.id
            ]
            decision_file = root / "input-decision.json"
            if not decision_file.exists():
                save(
                    decision_file,
                    dict(input_id=str(uuid4()), blocker_id=blockers[-1].id, answer="7"),
                )
            result = await resume.resume(actor, task.id, protected_config(decision_file))
        elif command == "missing-reference":
            task = next(t for t in store.get_tasks(actor.epic_run_id) if t.task_id == "C")
            original = root / "original-c-session.json"
            if original.exists():
                raise RuntimeError("MISSING_REFERENCE_PROBE_ALREADY_STARTED")
            save(original, task.model_dump(mode="json"))
            store.update_runtime_metadata(task.model_copy(update={"codex_session_id": None}))
            result = await recovery.poll()
        elif command == "restore-reference":
            from orchestrator.domain.models import TaskRun

            original = TaskRun.model_validate(protected_config(root / "original-c-session.json"))
            current = store.get_task(original.id)
            if current.model_dump(exclude={"codex_session_id"}) != original.model_dump(
                exclude={"codex_session_id"}
            ):
                raise RuntimeError("CONTROLLED_REFERENCE_SUBJECT_CHANGED")
            store.update_runtime_metadata(original)
            result = await recovery.poll()
        elif command == "stop":
            stopped = []
            for task in store.get_tasks(actor.epic_run_id):
                if task.worker_slot is not None:
                    op = resume.lifecycle.stop_task(
                        actor,
                        task.id,
                        key="f43-r1-final:" + task.id,
                        reason="Bounded F43 probe stopped by operator",
                    )
                    stopped.append(op.model_dump(mode="json"))
            stop = epic.lifecycle.stop_epic(
                runtime.principal, actor.epic_run_id, key="f43-r1-final-integration"
            )
            co = runtime.stop(runtime.principal, "f43-r1-final-coordinator")
            result = dict(
                tasks=stopped,
                integration=stop.model_dump(mode="json"),
                coordinator=co.model_dump(mode="json"),
                remaining_agents=epic.herdr.call("agent", "list"),
            )
        else:
            raise RuntimeError("UNKNOWN_F43_COMMAND")
        save(root / (command + "-latest.json"), result)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
