import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.config import load_settings
from orchestrator.event_log import EventLog


@pytest.fixture
def configuration(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    config = tmp_path / "config.toml"
    config.write_text(
        'repository = "repo"\nworktree_root = "trees"\nsqlite_path = "state/runtime.db"\n'
        'max_workers = 2\ncredential_env = ["HERDR_TEST_SECRET"]\n'
    )
    return config


def run_check(config: Path):
    return subprocess.run(
        [sys.executable, "-m", "orchestrator", "--config", str(config), "--check"],
        capture_output=True,
        text=True,
        env={**os.environ, "HERDR_TEST_SECRET": "planted-secret-123"},
        timeout=10,
    )


def test_valid_start_reports_paths_without_creating_resources(configuration):
    result = run_check(configuration)
    assert result.returncode == 0
    assert result.stdout == ""
    event = json.loads(result.stderr)
    assert event["operation"] == "configuration.validate"
    assert event["level"] == "INFO"
    assert event["correlation_id"]
    assert event["data"] == {
        "repository": str(configuration.parent / "repo"),
        "worktree_root": str(configuration.parent / "trees"),
        "sqlite_path": str(configuration.parent / "state/runtime.db"),
        "max_workers": 2,
    }
    assert "planted-secret-123" not in result.stderr
    assert not (configuration.parent / "trees").exists()
    assert not (configuration.parent / "state").exists()


@pytest.mark.parametrize(
    ("original", "replacement", "error"),
    [
        ("max_workers = 2", "max_workers = 0", "max_workers"),
        ("max_workers = 2", "max_workers = 3", "max_workers"),
        ("max_workers = 2", "max_workers = true", "max_workers"),
        ('repository = "repo"', 'repository = "missing"', "repository"),
        ('worktree_root = "trees"', 'worktree_root = "."', "ancestors"),
        ('worktree_root = "trees"', 'worktree_root = "/tmp"', "ancestors"),
        ('worktree_root = "trees"', 'worktree_root = "repo/src"', ".worktrees"),
        ('worktree_root = "trees"', 'worktree_root = "/etc/herdr"', "system directories"),
        ('sqlite_path = "state/runtime.db"', 'sqlite_path = "trees/state.db"', "separate"),
        ('sqlite_path = "state/runtime.db"', 'sqlite_path = "repo/.git/state.db"', "metadata"),
        ("max_workers = 2", 'max_workers = "planted-secret-123"', "max_workers"),
    ],
)
def test_invalid_configuration_fails_before_side_effects(
    configuration, original, replacement, error
):
    configuration.write_text(configuration.read_text().replace(original, replacement))
    result = run_check(configuration)
    assert result.returncode == 2
    assert error in result.stderr
    assert "planted-secret-123" not in result.stderr
    assert result.stdout == ""
    assert not (configuration.parent / "trees").exists()
    assert not (configuration.parent / "state").exists()


@pytest.mark.parametrize("content", ['max_workers = "secret', '"planted-secret-123" = 1'])
def test_parser_and_unknown_field_errors_do_not_echo_input(configuration, content):
    configuration.write_text(content)
    result = run_check(configuration)
    assert result.returncode == 2
    assert "planted-secret-123" not in result.stderr
    assert "secret" not in result.stderr


def test_missing_file_is_safe(configuration):
    configuration.unlink()
    assert run_check(configuration).returncode == 2


def test_symlink_to_protected_directory_is_rejected(configuration):
    (configuration.parent / "trees").symlink_to(configuration.parent / "repo/.git")
    result = run_check(configuration)
    assert result.returncode == 2
    assert "metadata" in result.stderr


def test_settings_are_immutable(configuration):
    settings = load_settings(configuration)
    with pytest.raises(ValueError):
        settings.max_workers = 3


def test_event_redacts_nested_secret_values_and_remains_json(capsys):
    log = EventLog(secrets=('secret"\nvalue',))
    log.emit("test", "ERROR", 'contains secret"\nvalue', nested={"items": ['secret"\nvalue']})
    event = json.loads(capsys.readouterr().err)
    assert event["message"] == "contains [REDACTED]"
    assert event["data"]["nested"]["items"] == ["[REDACTED]"]


def test_service_starts_and_stops_on_sigterm(configuration):
    process = subprocess.Popen(
        [sys.executable, "-m", "orchestrator", "--config", str(configuration)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert json.loads(process.stderr.readline())["operation"] == "configuration.validate"
        assert json.loads(process.stderr.readline())["operation"] == "state.initialize"
        assert json.loads(process.stderr.readline())["operation"] == "service.ready"
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0
        assert stdout == ""
        assert json.loads(stderr)["operation"] == "service.stopped"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_service_rejects_future_schema_with_safe_error(configuration):
    state = configuration.parent / "state"
    state.mkdir()
    path = state / "runtime.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator", "--config", str(configuration)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 3
    assert "unsupported state database schema version" in result.stderr
    assert "service.ready" not in result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99
