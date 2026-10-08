"""Read existing product evidence; never start, resume, stop, test or merge to satisfy sync."""

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.processes import ProcessObserver
from orchestrator.application.epic_integration_service import EpicIntegrationService
from orchestrator.application.runtime_assignment_service import digest
from orchestrator.application.task_delivery_evidence import known_merge, passed_test
from orchestrator.application.worktree_service import WorktreeService
from orchestrator.domain.states import EpicState, TaskState
from orchestrator.domain.worker_contracts import LocalTaskSpec, canonical_json


class SyncEvidenceError(RuntimeError):
    pass


def require(value):
    if not value:
        raise SyncEvidenceError("TEAMPLAYER_SOURCE_UNVERIFIED")


class TeamPlayerEvidence:
    def __init__(self, settings, store, *, codex=None, processes=None, scopes=None):
        self.settings, self.store = settings, store
        self.codex, self.processes = codex or CodexAdapter(), processes or ProcessObserver()
        self.worktrees = WorktreeService(settings, store)
        self.scopes = dict(scopes or {})

    def operations(self, run, kind):
        return [
            o
            for o in self.store.get_operations(run.epic_run_id, kind=kind)
            if o.task_run_id == run.id
        ]

    def spec(self, task):
        op = self.store.get_operation(task.project_id, "task_start", task.task_id)
        require(op and op.task_run_id == task.id and op.epic_run_id == task.epic_run_id)
        spec = LocalTaskSpec.model_validate(op.result["spec"])
        require(
            spec.task_id == task.task_id
            and spec.project_id == task.project_id
            and digest(canonical_json(spec.model_dump(mode="json"))) == op.result["spec_hash"]
        )
        spec.bind(self.store.get_epic(task.epic_run_id))
        return spec

    def _start(self, task):
        op = self.store.get_operation(task.project_id, "start_runtime", task.id)
        require(
            op
            and op.status == "SUCCEEDED"
            and op.task_run_id == task.id
            and op.epic_run_id == task.epic_run_id
            and op.result["stage"] == "READY"
            and op.result["branch"] == task.branch
            and op.result["cwd"] == task.worktree_path
            and op.result["name"] == task.worker_agent_id
            and op.result["server_session"] == task.herdr_server_session
            and all(getattr(task, "herdr_" + k) == v for k, v in op.result["binding"].items())
        )
        return op

    def _ack(self, task):
        start = self._start(task)
        parent = self.store.get_operation(task.project_id, "task_start", task.task_id)
        dispatch = self.store.get_operation(task.project_id, "dispatch_assignment", task.id)
        require(
            parent
            and parent.status == "SUCCEEDED"
            and parent.task_run_id == task.id
            and dispatch
            and dispatch.status == "SUCCEEDED"
            and dispatch.task_run_id == task.id
            and dispatch.result["start_operation_id"] == start.id
        )
        self._native_ack(task, dispatch)
        return {
            "start_id": start.id,
            "assignment_id": dispatch.id,
            "session_id": task.codex_session_id,
        }

    def _native_ack(self, task, dispatch):
        ack = dispatch.result["ack"]
        require(
            ack["session_id"] == task.codex_session_id
            and ack["correlation_id"] == dispatch.result["correlation_id"]
        )
        thread = self.codex.read_thread(task.codex_session_id, task.worktree_path)
        require(thread["id"] == task.codex_session_id)
        turns = [t for t in thread["turns"] if t["id"] == ack["turn_id"]]
        require(len(turns) == 1)
        items = turns[0]["items"]
        require(
            sum(
                i.get("id") == ack["item_id"]
                and i.get("type") == "agentMessage"
                and digest(i.get("text", "")) == ack["message_hash"]
                for i in items
            )
            == 1
        )
        require(
            any(
                i.get("type") == "userMessage"
                and any(
                    p.get("type") == "text"
                    and digest(p.get("text", "")) == dispatch.result["prompt_hash"]
                    for p in i.get("content", [])
                )
                for i in items
            )
        )

    def _stop(self, task, *, stop_id=None):
        start = self._start(task)
        matches = [
            o
            for o in self.operations(task, "stop_runtime")
            if o.status == "SUCCEEDED"
            and (stop_id is None or o.id == stop_id)
            and o.result.get("generation") == start.result.get("generation", start.id)
        ]
        require(len(matches) == 1 and task.worker_slot is None)
        stop = matches[0]
        require(
            stop.result.get("stage") == "STOPPED"
            and stop.result.get("inactive") is True
            and self.processes.inactive(stop.result["process_proof"])
        )
        return stop

    def task(self, task):
        self.spec(task)
        event = self.store.latest_event(task.project_id, task.epic_run_id, task.id)
        require(
            event
            and event.result["internal_status"] == task.internal_status
            and event.task_run_id == task.id
            and event.result["task_id"] == task.task_id
            and event.project_id == task.project_id
            and event.epic_run_id == task.epic_run_id
            and event.result["project_id"] == task.project_id
            and event.result["epic_run_id"] == task.epic_run_id
        )
        proof = {
            "event_id": event.id,
            "event_hash": digest(canonical_json(event.model_dump(mode="json"))),
        }
        phase = task.internal_status
        reason = ""
        if phase == TaskState.DONE:
            parents = [
                o
                for o in self.operations(task, "task_merge")
                if o.status == "SUCCEEDED"
                and o.result.get("stage") == "DONE"
                and o.result.get("merge_commit") == task.merge_commit
            ]
            require(len(parents) == 1 and task.completed_at is not None)
            parent = parents[0]
            require(event.id == "delivery-done:" + parent.id)
            sha = known_merge(self.settings, self.store, task, parent, require_tip=False)
            test = passed_test(self.store, task, parent, sha)
            stop = self._stop(task, stop_id=parent.result["stop_id"])
            proof.update(merge_commit=sha, test_id=test.id, stop_id=stop.id, delivery_id=parent.id)
            status = "Done"
            reason = (
                f"Verified Task→Epic merge {sha}; test {test.id} exit0; physical stop {stop.id}."
            )
        else:
            self.worktrees.verify_owned_worktree(task)
            if phase in {TaskState.BLOCKED, TaskState.PARKED}:
                events = self.store.events(task.project_id, task.epic_run_id, task.id)
                blocked = [e for e in events if e.request.get("target") == "BLOCKED"]
                require(bool(blocked))
                block = blocked[-1]
                reason = block.request.get("facts", {}).get("reason", "")
                require(isinstance(reason, str) and reason.strip())
                sources = [
                    o
                    for kind in ("stop_runtime", "worker_report")
                    for o in self.operations(task, kind)
                    if block.id in {"runtime-block:" + o.id, o.result.get("event_id")}
                ]
                require(len(sources) == 1)
                source = sources[0]
                if source.kind == "worker_report":
                    require(source.status == "SUCCEEDED" and source.result["stage"] == "BLOCKED")
                    report = source.result["report"]
                    require(
                        report["status"] == "BLOCKED"
                        and report["task_run_id"] == task.id
                        and report["branch"] == task.branch
                        and report["project_id"] == task.project_id
                        and report["reason"] + "\nInput: " + report["input_required"] == reason
                    )
                    proof.update(self._ack(task))
                    provenance = source.result["provenance"]
                    require(provenance["session_id"] == task.codex_session_id)
                    thread = self.codex.read_thread(task.codex_session_id, task.worktree_path)
                    matches = [
                        i
                        for t in thread["turns"]
                        if t["id"] == provenance["turn_id"] and t["status"] == "completed"
                        for i in t["items"]
                        if i.get("id") == provenance["item_id"]
                        and i.get("type") == "agentMessage"
                        and digest(i.get("text", "")) == provenance["message_hash"]
                    ]
                    require(thread["id"] == task.codex_session_id and len(matches) == 1)
                else:
                    require(
                        source.result["reason"] == reason
                        and source.result["generation"]
                        == self._start(task).result.get("generation", self._start(task).id)
                    )
                if phase == TaskState.PARKED:
                    stop = self._stop(task)
                    require(event.id == "runtime-park:" + stop.id)
                    proof["stop_id"] = stop.id
                proof["block_source_id"] = source.id
                status = "NeedsInput"
            elif phase in {TaskState.PLANNED, TaskState.CLAIMED, TaskState.STARTING}:
                require(task.completed_at is None)
                status = "Pending"
            else:
                proof.update(self._ack(task))
                if event.id.startswith("runtime-resume:"):
                    resumes = [
                        o
                        for o in self.operations(task, "resume_runtime")
                        if event.id == "runtime-resume:" + o.id
                    ]
                    require(
                        len(resumes) == 1
                        and resumes[0].status == "SUCCEEDED"
                        and resumes[0].result["session_id"] == task.codex_session_id
                        and self._start(task).result.get("generation") == resumes[0].id
                    )
                    proof["resume_id"] = resumes[0].id
                if event.id.startswith("correction-ack:"):
                    corrections = [
                        o
                        for o in self.operations(task, "task_request_changes")
                        if event.id == "correction-ack:" + o.id
                    ]
                    require(len(corrections) == 1 and corrections[0].status == "SUCCEEDED")
                    self._native_ack(task, corrections[0])
                    proof["correction_id"] = corrections[0].id
                status = "InProgress"
        return status, proof, reason

    def epic(self, actor, epic):
        self.worktrees.verify_owned_worktree(epic)
        event = self.store.latest_event(epic.project_id, epic.id)
        if epic.status == EpicState.PLANNED:
            creation = self.store.get_operation(epic.project_id, "create_epic_worktree", epic.id)
            require(creation and creation.status == "SUCCEEDED" and event is None)
            return (
                "Pending",
                {
                    "event_id": "epic-planned:" + creation.id,
                    "event_hash": digest(canonical_json(creation.model_dump(mode="json"))),
                },
                "",
            )
        require(
            event
            and event.result["status"] == epic.status
            and event.project_id == epic.project_id
            and event.epic_run_id == epic.id
            and event.result["project_id"] == epic.project_id
            and event.result["id"] == epic.id
        )
        proof = {
            "event_id": event.id,
            "event_hash": digest(canonical_json(event.model_dump(mode="json"))),
        }
        if epic.status != EpicState.DONE:
            return (
                "InProgress",
                proof,
                "Epic started; remains Active through review, blockers and main integration.",
            )
        expected = self.scopes.get(epic.id)
        require(
            expected
            and tuple(sorted(t.task_id for t in self.store.get_tasks(epic.id)))
            == tuple(sorted(expected))
        )
        for task in self.store.get_tasks(epic.id):
            require(task.internal_status == TaskState.DONE)
            self.task(task)
        manager = EpicIntegrationService(
            self.settings, self.store, expected_task_ids=tuple(expected)
        )
        manifest = manager._manifest(epic)
        merges = [
            o
            for o in self.store.get_operations(epic.id, kind="merge_epic_to_main")
            if o.status == "SUCCEEDED" and o.result.get("merge_commit") == epic.merge_commit
        ]
        require(len(merges) == 1 and epic.completed_at is not None)
        merge = merges[0]
        source, target, sha = (
            merge.result["source_commit"],
            merge.result["target_commit"],
            epic.merge_commit,
        )
        require(
            merge.result.get("requires_reconciliation") is False
            and manager.git.parents(sha) == (target, source)
            and manager.git.find_operation_merge("main", merge.id, target, source) == sha
            and manager.git.contains_commit("main", sha)
            and manager.git.head(epic.branch) == source
            and (epic.approved_source_commit, epic.approved_target_commit) == (source, target)
            and merge.result["manifest_hash"] == manager._manifest_hash(manifest)
        )
        reviews = self.store.get_operations(epic.id, kind="epic_review")
        require(bool(reviews))
        review = reviews[-1]
        require(
            self.settings.review_context is not None
            and review.result.get("verified_criteria")
            == self.settings.review_context.acceptance_criteria
        )
        verification = self.store.get_operation(
            epic.project_id, "verify_epic", review.result["verification_key"]
        )
        require(
            review.status == "SUCCEEDED"
            and review.result["approved"] is True
            and review.result["manifest_hash"] == manager._manifest_hash(manifest)
            and (review.result["source_commit"], review.result["target_commit"]) == (source, target)
            and verification
            and verification.status == "SUCCEEDED"
            and verification.result["exit_code"] == 0
            and verification.result["manifest"] == manifest
            and (verification.result["source_commit"], verification.result["target_commit"])
            == (source, target)
        )
        final = [
            o
            for o in self.store.get_operations(epic.id, kind="verify_main_merge")
            if o.status == "SUCCEEDED"
            and o.result.get("exit_code") == 0
            and o.result.get("merge_key") == merge.idempotency_key
            and o.result.get("merge_commit") == sha
            and o.result.get("source_commit") == source
            and o.result.get("target_commit") == sha
            and o.result.get("manifest_hash") == manager._manifest_hash(manifest)
            and o.result.get("command_hash") == verification.result.get("command_hash")
        ]
        require(len(final) == 1)
        proof.update(
            merge_commit=sha, merge_id=merge.id, review_id=review.id, final_test_id=final[0].id
        )
        return (
            "Done",
            proof,
            f"Verified Epic→main merge {sha}; aggregate review {review.id}; "
            f"final test {final[0].id} exit0.",
        )
