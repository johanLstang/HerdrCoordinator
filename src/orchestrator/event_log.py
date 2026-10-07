"""JSON events on stderr, with correlation IDs and configured secret redaction."""

import json
import sys
from datetime import UTC, datetime
from typing import TextIO
from uuid import uuid4


class EventLog:
    def __init__(self, secrets: tuple[str, ...] = (), stream: TextIO | None = None):
        self.correlation_id = str(uuid4())
        self.secrets = tuple(sorted((s for s in secrets if s), key=len, reverse=True))
        self.stream = stream if stream is not None else sys.stderr

    def _redact(self, value: object) -> object:
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {str(self._redact(k)): self._redact(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(v) for v in value]
        return value

    def emit(self, operation: str, level: str, message: str, **fields: object) -> None:
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "correlation_id": self.correlation_id,
            "operation": operation,
            "level": level,
            "message": message,
            "data": fields,
        }
        print(json.dumps(self._redact(event), ensure_ascii=False), file=self.stream, flush=True)
