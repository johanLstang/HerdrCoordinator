import pytest
from test_task_start_cli import launch  # noqa: F401

from orchestrator.cli import main
from orchestrator.domain.review_contracts import EpicReviewSpec


@pytest.mark.parametrize("configured", [True, False])
def test_operator_review_configuration_enables_mcp_without_implicit_runtime(
    launch, monkeypatch, configured  # noqa: F811
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
requirements=["Preserve task scope"]
acceptance_criteria=["Review uses current commits"]
sources=["README.md"]
"""
        )
    monkeypatch.delenv("HERDR_ENV", raising=False)
    captured = []

    async def serve(service):
        captured.append(service.task_review is not None)
        assert service.task_changes is None
        assert service.task_start is None and service.worker_reports is None
        assert service.store.get_runs() == []

    monkeypatch.setattr("orchestrator.mcp.server.serve_stdio", serve)
    assert main(["--config", str(config), "--mcp", "--principal", str(principal)]) == 0
    assert captured == [configured] and not (config.parent / "trees").exists()


@pytest.mark.parametrize(
    "source",
    ["/etc/passwd", "../outside.md", ".git/config", ".codex/auth.json", "folder/../rules.md"],
)
def test_review_rules_reject_unversioned_or_protected_source_paths(source):
    with pytest.raises(ValueError):
        EpicReviewSpec(
            version=1,
            project_id="p",
            epic_id="E",
            requirements=["Bounded review"],
            acceptance_criteria=["Correct base"],
            sources=[source],
        )


def test_bad_nested_review_configuration_is_safe_before_database_creation(launch, capsys):  # noqa: F811
    config, principal = launch
    config.write_text(
        config.read_text()
        + """[review_context]
version="planted-secret-review-input"
project_id="p"
epic_id="E"
requirements=["Bounded review"]
acceptance_criteria=["Correct base"]
sources=["README.md"]
"""
    )
    assert main(["--config", str(config), "--check"]) == 2
    captured = capsys.readouterr()
    assert "planted-secret-review-input" not in captured.err
    assert not (config.parent / "state.db").exists()
