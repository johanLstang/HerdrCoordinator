"""F40: actual temporary Git/SQLite/processes; controlled native/board/gate peers."""

import asyncio
import io
from types import SimpleNamespace

import pytest
from mcp import Client
from test_coordinator_claim import gate
from test_coordinator_review import (
    deliver_one,
    read_context,
    review_setup,  # noqa: F401
)
from test_epic_start import setup as epic_setup  # noqa: F401
from test_teamplayer_reader import T1

from orchestrator.application.coordinator_claim_service import CoordinatorClaimService
from orchestrator.application.coordinator_control_service import CoordinatorControlService
from orchestrator.application.coordinator_delivery_service import CoordinatorDeliveryService
from orchestrator.application.coordinator_review_service import CoordinatorReviewService
from orchestrator.application.coordinator_runtime_service import CoordinatorError
from orchestrator.application.epic_integration_service import EpicIntegrationService
from orchestrator.application.runtime_service import RuntimeService
from orchestrator.application.state_service import StateService
from orchestrator.application.teamplayer_sync import TeamPlayerSyncService
from orchestrator.domain.policy import Role
from orchestrator.domain.review_contracts import ApprovalDecision
from orchestrator.domain.states import EpicState
from orchestrator.event_log import EventLog
from orchestrator.mcp.server import create_server
from orchestrator.persistence.store import StateStore

pytestmark = pytest.mark.parametrize("review_setup", ["delivery"], indirect=True)


@pytest.fixture
def delivery_setup(review_setup):  # noqa: F811
    f = review_setup
    control = f.subject.context()[0]
    claims = CoordinatorClaimService(
        f.review.runtime, control.scheduler.selection.reader,
        lambda *args: pytest.fail("No further epic exists in this fixture"),
        runtime_gate=gate, delivery_contexts={"e": control.settings},
        scopes=control.scheduler.sync.evidence.scopes,
    )
    delivery = CoordinatorDeliveryService(f.review, claims)
    claims.completed_plan = delivery.completed_plan
    return SimpleNamespace(f=f, control=control, claims=claims, delivery=delivery)


async def approved(f):
    control = await deliver_one(f, T1, "F-01")
    run_id = f.integration.epic_run_id
    receipt = await control.epic_completion.complete(f.integration, run_id, key="baseline")
    assert receipt.status == "SUCCEEDED"
    summary, _ = await read_context(f, "review")
    decision = ApprovalDecision(version=1, result="APPROVED", context_id=summary["context_id"],
                                summary="Checked actual complete current evidence",
                                verified_criteria=f.policy.original.acceptance_criteria)
    result = await f.review.submit(f.coordinator, "p", run_id, "approved", decision)
    assert result["current"], result
    return result


def test_actual_merge_stop_final_test_done_sync_and_repeat(delivery_setup):
    d = delivery_setup
    f, service = d.f, d.delivery

    async def run():
        await approved(f)
        git = f.review.runtime.git
        before = f.review.runtime.main()
        epic = service.store.get_epic("e")
        source = git.head(epic.branch)
        sid = epic.codex_session_id
        result = await service.deliver(f.coordinator, "p", "e", "deliver", "final")
        assert result["status"] == "DONE", result
        epic = service.store.get_epic("e")
        assert epic.status == EpicState.DONE and epic.completed_at is not None
        assert git.parents(epic.merge_commit) == (before, source)
        assert git.head("main") == epic.merge_commit
        assert epic.codex_session_id == sid
        assert f.subject.original_start.lifecycle.reconnect_epic(f.coordinator, "e")["status"] == (
            "STOPPED"
        )
        assert f.board.epics[0]["status"] == "Done"
        assert result["continuation"]["status"] == "WAIT"
        assert not d.claims._active_claims()
        merges = service.store.get_operations("e", kind="merge_epic_to_main")
        tests = service.store.get_operations("e", kind="verify_main_merge")
        exits = f.sessions.exits
        again = await service.deliver(f.coordinator, "p", "e", "new-delivery-key", "new-final-key")
        assert again["merge_commit"] == epic.merge_commit
        assert service.store.get_operations("e", kind="merge_epic_to_main") == merges
        assert service.store.get_operations("e", kind="verify_main_merge") == tests
        assert f.sessions.exits == exits
        assert len(service._rows()) == 1

    asyncio.run(run())


def test_sdk_has_only_registered_delivery_arguments_and_no_operator_authorization(delivery_setup):
    d = delivery_setup
    control = CoordinatorControlService(d.claims, review=d.f.review, delivery=d.delivery)
    service = RuntimeService(d.delivery.store, d.f.coordinator, EventLog(stream=io.StringIO()),
                             coordinator_control=control)
    before = service.store.db.total_changes

    async def run():
        async with Client(create_server(service)) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert len(tools) == 6
            assert "coordinator_epic_deliver" in tools
            assert not {"epic_merge", "authorize_bounded_product_probe", "task_merge"} & set(tools)
            schema = tools["coordinator_epic_deliver"].input_schema
            assert set(schema["properties"]) == {
                "project_id", "epic_run_id", "request_key", "verification_key",
            }
            args = dict(project_id="p", epic_run_id="e", request_key="delivery",
                        verification_key="final")
            for field, value in (("role", "Coordinator"), ("tests_passed", True),
                                 ("command", ["true"]), ("authorization", True)):
                result = await client.call_tool("coordinator_epic_deliver", args | {field: value})
                assert result.structured_content["code"] == "INVALID_ARGUMENTS"
            missing = await client.call_tool("coordinator_epic_deliver", args)
            assert missing.structured_content["code"] == "COORDINATOR_DELIVERY_APPROVAL_MISSING"
            assert service.store.db.total_changes == before

    asyncio.run(run())


def test_lost_board_done_reply_retries_only_sync(delivery_setup):
    from orchestrator.adapters.teamplayer_mcp import TeamPlayerError

    d = delivery_setup
    f, service = d.f, d.delivery

    async def run():
        await approved(f)
        native_call = f.client.call_tool.side_effect
        lost = False

        async def call(name, arguments):
            nonlocal lost
            result = await native_call(name, arguments)
            if name == "update_epic_status" and arguments["status"] == "Done" and not lost:
                lost = True
                raise TeamPlayerError("TEAMPLAYER_WRITE_OUTCOME_UNKNOWN")
            return result

        f.client.call_tool.side_effect = call
        first = await service.deliver(f.coordinator, "p", "e", "delivery", "final")
        assert first["status"] == "ATTENTION" and first["stage"] == "LOCAL_DONE", first
        assert f.board.epics[0]["status"] == "Done"
        assert not service.store.project_operations(service.runtime.run_id, kind=service.CONTINUE)
        sha, exits = f.review.runtime.main(), f.sessions.exits
        result = await service.deliver(f.coordinator, "p", "e", "delivery", "new-key-not-rerun")
        assert result["status"] == "DONE", result
        assert f.review.runtime.main() == sha and f.sessions.exits == exits
        assert len(service.store.get_operations("e", kind="merge_epic_to_main")) == 1
        assert len(service.store.get_operations("e", kind="verify_main_merge")) == 1
        assert len([c for c in f.board.applied if c[0] == "update_epic_status"]) == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["owner", "main"])
def test_drift_after_actual_merge_preserves_sha_and_never_finishes(
    delivery_setup, monkeypatch, change,
):
    d = delivery_setup
    f, service = d.f, d.delivery

    async def run():
        await approved(f)
        original = EpicIntegrationService.merge_epic_to_main

        def drift(manager, *args, **kwargs):
            result = original(manager, *args, **kwargs)
            if change == "owner":
                f.board.tasks[0]["responsibleUserId"] = f.board.tasks[0]["responsibleAgentId"]
                f.board.tasks[0]["version"] += 1
            else:
                path = service.runtime.settings.repository
                (path / "external.txt").write_text("Independent main after actual merge\n")
                manager.git.run("add", "external.txt", cwd=path)
                manager.git.run("commit", "-m", "Independent current main", cwd=path)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(EpicIntegrationService, "merge_epic_to_main", drift)
            result = await service.deliver(f.coordinator, "p", "e", "delivery", "final")
        assert result["status"] == "ATTENTION", result
        merge = service.store.get_operations("e", kind="merge_epic_to_main")[0]
        assert result["observed_merge_commit"] == merge.result["merge_commit"]
        assert service.store.get_epic("e").status == EpicState.MERGING
        assert f.board.epics[0]["status"] == "InProgress"
        assert not service.store.project_operations(service.runtime.run_id, kind=service.CONTINUE)
        retry = await service.deliver(f.coordinator, "p", "e", "retry", "final")
        assert retry["status"] == "ATTENTION"
        assert len(service.store.get_operations("e", kind="merge_epic_to_main")) == 1

    asyncio.run(run())


def test_failed_final_test_keeps_actual_merge_and_requires_new_verification_key(delivery_setup):
    d = delivery_setup
    f, service = d.f, d.delivery

    async def run():
        await approved(f)
        f.verification_failure_file.write_text("Controlled external verification outage\n")
        result = await service.deliver(f.coordinator, "p", "e", "delivery", "failed-test")
        assert result["status"] == "ATTENTION", result
        assert result["attention"] == "COORDINATOR_DELIVERY_FINAL_TEST_FAILED"
        epic = service.store.get_epic("e")
        sha = f.review.runtime.main()
        assert epic.status == EpicState.MERGING and epic.merge_commit == sha
        assert f.board.epics[0]["status"] == "InProgress"
        assert not service.store.project_operations(f.review.runtime.run_id, kind=service.CONTINUE)
        f.verification_failure_file.unlink()
        again = await service.deliver(f.coordinator, "p", "e", "different-key", "failed-test")
        assert again["status"] == "ATTENTION"
        assert len(service.store.get_operations("e", kind="verify_main_merge")) == 1
        done = await service.deliver(f.coordinator, "p", "e", "delivery", "new-actual-test")
        assert done["status"] == "DONE", done
        assert f.review.runtime.main() == sha
        assert len(service.store.get_operations("e", kind="merge_epic_to_main")) == 1
        tests = service.store.get_operations("e", kind="verify_main_merge")
        assert len(tests) == 2 and [t.status for t in tests] == ["FAILED", "SUCCEEDED"]

    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["merge", "stop", "test", "done", "sync", "release"])
def test_crash_recovers_only_missing_delivery_steps(delivery_setup, monkeypatch, boundary):
    d = delivery_setup
    f, service = d.f, d.delivery

    async def run():
        await approved(f)
        exits_before = f.sessions.exits
        with monkeypatch.context() as patch:
            if boundary in {"merge", "test"}:
                name = "merge_epic_to_main" if boundary == "merge" else "verify_main_merge"
                original = getattr(EpicIntegrationService, name)

                def crashed(manager, *args, **kwargs):
                    original(manager, *args, **kwargs)
                    raise RuntimeError("Controlled crash after " + boundary)

                patch.setattr(EpicIntegrationService, name, crashed)
            elif boundary == "stop":
                original = f.subject.original_start.lifecycle.stop_epic

                def crashed(*args, **kwargs):
                    original(*args, **kwargs)
                    raise RuntimeError("Controlled crash after stop")

                patch.setattr(f.subject.original_start.lifecycle, "stop_epic", crashed)
            elif boundary == "done":
                original = StateService.transition_epic

                def crashed(manager, run_id, target, **kwargs):
                    result = original(manager, run_id, target, **kwargs)
                    if target == EpicState.DONE:
                        raise RuntimeError("Controlled crash after Done")
                    return result

                patch.setattr(StateService, "transition_epic", crashed)
            elif boundary == "sync":
                original = TeamPlayerSyncService.sync_epic

                async def crashed(*args, **kwargs):
                    await original(*args, **kwargs)
                    raise RuntimeError("Controlled crash after sync")

                patch.setattr(TeamPlayerSyncService, "sync_epic", crashed)
            else:
                original = CoordinatorDeliveryService._drive

                async def crashed(*args, **kwargs):
                    result = await original(*args, **kwargs)
                    assert result.result["stage"] == "RELEASED"
                    raise RuntimeError("Controlled crash after committed release")

                patch.setattr(CoordinatorDeliveryService, "_drive", crashed)
            with pytest.raises(RuntimeError, match="Controlled crash"):
                await service.deliver(f.coordinator, "p", "e", "delivery", "final")
        with StateStore(service.runtime.settings.sqlite_path) as other:
            stored = other.get_project_operation("p", service.KIND, "delivery")
            assert stored is not None and stored.id == service._rows()[0].id
            assert len(other.get_operations("e", kind="merge_epic_to_main")) == 1
        reopened = CoordinatorDeliveryService(f.review, d.claims)
        d.claims.completed_plan = reopened.completed_plan
        result = await reopened.deliver(f.coordinator, "p", "e", "retry-key", "final")
        assert result["status"] == "DONE", result
        assert len(service.store.get_operations("e", kind="merge_epic_to_main")) == 1
        assert len(service.store.get_operations("e", kind="verify_main_merge")) == 1
        assert len([c for c in f.board.applied if c[0] == "update_epic_status"]) == 1
        assert f.sessions.exits == exits_before + 1
        assert service.store.get_epic("e").status == EpicState.DONE

    asyncio.run(run())


def test_default_gate_has_no_effect_bounded_operator_permit_is_exact(delivery_setup):
    d = delivery_setup
    f, service = d.f, d.delivery

    async def run():
        decision = await approved(f)
        d.claims.gate = None
        sha = f.review.runtime.main()
        assert await service.deliver(f.coordinator, "p", "e", "delivery", "final") == dict(
            status="WAIT", reason="RUNTIME_SECURITY_GATE_UNVERIFIED",
        )
        assert not service._rows()
        assert not service.store.get_operations("e", kind="merge_epic_to_main")
        assert service.store.get_epic("e").status == EpicState.APPROVED
        with pytest.raises(CoordinatorError, match="AUTHORIZATION_STALE"):
            await service.authorize_bounded_product_probe(f.coordinator, "e", "foreign-review",
                                                          reason="Explicit bounded fixture")
        permit = await service.authorize_bounded_product_probe(
            f.coordinator, "e", decision["review_id"], reason="Explicit bounded fixture only",
        )
        assert permit.result["target_commit"] == sha
        result = await service.deliver(f.coordinator, "p", "e", "delivery", "final")
        assert result["status"] == "DONE", result
        assert result["authorization"]["kind"] == "bounded_operator_probe"
        assert d.claims.gate is None
        assert result["continuation"]["status"] == "WAIT"
        assert not d.claims._active_claims()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["main", "owner", "membership"])
def test_stale_approval_or_board_never_merges(delivery_setup, change):
    d = delivery_setup
    f, service = d.f, d.delivery

    async def run():
        await approved(f)
        git = f.review.runtime.git
        if change == "main":
            repo = f.review.runtime.settings.repository
            (repo / "independent.txt").write_text("Independent main change\n")
            git.run("add", "independent.txt", cwd=repo)
            git.run("commit", "-m", "Independent current main", cwd=repo)
        elif change == "owner":
            f.board.tasks[0]["responsibleUserId"] = f.board.tasks[0]["responsibleAgentId"]
            f.board.tasks[0]["version"] += 1
        else:
            f.board.tasks.append(f.board.tasks[0] | {
                "taskId": "00000000-0000-4000-8000-000000001111",
            })
        with pytest.raises(CoordinatorError, match="APPROVAL_STALE"):
            await service.deliver(f.coordinator, "p", "e", "delivery", "final")
        assert not service._rows()
        assert not service.store.get_operations("e", kind="merge_epic_to_main")
        assert service.store.get_epic("e").status == EpicState.APPROVED

    asyncio.run(run())


@pytest.mark.parametrize("role", [Role.INTEGRATION, Role.WORKER])
def test_foreign_role_cannot_authorize_or_deliver(delivery_setup, role):
    d = delivery_setup
    f, service = d.f, d.delivery
    actor = f.coordinator.model_copy(update={"role": role})
    before = service.store.db.total_changes

    async def run():
        with pytest.raises(CoordinatorError, match="SCOPE_DENIED"):
            await service.deliver(actor, "p", "e", "delivery", "final")
        with pytest.raises(CoordinatorError, match="SCOPE_DENIED"):
            await service.authorize_bounded_product_probe(actor, "e", "review", reason="Not owner")
        assert service.store.db.total_changes == before
        assert not service._rows()

    asyncio.run(run())


def test_unknown_final_result_is_not_replayed_with_same_key(delivery_setup, monkeypatch):
    d = delivery_setup
    f, service = d.f, d.delivery

    async def run():
        await approved(f)
        with monkeypatch.context() as patch:
            # Actual F08 intent is committed, but this controlled interruption
            # launches no process. Its absence is known to this test/operator.
            def crash(*args):
                raise RuntimeError("Controlled interruption before process launch")

            patch.setattr(EpicIntegrationService, "_execute", crash)
            with pytest.raises(RuntimeError, match="Controlled interruption"):
                await service.deliver(f.coordinator, "p", "e", "delivery", "unknown")
        sha = f.review.runtime.main()
        result = await service.deliver(f.coordinator, "p", "e", "delivery", "unknown")
        assert result["status"] == "ATTENTION", result
        assert result["attention"] == "COORDINATOR_DELIVERY_FINAL_TEST_UNKNOWN"
        assert service.store.get_epic("e").status == EpicState.MERGING
        assert f.board.epics[0]["status"] == "InProgress"
        assert len(service.store.get_operations("e", kind="verify_main_merge")) == 1
        done = await service.deliver(f.coordinator, "p", "e", "delivery", "explicit-new-attempt")
        assert done["status"] == "DONE", done
        assert f.review.runtime.main() == sha
        assert len(service.store.get_operations("e", kind="merge_epic_to_main")) == 1

    asyncio.run(run())


def test_project_lock_and_registered_policy_set_deny_unverified_subjects(delivery_setup):
    d = delivery_setup
    f, service = d.f, d.delivery
    before = service.store.db.total_changes

    async def run():
        with service.runtime.lock():
            with pytest.raises(CoordinatorError, match="COORDINATOR_BUSY"):
                await service.deliver(f.coordinator, "p", "e", "delivery", "final")
        waiting = CoordinatorReviewService(service.runtime, {}, policies=[f.policy])
        with pytest.raises(CoordinatorError, match="SCOPE_DENIED"):
            await waiting.context(f.coordinator, "p", "unregistered-run", "review")
        waiting.policies[f.policy.native_epic_id].original.title = "Unregistered policy change"
        with pytest.raises(CoordinatorError, match="CONFIGURATION_CHANGED"):
            waiting.verify_configuration()
        assert service.store.db.total_changes == before
        assert not service._rows()

    asyncio.run(run())
