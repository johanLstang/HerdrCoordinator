"""Purpose-bound create transport; existing status/details writer stays unchanged."""

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError, TeamPlayerMCPAdapter


class TeamPlayerCorrectionCreator:
    def __init__(self, adapter, *, project_id, epic_id, user_id):
        if not isinstance(adapter, TeamPlayerMCPAdapter):
            raise TeamPlayerError("TEAMPLAYER_CREATE_TRANSPORT_UNVERIFIED")
        self.adapter = adapter
        self.project_id, self.epic_id, self.user_id = project_id, epic_id, user_id

    async def verify_capability(self):
        await self.adapter.verify_catalog()
        tool = self.adapter._catalog.get("create_task")
        if tool is None or not tool.annotations or tool.annotations.read_only_hint is not False:
            raise TeamPlayerError("TEAMPLAYER_CREATE_CAPABILITY_UNVERIFIED")
        schema = tool.input_schema
        request = schema.get("properties", {}).get("request", {})
        fields = {"projectId", "name", "description", "taskType", "priority",
                  "acceptanceCriteria", "executionOwnerKind", "validationOwnerKind"}
        if schema.get("type") != "object" or set(schema.get("required", [])) != {
            "request", "idempotencyKey"
        } or request.get("type") != "object" or set(request.get("required", [])) != fields:
            raise TeamPlayerError("TEAMPLAYER_CREATE_SCHEMA_CHANGED")
        if not {"epicId", "responsibleUserId", "validationUserId", "dependsOnTaskIds"} <= set(
            request.get("properties", {})
        ):
            raise TeamPlayerError("TEAMPLAYER_CREATE_SCHEMA_CHANGED")

    async def create(self, request, key):
        if not isinstance(key, str) or not key.strip() or len(key) > 128 or any(
            ch in key for ch in "\x00\r\n"
        ):
            raise TeamPlayerError("TEAMPLAYER_CREATE_KEY_INVALID")
        if request.get("projectId") != self.project_id or request.get("epicId") != self.epic_id or (
            request.get("executionOwnerKind") != "User"
            or request.get("responsibleUserId") != self.user_id
            or request.get("validationOwnerKind") != "User"
            or request.get("validationUserId") != self.user_id
            or request.get("taskType") != "Task"
            or any(request.get(name) is not None for name in (
                "responsibleAgentId", "validationAgentId",
            ))
        ):
            raise TeamPlayerError("TEAMPLAYER_CREATE_SCOPE_DENIED")
        me = await self.adapter.read("get_me", {})
        projects = await self.adapter.read("list_projects", {})
        matches = [p for p in projects if p.get("projectId") == self.project_id]
        if me.get("userId") != self.user_id or len(matches) != 1 or (
            matches[0].get("access") != "Write"
        ):
            raise TeamPlayerError("TEAMPLAYER_CREATE_IDENTITY_OR_WRITE_DENIED")
        await self.verify_capability()
        return await self.adapter._invoke(
            "create_task", {"request": request, "idempotencyKey": key}, writing=True
        )
