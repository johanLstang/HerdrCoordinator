"""Operator-only stdio configuration; no tool schema, env values or authority claims."""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from orchestrator.domain.worker_contracts import Contract, Text, canonical_json


class CodexMCPLaunch(BaseModel):
    model_config = Contract.model_config
    command: Text
    args: Annotated[tuple[Text, ...], Field(min_length=1, max_length=64)]
    disabled_servers: Annotated[tuple[Text, ...], Field(max_length=128)] = ()
    disabled_plugins: Annotated[tuple[Text, ...], Field(max_length=128)] = ()
    auto_approved_tools: Annotated[tuple[Text, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def operator_argv(self):
        if (
            not Path(self.command).is_absolute()
            or not os.access(self.command, os.X_OK)
            or any(
                len(v) > 4096 or any(c in v for c in "\x00\r\n") for v in (self.command, *self.args)
            )
            or any(
                not re.fullmatch(r"[A-Za-z0-9_@-]{1,128}", name)
                for name in (*self.disabled_servers, *self.disabled_plugins)
            )
            or len(set(self.disabled_servers)) != len(self.disabled_servers)
            or len(set(self.disabled_plugins)) != len(self.disabled_plugins)
            or "herdr_coordinator" in self.disabled_servers
            or len(set(self.auto_approved_tools)) != len(self.auto_approved_tools)
            or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", name)
                   for name in self.auto_approved_tools)
        ):
            raise ValueError("explicit executable and bounded operator argv required")
        return self

    @property
    def fingerprint(self):
        return hashlib.sha256(canonical_json(self.cli_args()).encode()).hexdigest()

    def cli_args(self):
        args = [
            "-c",
            "mcp_servers.herdr_coordinator.command=" + json.dumps(self.command),
            "-c",
            "mcp_servers.herdr_coordinator.args=" + json.dumps(self.args),
            "-c",
            'mcp_servers.herdr_coordinator.env.HERDR_ENV="1"',
            "-c",
            "mcp_servers.herdr_coordinator.startup_timeout_sec=120",
            "-c",
            "mcp_servers.herdr_coordinator.tool_timeout_sec=120",
        ]
        if self.disabled_servers or self.disabled_plugins:
            args += ["-c", "mcp_servers.herdr_coordinator.enabled=true"]
        for server in self.disabled_servers:
            args += ["-c", "mcp_servers." + server + ".enabled=false"]
        for plugin in self.disabled_plugins:
            args += ["-c", "plugins." + plugin + ".enabled=false"]
        for tool in self.auto_approved_tools:
            args += ["-c", "mcp_servers.herdr_coordinator.tools." + tool
                     + '.approval_mode="auto"']
        return args
