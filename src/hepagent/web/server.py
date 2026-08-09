"""Launcher for the hepagent web UI.

Chainlit reads most of its configuration from the environment at import time,
so everything is set here *before* Chainlit is imported. Its working files
(``.chainlit/``, ``.files/``, ``chainlit.md``) are pinned to
``~/.hepagent/web`` so running ``hepagent web`` from any directory never
litters the user's project.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from hepagent.helpers import bootstrap_hepagent_home, get_hepagent_home

APP_MODULE_PATH = Path(__file__).with_name("app.py")

CHAINLIT_CONFIG = """\
[project]
enable_telemetry = false
user_env = []
session_timeout = 3600
cache = false

[UI]
name = "hepagent"
default_theme = "dark"
cot = "full"

[features]
unsafe_allow_html = false
latex = true

[meta]
generated_by = "hepagent"
"""


def get_web_root() -> Path:
    """Return the directory Chainlit uses for its working files."""
    return get_hepagent_home() / "web"


def ensure_web_root() -> Path:
    """Create the Chainlit working directory and seed its config once."""
    root = get_web_root()
    (root / ".chainlit").mkdir(parents=True, exist_ok=True)
    config_file = root / ".chainlit" / "config.toml"
    if not config_file.exists():
        config_file.write_text(CHAINLIT_CONFIG, encoding="utf-8")
    return root


def build_environment(
    *,
    agent_name: str,
    model: str | None,
    max_turns: int,
    mode: str,
    chat: str | None,
    host: str,
    port: int,
) -> dict[str, str]:
    """Build the environment overrides handed to the Chainlit process."""
    env = {
        "CHAINLIT_APP_ROOT": str(ensure_web_root()),
        "CHAINLIT_HOST": host,
        "CHAINLIT_PORT": str(port),
        "HEPAGENT_WEB_AGENT": agent_name,
        "HEPAGENT_WEB_MAX_TURNS": str(max_turns),
        "HEPAGENT_WEB_MODE": mode,
    }
    if model:
        env["HEPAGENT_WEB_MODEL"] = model
    if chat:
        env["HEPAGENT_WEB_CHAT"] = chat
    return env


def launch_web_ui(
    *,
    agent_name: str = "scientist",
    model: str | None = None,
    max_turns: int = 40,
    mode: str = "confirm",
    chat: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    headless: bool = False,
) -> None:
    """Start the Chainlit server for hepagent (blocking)."""
    bootstrap_hepagent_home()
    os.environ.update(
        build_environment(
            agent_name=agent_name,
            model=model,
            max_turns=max_turns,
            mode=mode,
            chat=chat,
            host=host,
            port=port,
        )
    )

    try:
        serve = _build_server(headless=headless, host=host, port=port)
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "The web UI requires the 'web' extra. Install it with:\n"
            "  uv sync --all-extras\n"
            "or\n"
            "  uv tool install 'hepagent[web]'"
        ) from exc

    asyncio.run(serve())


def _build_server(*, headless: bool, host: str, port: int):
    """Prepare Chainlit and return a coroutine that serves it.

    This deliberately reimplements ``chainlit.cli.run_chainlit`` instead of
    calling it. Importing ``chainlit.cli`` runs ``nest_asyncio.apply()`` at
    module scope, which on Python 3.12+ makes ``asyncio.wait_for`` raise
    ``RuntimeError: Timeout should be used inside a task`` instead of
    ``TimeoutError``. python-engineio's ping-timeout service task only catches
    ``TimeoutError``, so it dies and is restarted forever, flooding the log and
    breaking the websocket. Everything below mirrors ``run_chainlit`` minus that
    call.
    """
    import uvicorn
    from chainlit.auth import ensure_jwt_secret
    from chainlit.cache import init_lc_cache
    from chainlit.config import config, load_module
    from chainlit.markdown import init_markdown
    from chainlit.server import app
    from chainlit.utils import check_file

    config.run.headless = headless
    config.run.host = host
    config.run.port = port

    target = str(APP_MODULE_PATH)
    check_file(target)
    config.run.module_name = target
    load_module(config.run.module_name)

    ensure_jwt_secret()
    init_markdown(config.root)
    init_lc_cache()

    async def serve() -> None:
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="debug" if config.run.debug else "error",
            )
        )
        await server.serve()

    return serve
