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
from orchestrator.mcp.contracts import (
    EpicStartRequest,
    PolicyRequest,
    Target,
    TaskApprovalRequest,
    TaskBlockReviewRequest,
    TaskChangesRequest,
    TaskGetNextRequest,
    TaskMergeRequest,
    TaskParkRequest,
    TaskResumeRequest,
    TaskReviewRequest,
    TaskStartRequest,
    ToolResponse,
)


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

    if service.epic_start is not None:
        tools.append(
            Tool(
                name="epic_start",
                description="Start the operator-configured epic from a Coordinator connection.",
                input_schema=EpicStartRequest.model_json_schema(),
                output_schema=ToolResponse.model_json_schema(),
                annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
            )
        )

    if service.task_scheduler is not None:
        tools.append(
            Tool(
                name="task_schedule",
                description="Run one bounded scheduling tick in registered Integration scope.",
                input_schema=TaskGetNextRequest.model_json_schema(),
                output_schema=ToolResponse.model_json_schema(),
                annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
            )
        )

    if service.task_selection is not None:
        tools.append(
            Tool(
                name="task_get_next",
                description="Read ordered candidates and blockers without reserving or starting.",
                input_schema=TaskGetNextRequest.model_json_schema(),
                output_schema=ToolResponse.model_json_schema(),
                annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
            )
        )

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

    if service.worker_reports is not None:
        for name in ("task_report_ready", "task_report_blocked"):
            tools.append(
                Tool(
                    name=name,
                    description="Verify the assigned Worker's native final report.",
                    input_schema=Target.model_json_schema(),
                    output_schema=ToolResponse.model_json_schema(),
                    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
                )
            )

    if service.task_review is not None:
        tools.append(
            Tool(
                name="task_review_request",
                description="Prepare verified review material in the registered Integration scope.",
                input_schema=TaskReviewRequest.model_json_schema(),
                output_schema=ToolResponse.model_json_schema(),
                annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
            )
        )

    if service.task_changes is not None:
        tools.append(
            Tool(
                name="task_request_changes",
                description="Record negative review and correct in the same Worker session.",
                input_schema=TaskChangesRequest.model_json_schema(),
                output_schema=ToolResponse.model_json_schema(),
                annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
            )
        )

    if service.task_approval is not None:
        tools.append(
            Tool(
                name="task_approve",
                description="Approve current context in the registered Integration scope.",
                input_schema=TaskApprovalRequest.model_json_schema(),
                output_schema=ToolResponse.model_json_schema(),
                annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
            )
        )

    if service.task_merge is not None:
        tools.append(
            Tool(
                name="task_merge",
                description="Verify delivery, integration tests and Worker stop before Done.",
                input_schema=TaskMergeRequest.model_json_schema(),
                output_schema=ToolResponse.model_json_schema(),
                annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
            )
        )

    if service.task_attention is not None:
        for name, model in (
            ("task_park_blocked", TaskParkRequest),
            ("task_block_review", TaskBlockReviewRequest),
        ):
            tools.append(
                Tool(
                    name=name,
                    description="Persist a verified blocker, mirror Attention and physically park.",
                    input_schema=model.model_json_schema(),
                    output_schema=ToolResponse.model_json_schema(),
                    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
                )
            )

    if service.task_resume is not None:
        for name in ("resume_task", "worker_resume"):
            tools.append(
                Tool(
                    name=name,
                    description="Save explicit input and resume its original Worker session.",
                    input_schema=TaskResumeRequest.model_json_schema(),
                    output_schema=ToolResponse.model_json_schema(),
                    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
                )
            )

    if service.teamplayer_sync is not None:
        for name in ("set_task_status", "set_epic_status"):
            tools.append(
                Tool(
                    name=name,
                    description="Mirror verified state; caller cannot choose status or evidence.",
                    input_schema=Target.model_json_schema(),
                    output_schema=ToolResponse.model_json_schema(),
                    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
                )
            )

    async def list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None):
        return ListToolsResult(tools=tools)

    async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams):
        result = (await service.call_async(params.name, params.arguments or {})).model_dump(
            mode="json"
        )
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
