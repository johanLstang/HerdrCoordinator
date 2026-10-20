"""Prepare concrete bounded fixtures; no native start, task scheduling or merge."""

import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from uuid import uuid4

from native_f37_probe import save
from native_f41_gate import isolated_argv

from orchestrator.config import Settings
from orchestrator.domain.codex_execution import CodexExecutionPolicy
from orchestrator.domain.coordinator_contracts import CoordinatorSpec
from orchestrator.domain.coordinator_review import CoordinatorReviewPolicy
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.worker_contracts import canonical_json

PROJECT = "d2ee4c75-7b80-465f-83ac-1750854a8e80"
USER = "105f26a7-0648-438d-94fd-3260ac3af4ee"
LOCAL = "f41-r1-4"
PRIMARY, NEXT = "F41R14", "F41NEXT14"


def task(epic, identity, name, goal, criteria, scope, dependencies=()):
    return dict(version=1, priority="P0", task=dict(
        version=1, project_id=LOCAL, epic_id=epic, task_id=identity, name=name,
        goal=goal, requirements=criteria, acceptance_criteria=criteria, scope=scope,
        dependencies=list(dependencies), sources=["README.md", "AGENTS.md"],
        out_of_scope=["Other tasks, branch switches, shared Git, merges, direct board writes"],
        verification_steps=["python3 -B -m unittest discover -s tests -v",
                            "Use worker_workspace and worker_commit for own actual SHA; "
                            "normal git cannot access shared metadata in this profile"],
    ))


def specs():
    a = task(PRIMARY, "A", "F41-r1.4 A Unicode labels",
             "Implement normalize_label(value) in labels.py, strip whitespace, normalize NFC, "
             "reject non-str and blank. Controlled first-attempt injection for this bounded test: "
             "first implement strip/validation with real passing ASCII baseline tests, explicitly "
             "report NFC as a known limitation; Integration must identify the actual gap, request "
             "changes and let THIS SAME Worker add NFC and Unicode tests. Never claim NFC passed.",
             ["normalize_label strips and normalizes Unicode NFC",
              "Non-string and blank labels are rejected"], ["labels.py", "tests/test_labels.py"])
    b = task(PRIMARY, "B", "F41-r1.4 B validated even totals",
             "Implement even_total(values) in totals.py: list of integers, exclude bool, "
             "sum even values including negatives, empty list returns 0; reject invalid input.",
             ["Even integers are summed including negatives and empty input",
              "Non-list, bool and non-int elements raise TypeError"],
             ["totals.py", "tests/test_totals.py"])
    c = task(PRIMARY, "C", "F41-r1.4 C composed caption",
             "Implement caption(name,values) in caption.py using labels.normalize_label and "
             "totals.even_total; output '<normalized name>: <even sum>'. Add real tests and README "
             "example showing caption with decomposed A-ring name and [2,3,4,-2], producing "
             "exactly Åsa: 4 as required by the epic. Public api.py is an aggregate epic "
             "criterion reserved for final review, "
             "outside this original task scope; report that limitation truthfully.",
             ["Caption composes both reviewed functions with exact '<name>: <sum>' format",
              "README contains a working caption example"],
             ["caption.py", "tests/test_caption.py", "README.md"], ("A", "B"))
    criteria = ["Unicode labels and validated even totals work together",
                "Caption and README example produce 'Åsa: 4'",
                "Public api.py exports normalize_label, even_total and caption"]
    first = dict(version=1, project_id=LOCAL, epic_id=PRIMARY, title="F41-r1.4 native pipeline",
                 goal="Actual two-Worker pipeline, same-session task fix and aggregate correction",
                 requirements=criteria, acceptance_criteria=criteria,
                 sources=["README.md", "AGENTS.md"], project_instructions=[
                     "Use only your registered role tools. Keep ticking task_schedule until a "
                     "review/decision is required; read actual contexts, decide yourself, and "
                     "continue after each task. Up to TWO real Workers, no third.",
                     "The A first draft and absent public api.py are explicit fault injections. "
                     "Review actual code and missing criteria. Never invent a failing test, "
                     "approval, completion, runtime identity or permission.",
                     "Use Python -B for tests. Workspace commands are isolated; shared Git is "
                     "readonly/hidden. Workers commit only via their registered broker.",
                     "After real task reviews/integrations, epic_complete runs baseline suite. "
                     "Its known limitation is missing independent public-API verification. "
                     "Coordinator must independently review all epic criteria before delivery.",
                 ], tasks=[a, b, c], dependencies=[])
    d = task(NEXT, "D", "F41-r1.4 next report on delivered main",
             "In report.py import caption from api (provided by previous main delivery), "
             "implement report(name,values) returning 'Report '+caption(name,values), add tests.",
             ["report(' Åsa ',[2,3,4,-2]) equals 'Report Åsa: 4'"],
             ["report.py", "tests/test_report.py"])
    second = dict(first, epic_id=NEXT, title="F41-r1.4 dependent next epic",
                  goal="Start from new actual main after previous verified delivery",
                  requirements=d["task"]["acceptance_criteria"],
                  acceptance_criteria=d["task"]["acceptance_criteria"],
                  tasks=[d], dependencies=[PRIMARY])
    second["project_instructions"] = [
        "Use actual current main and registered tools; previous epic must be Done. "
        "No injected code limitation in this epic; implement/review/deliver its one task.",
    ]
    return first, second


def board_request():
    return dict(project=PROJECT, user=USER, specs=specs())


def prepare(root, ids, binary):
    root = root.resolve()
    if root.exists():
        raise RuntimeError("F41_SETUP_ALREADY_EXISTS_NO_OVERWRITE")
    root.mkdir(mode=0o700)
    implementation = Path(__file__).resolve().parents[1]
    tools = root / "sandbox-tools"
    tools.mkdir(mode=0o700)
    helper = tools / "codex-linux-sandbox"
    helper.symlink_to(binary)
    profile = CodexExecutionPolicy(workspace_access="write", helper=helper, binary=binary,
                                   binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    readonly = profile.model_copy(update={"workspace_access": "read"})
    argv = isolated_argv("/usr/bin/python3", "-B", "-m", "unittest",
                         "discover", "-s", "tests", "-v")
    policies = []
    for i, spec in enumerate(specs()):
        command = dict(argv=argv, timeout=60)
        completion = dict(version=1, project_id=LOCAL, epic_id=spec["epic_id"],
                          requirements=spec["requirements"],
                          acceptance_criteria=spec["acceptance_criteria"],
                          sources=spec["sources"], acceptance_version=1,
                          task_ids=[e["task"]["task_id"] for e in spec["tasks"]],
                          build=command, test=command, acceptance=command,
                          known_limitations=[] if i else [
                              "Baseline suite does not independently cover public API criterion; "
                              "Coordinator independent current review is mandatory",
                          ])
        assertions = ("from api import normalize_label,even_total,caption; "
                      "assert normalize_label(' A\\u030asa ')=='Åsa'; "
                      "assert even_total([2,3,4,-2])==4; "
                      "assert caption(' A\\u030asa ',[2,3,4,-2])=='Åsa: 4'; ")
        if i:
            assertions += ("from report import report; "
                           "assert report('Åsa',[2,4,-2])=='Report Åsa: 4'")
        else:
            assertions += ("from pathlib import Path; "
                           "assert 'caption' in Path('README.md').read_text()")
        independent = dict(argv=isolated_argv("/usr/bin/python3", "-B", "-c", assertions),
                           timeout=60)
        fix = task(spec["epic_id"], "PUBLIC", "Public API exports",
                   "Add api.py exporting the three reviewed functions and real public-API tests. "
                   "Do not modify earlier Done task implementations or fake verification.",
                   [spec["acceptance_criteria"][-1]], ["api.py", "tests/test_api.py"],
                   tuple(e["task"]["task_id"] for e in spec["tasks"]))
        policy = CoordinatorReviewPolicy.model_validate_json(canonical_json(dict(
            version=1, native_project_id=PROJECT, native_epic_id=ids["epics"][i],
            user_id=USER, native_task_ids=ids["tasks"][i], original=spec,
            completion=completion, correction_completion=completion,
            verification=independent, templates=[] if i else [dict(
                version=1, template_id="public-api", criteria=[spec["acceptance_criteria"][-1]],
                entry=fix,
            )],
        )))
        policies.append(policy)
    native = tomllib.loads((Path.home() / ".codex/config.toml").read_text())
    pins = [*implementation.glob("src/**/*.py"), *implementation.glob("prompts/*.md"),
            *implementation.glob("tests/native_f41*.py"),
            implementation / "tests/native_f37_host.py",
            implementation / "tests/native_f37_probe.py"]
    config = dict(
        version=1, root=str(root / "observations"), implementation=str(implementation),
        python=sys.executable, host=str(implementation / "tests/native_f41_host.py"),
        server="hc-f41-r1-20261020", run_id=str(uuid4()), native_project=PROJECT, user=USER,
        coordinator=Actor(actor_id="f41-r1-coordinator", role=Role.COORDINATOR,
                          project_id=LOCAL).model_dump(mode="json"),
        profiles=dict(worker=profile.model_dump(mode="json"),
                      coordinator=readonly.model_dump(mode="json"),
                      integration=readonly.model_dump(mode="json")),
        disabled_servers=[k for k in native.get("mcp_servers", {}) if k != "herdr_coordinator"],
        disabled_plugins=list(native.get("plugins", {})),
        policies=[p.model_dump(mode="json") for p in policies],
        source_pins={str(p.relative_to(implementation)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in pins}, gate_receipt=str(root / "gate.json"),
        preflight_config=str(root / "preflight.json"), approval=str(root / "user-approval.json"),
    )
    config["spec"] = CoordinatorSpec.model_validate_json(canonical_json(dict(
        version=1, project_id=LOCAL, native_project_id=PROJECT, title="F41-r1 bounded native proof",
        sources=["README.md", "AGENTS.md"], epics=[dict(
            version=1, native_epic_id=p.native_epic_id, native_task_ids=p.native_task_ids,
            priority="P0", spec=p.original.model_dump(mode="json"),
        ) for p in policies],
    ))).model_dump(mode="json")
    for preflight in (False, True):
        home = root / ("preflight" if preflight else "fixture")
        repo = home / "repo"
        repo.mkdir(parents=True)
        for args in (("init", "-b", "main"), ("config", "user.name", "Herdr F41 fixture"),
                     ("config", "user.email", "blitterbot@gmail.com")):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        (repo / "README.md").write_text(
            "# F41-r1 harmless fixture\n\nTests: python3 -B -m unittest discover -s tests -v\n"
            "Coordinator and Integration use registered services. Workers implement only own "
            "task and use worker_workspace/worker_commit for actual Git state.\n"
        )
        (repo / "AGENTS.md").write_text(
            "Read your actual assignment. In HERDR_ASSIGNMENT reply first with its exact ACK "
            "JSON, then implement your own task. In HERDR_EPIC_ASSIGNMENT return only the "
            "confirmation VALUE, top-level with no wrapper, end that turn and wait for the "
            "registered HERDR_INTEGRATION_ACTIVATE control. Coordinator startup ACK waits "
            "for its subsequent "
            "operator control request. If registration is still pending, wait "
            "briefly and retry the SAME key; never escalate. "
            "After implementation verify actual tests with python3 -B, commit ONLY with "
            "worker_commit(request_key,message), obtain actual SHA via worker_workspace. "
            "No shell Git mutation, branch switch, merge, host access or escalation. "
            "Preserve existing files and give exact required final JSON report.\n"
        )
        (repo / "tests").mkdir()
        (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "Seed harmless F41 fixture"],
                       check=True, capture_output=True)
        current = dict(config, root=str(home / "observations"), settings=Settings(
            repository=repo, worktree_root=home / "worktrees", sqlite_path=home / "state.db",
            max_workers=2, worker_test_command=argv,
        ).model_dump(mode="json"))
        Path(current["root"]).mkdir(mode=0o700)
        save(root / ("preflight.json" if preflight else "config.json"), current, exclusive=True)
    save(root / "board-ids.json", ids, exclusive=True)
    save(root / "scope.json", dict(
        version=1, source="F41_SCOPED_TEST_AWAITING_USER_CONSENT", server=config["server"],
        root=str(root), native_project=PROJECT, tasks=ids, max_workers=2,
        effects=["Actual native preflight Worker in own fixture; preserved refs and worktrees",
                 "Actual Coordinator/Integration/two Workers, scoped TeamPlayer test records",
                 "Own task commits, Task→Epic→fixture main merges, correction and next epic",
                 "Stop only own runtimes; no production merge or cleanup"],
    ), exclusive=True)
    return root / "config.json"


if __name__ == "__main__":
    if sys.argv[1] == "board-request":
        print(json.dumps(board_request(), ensure_ascii=False))
    elif sys.argv[1] == "prepare":
        binary = Path(os.environ["HERDR_REAL_CODEX_BINARY"]).resolve()
        print(prepare(Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text()), binary))
    else:
        raise RuntimeError("F41_SETUP_COMMAND_INVALID")
