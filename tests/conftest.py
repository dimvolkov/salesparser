import os

import httpx
import pytest
from fastapi import FastAPI

# Интеграционные тесты работают с отдельной БД (она очищается!) — задаётся через TEST_DATABASE_URL
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["DEMO_BASE_URL"] = "http://demo"
os.environ["DEMO_ENABLED"] = "true"

from app.demo.router import router as demo_router  # noqa: E402


@pytest.fixture(scope="session")
def demo_transport() -> httpx.ASGITransport:
    app = FastAPI()
    app.include_router(demo_router)
    return httpx.ASGITransport(app=app)
