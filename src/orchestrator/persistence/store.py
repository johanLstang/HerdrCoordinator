import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from orchestrator.domain.models import (
    EpicRun,
    ExternalReference,
    Operation,
    Review,
    TaskRun,
    TransitionEvent,
)

SCHEMA_VERSION = 2
_EVENT_SCHEMA = """CREATE TABLE transition_events (
    project_id TEXT NOT NULL, event_id TEXT NOT NULL, epic_run_id TEXT NOT NULL,
    task_run_id TEXT, payload TEXT NOT NULL, PRIMARY KEY(project_id, event_id),
    FOREIGN KEY(epic_run_id, project_id) REFERENCES epic_runs(id, project_id),
    FOREIGN KEY(task_run_id, epic_run_id, project_id)
        REFERENCES task_runs(id, epic_run_id, project_id))"""
_SCHEMA = (
    """CREATE TABLE epic_runs (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, epic_id TEXT NOT NULL,
        status TEXT NOT NULL, completed_at TEXT, payload TEXT NOT NULL,
        UNIQUE(id, project_id))""",
    """CREATE TABLE task_runs (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, epic_run_id TEXT NOT NULL,
        task_id TEXT NOT NULL, internal_status TEXT NOT NULL, completed_at TEXT,
        payload TEXT NOT NULL, UNIQUE(id, epic_run_id, project_id),
        FOREIGN KEY(epic_run_id, project_id) REFERENCES epic_runs(id, project_id))""",
    """CREATE UNIQUE INDEX task_owner ON task_runs(project_id, task_id)
        WHERE completed_at IS NULL""",
    """CREATE TABLE reviews (
        id TEXT PRIMARY KEY, task_run_id TEXT NOT NULL REFERENCES task_runs(id),
        review_number INTEGER NOT NULL CHECK(review_number > 0), payload TEXT NOT NULL,
        UNIQUE(task_run_id, review_number))""",
    """CREATE TABLE operations (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, epic_run_id TEXT NOT NULL,
        task_run_id TEXT, kind TEXT NOT NULL, idempotency_key TEXT NOT NULL,
        payload TEXT NOT NULL, UNIQUE(project_id, kind, idempotency_key),
        FOREIGN KEY(epic_run_id, project_id) REFERENCES epic_runs(id, project_id),
        FOREIGN KEY(task_run_id, epic_run_id, project_id)
            REFERENCES task_runs(id, epic_run_id, project_id))""",
    """CREATE TABLE external_references (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, epic_run_id TEXT NOT NULL,
        task_run_id TEXT, provider TEXT NOT NULL, kind TEXT NOT NULL,
        external_id TEXT NOT NULL, payload TEXT NOT NULL,
        UNIQUE(project_id, provider, kind, external_id),
        FOREIGN KEY(epic_run_id, project_id) REFERENCES epic_runs(id, project_id),
        FOREIGN KEY(task_run_id, epic_run_id, project_id)
            REFERENCES task_runs(id, epic_run_id, project_id))""",
    """CREATE UNIQUE INDEX reference_owner ON external_references
        (epic_run_id, coalesce(task_run_id, ''), provider, kind)""",
)


class StoreError(RuntimeError):
    """Safe storage error without payloads, paths, or credentials."""


class StateStore:
    """One connection per service thread; explicit transactions and relational ownership."""

    def __init__(self, path: Path):
        self.connection: sqlite3.Connection | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(
                path,
                timeout=5,
                isolation_level=None,
                autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL,
            )
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.initialize()
        except (OSError, sqlite3.Error, StoreError) as exc:
            self.close()
            if isinstance(exc, StoreError):
                raise
            raise StoreError("state database cannot be opened or initialized") from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    @property
    def db(self) -> sqlite3.Connection:
        if self.connection is None:
            raise StoreError("state database is closed")
        return self.connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = self.db
        nested = db.in_transaction
        savepoint = "save_" + uuid4().hex
        try:
            db.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
            yield db
            db.execute(f"RELEASE {savepoint}" if nested else "COMMIT")
        except BaseException as exc:
            if db.in_transaction:
                if nested:
                    db.execute(f"ROLLBACK TO {savepoint}")
                    db.execute(f"RELEASE {savepoint}")
                else:
                    db.execute("ROLLBACK")
            if isinstance(exc, sqlite3.IntegrityError):
                raise StoreError(
                    "state violates identity, relation or uniqueness constraints"
                ) from None
            if isinstance(exc, sqlite3.Error):
                raise StoreError("state database operation failed") from None
            raise

    def initialize(self) -> None:
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version in {1, SCHEMA_VERSION}:
                required = {
                    "epic_runs",
                    "task_runs",
                    "reviews",
                    "operations",
                    "external_references",
                    "task_owner",
                    "reference_owner",
                }
                actual = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
                if not required <= actual:
                    raise StoreError("state database schema is incomplete")
                if version == 1:
                    db.execute(_EVENT_SCHEMA)
                    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif "transition_events" not in actual:
                    raise StoreError("state database schema is incomplete")
                return
            if version != 0:
                raise StoreError("unsupported state database schema version")
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone():
                raise StoreError("unversioned database already contains tables")
            for statement in _SCHEMA:
                db.execute(statement)
            db.execute(_EVENT_SCHEMA)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def add_epic(self, record: EpicRun) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO epic_runs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.project_id,
                    record.epic_id,
                    record.status,
                    record.completed_at.isoformat() if record.completed_at else None,
                    record.model_dump_json(),
                ),
            )

    def add_task(self, record: TaskRun) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO task_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.project_id,
                    record.epic_run_id,
                    record.task_id,
                    record.internal_status,
                    record.completed_at.isoformat() if record.completed_at else None,
                    record.model_dump_json(),
                ),
            )

    def add_review(self, record: Review) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO reviews VALUES (?, ?, ?, ?)",
                (
                    record.id,
                    record.task_run_id,
                    record.review_number,
                    record.model_dump_json(),
                ),
            )

    def get_epic(self, run_id: str) -> EpicRun | None:
        row = self.db.execute("SELECT payload FROM epic_runs WHERE id = ?", (run_id,)).fetchone()
        return EpicRun.model_validate_json(row[0]) if row else None

    def get_task(self, run_id: str) -> TaskRun | None:
        row = self.db.execute("SELECT payload FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        return TaskRun.model_validate_json(row[0]) if row else None

    def get_reviews(self, task_run_id: str) -> list[Review]:
        return [
            Review.model_validate_json(row[0])
            for row in self.db.execute(
                "SELECT payload FROM reviews WHERE task_run_id = ? ORDER BY review_number",
                (task_run_id,),
            )
        ]

    def add_operation(self, record: Operation) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.project_id,
                    record.epic_run_id,
                    record.task_run_id,
                    record.kind,
                    record.idempotency_key,
                    record.model_dump_json(),
                ),
            )

    def get_operation(self, project_id: str, kind: str, key: str) -> Operation | None:
        row = self.db.execute(
            "SELECT payload FROM operations "
            "WHERE project_id = ? AND kind = ? AND idempotency_key = ?",
            (project_id, kind, key),
        ).fetchone()
        return Operation.model_validate_json(row[0]) if row else None

    def add_reference(self, record: ExternalReference) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO external_references VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.project_id,
                    record.epic_run_id,
                    record.task_run_id,
                    record.provider,
                    record.kind,
                    record.external_id,
                    record.model_dump_json(),
                ),
            )

    def get_tasks(self, epic_run_id: str) -> list[TaskRun]:
        return [
            TaskRun.model_validate_json(row[0])
            for row in self.db.execute(
                "SELECT payload FROM task_runs WHERE epic_run_id = ? ORDER BY id", (epic_run_id,)
            )
        ]

    def get_event(self, project_id: str, event_id: str) -> TransitionEvent | None:
        row = self.db.execute(
            "SELECT payload FROM transition_events WHERE project_id = ? AND event_id = ?",
            (project_id, event_id),
        ).fetchone()
        return TransitionEvent.model_validate_json(row[0]) if row else None

    def record_transition(self, record: EpicRun | TaskRun, event: TransitionEvent) -> None:
        with self.transaction() as db:
            if isinstance(record, TaskRun):
                cursor = db.execute(
                    "UPDATE task_runs SET internal_status=?, completed_at=?, payload=? WHERE id=?",
                    (
                        record.internal_status,
                        record.completed_at.isoformat() if record.completed_at else None,
                        record.model_dump_json(),
                        record.id,
                    ),
                )
            else:
                cursor = db.execute(
                    "UPDATE epic_runs SET status=?, completed_at=?, payload=? WHERE id=?",
                    (
                        record.status,
                        record.completed_at.isoformat() if record.completed_at else None,
                        record.model_dump_json(),
                        record.id,
                    ),
                )
            if cursor.rowcount != 1:
                raise StoreError("transition target does not exist")
            db.execute(
                "INSERT INTO transition_events VALUES (?, ?, ?, ?, ?)",
                (
                    event.project_id,
                    event.id,
                    event.epic_run_id,
                    event.task_run_id,
                    event.model_dump_json(),
                ),
            )

    def get_references(self, epic_run_id: str) -> list[ExternalReference]:
        return [
            ExternalReference.model_validate_json(row[0])
            for row in self.db.execute(
                "SELECT payload FROM external_references WHERE epic_run_id = ? ORDER BY id",
                (epic_run_id,),
            )
        ]
