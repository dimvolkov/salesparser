from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.sources import sync_sources
from app.web import routes_admin, routes_catalog


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with SessionLocal() as session:
        await sync_sources(session)
    yield


app = FastAPI(title="SalesParser", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")
app.include_router(routes_catalog.router)
app.include_router(routes_admin.router)
app.include_router(routes_admin.protected)

if settings.demo_enabled:
    from app.demo.router import router as demo_router
    app.include_router(demo_router)


@app.get("/health", include_in_schema=False)
async def health():
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok"}
