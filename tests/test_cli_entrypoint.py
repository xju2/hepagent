from typer.testing import CliRunner

from hepagent.main import app


def test_cli_requires_task_prompt():
    runner = CliRunner()
    result = runner.invoke(app, ["run", "--agent", "research_scientist"])

    assert result.exit_code == 2
    assert "Missing task prompt" in result.stdout
