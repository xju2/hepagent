"""HTTP routes for the plan editor.

This is the **only** module in the codebase that imports FastAPI, mirroring the
rule that only `app.py` imports Chainlit: CI installs neither extra, so anything
importable without them stays testable. Everything with a decision in it lives in
`plan/service.py`; this file translates between HTTP and that façade and does
nothing else.

Routes:
    GET  /plan/{name}               the editor page
    GET  /api/plan/{name}           the plan, its layout, order and findings
    PUT  /api/plan/{name}           save an edited plan, returns the fresh view
    POST /api/plan/{name}/layout    auto-layout an unsaved plan, saving nothing
    GET  /api/plan/{name}/predefined   nodes the editor may offer to insert
    GET  /api/plan/{name}/state     what the analysis has established so far
    POST /api/plan/{name}/approve   release the plan to the orchestrator
    POST /api/plan/{name}/run       start the analysis in this process
                                    (`only_node` runs one node and stops)
    GET  /api/plan/{name}/run       run status, new events, pending question
    POST /api/plan/{name}/run/answer   answer what the run is blocked on
    POST /api/plan/{name}/run/cancel   ask the run to stop at the next node
    GET  /api/plan                  list the analyses that have a plan
    GET  /api/platforms/{platform}/models   models a platform serves

`AnalysisGraph` and plan I/O are filesystem work on the request path, so every
handler offloads through `asyncio.to_thread` — invariant 5 of `docs/WEB.md`
applies to the web server generally, not only to agent tools.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from hepagent.plan import runs, service, store

STATIC_DIR = Path(__file__).with_name("static")
EDITOR_PAGE = STATIC_DIR / "plan.html"

# Deliberately no re-exports of `service.BASE_DIR_ENV` / `service.analyses_dir`:
# they made this module look like a safe import for code that must run without
# the `web` extra, and `session.py` duly imported them from here — which pulled
# FastAPI into a CI path that has none. Import them from `plan.service`.


def _resolve_root(name: str, base_dir: str | Path | None) -> Path:
    """Resolve an analysis name to its root, as an HTTP error on failure."""
    try:
        return service.resolve_analysis(name, base_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _vocabulary() -> service.PlanVocabulary:
    """Reviewers, tools, skills and MCP servers the editor may offer.

    Imported inside the function on purpose: this router must stay importable
    without the JFC agent stack behind it, the same reason the reviewer registry
    was never a module-level import here.
    """
    from hepagent.agents.jfc.capabilities import plan_vocabulary

    return plan_vocabulary()


def _analysis_state(root: Path) -> dict[str, Any]:
    """What the analysis has established, for the progress panel.

    Late-imported like `_vocabulary`: the state view reads JFC artifacts, and
    this router must stay importable without the JFC agent stack behind it.
    """
    from hepagent.agents.jfc.state import analysis_state

    return analysis_state(root)


def _platform_models(platform: str) -> dict[str, Any]:
    """The models one platform serves, the model it defaults to, and what they cost.

    Late-imported like `_vocabulary`, and separate from the vocabulary for a
    different reason: listing models is a network call against the provider, so
    it happens when a user opens the dropdown rather than on every plan view.

    `costs` is ``{model: {"input": $/M, "output": $/M}}`` and is **empty for a
    platform that does not publish prices** — the dropdown shows a price where
    one exists and a bare model id where it does not.

    Raises:
        ValueError: the platform is not configured, or its API key is missing.
    """
    from hepagent.model_providers import (
        get_model_provider_settings,
        list_available_models,
        list_model_costs,
    )

    settings = get_model_provider_settings(platform)
    models = list(list_available_models(platform, settings=settings))
    costs = list_model_costs(platform, settings=settings)
    return {
        "platform": platform,
        "default": settings.default_model,
        "models": models,
        "costs": {model: costs[model] for model in models if model in costs},
    }


def _default_launcher(**kwargs: Any) -> runs.RunHandle:
    """Start a JFC analysis. Imported late, like `_vocabulary`, and for the same
    reason: this router must stay importable without the JFC agent stack."""
    from hepagent.agents.jfc.launch import start_analysis_run

    return start_analysis_run(**kwargs)


def create_router(
    base_dir: str | Path | None = None,
    gate: service.PlanApprovalGate | None = None,
    launcher: Any = None,
) -> APIRouter:
    """Build the plan-editor router.

    Args:
        base_dir: Directory holding analyses. Falls back to `$HEPAGENT_ANALYSES_DIR`,
            then to `./analyses`, resolved per request so a test can move it.
        gate: Approval latch. Defaults to the process-wide one, which is what
            makes approval in the browser release the orchestrator in-process.
        launcher: What starts a run, as `(analysis_root, *, name, model,
            unattended, max_iterations, only_node) -> RunHandle`. Defaults to the JFC
            orchestrator; a test supplies something that finishes in a
            millisecond instead of an afternoon.
    """
    router = APIRouter()
    latch = gate or service.APPROVAL_GATE
    start = launcher or _default_launcher

    def view(root: Path) -> dict[str, Any]:
        return service.get_plan_view(root, vocabulary=_vocabulary(), gate=latch).to_dict()

    @router.get("/plan/{name}", response_class=HTMLResponse)
    async def editor_page(name: str) -> HTMLResponse:
        """Serve the editor. The page fetches its own data from the API."""
        _resolve_root(name, base_dir)
        try:
            html = await asyncio.to_thread(EDITOR_PAGE.read_text, encoding="utf-8")
        except OSError as exc:  # pragma: no cover - a broken install
            raise HTTPException(status_code=500, detail=f"Editor page missing: {exc}") from exc
        return HTMLResponse(html)

    @router.get("/api/plan")
    async def list_plans() -> JSONResponse:
        """List analyses that have a plan, for the editor's picker."""
        base = service.analyses_dir(base_dir)
        names = await asyncio.to_thread(service.list_planned, base_dir)
        return JSONResponse({"base_dir": str(base), "analyses": [{"name": n} for n in names]})

    @router.get("/api/platforms/{platform}/models")
    async def platform_models(platform: str) -> JSONResponse:
        """The models a platform serves, for the node panel's model dropdown.

        A provider that cannot be reached — no API key, no network — is a 502
        with the reason in it, not a 500: the page falls back to letting the user
        type a model name, and says why it had to.
        """
        try:
            return JSONResponse(await asyncio.to_thread(_platform_models, platform))
        except ValueError as exc:  # unknown platform, or no API key configured
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - provider/network failures are user-facing
            raise HTTPException(status_code=502, detail=f"Could not list models: {exc}") from exc

    @router.get("/api/plan/{name}")
    async def get_plan(name: str) -> JSONResponse:
        root = _resolve_root(name, base_dir)
        try:
            return JSONResponse(await asyncio.to_thread(view, root))
        except store.PlanNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except store.PlanFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.put("/api/plan/{name}")
    async def put_plan(name: str, payload: dict[str, Any]) -> JSONResponse:
        """Save an edited plan.

        A plan with blocking findings is saved anyway — half-finished work has to
        survive a page reload — and the response says so. `approve` is what
        refuses.
        """
        root = _resolve_root(name, base_dir)
        document = payload.get("plan", payload)

        def save() -> dict[str, Any]:
            return service.put_plan(root, document, vocabulary=_vocabulary(), gate=latch).to_dict()

        try:
            return JSONResponse(await asyncio.to_thread(save))
        except store.PlanFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/api/plan/{name}/layout")
    async def layout_plan(name: str, payload: dict[str, Any]) -> JSONResponse:
        """Auto-layout the *submitted* plan without saving it.

        The editor's "Auto-layout" button acts on what is on screen, which
        routinely contains nodes and edges the server has never seen. Laying that
        out needs `plan/layout.py`, so the unsaved document comes here rather
        than the algorithm going to the browser.
        """
        _resolve_root(name, base_dir)
        document = payload.get("plan", payload)

        def computed() -> dict[str, list[int]]:
            return service.preview_layout(document)

        try:
            return JSONResponse({"layout": await asyncio.to_thread(computed)})
        except store.PlanFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/plan/{name}/predefined")
    async def predefined_nodes(name: str) -> JSONResponse:
        """Nodes a user may drop into this plan, from the built-in templates.

        Its own route rather than part of the plan view: the library only
        matters when somebody opens the picker, and reading every template's
        prompt markdown on each save would be work nobody asked for.
        """
        root = _resolve_root(name, base_dir)

        def library() -> list[dict[str, Any]]:
            return service.predefined_nodes(root)

        return JSONResponse({"nodes": await asyncio.to_thread(library)})

    @router.get("/api/plan/{name}/state")
    async def plan_state(name: str) -> JSONResponse:
        """What the analysis has established so far.

        Read from the analysis directory, not from a run: the panel shows the
        same thing before a run starts, after a reload, and for an analysis this
        process never ran. The run's own live status stays on `/run`.
        """
        root = _resolve_root(name, base_dir)
        try:
            return JSONResponse(await asyncio.to_thread(_analysis_state, root))
        except store.PlanNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except store.PlanFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/api/plan/{name}/approve")
    async def approve_plan(name: str) -> JSONResponse:
        """Release the plan to the orchestrator.

        The response says whether anything was actually waiting on the latch.
        A run started by `jfc run --review-plan` is; a plan page opened on its
        own is not, and the page starts the run itself in that case — otherwise
        approving would silently do nothing, which is what it used to do.
        """
        root = _resolve_root(name, base_dir)

        def release() -> dict[str, Any]:
            return service.approve(root, vocabulary=_vocabulary(), gate=latch).to_dict()

        try:
            payload = await asyncio.to_thread(release)
        except service.PlanNotApprovable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except store.PlanNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(payload | {"awaited": latch.waiting(root) > 0})

    @router.post("/api/plan/{name}/run")
    async def start_run(name: str, payload: dict[str, Any] | None = None) -> JSONResponse:
        """Start the analysis, and report the run's first snapshot.

        Refuses a plan that has not been approved: approval is the point at which
        a human took responsibility for what is about to run, and an API caller
        must not be able to skip it any more than the button can.

        `only_node` runs that one node and nothing else — the page's per-node
        "Run" button. It does not consult the approval latch and does not set
        it: pressing Run on a node *is* the human taking responsibility, for
        that node only, and requiring approval would be circular anyway because
        saving the edit the user is about to test withdraws it. A blocking
        finding still refuses, exactly as it refuses approval.
        """
        root = _resolve_root(name, base_dir)
        body = payload or {}
        only_node = str(body.get("only_node") or "") or None
        if only_node is None and not latch.is_approved(root):
            raise HTTPException(
                status_code=409,
                detail="Approve the plan before starting the run.",
            )

        def launch() -> dict[str, Any]:
            if only_node is not None:
                blocked = service.get_plan_view(root, vocabulary=_vocabulary(), gate=latch).blocking
                if blocked:
                    raise service.PlanNotApprovable(
                        "Resolve the blocking findings before running a node."
                    )
            handle = start(
                analysis_root=root,
                name=name,
                model=body.get("model") or None,
                unattended=bool(body.get("unattended")),
                max_iterations=int(body.get("max_iterations") or 3),
                max_turns=int(body["max_turns"]) if body.get("max_turns") else None,
                only_node=only_node,
            )
            return handle.snapshot()

        try:
            return JSONResponse(await asyncio.to_thread(launch))
        except runs.RunAlreadyActive as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except service.PlanNotApprovable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:  # only_node names a node the plan does not have
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except store.PlanNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/plan/{name}/run")
    async def run_status(name: str, since: int = 0) -> JSONResponse:
        """Status, events after `since`, and whatever the run is blocked on.

        A page that has never seen a run gets `{"status": "idle"}` rather than a
        404: "no run yet" is the normal state of the editor, not an error.
        """
        root = _resolve_root(name, base_dir)
        handle = runs.RUNS.get(root)
        if handle is None:
            return JSONResponse({"name": name, "status": "idle", "active": False, "events": []})
        return JSONResponse(handle.snapshot(since=since))

    @router.post("/api/plan/{name}/run/answer")
    async def answer_run(name: str, payload: dict[str, Any]) -> JSONResponse:
        """Answer the question a run is blocked on."""
        root = _resolve_root(name, base_dir)
        handle = runs.RUNS.active(root)
        if handle is None:
            raise HTTPException(status_code=404, detail="No run is waiting for an answer.")
        prompt_id = str(payload.get("id") or "")
        if not handle.answer(prompt_id, payload):
            # Stale: the prompt timed out, was cancelled, or two tabs answered.
            raise HTTPException(status_code=409, detail="That question is no longer waiting.")
        return JSONResponse(handle.snapshot(since=payload.get("since") or 0))

    @router.post("/api/plan/{name}/run/cancel")
    async def cancel_run(name: str) -> JSONResponse:
        """Ask the run to stop at its next node boundary."""
        root = _resolve_root(name, base_dir)
        handle = runs.RUNS.active(root)
        if handle is None:
            raise HTTPException(status_code=404, detail="No run is in progress.")
        handle.cancel()
        return JSONResponse(handle.snapshot())

    return router


def mount(app, base_dir: str | Path | None = None, gate=None, launcher=None) -> None:
    """Attach the plan routes to an existing app, ahead of any catch-all.

    Chainlit registers a SPA catch-all (`/{full_path:path}`) at import time, and
    Starlette matches routes in order, so a router added with `include_router`
    is never reached for a GET. The routes are therefore *inserted at the front*.
    Verified against chainlit 2.11.1; see `docs/WEB.md`.
    """
    router = create_router(base_dir=base_dir, gate=gate, launcher=launcher)
    app.router.routes[:0] = router.routes
