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
from contextlib import asynccontextmanager, suppress
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
    base_dir: str | None = None,
) -> dict[str, str]:
    """Build the environment overrides handed to the Chainlit process."""
    from hepagent.web.plan_api import BASE_DIR_ENV

    env = {
        "CHAINLIT_APP_ROOT": str(ensure_web_root()),
        "CHAINLIT_HOST": host,
        "CHAINLIT_PORT": str(port),
        "HEPAGENT_WEB_AGENT": agent_name,
        "HEPAGENT_WEB_MAX_TURNS": str(max_turns),
        "HEPAGENT_WEB_MODE": mode,
        "HEPAGENT_WEB_HOST": host,
        "HEPAGENT_WEB_PORT": str(port),
        BASE_DIR_ENV: str(Path(base_dir or "analyses").resolve()),
    }
    if model:
        env["HEPAGENT_WEB_MODEL"] = model
    if chat:
        env["HEPAGENT_WEB_CHAT"] = chat
    return env


def plan_editor_url(name: str, host: str | None = None, port: int | None = None) -> str:
    """The URL of the plan editor for one analysis.

    Reads the host and port the running server was started with, so the `/plan`
    slash command hands out a link that actually resolves.
    """
    import os
    from urllib.parse import quote

    host = host or os.environ.get("HEPAGENT_WEB_HOST") or "127.0.0.1"
    port = port or int(os.environ.get("HEPAGENT_WEB_PORT") or 8000)
    return f"http://{host}:{port}/plan/{quote(name)}"


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
    base_dir: str = "analyses",
) -> None:
    """Start the Chainlit server for hepagent (blocking).

    The plan editor is served from the same app, so approving a plan in the
    browser releases an orchestrator running in this process.
    """
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
            base_dir=base_dir,
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

    # Chainlit's SPA catch-all is already registered by the import above, and
    # Starlette matches in order, so these have to go in front of it.
    from hepagent.web.plan_api import mount

    mount(app)

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


def build_plan_app(base_dir: str | Path = "analyses", gate=None):
    """Build a bare FastAPI app serving only the plan editor.

    `hepagent jfc plan edit` uses this so a physicist reviewing a plan does not
    have to start a chat server — and so the editor works without the `web`
    extra's Chainlit dependency, which is much the heavier half.
    """
    from fastapi import FastAPI

    from hepagent.web.plan_api import mount

    app = FastAPI(title="hepagent plan editor", docs_url=None, redoc_url=None)
    mount(app, base_dir=base_dir, gate=gate)
    return app


@asynccontextmanager
async def plan_editor_running(
    *,
    base_dir: str | Path = "analyses",
    host: str = "127.0.0.1",
    port: int = 8001,
    gate=None,
):
    """Serve the plan editor for the duration of the `async with` block.

    The orchestrator and the editor share one event loop and one
    `PlanApprovalGate`, which is what lets "approve" in the browser release a
    run that is already waiting. Two processes would need a real IPC channel.
    """
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            build_plan_app(base_dir=base_dir, gate=gate),
            host=host,
            port=port,
            log_level="error",
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):  # wait for the bind, up to ~5s
            if server.started or task.done():
                break
            await asyncio.sleep(0.05)
        yield server
    finally:
        server.should_exit = True
        with suppress(asyncio.CancelledError):
            await task


async def open_when_plan_exists(analysis_root: Path, url: str, *, timeout: float = 120.0) -> bool:
    """Open a browser once the analysis has a plan to show.

    `jfc run --review-plan` starts the editor before the scaffold has written
    `plan.json`, so opening the page immediately would show a 404.
    """
    import webbrowser

    from hepagent.plan.store import has_plan

    waited = 0.0
    while waited < timeout:
        if await asyncio.to_thread(has_plan, analysis_root):
            webbrowser.open(url)
            return True
        await asyncio.sleep(0.25)
        waited += 0.25
    return False


def launch_plan_editor(
    name: str,
    *,
    base_dir: str | Path = "analyses",
    host: str = "127.0.0.1",
    port: int = 8001,
    open_browser: bool = True,
    gate=None,
    wait_for_approval: bool = False,
) -> bool:
    """Serve the plan editor for one analysis (blocking).

    Args:
        name: Analysis to open.
        base_dir: Directory holding analyses.
        host: Interface to bind. The editor writes files as the server user, so
            keep this on loopback.
        port: Port to bind.
        open_browser: Open the page automatically.
        gate: Approval latch. Defaults to the process-wide one.
        wait_for_approval: Shut the server down once the plan is approved,
            instead of serving until interrupted. This is what makes
            `jfc run --review-plan` a single blocking step.

    Returns:
        True if the plan was approved, False if the editor was closed without
        approving.
    """
    import webbrowser

    from hepagent.plan.service import APPROVAL_GATE

    latch = gate or APPROVAL_GATE
    root = Path(base_dir).resolve() / name
    url = f"http://{host}:{port}/plan/{name}"

    async def serve() -> bool:
        async with plan_editor_running(
            base_dir=base_dir, host=host, port=port, gate=latch
        ) as server:
            if open_browser:
                webbrowser.open(url)
            if wait_for_approval:
                return await latch.wait(root)
            # Serve until interrupted; the caller catches KeyboardInterrupt.
            while not server.should_exit:
                await asyncio.sleep(0.2)
            return latch.is_approved(root)

    return asyncio.run(serve())
