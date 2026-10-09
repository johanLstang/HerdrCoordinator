"""Operator argv isolation and immutable native host binding."""

import json
import sys
import tomllib

import pytest
from pydantic import ValidationError

from orchestrator.adapters.herdr import HerdrAdapter
from orchestrator.domain.codex_mcp_launch import CodexMCPLaunch


def test_launch_round_trips_literal_toml_and_does_not_change_worker_argv(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    launch = CodexMCPLaunch(
        command=sys.executable, args=('/operator/a "quoted" host.py', "/operator/config.json")
    )
    configs = launch.cli_args()
    decoded = tomllib.loads("\n".join(configs[1::2]))["mcp_servers"]["herdr_coordinator"]
    assert decoded["command"] == sys.executable and decoded["args"] == list(launch.args)
    assert decoded["env"] == {"HERDR_ENV": "1"}
    seen = []
    integration = HerdrAdapter("isolated", mcp=launch)
    worker = HerdrAdapter("isolated", sandbox="workspace-write")
    monkeypatch.setattr(integration, "call", lambda *a, **k: seen.append(a))
    monkeypatch.setattr(worker, "call", lambda *a, **k: seen.append(a))
    integration.start_agent("integration", "w1:p1", "/own/epic")
    worker.start_agent("worker", "w2:p1", "/own/task")
    assert list(seen[0][-len(configs) :]) == configs
    assert seen[1][-2:] == ("--cd", "/own/task")
    assert integration.mcp_fingerprint == launch.fingerprint and worker.mcp_fingerprint is None
    changed = CodexMCPLaunch(command=sys.executable, args=("/different.py",))
    integration.mcp = changed
    assert integration.mcp_fingerprint == changed.fingerprint != launch.fingerprint


@pytest.mark.parametrize(
    "data",
    [
        {"command": "relative/python", "args": ("host.py",)},
        {"command": "/no/such/executable", "args": ("host.py",)},
        {"command": sys.executable, "args": ("host.py\nunsafe",)},
        {"command": sys.executable, "args": ()},
        {"command": sys.executable, "args": ("host.py",), "role": "Coordinator"},
    ],
)
def test_invalid_operator_configuration_rejected(data):
    with pytest.raises(ValidationError):
        CodexMCPLaunch.model_validate(data)


def test_model_has_no_credentials_env_or_role_schema_and_is_frozen():
    launch = CodexMCPLaunch(command=sys.executable, args=("host.py",))
    assert set(CodexMCPLaunch.model_fields) == {"command", "args"}
    with pytest.raises(ValidationError):
        launch.args = ("replacement.py",)
    assert json.loads(launch.model_dump_json())["args"] == ["host.py"]
