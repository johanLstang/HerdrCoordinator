import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent
from test_teamplayer_reader import SECRET, native_tools

from orchestrator.adapters.teamplayer_mcp import TeamPlayerError, TeamPlayerMCPWriter


def writer():
    client = Mock()
    client.list_tools = AsyncMock(return_value=ListToolsResult(tools=native_tools()))
    client.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text='{"success":true,"data":{"version":2}}')]
        )
    )
    adapter = TeamPlayerMCPWriter(client, private_values=("Bearer " + SECRET,))
    asyncio.run(adapter.verify_catalog())
    adapter.verify_write_catalog()
    return adapter, client


@pytest.mark.parametrize(
    "tool", ["update_task_status", "update_task_details", "update_epic_status"]
)
def test_verified_native_write_catalog_allows_only_three_mutations(tool):
    adapter, client = writer()
    assert asyncio.run(adapter.write(tool, {"native": "args"})) == {"version": 2}
    client.call_tool.assert_awaited_once_with(tool, {"native": "args"})


@pytest.mark.parametrize(
    "tool", ["create_task", "set_task_epic", "update_epic_details", "delete_task", "get_task"]
)
def test_unrelated_mutations_and_reads_cannot_pass_write_boundary(tool):
    adapter, client = writer()
    with pytest.raises(TeamPlayerError, match="OPERATION_DENIED"):
        asyncio.run(adapter.write(tool, {}))
    client.call_tool.assert_not_called()


@pytest.mark.parametrize("attack", ["missing", "readonly", "required", "nested"])
def test_changed_write_catalog_is_fail_closed(attack):
    adapter, client = writer()
    tool = adapter._catalog["update_task_status"]
    if attack == "missing":
        del adapter._catalog["update_task_status"]
    elif attack == "readonly":
        tool.annotations.read_only_hint = True
    elif attack == "required":
        tool.input_schema["required"] = ["arbitrary"]
    else:
        tool.input_schema["properties"]["request"]["required"] = ["role", "taskId"]
    with pytest.raises(TeamPlayerError, match="UNVERIFIED|CHANGED"):
        asyncio.run(adapter.write("update_task_status", {}))
    client.call_tool.assert_not_called()


@pytest.mark.parametrize(
    "payload,code,version",
    [
        (
            '{"success":false,"code":"version_conflict","currentVersion":7,"message":"SECRET"}',
            "TEAMPLAYER_VERSION_CONFLICT",
            7,
        ),
        (
            '{"success":false,"error":{"errorCode":"permission_denied","message":"SECRET"}}',
            "TEAMPLAYER_PERMISSION_DENIED",
            None,
        ),
        (
            '{"success":false,"code":"approval_rejected","message":"SECRET"}',
            "TEAMPLAYER_APPROVAL_REJECTED",
            None,
        ),
        (
            '{"success":false,"code":"server_error","message":"SECRET"}',
            "TEAMPLAYER_WRITE_REJECTED",
            None,
        ),
    ],
)
def test_native_rejections_are_safe_and_preserve_only_version_fact(payload, code, version):
    adapter, client = writer()
    client.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=payload.replace("SECRET", SECRET))]
    )
    with pytest.raises(TeamPlayerError) as error:
        asyncio.run(adapter.write("update_task_status", {}))
    assert str(error.value) == code and error.value.current_version == version
    assert SECRET not in repr(error.value)


def test_write_transport_error_is_unknown_and_ephemeral_credentials_are_redacted(capsys):
    adapter, client = writer()
    client.call_tool.side_effect = TimeoutError(SECRET)
    with pytest.raises(TeamPlayerError, match="WRITE_OUTCOME_UNKNOWN"):
        asyncio.run(adapter.write("update_task_status", {}))
    assert SECRET not in str(adapter) and SECRET not in adapter.redact_text("reason " + SECRET)
    assert adapter.redact_text("Bearer " + SECRET) == "[REDACTED]"
    assert not any(capsys.readouterr())
