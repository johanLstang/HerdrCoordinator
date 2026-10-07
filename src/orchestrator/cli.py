"""Foundation lifecycle; adapters and agent startup are delivered by later tasks."""

import argparse
import asyncio
import os
import signal
from pathlib import Path

from orchestrator.config import ConfigurationError, load_settings
from orchestrator.event_log import EventLog


async def serve(log: EventLog) -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = []
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stopped.set)
                installed.append(sig)
            except NotImplementedError:
                pass
        log.emit("service.ready", "INFO", "foundation service is ready; no agents started")
        await stopped.wait()
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        log.emit("service.stopped", "INFO", "service stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HerdrCoordinator local orchestrator")
    parser.add_argument("--config", type=Path, required=True, help="path to a TOML configuration")
    parser.add_argument("--check", action="store_true", help="validate configuration and exit")
    args = parser.parse_args(argv)
    log = EventLog()
    try:
        settings = load_settings(args.config)
    except ConfigurationError as exc:
        log.emit("configuration.validate", "ERROR", str(exc))
        return 2
    log.secrets = tuple(
        sorted(
            (os.environ[name] for name in settings.credential_env if os.environ.get(name)),
            key=len,
            reverse=True,
        )
    )
    log.emit("configuration.validate", "INFO", "configuration is valid", **settings.public_config())
    if args.check:
        return 0
    try:
        asyncio.run(serve(log))
    except KeyboardInterrupt:
        return 0
    return 0
