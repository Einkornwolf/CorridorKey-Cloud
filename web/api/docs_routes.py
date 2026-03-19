"""Protected API documentation routes (CRKY-32).

When DOCS_PUBLIC=false, the built-in FastAPI /docs, /redoc, and /openapi.json
are disabled. This module mounts equivalent routes that require JWT
authentication (any valid tier, including pending).

This lets production deployments keep API docs accessible to authenticated
users while preventing public exposure of the API schema.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse

from .auth import get_current_user


def mount_protected_docs(app: FastAPI) -> None:
    """Add auth-gated /docs, /redoc, and /openapi.json routes."""

    @app.get("/openapi.json", include_in_schema=False)
    async def protected_openapi(request: Request):
        user = get_current_user(request)
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "Authentication required to view API docs"})
        return JSONResponse(content=app.openapi())

    @app.get("/docs", include_in_schema=False)
    async def protected_swagger(request: Request):
        user = get_current_user(request)
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "Authentication required to view API docs"})
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — Swagger UI",
        )

    @app.get("/redoc", include_in_schema=False)
    async def protected_redoc(request: Request):
        user = get_current_user(request)
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "Authentication required to view API docs"})
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — ReDoc",
        )
