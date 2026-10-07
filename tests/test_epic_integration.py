import subprocess
import sys
from pathlib import Path

import pytest
from test_git_integration import COMMAND, approve, commit, git
from test_git_integration import setup as integration_setup

from orchestrator.application.epic_integration_service import EpicIntegrationService
from orchestrator.application.git_integration_service import IntegrationError
from orchestrator.application.state_service import StateService
from orchestrator.domain.policy import Actor, Role, VerifiedFacts
from orchestrator.domain.states import EpicState as E
from orchestrator.domain.states import TaskState as T
from orchestrator.persistence.store import StateStore


@pytest.fixture(name="setup")
def setup_fixture(tmp_path):
    yield from integration_setup.__wrapped__(tmp_path)


def epic_service(service, *, command=COMMAND, scope=("F-01", "F-02")):
    return EpicIntegrationService(
        service.worktrees.settings, service.store, test_command=command, expected_task_ids=scope
    )


def deliver_tasks(setup):
    service, i, c, epic, tasks = setup
    for n, task in enumerate(tasks):
        if n:
            service.sync_task_with_epic(i, task.id, key=f"sync-{n}")
        approve(service, i, task)
        op = service.merge_task_to_epic(i, task.id, key=f"merge-{n}")
        # Real postmerge process and state foundation; future completion agent is not simulated.
        actual = subprocess.run(COMMAND, cwd=epic.worktree_path, check=False).returncode
        assert actual == 0
        StateService(service.store).transition_task(
            task.id,
            T.DONE,
            expected=T.MERGING,
            event_id=f"done-{n}",
            actor=i,
            facts=VerifiedFacts(
                source_commit=op.result["source_commit"],
                target_commit=op.result["target_commit"],
                merge_commit=op.result["merge_commit"],
                verification_commit=op.result["merge_commit"],
                tests_passed=actual == 0,
            ),
        )
    old = service.store.get_epic(epic.id)
    StateService(service.store).transition_epic(
        old.id, E.ACTIVE, expected=E.PLANNED, event_id="start", actor=c
    )
    return epic_service(service), c, epic


def epic_approve(service, c, epic, suffix=""):
    op = service.verify_epic(c, epic.id, key=f"verify{suffix}")
    assert op.status == "SUCCEEDED"
    return service.register_epic_review(
        c, epic.id, verification_key=op.idempotency_key, key=f"review{suffix}", approved=True
    )


def test_actual_main_delivery_and_verification_do_not_set_done(setup):
    s, c, e = deliver_tasks(setup)
    review = epic_approve(s, c, e)
    op = s.merge_epic_to_main(c, e.id, key="main")
    merged = op.result["merge_commit"]
    assert s.git.parents(merged) == (review.result["target_commit"], review.result["source_commit"])
    assert s.git.head("main") == merged
    assert (s.worktrees.settings.repository / "task2.txt").is_file()
    final = s.verify_main_merge(c, e.id, merge_key="main", key="final")
    assert final.status == "SUCCEEDED" and final.result["exit_code"] == 0
    assert s.verify_main_merge(c, e.id, merge_key="main", key="final") == final
    assert s.store.get_epic(e.id).status == E.MERGING
    assert s.store.get_epic(e.id).completed_at is None
    assert s.store.get_epic(e.id).merge_commit == merged
    assert s.merge_epic_to_main(c, e.id, key="main") == op
    with pytest.raises(IntegrationError, match="already merged|approval"):
        s.merge_epic_to_main(c, e.id, key="duplicate")


@pytest.mark.parametrize(
    "actor",
    [
        Actor(actor_id="i", role=Role.INTEGRATION, project_id="p", epic_run_id="epic"),
        Actor(
            actor_id="w", role=Role.WORKER, project_id="p", epic_run_id="epic", task_run_id="task1"
        ),
        Actor(actor_id="c", role=Role.COORDINATOR, project_id="wrong"),
        Actor(actor_id="c", role=Role.COORDINATOR, project_id="p", epic_run_id="wrong"),
    ],
)
def test_wrong_principal_cannot_verify_review_sync_or_merge(setup, actor):
    s, c, e = deliver_tasks(setup)
    epic_approve(s, c, e)
    before = s.git.head("main")
    for call in [
        lambda: s.verify_epic(actor, e.id, key="bad-test"),
        lambda: s.register_epic_review(
            actor, e.id, verification_key="verify", key="bad-review", approved=True
        ),
        lambda: s.sync_epic_with_main(actor, e.id, key="bad-sync"),
        lambda: s.merge_epic_to_main(actor, e.id, key="bad-merge"),
    ]:
        with pytest.raises(IntegrationError, match="Coordinator"):
            call()
    assert s.git.head("main") == before
    assert s.store.get_operations(e.id, kind="merge_epic_to_main") == []


def test_unfinished_and_missing_scope_cannot_approve(setup):
    service, i, c, e, tasks = setup
    s = epic_service(service)
    with pytest.raises(IntegrationError, match="Done"):
        s.verify_epic(c, e.id, key="unfinished")
    missing = epic_service(service, scope=("F-01", "F-02", "F-03"))
    with pytest.raises(IntegrationError, match="scope"):
        missing.verify_epic(c, e.id, key="missing")
    with pytest.raises(IntegrationError):
        epic_service(service, scope=())
    assert s.git.head("main") == e.base_commit


def test_changed_main_requires_explicit_sync_and_new_review(setup):
    s, c, e = deliver_tasks(setup)
    old = epic_approve(s, c, e)
    repo = s.worktrees.settings.repository
    (repo / "main-change.txt").write_text("main\n")
    main = commit(repo, "advance main")
    with pytest.raises(IntegrationError, match="approval"):
        s.merge_epic_to_main(c, e.id, key="stale")
    with pytest.raises(IntegrationError):
        s.register_epic_review(
            c, e.id, verification_key="verify", key="stale-review", approved=True
        )
    assert s.git.head("main") == main
    synced = s.sync_epic_with_main(c, e.id, key="sync")
    assert s.store.get_epic(e.id).status == E.CHANGES_REQUESTED
    assert s.store.get_epic(e.id).approved_source_commit is None
    assert s.git.parents(synced.result["merge_commit"])[1] == main
    assert (
        s.register_epic_review(c, e.id, verification_key="verify", key="review", approved=True)
        == old
    )
    epic_approve(s, c, e, "-new")
    merged = s.merge_epic_to_main(c, e.id, key="main-new")
    assert s.git.parents(merged.result["merge_commit"])[0] == main
    assert s.sync_epic_with_main(c, e.id, key="sync") == synced


@pytest.mark.parametrize("timing", ["before", "after"])
def test_crash_around_git_reconciles_one_main_merge_after_reopen(setup, monkeypatch, timing):
    s, c, e = deliver_tasks(setup)
    epic_approve(s, c, e)
    original = s.git.merge_commit

    def crash(*args):
        if timing == "after":
            original(*args)
        raise RuntimeError("injected crash")

    monkeypatch.setattr(s.git, "merge_commit", crash)
    with pytest.raises(RuntimeError, match="injected crash"):
        s.merge_epic_to_main(c, e.id, key="crash")
    op = s.store.get_operation("p", "merge_epic_to_main", "crash")
    assert op.status == "PENDING"
    assert s.store.get_epic(e.id).status == E.MERGING
    with StateStore(s.worktrees.settings.sqlite_path) as db:
        other = EpicIntegrationService(
            s.worktrees.settings, db, test_command=COMMAND, expected_task_ids=("F-01", "F-02")
        )
        done = other.merge_epic_to_main(c, e.id, key="crash")
        assert done.status == "SUCCEEDED"
        assert other.merge_epic_to_main(c, e.id, key="crash") == done
        assert (
            git(s.worktrees.settings.repository, "rev-list", "--first-parent", "--count", "main")
            == "2"
        )
        assert db.get_epic(e.id).status == E.MERGING


def test_failed_final_verification_preserves_merge_and_retry_only_tests(setup):
    s, c, e = deliver_tasks(setup)
    epic_approve(s, c, e)
    op = s.merge_epic_to_main(c, e.id, key="main")
    command = (sys.executable, "-c", "import sys; sys.exit(9)")
    failed = epic_service(s, command=command).verify_main_merge(
        c, e.id, merge_key="main", key="failed"
    )
    assert failed.status == "FAILED" and failed.error_code == "TEST_FAILED"
    assert failed.result["exit_code"] == 9
    assert s.store.get_epic(e.id).merge_commit == op.result["merge_commit"]
    assert s.store.get_epic(e.id).completed_at is None
    assert s.verify_main_merge(c, e.id, merge_key="main", key="retry").status == "SUCCEEDED"
    assert s.git.head("main") == op.result["merge_commit"]
    assert s.store.get_epic(e.id).status == E.MERGING


@pytest.mark.parametrize("destination", ["main", "epic"])
def test_dirty_worktree_blocks_main_delivery(setup, destination):
    s, c, e = deliver_tasks(setup)
    epic_approve(s, c, e)
    path = s.worktrees.settings.repository if destination == "main" else Path(e.worktree_path)
    (path / "base.txt").write_text("dirty\n")
    with pytest.raises(IntegrationError, match="clean"):
        s.merge_epic_to_main(c, e.id, key="dirty")
    assert s.git.head("main") == e.base_commit
    assert (path / "base.txt").read_text() == "dirty\n"


def test_scope_change_and_missing_merge_proof_rejected(setup):
    s, c, e = deliver_tasks(setup)
    epic_approve(s, c, e)
    other = epic_service(s, scope=("F-01",))
    with pytest.raises(IntegrationError, match="scope"):
        other.merge_epic_to_main(c, e.id, key="changed-scope")
    op = s.store.get_operations(e.id, kind="merge_task_to_epic")[0]
    s._finish(op, "FAILED", error="INJECTED")
    with pytest.raises(IntegrationError, match="actual registered"):
        s.merge_epic_to_main(c, e.id, key="missing-proof")
    assert s.git.head("main") == e.base_commit


def test_changed_epic_or_test_configuration_requires_new_evidence(setup):
    s, c, e = deliver_tasks(setup)
    epic_approve(s, c, e)
    changed = epic_service(s, command=(sys.executable, "-c", "pass"))
    with pytest.raises(IntegrationError, match="verification"):
        changed.merge_epic_to_main(c, e.id, key="changed-command")
    (Path(e.worktree_path) / "extra.txt").write_text("extra\n")
    commit(Path(e.worktree_path), "changed epic")
    with pytest.raises(IntegrationError, match="approval"):
        s.merge_epic_to_main(c, e.id, key="changed-source")
    assert s.git.head("main") == e.base_commit


def test_failed_aggregate_tests_cannot_approve(setup):
    s, c, e = deliver_tasks(setup)
    bad = epic_service(s, command=(sys.executable, "-c", "raise SystemExit(7)"))
    verified = bad.verify_epic(c, e.id, key="bad")
    assert verified.status == "FAILED" and verified.result["exit_code"] == 7
    with pytest.raises(IntegrationError, match="test evidence"):
        bad.register_epic_review(c, e.id, verification_key="bad", key="bad-review", approved=True)
    assert s.git.head("main") == e.base_commit


def test_sync_conflict_is_preserved_without_main_changes(setup):
    s, c, e = deliver_tasks(setup)
    path, repo = Path(e.worktree_path), s.worktrees.settings.repository
    (path / "base.txt").write_text("epic\n")
    commit(path, "epic conflict")
    (repo / "base.txt").write_text("main\n")
    main = commit(repo, "main conflict")
    op = s.sync_epic_with_main(c, e.id, key="conflict")
    assert op.status == "CONFLICT" and op.error_code == "MERGE_CONFLICT"
    assert s.git.in_progress(path)
    assert "<<<<<<<" in (path / "base.txt").read_text()
    assert s.git.head("main") == main
    assert s.store.get_epic(e.id).status == E.ACTIVE
    with pytest.raises(IntegrationError, match="reconciliation"):
        s.sync_epic_with_main(c, e.id, key="conflict")


def test_manual_rejection_is_persistent_and_cannot_merge(setup):
    s, c, e = deliver_tasks(setup)
    s.verify_epic(c, e.id, key="verify")
    op = s.register_epic_review(
        c,
        e.id,
        verification_key="verify",
        key="reject",
        approved=False,
        feedback="missing acceptance evidence",
    )
    assert op.result["approved"] is False
    assert s.store.get_epic(e.id).status == E.CHANGES_REQUESTED
    with pytest.raises(IntegrationError, match="approval"):
        s.merge_epic_to_main(c, e.id, key="denied")
    with pytest.raises(IntegrationError, match="another decision"):
        s.register_epic_review(c, e.id, verification_key="verify", key="reject", approved=True)


def test_main_changed_after_merge_does_not_receive_stale_final_verification(setup):
    s, c, e = deliver_tasks(setup)
    epic_approve(s, c, e)
    op = s.merge_epic_to_main(c, e.id, key="main")
    repo = s.worktrees.settings.repository
    (repo / "later.txt").write_text("later\n")
    commit(repo, "later main")
    assert s.merge_epic_to_main(c, e.id, key="main") == op
    with pytest.raises(IntegrationError, match="exact actual merge"):
        s.verify_main_merge(c, e.id, merge_key="main", key="stale")
    assert s.store.get_epic(e.id).completed_at is None


def test_actual_test_mutation_invalidates_aggregate_evidence(setup):
    s, c, e = deliver_tasks(setup)
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; Path('base.txt').write_text('changed')",
    )
    changed = epic_service(s, command=command)
    result = changed.verify_epic(c, e.id, key="mutates")
    assert result.status == "FAILED" and result.error_code == "STALE_TEST_EVIDENCE"
    assert s.git.head("main") == e.base_commit


def test_pending_merge_with_unrelated_destination_change_requires_reconciliation(
    setup, monkeypatch
):
    s, c, e = deliver_tasks(setup)
    epic_approve(s, c, e)

    def crash(*args):
        raise RuntimeError("before Git")

    monkeypatch.setattr(s.git, "merge_commit", crash)
    with pytest.raises(RuntimeError):
        s.merge_epic_to_main(c, e.id, key="pending")
    repo = s.worktrees.settings.repository
    (repo / "unrelated.txt").write_text("manual\n")
    changed = commit(repo, "manual Git")
    with pytest.raises(IntegrationError, match="reconciliation"):
        s.merge_epic_to_main(c, e.id, key="pending")
    assert s.git.head("main") == changed
    assert s.store.get_epic(e.id).merge_commit is None


def test_parallel_main_delivery_from_separate_stores_creates_one_merge(setup):
    from concurrent.futures import ThreadPoolExecutor

    s, c, e = deliver_tasks(setup)
    epic_approve(s, c, e)

    def deliver():
        with StateStore(s.worktrees.settings.sqlite_path) as db:
            service = EpicIntegrationService(
                s.worktrees.settings, db, test_command=COMMAND, expected_task_ids=("F-01", "F-02")
            )
            return service.merge_epic_to_main(c, e.id, key="same")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: deliver(), range(2)))
    assert results[0] == results[1]
    assert (
        git(s.worktrees.settings.repository, "rev-list", "--first-parent", "--count", "main") == "2"
    )


def test_forged_epic_approval_without_registered_review_cannot_merge(setup):
    s, c, e = deliver_tasks(setup)
    source, target = s._epic_pair(s.store.get_epic(e.id))
    # Internal state metadata alone is not review/test proof.
    old = s.store.get_epic(e.id)
    from orchestrator.domain.models import EpicRun, TransitionEvent

    forged = EpicRun.model_validate(
        old.model_dump()
        | dict(
            status=E.APPROVED,
            current_commit=source,
            approved_source_commit=source,
            approved_target_commit=target,
        )
    )
    s.store.record_transition(
        forged,
        TransitionEvent(
            id="forged",
            project_id="p",
            epic_run_id=e.id,
            request={"corrupt_fixture": True},
            result={},
        ),
    )
    with pytest.raises(IntegrationError, match="approval"):
        s.merge_epic_to_main(c, e.id, key="forged")
    assert s.git.head("main") == target
