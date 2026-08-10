"""Tests for the web UI launcher."""

from __future__ import annotations

from pathlib import Path

from hepagent.web import server


def _pin_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "get_hepagent_home", lambda: tmp_path)


def test_web_root_lives_under_hepagent_home(monkeypatch, tmp_path):
    _pin_home(monkeypatch, tmp_path)
    assert server.get_web_root() == tmp_path / "web"


def test_ensure_web_root_seeds_a_config_once(monkeypatch, tmp_path):
    _pin_home(monkeypatch, tmp_path)

    root = server.ensure_web_root()
    config_file = root / ".chainlit" / "config.toml"

    assert config_file.exists()
    assert 'name = "hepagent"' in config_file.read_text(encoding="utf-8")

    config_file.write_text("# edited by the user\n", encoding="utf-8")
    server.ensure_web_root()
    assert config_file.read_text(encoding="utf-8") == "# edited by the user\n"


def test_build_environment_pins_chainlit_working_files(monkeypatch, tmp_path):
    _pin_home(monkeypatch, tmp_path)

    env = server.build_environment(
        agent_name="scientist",
        model=None,
        max_turns=40,
        mode="confirm",
        chat=None,
        host="127.0.0.1",
        port=8123,
    )

    # Chainlit resolves its working directory at import time, so it must never
    # default to the user's current directory.
    assert env["CHAINLIT_APP_ROOT"] == str(tmp_path / "web")
    assert env["CHAINLIT_PORT"] == "8123"
    assert env["HEPAGENT_WEB_AGENT"] == "scientist"
    assert env["HEPAGENT_WEB_MODE"] == "confirm"
    assert "HEPAGENT_WEB_MODEL" not in env
    assert "HEPAGENT_WEB_CHAT" not in env


def test_build_environment_forwards_optional_settings(monkeypatch, tmp_path):
    _pin_home(monkeypatch, tmp_path)

    env = server.build_environment(
        agent_name="shell",
        model="openai:gpt-5-mini",
        max_turns=80,
        mode="yolo",
        chat="web-abc123",
        host="0.0.0.0",
        port=8000,
    )

    assert env["HEPAGENT_WEB_MODEL"] == "openai:gpt-5-mini"
    assert env["HEPAGENT_WEB_CHAT"] == "web-abc123"
    assert env["HEPAGENT_WEB_MAX_TURNS"] == "80"
    assert env["HEPAGENT_WEB_MODE"] == "yolo"


def test_app_module_ships_with_the_package():
    assert server.APP_MODULE_PATH.name == "app.py"
    assert server.APP_MODULE_PATH.exists()


def test_web_command_is_registered_and_forwards_options(monkeypatch):
    from typer.testing import CliRunner

    from hepagent.main import app

    captured: dict[str, object] = {}

    def fake_launch(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(server, "launch_web_ui", fake_launch)

    result = CliRunner().invoke(
        app,
        ["web", "--agent", "shell", "--port", "9001", "--yolo", "--headless"],
    )

    assert result.exit_code == 0, result.output
    assert captured["agent_name"] == "shell"
    assert captured["port"] == 9001
    assert captured["mode"] == "yolo"
    assert captured["headless"] is True


# ------------------------------------------------------------- plan editor


def test_the_analyses_directory_is_handed_to_the_web_process(tmp_path):
    from hepagent.plan.service import BASE_DIR_ENV
    from hepagent.web.server import build_environment

    env = build_environment(
        agent_name="scientist",
        model=None,
        max_turns=40,
        mode="confirm",
        chat=None,
        host="127.0.0.1",
        port=8000,
        base_dir=str(tmp_path / "elsewhere"),
    )
    assert env[BASE_DIR_ENV] == str((tmp_path / "elsewhere").resolve())
    # The `/plan` command builds links from these, so the URL resolves.
    assert env["HEPAGENT_WEB_HOST"] == "127.0.0.1"
    assert env["HEPAGENT_WEB_PORT"] == "8000"


def test_plan_editor_url_follows_the_running_server(monkeypatch):
    from hepagent.web.server import plan_editor_url

    monkeypatch.setenv("HEPAGENT_WEB_HOST", "0.0.0.0")
    monkeypatch.setenv("HEPAGENT_WEB_PORT", "9123")
    assert plan_editor_url("zbb") == "http://0.0.0.0:9123/plan/zbb"


def test_plan_editor_url_quotes_the_name(monkeypatch):
    from hepagent.web.server import plan_editor_url

    monkeypatch.delenv("HEPAGENT_WEB_HOST", raising=False)
    monkeypatch.delenv("HEPAGENT_WEB_PORT", raising=False)
    assert plan_editor_url("z bb/x") == "http://127.0.0.1:8000/plan/z%20bb/x"


def test_only_plan_api_imports_fastapi():
    """The mirror of invariant 1: CI installs neither web extra.

    `session.py` and `server.py` are imported by paths that run without the
    extra, so a top-level `fastapi` import in either would break the suite.
    """
    import ast
    from pathlib import Path

    web = Path("src/hepagent/web")
    offenders = []
    for source in sorted(web.glob("*.py")):
        if source.name in {"plan_api.py", "app.py"}:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in tree.body:  # module scope only; function-local is fine
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(n.split(".")[0] in {"fastapi", "chainlit", "uvicorn"} for n in names):
                offenders.append(f"{source.name}: {names}")
    assert offenders == []
