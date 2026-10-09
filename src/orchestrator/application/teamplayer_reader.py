"""Validate a complete external project graph without changing runtime, Git or TeamPlayer."""

from pydantic import ValidationError

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError
from orchestrator.domain.teamplayer import (
    BoardEpic,
    BoardIssue,
    BoardSnapshot,
    BoardStatus,
    BoardTask,
    LocalBoardBinding,
    external_id,
)


class TeamPlayerReader:
    def __init__(self, adapter, *, project_id, user_id, project_name=None, bindings=None):
        try:
            self.project_id, self.user_id = external_id(project_id), external_id(user_id)
            self.bindings = dict(bindings or {})
            for key, value in self.bindings.items():
                external_id(key)
                if not isinstance(value, LocalBoardBinding):
                    raise ValueError
            if len({v.local_id for v in self.bindings.values()}) != len(self.bindings):
                raise ValueError
        except Exception:
            raise TeamPlayerError("TEAMPLAYER_BINDING_INVALID") from None
        self.adapter, self.project_name = adapter, project_name

    async def _identity(self):
        me = await self.adapter.read("get_me", {})
        projects = await self.adapter.read("list_projects", {})
        if not isinstance(me, dict) or me.get("userId") != self.user_id:
            raise TeamPlayerError("TEAMPLAYER_IDENTITY_MISMATCH")
        if not isinstance(projects, list):
            raise TeamPlayerError("TEAMPLAYER_PROJECTS_INVALID")
        matches = [
            p for p in projects if isinstance(p, dict) and p.get("projectId") == self.project_id
        ]
        if len(matches) != 1 or matches[0].get("access") not in {"Read", "Write"}:
            raise TeamPlayerError("TEAMPLAYER_PROJECT_UNAVAILABLE")
        if self.project_name is not None and matches[0].get("name") != self.project_name:
            raise TeamPlayerError("TEAMPLAYER_PROJECT_MISMATCH")

    async def _rows(self):
        args = {"projectId": self.project_id}
        epics = await self.adapter.read("list_epics", args)
        result = await self.adapter.read("list_tasks", args)
        if not isinstance(epics, list) or not isinstance(result, dict):
            raise TeamPlayerError("TEAMPLAYER_LIST_INVALID")
        if result.get("projectId") != self.project_id or not isinstance(result.get("tasks"), list):
            raise TeamPlayerError("TEAMPLAYER_LIST_SCOPE_MISMATCH")
        if any(
            result.get(k) not in (None, False, "")
            for k in ("nextCursor", "next_cursor", "hasMore", "continuationToken")
        ):
            raise TeamPlayerError("TEAMPLAYER_PAGINATION_UNSUPPORTED")
        if set(result) - {"projectId", "status", "responsibleAgentId", "tasks"}:
            raise TeamPlayerError("TEAMPLAYER_LIST_CONTRACT_CHANGED")
        if result.get("status") is not None or result.get("responsibleAgentId") is not None:
            raise TeamPlayerError("TEAMPLAYER_LIST_FILTERED")
        return epics, result["tasks"]

    def _parse(self, epic_rows, task_rows):
        try:
            epics = tuple(BoardEpic.model_validate(row) for row in epic_rows)
            tasks = tuple(BoardTask.model_validate(row) for row in task_rows)
        except (ValidationError, TypeError, ValueError):
            raise TeamPlayerError("TEAMPLAYER_BOARD_INVALID") from None
        if any(row.project_id != self.project_id for row in (*epics, *tasks)):
            raise TeamPlayerError("TEAMPLAYER_BOARD_SCOPE_MISMATCH")
        if len({e.id for e in epics}) != len(epics) or len({t.id for t in tasks}) != len(tasks):
            raise TeamPlayerError("TEAMPLAYER_DUPLICATE_ID")
        if {e.id for e in epics} & {t.id for t in tasks}:
            raise TeamPlayerError("TEAMPLAYER_ID_KIND_COLLISION")
        return (
            tuple(
                sorted(
                    (e.model_copy(update={"binding": self.bindings.get(e.id)}) for e in epics),
                    key=lambda e: e.id,
                )
            ),
            tuple(
                sorted(
                    (t.model_copy(update={"binding": self.bindings.get(t.id)}) for t in tasks),
                    key=lambda t: t.id,
                )
            ),
        )

    async def read_project(self, *, previous=None):
        if previous is not None and (
            previous.project_id != self.project_id or previous.authenticated_user_id != self.user_id
        ):
            raise TeamPlayerError("TEAMPLAYER_PREVIOUS_SCOPE_MISMATCH")
        await self._identity()
        # No atomic snapshot endpoint: two complete, equal readings, bounded retry.
        observed = set()
        for _ in range(3):
            first = self._parse(*(await self._rows()))
            observed.update(row.id for rows in first for row in rows)
            second = self._parse(*(await self._rows()))
            if first == second:
                break
        else:
            raise TeamPlayerError("TEAMPLAYER_SNAPSHOT_CHANGED")
        epics, tasks = second
        issues = self._issues(epics, tasks)
        known = set(self.bindings) | observed
        if previous:
            known.update(t.id for t in (*previous.tasks, *previous.epics))
        existing = {e.id for e in epics} | {t.id for t in tasks}
        issues.extend(
            BoardIssue(code="MISSING_BOUND_OR_PREVIOUS_ITEM", task_id=k)
            for k in sorted(known - existing)
        )
        return BoardSnapshot(
            project_id=self.project_id,
            authenticated_user_id=self.user_id,
            epics=epics,
            tasks=tasks,
            issues=tuple(issues),
        )

    def _issues(self, epics, tasks):
        index, epic_ids = {t.id: t for t in tasks}, {e.id for e in epics}
        epic_index = {e.id: e for e in epics}
        issues = []
        for task in tasks:

            def issue(code, related=None, task_id=task.id):
                issues.append(BoardIssue(code=code, task_id=task_id, related_id=related))

            if task.task_type == "Epic":
                issue("LEGACY_EPIC_NOT_EXECUTABLE")
            if task.epic_id is None or task.epic_id not in epic_ids:
                issue("TASK_EPIC_MISSING", task.epic_id)
            elif epic_index[task.epic_id].status == "Done" and task.status != BoardStatus.DONE:
                issue("UNFINISHED_TASK_IN_DONE_EPIC", task.epic_id)
            if not task.acceptance_criteria or any(
                not t.strip() or "\x00" in t for t in task.acceptance_criteria
            ):
                issue("TASK_ACCEPTANCE_MISSING")
            if (
                not task.name.strip()
                or not task.description.strip()
                or "\x00" in task.name
                or "\x00" in task.description
            ):
                issue("TASK_INSTRUCTION_MISSING")
            if task.execution_owner_kind != "User" or task.responsible_user_id != self.user_id:
                issue("TASK_EXECUTION_OWNER_MISMATCH")
            if task.status == BoardStatus.CANCELLED:
                issue("TASK_CANCELLED_NOT_VERIFIED_DONE")
            if len(set(task.dependencies)) != len(task.dependencies):
                issue("DUPLICATE_DEPENDENCY")
            for dependency in task.dependencies:
                if dependency not in index:
                    issue("DEPENDENCY_MISSING", dependency)
                elif index[dependency].task_type == "Epic":
                    issue("DEPENDENCY_NOT_EXECUTABLE", dependency)
                elif index[dependency].status != BoardStatus.DONE:
                    issue("DEPENDENCY_NOT_DONE", dependency)
        # Iterative reachability avoids recursion failure on a long valid graph.
        for task in tasks:
            seen, pending = set(), list(task.dependencies)
            while pending:
                node = pending.pop()
                if node == task.id:
                    issues.append(BoardIssue(code="DEPENDENCY_CYCLE", task_id=task.id))
                    break
                if node not in seen and node in index:
                    seen.add(node)
                    pending.extend(index[node].dependencies)
        invalid = {i.task_id for i in issues}
        # Invalidate descendants of malformed nodes, including externally Done nodes.
        while True:
            affected = [
                (t.id, d)
                for t in tasks
                if t.id not in invalid
                for d in t.dependencies
                if d in invalid
            ]
            if not affected:
                break
            for task_id, dependency in affected:
                issues.append(
                    BoardIssue(code="DEPENDENCY_INVALID", task_id=task_id, related_id=dependency)
                )
                invalid.add(task_id)
        return issues

    async def get_task(self, task_id):
        await self._identity()
        try:
            external_id(task_id)
            task = BoardTask.model_validate(
                await self.adapter.read(
                    "get_task", {"projectId": self.project_id, "taskId": task_id}
                )
            )
        except TeamPlayerError:
            raise
        except (ValidationError, TypeError, ValueError):
            raise TeamPlayerError("TEAMPLAYER_TASK_INVALID") from None
        if task.id != task_id or task.project_id != self.project_id:
            raise TeamPlayerError("TEAMPLAYER_TASK_SCOPE_MISMATCH")
        return task.model_copy(update={"binding": self.bindings.get(task.id)})
