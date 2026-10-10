"""Operator-only stdio configuration; no tool schema, env values or authority claims."""

import hashlib
import json
import os
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from orchestrator.domain.worker_contracts import Contract, Text, canonical_json


class CodexMCPLaunch(BaseModel):
    model_config = Contract.model_config
    command: Text
    args: Annotated[tuple[Text, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def operator_argv(self):
        if (
            not Path(self.command).is_absolute()
            or not os.access(self.command, os.X_OK)
            or any(
                len(v) > 4096 or any(c in v for c in "\x00\r\n") for v in (self.command, *self.args)
            )
        ):
            raise ValueError("explicit executable and bounded operator argv required")
        return self

    @property
    def fingerprint(self):
        return hashlib.sha256(canonical_json(self.cli_args()).encode()).hexdigest()

    def cli_args(self):
        return [
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
