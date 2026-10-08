import subprocess

import pytest

from orchestrator.cli import main
from orchestrator.domain.policy import Actor, Role


@pytest.fixture
def launch(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    config = tmp_path / "config.toml"
    config.write_text('repository="repo"\nworktree_root="trees"\nsqlite_path="state.db"\n')
    principal = tmp_path / "integration.json"
    principal.write_text(
        Actor(
            actor_id="i", role=Role.INTEGRATION, project_id="p", epic_run_id="e"
        ).model_dump_json()
    )
    principal.chmod(0o600)
    return config, principal


def test_operator_explicit_cli_session_constructs_scoped_service_without_starting_runtime(
    launch, monkeypatch
):
    config, principal = launch
    constructed = []

    # Controlled constructor; this is CLI wiring, not a live HERDR_ENV proof.
    def adapter(session, *, sandbox):
        constructed.append((session, sandbox))
        return type("ControlledHerdr", (), {"server_session": session, "sandbox": sandbox})()

    async def serve(service):
        assert service.actor.role == Role.INTEGRATION
        assert service.task_start.settings.max_workers == 1
        assert service.store.get_runs() == []

    monkeypatch.setattr("orchestrator.adapters.herdr.HerdrAdapter", adapter)
    monkeypatch.setattr("orchestrator.mcp.server.serve_stdio", serve)
    assert (
        main(
            [
                "--config",
                str(config),
                "--mcp",
                "--principal",
                str(principal),
                "--herdr-session",
                "explicit-test",
            ]
        )
        == 0
    )
    assert constructed == [("explicit-test", "workspace-write")]


def test_missing_actual_herdr_environment_rejects_opt_in_before_runtime_resources(
    launch, monkeypatch
):
    config, principal = launch
    monkeypatch.delenv("HERDR_ENV", raising=False)
    assert (
        main(
            [
                "--config",
                str(config),
                "--mcp",
                "--principal",
                str(principal),
                "--herdr-session",
                "explicit-test",
            ]
        )
        == 5
    )
    assert not (config.parent / "trees").exists()


def test_herdr_session_flag_requires_mcp_instead_of_starting_implicit_agents(launch):
    config, _ = launch
    with pytest.raises(SystemExit) as e:
        main(["--config", str(config), "--herdr-session", "explicit-test"])
    assert e.value.code == 2 and not (config.parent / "state.db").exists()
