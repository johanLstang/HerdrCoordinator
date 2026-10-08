"""Local lifecycle and explicit operator-controlled MCP/runtime configuration."""

import argparse
import asyncio
import os
import signal
from pathlib import Path

from orchestrator.config import ConfigurationError, load_settings
from orchestrator.event_log import EventLog
from orchestrator.persistence.store import SCHEMA_VERSION, StateStore, StoreError


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
    parser.add_argument("--mcp", action="store_true", help="serve local MCP over stdio")
    parser.add_argument(
        "--principal", type=Path, help="operator-controlled external principal JSON"
    )
    parser.add_argument(
        "--herdr-session", help="explicit operator-selected Herdr session for task_start"
    )
    parser.add_argument(
        "--worker-sandbox", choices=["read-only", "workspace-write"], default="workspace-write"
    )
    args = parser.parse_args(argv)
    if args.herdr_session and not args.mcp:
        parser.error("--herdr-session requires --mcp")
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
    from orchestrator.mcp.principal import PrincipalError, load_principal

    try:
        actor = load_principal(args.principal, settings) if args.mcp else None
    except PrincipalError as exc:
        log.emit("principal.validate", "ERROR", str(exc))
        return 4
    try:
        with StateStore(settings.sqlite_path) as store:
            log.emit("state.initialize", "INFO", "state database is ready", version=SCHEMA_VERSION)
            if args.mcp:
                from orchestrator.application.runtime_service import RuntimeService
                from orchestrator.mcp.server import serve_stdio

                task_start = None
                worker_reports = None
                task_review = None
                task_changes = None
                if settings.review_context is not None and settings.worker_test_command:
                    from orchestrator.application.task_review_service import TaskReviewService

                    task_review = TaskReviewService(settings, store)
                if args.herdr_session:
                    from orchestrator.adapters.herdr import HerdrAdapter, HerdrError
                    from orchestrator.application.task_start_service import TaskStartService

                    try:
                        herdr = HerdrAdapter(args.herdr_session, sandbox=args.worker_sandbox)
                    except HerdrError:
                        log.emit(
                            "runtime.configure", "ERROR", "explicit Herdr environment unavailable"
                        )
                        return 5
                    task_start = TaskStartService(settings, store, herdr)
                    from orchestrator.application.worker_report_service import WorkerReportService

                    worker_reports = WorkerReportService(settings, store, herdr)
                    if task_review is not None:
                        from orchestrator.application.task_changes_service import TaskChangesService

                        task_changes = TaskChangesService(settings, store, herdr)
                asyncio.run(
                    serve_stdio(
                        RuntimeService(
                            store,
                            actor,
                            log,
                            task_start=task_start,
                            worker_reports=worker_reports,
                            task_review=task_review,
                            task_changes=task_changes,
                        )
                    )
                )
            else:
                asyncio.run(serve(log))
    except StoreError as exc:
        log.emit("state.initialize", "ERROR", str(exc))
        return 3
    except KeyboardInterrupt:
        return 0
    return 0
