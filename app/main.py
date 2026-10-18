from contextlib import asynccontextmanager
from pathlib import Path

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import text

from app import auth
from app.config import SECRET_KEY_SET, settings
from app.db import SessionLocal
from app.sources import sync_sources
from app.web import routes_admin, routes_auth, routes_catalog
from app.web.common import templates

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with SessionLocal() as session:
        await sync_sources(session)
        await auth.ensure_admin(session)
    if not SECRET_KEY_SET:
        log.warning("SECRET_KEY не задан: сессии сбросятся при перезапуске")
    yield


app = FastAPI(title="SalesParser", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")
app.include_router(routes_auth.router)
app.include_router(routes_catalog.router)
app.include_router(routes_admin.router)


@app.exception_handler(StarletteHTTPException)
async def html_errors(request: Request, exc: StarletteHTTPException):
    """Ошибки доступа и «не найдено» на страницах — человеческой страницей, а не JSON."""
    if exc.status_code in (400, 403, 404) and "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(request, "error.html", {"exc": exc}, status_code=exc.status_code)
    return await http_exception_handler(request, exc)

if settings.demo_enabled:
    from app.demo.router import router as demo_router
    app.include_router(demo_router)


@app.get("/health", include_in_schema=False)
async def health():
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok"}
