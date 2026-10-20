"""F39: real Git/process/SQLite and MCP transport, controlled native peers."""

import asyncio
import io
import json
import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from mcp import Client
from mcp.types import CallToolResult, ListToolsResult, TextContent
from test_epic_start import Native
from test_epic_start import setup as epic_setup  # noqa: F401
from test_integration_control import Sessions, finish, task
from test_runtime_lifecycle import FakeProcesses
from test_task_scheduler import Board, approve
from test_teamplayer_reader import T1, E, P, U, native_tools, reader
from test_teamplayer_reader import task as native_task

from orchestrator.adapters.herdr import HerdrError
from orchestrator.adapters.teamplayer_correction import TeamPlayerCorrectionCreator
from orchestrator.adapters.teamplayer_mcp import TeamPlayerError, TeamPlayerMCPWriter
from orchestrator.application.coordinator_claim_service import CoordinatorClaimService
from orchestrator.application.coordinator_control_service import CoordinatorControlService
from orchestrator.application.coordinator_review_service import (
    CoordinatorReviewService,
    review_capability,
)
from orchestrator.application.coordinator_review_subject import (
    CoordinatorReviewSubject,
    completion_for,
)
from orchestrator.application.coordinator_runtime_service import (
    CoordinatorError,
    CoordinatorRuntimeService,
)
from orchestrator.application.epic_completion_service import EpicCompletionService
from orchestrator.application.epic_integration_service import EpicIntegrationService
from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.integration_control_service import IntegrationControlService
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.task_scheduler_service import TaskSchedulerService
from orchestrator.application.task_selection_service import TaskSelectionService
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.domain.coordinator_review import CoordinatorReviewPolicy, CorrectionChoice
from orchestrator.domain.epic_completion import EpicCompletionSpec
from orchestrator.domain.models import utc_now
from orchestrator.domain.policy import Role
from orchestrator.domain.review_contracts import ApprovalDecision, ChangesDecision, EpicReviewSpec
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.domain.worker_contracts import canonical_json
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server


class ReviewSessions(Sessions):
    def prompt(self, name, text, *, timeout_ms):
        if json.loads(text).get("type") != "EPIC_CORRECTION_PLAN_ACTIVE":
            return super().prompt(name, text, timeout_ms=timeout_ms)
        agent = self.agents[name]
        turns = self.threads[agent["sid"]]["turns"]
        turns.append(dict(id="correction-" + str(len(turns)), status="completed", items=[dict(
            id="correction-user", type="userMessage", content=[dict(type="text", text=text)],
        )]))
        self.sent.append(text)


@pytest.fixture
def review_setup(epic_setup, request):  # noqa: F811
    delivery_enabled = getattr(request, "param", False) == "delivery"
    initial, coordinator, integration, _ = epic_setup
    data = initial.spec.model_dump(mode="json")
    data.update(dependencies=[], external_prerequisites=[], tasks=data["tasks"][:1])
    command = (sys.executable, "-c", "from pathlib import Path; "
               "assert all(p.read_text()=='verified\\n' for p in Path('.').glob('*.result'))")
    settings = initial.settings.model_copy(update=dict(
        worker_test_command=command,
        review_context=EpicReviewSpec(version=1, project_id="p", epic_id="E-34",
                                     requirements=data["requirements"],
                                     acceptance_criteria=data["acceptance_criteria"],
                                     sources=data["sources"]),
    ))
    sessions = ReviewSessions()
    parent = EpicStartService(settings, initial.store, sessions, data, integration, codex=sessions,
                              processes=sessions if delivery_enabled else None)
    assert asyncio.run(parent.start(coordinator, "p", "E-34", "e"))["stage"] == "REGISTERED"
    board = Board({T1: data["tasks"][0]["task"]})
    if delivery_enabled:
        board.tasks[0]["priority"] = 3
    client = Mock()
    client.list_tools = AsyncMock(return_value=ListToolsResult(tools=native_tools()))
    creates = []
    failures = []

    async def call(name, arguments):
        if name == "create_task":
            creates.append(deepcopy(arguments))
            failure = failures.pop(0) if failures else None
            if failure == "before":
                raise TeamPlayerError("TEAMPLAYER_WRITE_OUTCOME_UNKNOWN")
            request = arguments["request"]
            value = native_task(str(uuid4()), **{k: v for k, v in request.items() if k not in {
                "priority", "dependsOnTaskIds", "validationOwnerKind", "validationUserId",
            }}, priority={"Critical": 3, "High": 2, "Medium": 1, "Low": 0}[request["priority"]],
                dependencyTaskIds=request["dependsOnTaskIds"])
            board.tasks.append(value)
            if failure == "after":
                raise TeamPlayerError("TEAMPLAYER_WRITE_OUTCOME_UNKNOWN")
        elif delivery_enabled and name == "update_epic_status":
            row = next(e for e in board.epics if e["id"] == arguments["epicId"])
            if arguments["version"] != row["version"]:
                raise TeamPlayerError("TEAMPLAYER_VERSION_CONFLICT")
            row.update(status=arguments["status"], version=row["version"] + 1)
            board.applied.append((name, deepcopy(arguments)))
            value = deepcopy(row)
        elif name.startswith("update_"):
            value = await board.write(name, arguments)
        else:
            value = await board.read(name, arguments)
        return CallToolResult(content=[TextContent(
            type="text", text=json.dumps(dict(success=True, data=value)),
        )])

    client.call_tool = AsyncMock(side_effect=call)
    adapter = TeamPlayerMCPWriter(client)
    asyncio.run(adapter.verify_catalog())
    adapter.verify_write_catalog()
    creator = TeamPlayerCorrectionCreator(adapter, project_id=P, epic_id=E, user_id=U)
    aggregate = dict(argv=(sys.executable, "-c", "from pathlib import Path; "
                          "assert len(list(Path('.').glob('*.result')))==1"), timeout=30)
    completion = EpicCompletionSpec.model_validate_json(canonical_json(dict(
        version=1, acceptance_version=1, project_id="p", epic_id="E-34",
        requirements=data["requirements"], acceptance_criteria=data["acceptance_criteria"],
        sources=data["sources"], task_ids=["F-01"], build=aggregate, test=aggregate,
        acceptance=aggregate,
        known_limitations=["Controlled native peers; actual Git/process/SQLite"],
    )))
    extended = completion.model_dump(mode="json")
    for name in ("build", "test", "acceptance"):
        extended[name]["argv"][-1] = extended[name]["argv"][-1][:-1] + "{task_count}"
    template = deepcopy(data["tasks"][0])
    template["task"].update(task_id="F-FIX", name="Correct aggregate result",
                             dependencies=["F-01"])
    independent = command
    failure_file = settings.sqlite_path.parent / "controlled-final-verification-failure"
    if delivery_enabled:
        independent = (*command[:-1], command[-1] + "; assert not Path("
                       + repr(str(failure_file)) + ").exists()")
    policy = CoordinatorReviewPolicy.model_validate_json(canonical_json(dict(
        version=1, native_project_id=P, native_epic_id=E, user_id=U, native_task_ids=[T1],
        original=data, completion=completion.model_dump(mode="json"),
        correction_completion=extended,
        verification=dict(argv=(independent if getattr(request, "param", False) is not True else (
            sys.executable, "-c", "from pathlib import Path; "
            "assert Path('F-FIX-r1.result').read_text()=='verified\\n'",
        )), timeout=30), templates=[dict(
            version=1, template_id="fix", criteria=data["acceptance_criteria"], entry=template,
        )],
    )))
    root_native = Native()
    root_sid = str(uuid4())

    def root_prompt(name, text, *, timeout_ms):
        root_native.prompts.append(text)
        root_native.sid = root_sid
        root_native.turns.append(dict(id="root-turn", status="completed", items=[
            dict(id="root-user", type="userMessage", content=[dict(type="text", text=text)]),
            dict(id="root-ack", type="agentMessage", nativeObservedAt=utc_now().isoformat(),
                 text=canonical_json(json.loads(text)["confirmation"])),
        ]))

    root_native.prompt = root_prompt
    capabilities = {"coordinator_review": review_capability([policy])}
    if delivery_enabled:
        from orchestrator.application.coordinator_delivery_service import delivery_capability
        capabilities["coordinator_delivery"] = delivery_capability(
            capabilities["coordinator_review"],
        )
    runtime = CoordinatorRuntimeService(
        settings.model_copy(update={"review_context": None}), initial.store, root_native, dict(
            version=1, project_id="p", native_project_id=P, title="Fixture project",
            sources=["README.md"], epics=[dict(version=1, native_epic_id=E,
                                              native_task_ids=[T1], priority="P0", spec=data)],
        ), coordinator, "coordinator-review", codex=root_native, processes=FakeProcesses(),
        capabilities=capabilities,
    )
    assert runtime.start(coordinator).status == "ACTIVE"
    log = EventLog(stream=io.StringIO())

    def factory(view):
        specs = {identity: entry.task.model_dump(mode="json") for identity, entry in zip(
            view.native_task_ids, view.spec.tasks, strict=True,
        )}
        bindings = {E: LocalBoardBinding(local_id="E-34")} | {
            identity: LocalBoardBinding(local_id=value["task_id"])
            for identity, value in specs.items()
        }
        selection = TaskSelectionService(
            settings, initial.store, reader(adapter, bindings=bindings), specs=specs,
            task_order=tuple(specs), epic_prerequisites={"E-34": ()},
            codex=sessions, processes=sessions,
        )
        sync = TeamPlayerSyncService(
            settings, initial.store, adapter, project_id="p", external_project_id=P,
            user_id=U, scopes={"e": tuple(value["task_id"] for value in specs.values())},
            codex=sessions, processes=sessions,
        )
        scheduler = TaskSchedulerService(settings, initial.store, selection, sync, sessions,
                                         sessions, processes=sessions, review_provider=None,
                                         timeout_seconds=45, registration=view)
        complete = EpicCompletionService(view, scheduler, completion_for(policy, view.spec))
        return IntegrationControlService(view, scheduler, log, epic_completion=complete)

    subject = CoordinatorReviewSubject(runtime, parent, policy, factory, creator)
    review = CoordinatorReviewService(runtime, {"e": subject})
    initial_control = subject.context()[0]
    asyncio.run(initial_control.scheduler.sync.bind_epic(coordinator, "e", E))
    asyncio.run(review.attach_registered_product_run(coordinator, "e"))
    yield SimpleNamespace(review=review, subject=subject, coordinator=coordinator,
                          integration=integration, board=board, sessions=sessions,
                          creates=creates, client=client, policy=policy, failures=failures,
                          verification_failure_file=failure_file)


async def deliver_one(fixture, identity, local):
    control = fixture.subject.context()[0]
    run_id = fixture.integration.epic_run_id
    runtime = control.runtime(control.delegate.log)
    result = await runtime.call_async("task_start", dict(
        project_id="p", epic_run_id=run_id, task=control.scheduler.selection.specs[identity],
    ))
    assert result.ok, result.model_dump(mode="json")
    finish(control, fixture.sessions, local)
    result = await runtime.call_async("task_schedule", dict(project_id="p", epic_run_id=run_id))
    assert result.ok, result
    current = task(control, local)
    assert current.internal_status == TaskState.REVIEWING
    context = next(o.result["context"] for o in reversed(
        control.store.get_operations(run_id, kind="task_review_request"))
                   if o.task_run_id == current.id)
    result = await runtime.call_async("task_approve", dict(
        project_id="p", task_run_id=current.id, request_key="approve-" + local,
        decision=approve(context),
    ))
    assert result.ok, result
    result = await runtime.call_async("task_merge", dict(
        project_id="p", task_run_id=current.id, request_key="merge-" + local,
        verification_key="after-" + local,
    ))
    assert result.ok, result
    assert task(control, local).internal_status == TaskState.DONE
    return control


async def read_context(f, key):
    run_id = f.integration.epic_run_id
    summary = await f.review.context(f.coordinator, "p", run_id, key)
    parts = [await f.review.read(f.coordinator, "p", run_id, summary["context_id"], index)
             for index in range(summary["chunks"])]
    document = json.loads("".join(part["text"] for part in parts))
    return summary, document


def rejection(context_id):
    return ChangesDecision(version=1, result="CHANGES_REQUESTED", context_id=context_id,
                           issues=[dict(number=1, problem="Aggregate needs correction",
                                        requested_change="Deliver independent corrective task",
                                        acceptance_criteria=["Native receipt"])])


def test_reject_create_same_session_correct_and_approve_preserves_history(
    review_setup, monkeypatch,
):
    f = review_setup

    async def run():
        control = await deliver_one(f, T1, "F-01")
        original = task(control, "F-01")
        main = f.review.runtime.main()
        sid = control.store.get_epic("e").codex_session_id
        receipt = await control.epic_completion.complete(f.integration, "e", key="baseline")
        assert receipt.status == "SUCCEEDED", receipt
        summary = await f.review.context(f.coordinator, "p", "e", "review-before")
        decision = rejection(summary["context_id"])
        with pytest.raises(CoordinatorError, match="NOT_FULLY_READ"):
            await f.review.submit(f.coordinator, "p", "e", "unread", decision)
        with pytest.raises(CoordinatorError, match="CHUNK_INVALID"):
            await f.review.read(f.coordinator, "p", "e", summary["context_id"], True)
        summary, document = await read_context(f, "review-before")
        assert len(document["task_results"]) == 1
        assert document["independent_verification"]["result"]["exit_code"] == 0
        decision = rejection(summary["context_id"])
        choices = [CorrectionChoice(version=1, issue_number=1, template_id="fix")]
        response = await f.review.submit(f.coordinator, "p", "e", "reject", decision, choices)
        assert response.get("dispatch") == "DISPATCHED", response
        assert len(f.creates) == 1
        assert control.store.get_epic("e").codex_session_id == sid
        assert control.store.get_epic("e").status == EpicState.ACTIVE
        assert task(control, "F-01") == original
        repeated = await f.review.submit(f.coordinator, "p", "e", "reject", decision, choices)
        assert repeated == response
        assert len(f.creates) == 1
        corrective = response["tasks"][0]
        # Actual effective F39 plan: force a known startup pause, then recover
        # through only the registered read response and original guarded API.
        control = f.subject.context()[0]
        registered = control.scheduler.selection.specs[corrective["native_task_id"]]
        original_start = control.scheduler.start.start

        def not_ready(*args, **kwargs):
            from orchestrator.application.task_start_service import TaskStartError

            raise TaskStartError("RUNTIME_NOT_IDLE")

        monkeypatch.setattr(control.scheduler.start, "start", not_ready)
        runtime = control.runtime(control.delegate.log)
        paused = await runtime.call_async("task_start", dict(
            project_id="p", epic_run_id="e", task=registered,
        ))
        assert not paused.ok
        retained = task(control, corrective["local_task_id"])
        row = next(r for r in paused.data["overview"]["tasks"]
                   if r["task_run_id"] == retained.id)
        assert row["task_spec"] == registered
        assert task(control, "F-01") == original
        monkeypatch.setattr(control.scheduler.start, "start", original_start)
        starts = f.sessions.starts
        recovered = await runtime.call_async("task_start", dict(
            project_id="p", epic_run_id="e", task=row["task_spec"],
        ))
        assert recovered.ok and task(control, corrective["local_task_id"]).id == retained.id
        assert f.sessions.starts == starts + 1
        control = await deliver_one(f, corrective["native_task_id"], corrective["local_task_id"])
        assert f.sessions.starts == starts + 1
        receipt2 = await control.epic_completion.complete(f.integration, "e", key="corrected")
        assert receipt2.status == "SUCCEEDED", receipt2
        summary2, document2 = await read_context(f, "review-after")
        assert summary2["context_id"] != summary["context_id"]
        assert len(document2["task_results"]) == 2
        approved = ApprovalDecision(version=1, result="APPROVED",
                                    context_id=summary2["context_id"],
                                    summary="Checked full diff and both actual task deliveries",
                                    verified_criteria=f.policy.original.acceptance_criteria)
        final = await f.review.submit(f.coordinator, "p", "e", "approve", approved)
        assert final["status"] == "EPIC_APPROVED" and final["current"], final
        assert await f.review.submit(f.coordinator, "p", "e", "approve", approved) == final
        assert len(control.store.project_operations(f.review.runtime.run_id,
                                                   kind=f.review.REVIEW)) == 2
        assert control.store.get_epic("e").codex_session_id == sid
        assert f.review.runtime.main() == main
        assert not control.store.get_operations("e", kind="merge_epic_to_main")
        git = control.scheduler.selection.git
        path = control.settings.repository
        (path / "new-main.txt").write_text("Independent current main update\n")
        git.run("add", "--", "new-main.txt", cwd=path)
        git.run("commit", "-m", "advance fixture main", cwd=path)
        historical = await f.review.submit(f.coordinator, "p", "e", "approve", approved)
        assert historical["status"] == "HISTORICAL_APPROVAL" and not historical["current"]

    asyncio.run(run())


def test_foreign_principals_and_missing_handoff_have_no_review_effects(review_setup):
    f = review_setup

    async def run():
        before = f.review.store.db.total_changes
        for actor in (f.integration, None,
                      f.coordinator.model_copy(update={"role": Role.WORKER}),
                      f.coordinator.model_copy(update={"actor_id": "foreign"})):
            with pytest.raises(CoordinatorError):
                await f.review.context(actor, "p", "e", "denied")
        assert f.review.store.db.total_changes == before
        with pytest.raises(CoordinatorError, match="HANDOFF_MISSING"):
            await f.review.context(f.coordinator, "p", "e", "missing")
        assert not f.creates
        assert not f.review.store.project_operations(f.review.runtime.run_id, kind=f.review.REVIEW)

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["before", "after"])
def test_unknown_create_outcome_reconciles_without_replay(review_setup, failure):
    f = review_setup

    async def run():
        control = await deliver_one(f, T1, "F-01")
        await control.epic_completion.complete(f.integration, "e", key="baseline")
        summary, _ = await read_context(f, "review")
        decision = rejection(summary["context_id"])
        choices = [CorrectionChoice(version=1, issue_number=1, template_id="fix")]
        f.failures.append(failure)
        first = await f.review.submit(f.coordinator, "p", "e", "reject", decision, choices)
        again = await f.review.submit(f.coordinator, "p", "e", "reject", decision, choices)
        assert len(f.creates) == 1
        assert first == again
        if failure == "before":
            assert first["attention"] == "CORRECTION_CREATE_OUTCOME_UNCONFIRMED"
            assert len(f.board.tasks) == 1
            assert control.store.get_epic("e").status == EpicState.CHANGES_REQUESTED
            assert len(f.subject.plan.current()[0].tasks) == 1
        else:
            assert first["dispatch"] == "DISPATCHED"
            assert len(f.board.tasks) == 2
            assert len(f.subject.plan.current()[0].tasks) == 2
            with pytest.raises(CoordinatorError, match="EFFECTIVE_PLAN_STALE"):
                control.epic_start.connection_principal(f.integration)

    asyncio.run(run())


def test_lost_outer_review_receipt_recovers_atomic_decision(review_setup, monkeypatch):
    f = review_setup

    async def run():
        control = await deliver_one(f, T1, "F-01")
        await control.epic_completion.complete(f.integration, "e", key="baseline")
        summary, _ = await read_context(f, "review")
        approved = ApprovalDecision(version=1, result="APPROVED", context_id=summary["context_id"],
                                    summary="Actual independent verification and full diff checked",
                                    verified_criteria=f.policy.original.acceptance_criteria)
        save = f.review._save

        def lost(op, **kwargs):
            if op.kind == f.review.REVIEW:
                raise RuntimeError("Simulated process loss before outer receipt")
            return save(op, **kwargs)

        monkeypatch.setattr(f.review, "_save", lost)
        with pytest.raises(RuntimeError, match="Simulated process loss"):
            await f.review.submit(f.coordinator, "p", "e", "approve", approved)
        assert control.store.get_epic("e").status == EpicState.APPROVED
        assert len(control.store.get_operations("e", kind="epic_review")) == 1
        monkeypatch.setattr(f.review, "_save", save)
        result = await f.review.submit(f.coordinator, "p", "e", "approve", approved)
        assert result["status"] == "EPIC_APPROVED" and result["current"]
        assert len(control.store.get_operations("e", kind="epic_review")) == 1

    asyncio.run(run())


def test_sdk_only_scoped_review_contracts_and_no_task_or_main_authority(review_setup):
    f = review_setup
    control = f.subject.context()[0]
    claims = CoordinatorClaimService(f.review.runtime, control.scheduler.selection.reader,
                                      lambda *args: None, runtime_gate=None)
    runtime = RuntimeService(f.review.store, f.coordinator, control.delegate.log,
                             coordinator_control=CoordinatorControlService(claims, review=f.review))

    async def run():
        async with Client(create_server(runtime)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert names == {"coordinator_overview", "epic_claim_next",
                             "coordinator_review_context", "coordinator_review_read",
                             "coordinator_review_submit"}
            for extra in ({"role": "Coordinator"}, {"command": ["true"]}, {"tests_passed": True}):
                result = await client.call_tool("coordinator_review_context", dict(
                    project_id="p", epic_run_id="e", request_key="bad", **extra,
                ))
                assert not result.structured_content["ok"]
            result = await client.call_tool("coordinator_review_context", dict(
                project_id="other", epic_run_id="e", request_key="wrong-project",
            ))
            assert not result.structured_content["ok"]
        assert not f.creates

    asyncio.run(run())


@pytest.mark.parametrize("review_setup", [True], indirect=True)
def test_actual_independent_failure_can_only_reject_then_updated_code_passes(review_setup):
    f = review_setup

    async def run():
        control = await deliver_one(f, T1, "F-01")
        await control.epic_completion.complete(f.integration, "e", key="baseline")
        summary, document = await read_context(f, "before")
        verification = document["independent_verification"]
        assert verification["status"] == "FAILED" and verification["result"]["exit_code"] > 0
        approved = ApprovalDecision(version=1, result="APPROVED", context_id=summary["context_id"],
                                    summary="This unsupported approval must be rejected",
                                    verified_criteria=f.policy.original.acceptance_criteria)
        with pytest.raises(CoordinatorError, match="INDEPENDENT_TEST_FAILED"):
            await f.review.submit(f.coordinator, "p", "e", "bad-approval", approved)
        assert control.store.get_epic("e").status == EpicState.READY_FOR_REVIEW
        corrected = await f.review.submit(f.coordinator, "p", "e", "reject",
                                          rejection(summary["context_id"]), [
                                              CorrectionChoice(version=1, issue_number=1,
                                                               template_id="fix"),
                                          ])
        assert corrected.get("dispatch") == "DISPATCHED", corrected
        current = corrected["tasks"][0]
        control = await deliver_one(f, current["native_task_id"], current["local_task_id"])
        await control.epic_completion.complete(f.integration, "e", key="corrected")
        new, document2 = await read_context(f, "after")
        assert document2["independent_verification"]["result"]["exit_code"] == 0
        assert len(document2["task_results"]) == 2
        updated = approved.model_copy(update={"context_id": new["context_id"],
                                               "summary": "Corrected code now passes actual check"})
        result = await f.review.submit(f.coordinator, "p", "e", "good-approval", updated)
        assert result["status"] == "EPIC_APPROVED" and result["current"]

    asyncio.run(run())


def test_lost_dispatch_response_and_partial_group_receipt_do_not_repeat_effects(
    review_setup, monkeypatch,
):
    f = review_setup

    async def run():
        control = await deliver_one(f, T1, "F-01")
        await control.epic_completion.complete(f.integration, "e", key="baseline")
        summary, _ = await read_context(f, "review")
        first = rejection(summary["context_id"])
        decision = ChangesDecision(version=1, result="CHANGES_REQUESTED",
                                   context_id=first.context_id, issues=[
                                       first.issues[0], first.issues[0].model_copy(update={
                                           "number": 2, "problem": "Second concrete correction",
                                       }),
                                   ])
        choices = [CorrectionChoice(version=1, issue_number=n, template_id="fix") for n in (1, 2)]
        actual_prompt = f.sessions.prompt

        def lost_prompt_reply(name, text, *, timeout_ms):
            actual_prompt(name, text, timeout_ms=timeout_ms)
            raise HerdrError("UNKNOWN_RUNTIME_OUTCOME")

        monkeypatch.setattr(f.sessions, "prompt", lost_prompt_reply)
        actual_save = f.subject.plan.save

        def lost_group_receipt(op, **kwargs):
            saved = actual_save(op, **kwargs)
            if kwargs.get("stage") == "DISPATCHED":
                raise RuntimeError("Process loss after first actual dispatch receipt")
            return saved

        monkeypatch.setattr(f.subject.plan, "save", lost_group_receipt)
        with pytest.raises(RuntimeError, match="Process loss after first"):
            await f.review.submit(f.coordinator, "p", "e", "reject", decision, choices)
        ops = f.subject.plan.operations()
        assert [o.result["stage"] for o in ops] == ["DISPATCHED", "ACTIVE"]
        assert len(f.creates) == 2
        sent = len(f.sessions.sent)
        monkeypatch.setattr(f.subject.plan, "save", actual_save)
        result = await f.review.submit(f.coordinator, "p", "e", "reject", decision, choices)
        assert result["dispatch"] == "DISPATCHED"
        assert len(result["tasks"]) == 2 and len(f.creates) == 2
        assert all(o.result["stage"] == "DISPATCHED" for o in f.subject.plan.operations())
        assert len(f.sessions.sent) == sent
        assert len(control.store.get_tasks("e")) == 1
        assert all(t.worker_slot is None for t in control.store.get_tasks("e"))

    asyncio.run(run())


@pytest.mark.parametrize("change", ["none", "criteria", "identity", "capacity"])
def test_child_review_context_must_match_policy_and_other_settings_cannot_change(
    review_setup, change,
):
    f = review_setup
    parent = f.subject.original_start
    settings = parent.settings
    assert f.review.runtime.settings.review_context is None
    assert settings.review_context is not None
    if change == "capacity":
        altered = settings.model_copy(update={"max_workers": 1})
    elif change == "none":
        altered = settings.model_copy(update={"review_context": None})
    else:
        fields = {"acceptance_criteria": ["Foreign acceptance"]} if change == "criteria" else {
            "epic_id": "foreign-epic",
        }
        altered = settings.model_copy(update={
            "review_context": settings.review_context.model_copy(update=fields),
        })
    other = EpicStartService(altered, parent.store, parent.herdr, parent.spec, parent.principal,
                              codex=parent.codex)
    before = parent.store.db.total_changes
    with pytest.raises(CoordinatorError, match="SUBJECT_CONFIGURATION_INVALID"):
        CoordinatorReviewSubject(f.review.runtime, other, f.policy,
                                 f.subject.factory, f.subject.creator)
    assert parent.store.db.total_changes == before
    assert not f.creates


@pytest.mark.parametrize("moment", ["before", "after"])
@pytest.mark.parametrize("change", ["owner", "criteria", "version", "extra"])
def test_board_drift_during_correction_never_activates_or_dispatches(
    review_setup, monkeypatch, moment, change,
):
    f = review_setup

    def drift():
        row = f.board.tasks[0]
        if change == "extra":
            f.board.tasks.append(deepcopy(row) | {"taskId": str(uuid4())})
        else:
            row["version"] += 1
            if change == "owner":
                row["responsibleUserId"] = str(uuid4())
            elif change == "criteria":
                row["acceptanceCriteria"] = ["Changed externally"]

    async def run():
        control = await deliver_one(f, T1, "F-01")
        await control.epic_completion.complete(f.integration, "e", key="baseline")
        summary, _ = await read_context(f, "review")
        decision = rejection(summary["context_id"])
        choices = [CorrectionChoice(version=1, issue_number=1, template_id="fix")]
        if moment == "before":
            register = EpicIntegrationService.register_epic_review

            def changed_after_review(manager, *args, **kwargs):
                result = register(manager, *args, **kwargs)
                drift()
                return result

            monkeypatch.setattr(
                EpicIntegrationService, "register_epic_review", changed_after_review,
            )
        else:
            call = f.client.call_tool.side_effect

            async def changed_after_create(name, arguments):
                result = await call(name, arguments)
                if name == "create_task":
                    drift()
                return result

            f.client.call_tool.side_effect = changed_after_create
        sent = len(f.sessions.sent)
        result = await f.review.submit(f.coordinator, "p", "e", "reject", decision, choices)
        expected = ("CORRECTION_UNREGISTERED_TASK_MEMBERSHIP" if change == "extra"
                    else "CORRECTION_REVIEWED_TASK_SCOPE_CHANGED")
        assert result.get("attention") == expected, result
        assert len(f.creates) == (0 if moment == "before" else 1)
        assert control.store.get_epic("e").status == EpicState.CHANGES_REQUESTED
        assert len(f.subject.plan.current()[0].tasks) == 1
        assert len(f.sessions.sent) == sent
        assert len(control.store.get_tasks("e")) == 1
        again = await f.review.submit(f.coordinator, "p", "e", "reject", decision, choices)
        assert again["attention"] == expected
        assert len(f.creates) == (0 if moment == "before" else 1)
        assert len(f.sessions.sent) == sent

    asyncio.run(run())
