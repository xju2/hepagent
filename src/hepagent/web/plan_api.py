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
    POST /api/plan/{name}/approve   release the plan to the orchestrator
    GET  /api/plan                  list the analyses that have a plan

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

from hepagent.plan import service, store

STATIC_DIR = Path(__file__).with_name("static")
EDITOR_PAGE = STATIC_DIR / "plan.html"

#: Re-exported so callers can reach them without importing FastAPI themselves.
BASE_DIR_ENV = service.BASE_DIR_ENV
analyses_dir = service.analyses_dir


def _resolve_root(name: str, base_dir: str | Path | None) -> Path:
    """Resolve an analysis name to its root, as an HTTP error on failure."""
    try:
        return service.resolve_analysis(name, base_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _reviewer_names() -> set[str]:
    """Reviewer factory names, so the editor can offer them and validate against them."""
    from hepagent.agents.jfc.reviewers import REVIEWER_NAMES

    return set(REVIEWER_NAMES)


def create_router(
    base_dir: str | Path | None = None,
    gate: service.PlanApprovalGate | None = None,
) -> APIRouter:
    """Build the plan-editor router.

    Args:
        base_dir: Directory holding analyses. Falls back to `$HEPAGENT_ANALYSES_DIR`,
            then to `./analyses`, resolved per request so a test can move it.
        gate: Approval latch. Defaults to the process-wide one, which is what
            makes approval in the browser release the orchestrator in-process.
    """
    router = APIRouter()
    latch = gate or service.APPROVAL_GATE

    def view(root: Path) -> dict[str, Any]:
        return service.get_plan_view(root, known_reviewers=_reviewer_names(), gate=latch).to_dict()

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
        base = analyses_dir(base_dir)
        names = await asyncio.to_thread(service.list_planned, base_dir)
        return JSONResponse({"base_dir": str(base), "analyses": [{"name": n} for n in names]})

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
            return service.put_plan(
                root, document, known_reviewers=_reviewer_names(), gate=latch
            ).to_dict()

        try:
            return JSONResponse(await asyncio.to_thread(save))
        except store.PlanFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/api/plan/{name}/approve")
    async def approve_plan(name: str) -> JSONResponse:
        """Release the plan to the orchestrator."""
        root = _resolve_root(name, base_dir)

        def release() -> dict[str, Any]:
            return service.approve(root, known_reviewers=_reviewer_names(), gate=latch).to_dict()

        try:
            return JSONResponse(await asyncio.to_thread(release))
        except service.PlanNotApprovable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except store.PlanNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router


def mount(app, base_dir: str | Path | None = None, gate=None) -> None:
    """Attach the plan routes to an existing app, ahead of any catch-all.

    Chainlit registers a SPA catch-all (`/{full_path:path}`) at import time, and
    Starlette matches routes in order, so a router added with `include_router`
    is never reached for a GET. The routes are therefore *inserted at the front*.
    Verified against chainlit 2.11.1; see `docs/WEB.md`.
    """
    router = create_router(base_dir=base_dir, gate=gate)
    app.router.routes[:0] = router.routes
