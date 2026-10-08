import pytest
from test_task_start_cli import launch  # noqa: F401

from orchestrator.cli import main


@pytest.mark.parametrize("configured", [True, False])
def test_feedback_mcp_requires_review_rules_and_explicit_runtime(
    launch,  # noqa: F811
    monkeypatch,
    configured,
):
    config, principal = launch
    if configured:
        config.write_text(
            config.read_text()
            + """worker_test_command=["python3","-m","unittest"]
[review_context]
version=1
project_id="p"
epic_id="E"
requirements=["Bounded correction"]
acceptance_criteria=["Same native session"]
sources=["README.md"]
"""
        )
    captured = []

    def adapter(session, *, sandbox):
        return type("ControlledHerdr", (), {"server_session": session, "sandbox": sandbox})()

    async def serve(service):
        captured.append(service.task_changes is not None)
        assert (service.task_merge is not None) == configured
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
    assert captured == [configured] and not (config.parent / "trees").exists()
