"""Two genuine F38 claims through real Git/state/services; simulated native/board/gate."""

import asyncio
import io
import json
import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent
from test_coordinator_claim import gate
from test_coordinator_review import ReviewSessions, deliver_one, read_context, rejection
from test_epic_start import Native
from test_epic_start import setup as epic_setup  # noqa: F401
from test_task_scheduler import Board
from test_teamplayer_reader import T1, E, P, U, epic, native_tools, reader, task

from orchestrator.adapters.teamplayer_correction import TeamPlayerCorrectionCreator
from orchestrator.adapters.teamplayer_mcp import TeamPlayerError, TeamPlayerMCPWriter
from orchestrator.application.coordinator_claim_service import CoordinatorClaimService
from orchestrator.application.coordinator_delivery_service import (
    CoordinatorDeliveryService,
    delivery_capability,
)
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
from orchestrator.application.epic_start_service import EpicStartService
from orchestrator.application.integration_control_service import IntegrationControlService
from orchestrator.application.task_scheduler_service import TaskSchedulerService
from orchestrator.application.task_selection_service import TaskSelectionService
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.domain.coordinator_review import CoordinatorReviewPolicy, CorrectionChoice
from orchestrator.domain.models import utc_now
from orchestrator.domain.policy import Actor, Role
from orchestrator.domain.review_contracts import ApprovalDecision, EpicReviewSpec
from orchestrator.domain.states import EpicState
from orchestrator.domain.teamplayer import LocalBoardBinding
from orchestrator.domain.worker_contracts import canonical_json
from orchestrator.event_log import EventLog

E2, T2 = str(UUID(int=2000)), str(UUID(int=2001))


@pytest.fixture
def project(epic_setup):  # noqa: F811
    initial, actor, _, _ = epic_setup
    first = initial.spec.model_dump(mode="json")
    first.update(dependencies=[], external_prerequisites=[], tasks=first["tasks"][:1])
    second = deepcopy(first)
    second.update(epic_id="E-35", title="Dependent epic", dependencies=["E-34"],
                  acceptance_criteria=["Independent second epic criteria"])
    second["tasks"][0]["task"].update(epic_id="E-35", task_id="F-02", dependencies=["F-01"])
    command = (sys.executable, "-c", "from pathlib import Path; "
               "assert all(p.read_text()=='verified\\n' for p in Path('.').glob('*.result'))")
    settings = initial.settings.model_copy(update=dict(worker_test_command=command))
    policies = []
    for spec, native, identity in ((first, E, T1), (second, E2, T2)):
        aggregate = dict(argv=command, timeout=30)
        complete = dict(version=1, project_id="p", epic_id=spec["epic_id"], acceptance_version=1,
                        task_ids=[t["task"]["task_id"] for t in spec["tasks"]],
                        requirements=spec["requirements"],
                        acceptance_criteria=spec["acceptance_criteria"], sources=spec["sources"],
                        build=aggregate, test=aggregate, acceptance=aggregate,
                        known_limitations=["Controlled board/native/security peers"])
        template = deepcopy(spec["tasks"][0])
        template["task"].update(task_id="FIX-" + spec["epic_id"], dependencies=[
            spec["tasks"][0]["task"]["task_id"],
        ])
        policies.append(CoordinatorReviewPolicy.model_validate_json(canonical_json(dict(
            version=1, native_project_id=P, native_epic_id=native, native_task_ids=[identity],
            user_id=U, original=spec, completion=complete, correction_completion=complete,
            verification=aggregate, templates=[dict(
                version=1, template_id="fix", criteria=spec["acceptance_criteria"], entry=template,
            )],
        ))))
    board = Board({T1: first["tasks"][0]["task"]})
    board.epics = [epic() | dict(id=n, name=s["title"], status="Pending")
                   for n, s in ((E, first), (E2, second))]
    board.tasks[0]["priority"] = 3
    board.tasks.append(task(T2, epicId=E2, name=second["tasks"][0]["task"]["name"], priority=3,
                            acceptanceCriteria=second["tasks"][0]["task"]["acceptance_criteria"],
                            dependencyTaskIds=[T1]))
    client = Mock()
    client.list_tools = AsyncMock(return_value=ListToolsResult(tools=native_tools()))

    async def call(name, arguments):
        if name == "update_epic_status":
            row = next(e for e in board.epics if e["id"] == arguments["epicId"])
            if row["version"] != arguments["version"]:
                raise TeamPlayerError("TEAMPLAYER_VERSION_CONFLICT")
            row.update(status=arguments["status"], version=row["version"] + 1)
            board.applied.append((name, deepcopy(arguments)))
            result = deepcopy(row)
        elif name == "create_task":
            req = arguments["request"]
            result = task(str(uuid4()), **{k: v for k, v in req.items() if k not in {
                "priority", "dependsOnTaskIds", "validationOwnerKind", "validationUserId",
            }}, priority=3, dependencyTaskIds=req["dependsOnTaskIds"])
            board.tasks.append(result)
        elif name.startswith("update_"):
            result = await board.write(name, arguments)
        else:
            result = await board.read(name, arguments)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(
            dict(success=True, data=result),
        ))])

    client.call_tool = AsyncMock(side_effect=call)
    adapter = TeamPlayerMCPWriter(client)
    asyncio.run(adapter.verify_catalog())
    adapter.verify_write_catalog()
    peers = ReviewSessions()
    root = Native()
    sid = str(uuid4())

    def prompt(name, text, *, timeout_ms):
        root.prompts.append(text)
        root.sid = sid
        root.turns.append(dict(id="root-turn", status="completed", items=[
            dict(id="u", type="userMessage", content=[dict(type="text", text=text)]),
            dict(id="a", type="agentMessage", nativeObservedAt=utc_now().isoformat(),
                 text=canonical_json(json.loads(text)["confirmation"])),
        ]))

    root.prompt = prompt
    codex = SimpleNamespace(
        read_thread=lambda identity, cwd: (root.read_thread(identity, cwd) if identity == sid
                                           else peers.read_thread(identity, cwd)),
        read_session=lambda identity, cwd: (dict(id=identity, session_id=identity, cwd=cwd)
                                            if identity == sid else peers.read_session(
                                                identity, cwd)),
    )
    processes = SimpleNamespace(capture=peers.capture, combine=peers.combine,
        inactive=lambda proof: peers.inactive(proof) and not (
            20 in proof["groups"] and root.agents),
    )
    review_hash = review_capability(policies)
    runtime = CoordinatorRuntimeService(settings, initial.store, root, dict(
        version=1, project_id="p", native_project_id=P, title="Fixture project",
        sources=["README.md"], epics=[dict(version=1, native_epic_id=p.native_epic_id,
                                          native_task_ids=p.native_task_ids, priority="P0",
                                          spec=p.original.model_dump(mode="json"))
                                     for p in policies],
    ), actor, "delivery-project", codex=codex, processes=processes, capabilities=dict(
        coordinator_review=review_hash, coordinator_delivery=delivery_capability(review_hash),
    ))
    runtime.start(actor)
    parents, subjects = {}, {}
    log = EventLog(stream=io.StringIO())

    def bindings():
        result = {}
        for policy in policies:
            result[policy.native_epic_id] = LocalBoardBinding(local_id=policy.original.epic_id)
            actual = next((s for s in subjects.values()
                           if s.policy.native_epic_id == policy.native_epic_id), None)
            spec, ids = (actual.plan.current()[:2] if actual else
                         (policy.original, policy.native_task_ids))
            result.update({identity: LocalBoardBinding(local_id=entry.task.task_id)
                           for identity, entry in zip(ids, spec.tasks, strict=True)})
        return result

    def start_factory(configured, run_id):
        if run_id not in parents:
            spec = configured.spec
            child = settings.model_copy(update=dict(review_context=EpicReviewSpec(
                version=1, project_id="p", epic_id=spec.epic_id, requirements=spec.requirements,
                acceptance_criteria=spec.acceptance_criteria, sources=spec.sources,
            )))
            principal = Actor(actor_id="integration-" + run_id, role=Role.INTEGRATION,
                              project_id="p", epic_run_id=run_id)
            sync = TeamPlayerSyncService(child, initial.store, adapter, project_id="p",
                                        external_project_id=P, user_id=U,
                                        scopes={run_id: tuple(e.task.task_id for e in spec.tasks)},
                                        codex=peers, processes=peers)
            parents[run_id] = EpicStartService(child, initial.store, peers, spec, principal,
                                               codex=peers, processes=peers, sync=sync,
                                               external_epic_id=configured.native_epic_id)
        return parents[run_id]

    claims = CoordinatorClaimService(runtime, reader(adapter, bindings=bindings()), start_factory,
                                     runtime_gate=gate)
    first_claim = asyncio.run(claims.claim(actor, "p", "first"))
    assert first_claim["status"] == "REGISTERED", first_claim
    run_id = first_claim["epic_run_id"]
    parent = parents[run_id]

    def factory(view):
        policy = next(p for p in policies if p.original.epic_id == view.spec.epic_id)
        specs = {identity: entry.task.model_dump(mode="json") for identity, entry in zip(
            view.native_task_ids, view.spec.tasks, strict=True,
        )}
        all_scopes = {identity: tuple(s.plan.current()[0].tasks[i].task.task_id
                                    for i in range(len(s.plan.current()[0].tasks)))
                      for identity, s in subjects.items()}
        previous = {identity: s for identity, s in subjects.items()
                    if identity != view.principal.epic_run_id
                    and initial.store.get_epic(identity).status == EpicState.DONE}
        prior_specs = {}
        for subject in previous.values():
            prior, native_ids, _ = subject.plan.current()
            prior_specs.update({identity: entry.task.model_dump(mode="json")
                                for identity, entry in zip(native_ids, prior.tasks, strict=True)})
        selection = TaskSelectionService(
            view.settings, initial.store, reader(adapter, bindings=bindings()), specs=specs,
            task_order=tuple(specs), dependency_specs=prior_specs,
            epic_prerequisites={view.spec.epic_id: tuple(
                p.native_epic_id for p in policies
                if p.original.epic_id in view.spec.dependencies)},
            delivery_contexts={identity: s.original_start.settings
                               for identity, s in previous.items()},
            scopes={identity: all_scopes[identity] for identity in previous},
            codex=peers, processes=peers,
        )
        sync = TeamPlayerSyncService(view.settings, initial.store, adapter, project_id="p",
                                    external_project_id=P, user_id=U, scopes=all_scopes,
                                    codex=peers, processes=peers)
        scheduler = TaskSchedulerService(view.settings, initial.store, selection, sync, peers,
                                         peers, processes=peers, review_provider=None,
                                         registration=view)
        completion = EpicCompletionService(view, scheduler, completion_for(policy, view.spec))
        return IntegrationControlService(view, scheduler, log, epic_completion=completion)

    subject = CoordinatorReviewSubject(runtime, parent, policies[0], factory,
        TeamPlayerCorrectionCreator(adapter, project_id=P, epic_id=E, user_id=U))
    subjects[run_id] = subject
    reviews = CoordinatorReviewService(runtime, subjects, policies=policies)
    delivery = CoordinatorDeliveryService(reviews, claims)
    claims.completed_plan = delivery.completed_plan
    f = SimpleNamespace(review=reviews, subject=subject, coordinator=actor,
                        integration=parent.principal, policy=policies[0], sessions=peers)
    yield SimpleNamespace(f=f, claims=claims, delivery=delivery, board=board,
                          parents=parents, bindings=bindings, adapter=adapter,
                          policies=policies, subjects=subjects, root=root, first_claim=first_claim)


@pytest.mark.parametrize("corrected", [False, True])
@pytest.mark.parametrize("lost_reply", [False, True])
def test_release_real_claim_and_start_dependent_epic_from_new_main(
    project, monkeypatch, corrected, lost_reply,
):
    p = project
    f = p.f
    service = p.delivery
    run_id = f.integration.epic_run_id

    async def run():
        control = await deliver_one(f, T1, "F-01")
        await control.epic_completion.complete(f.integration, run_id, key="baseline")
        if corrected:
            summary, _ = await read_context(f, "before")
            response = await f.review.submit(f.coordinator, "p", run_id, "reject",
                rejection(summary["context_id"]),
                [CorrectionChoice(version=1, issue_number=1, template_id="fix")])
            assert response.get("dispatch") == "DISPATCHED", response
            correction = response["tasks"][0]
            control = await deliver_one(f, correction["native_task_id"],
                                        correction["local_task_id"])
            await control.epic_completion.complete(f.integration, run_id, key="corrected")
        summary, _ = await read_context(f, "final")
        approval = await f.review.submit(f.coordinator, "p", run_id, "approve",
            ApprovalDecision(version=1, result="APPROVED", context_id=summary["context_id"],
                             summary="Complete actual current evidence",
                             verified_criteria=f.policy.original.acceptance_criteria))
        assert approval["current"], approval
        p.claims.reader = reader(p.adapter, bindings=p.bindings())
        p.claims.contexts[run_id] = control.settings
        p.claims.scopes[run_id] = tuple(e.task.task_id for e in f.subject.plan.current()[0].tasks)
        before = f.review.runtime.main()
        root_sid = service.store.get_coordinator(service.runtime.run_id).codex_session_id
        original_spec = service.runtime.spec.model_dump(mode="json")
        if lost_reply:
            drive = p.claims._drive

            async def lost(*args, **kwargs):
                await drive(*args, **kwargs)
                raise RuntimeError("Controlled lost reply after real next F34 registration")

            with monkeypatch.context() as patch:
                patch.setattr(p.claims, "_drive", lost)
                with pytest.raises(CoordinatorError, match="EPIC_CLAIM_START_UNVERIFIED"):
                    await service.deliver(f.coordinator, "p", run_id, "deliver", "verify")
            started = f.sessions.starts
            result = await service.deliver(f.coordinator, "p", run_id, "retry", "verify")
            assert f.sessions.starts == started
        else:
            result = await service.deliver(f.coordinator, "p", run_id, "deliver", "verify")
        assert result["status"] == "DONE", result
        next_run = result["continuation"]
        assert next_run["status"] == "REGISTERED", next_run
        assert next_run["epic_id"] == "E-35"
        first = service.store.get_epic(run_id)
        second = service.store.get_epic(next_run["epic_run_id"])
        assert first.status == EpicState.DONE and before != first.merge_commit
        assert second.base_commit == first.merge_commit == next_run["main_commit"]
        assert service.store.get_coordinator(service.runtime.run_id).codex_session_id == root_sid
        assert second.codex_session_id != first.codex_session_id
        assert p.parents[run_id].lifecycle.reconnect_epic(
            f.coordinator, run_id)["status"] == "STOPPED"
        assert p.parents[second.id].settings.review_context.epic_id == "E-35"
        assert p.parents[second.id].settings.review_context.acceptance_criteria == (
            ["Independent second epic criteria"]
        )
        assert service.runtime.spec.model_dump(mode="json") == original_spec
        claim = service.store.get_project_operation("p", "epic_claim", "first")
        assert claim.result["stage"] == "RELEASED"
        assert claim.result["delivery_id"] == result["delivery_id"]
        assert len(p.claims._active_claims()) == 1
        starts = f.sessions.starts
        again = await service.deliver(f.coordinator, "p", run_id, "retry", "retry-verify")
        assert again["continuation"] == next_run
        assert f.sessions.starts == starts and len(service._rows()) == 1
        assert len(service.store.get_operations(run_id, kind="merge_epic_to_main")) == 1
        # Bind the second actual F34 run to its policy without restarting or
        # changing the original root's capability/configuration hash.
        second_subject = CoordinatorReviewSubject(
            service.runtime, p.parents[second.id], p.policies[1],
            f.subject.factory,
            TeamPlayerCorrectionCreator(p.adapter, project_id=P, epic_id=E2, user_id=U),
        )
        expanded = CoordinatorReviewService(service.runtime,
            {run_id: f.subject, second.id: second_subject}, policies=p.policies)
        assert expanded.configuration_hash == f.review.configuration_hash
        p.subjects[second.id] = second_subject
        second_delivery = CoordinatorDeliveryService(expanded, p.claims)
        second_fixture = SimpleNamespace(
            review=expanded, subject=second_subject, coordinator=f.coordinator,
            integration=p.parents[second.id].principal, policy=p.policies[1], sessions=f.sessions,
        )
        next_control = second_subject.context()[0]
        advice = await next_control.scheduler.selection.get_next(
            second_fixture.integration, second.id,
        )
        assert advice["candidates"] == [T2], advice
        next_control = await deliver_one(second_fixture, T2, "F-02")
        assert len(service.store.get_tasks(second.id)) == 1
        await next_control.epic_completion.complete(
            second_fixture.integration, second.id, key="second-complete",
        )
        next_summary, _ = await read_context(second_fixture, "second-final")
        await expanded.submit(f.coordinator, "p", second.id, "second-approve",
            ApprovalDecision(version=1, result="APPROVED",
                context_id=next_summary["context_id"], summary="Verified second actual delivery",
                verified_criteria=p.policies[1].original.acceptance_criteria))
        final = await second_delivery.deliver(
            f.coordinator, "p", second.id, "second-deliver", "second-verify",
        )
        assert final["status"] == "DONE", final
        assert service.store.get_epic(second.id).status == EpicState.DONE
        assert len(service.store.get_operations(second.id, kind="merge_epic_to_main")) == 1
        assert p.parents[second.id].lifecycle.reconnect_epic(
            f.coordinator, second.id)["status"] == "STOPPED"
        service.runtime.connection_principal(f.coordinator)

    asyncio.run(run())
