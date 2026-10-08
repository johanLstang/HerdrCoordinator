import json

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)

from orchestrator.application.runtime_service import RuntimeService
from orchestrator.mcp.contracts import PolicyRequest, Target, TaskStartRequest, ToolResponse


def create_server(service: RuntimeService) -> Server:
    tools = [
        Tool(
            name=name,
            description=description,
            input_schema=model.model_json_schema(),
            output_schema=ToolResponse.model_json_schema(),
            annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
        )
        for name, description, model in [
            ("runtime_status", "Read runtime within the connection's registered scope.", Target),
            (
                "policy_check",
                "Check role, scope and operation availability without changing state.",
                PolicyRequest,
            ),
        ]
    ]

    if service.task_start is not None:
        tools.append(
            Tool(
                name="task_start",
                description="Start one explicit task using the registered Integration scope.",
                input_schema=TaskStartRequest.model_json_schema(),
                output_schema=ToolResponse.model_json_schema(),
                annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
            )
        )

    async def list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None):
        return ListToolsResult(tools=tools)

    async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams):
        result = service.call(params.name, params.arguments or {}).model_dump(mode="json")
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result))],
            structured_content=result,
            is_error=not result["ok"],
        )

    return Server(
        "HerdrCoordinator", version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool
    )


async def serve_stdio(service: RuntimeService) -> None:
    server = create_server(service)
    service.log.emit("mcp.ready", "INFO", "local stdio MCP server is ready")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
