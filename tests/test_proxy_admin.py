"""Прокси в админке: заведение, выбор у источника, проверка, удаление. Нужен TEST_DATABASE_URL."""
import os

import httpx
import pytest
from sqlalchemy import text

from app import auth

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL не задан")


@pytest.fixture(scope="module")
async def client():
    from app.config import settings
    from app.db import SessionLocal, engine
    from app.main import app
    from app.models import Base, Source

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    settings.admin_login, settings.admin_password = "admin", "admin-pass-123"
    async with SessionLocal() as session:
        await auth.ensure_admin(session)
        session.add(Source(name="shop", title="Магазин", base_url="https://shop.example/", adapter="generic",
                           config={"proxy": "${SCRAPE_PROXY}"}, is_master=True, enabled=True, position=0))
        await session.commit()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"login": "admin", "password": "admin-pass-123", "next": "/"})
        yield c


async def _source():
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Source
    from app.runner import adapter_config
    async with SessionLocal() as session:
        source = await session.scalar(select(Source).where(Source.name == "shop"))
        return source, await adapter_config(session, source)


async def test_create_and_validate(client):
    r = await client.post("/admin/proxies", data={"name": "bad", "url": "ftp://host:21"})
    assert "error=invalid" in r.headers["location"]
    r = await client.post("/admin/proxies", data={"name": "RU-1", "url": "http://user:secret@10.0.0.1:3128"})
    assert r.status_code == 303 and "error" not in r.headers["location"]
    r = await client.post("/admin/proxies", data={"name": "RU-1", "url": "http://10.0.0.2:3128"})
    assert "error=exists" in r.headers["location"]
    page = (await client.get("/admin/proxies")).text
    assert "RU-1" in page and "user:***@10.0.0.1:3128" in page and "secret" not in page


async def test_select_for_source_overrides_yaml(client):
    source, config = await _source()
    assert config["proxy"] == "${SCRAPE_PROXY}"
    assert "напрямую" not in (await client.get("/admin")).text  # подпись «из YAML»
    await client.post(f"/admin/sources/{source.id}/proxy", data={"proxy_id": "1"})
    source, config = await _source()
    assert source.proxy_id == 1 and config["proxy"] == "http://user:secret@10.0.0.1:3128"
    assert "Магазин" in (await client.get("/admin/proxies")).text  # колонка «Источники»


async def test_check_and_update_keep_url(client, monkeypatch):
    async def fake_probe(url):
        assert url == "http://user:secret@10.0.0.1:3128"
        return "RU · 1.2.3.4 · AS12389 Rostelecom"
    monkeypatch.setattr("app.web.routes_admin.probe", fake_probe)
    await client.post("/admin/proxies/1/check")
    assert "AS12389 Rostelecom" in (await client.get("/admin/proxies")).text
    # Пустой адрес в форме — пароль и результат проверки сохраняются
    await client.post("/admin/proxies/1", data={"name": "RU-main", "url": "", "note": "резидентный"})
    page = (await client.get("/admin/proxies")).text
    assert "RU-main" in page and "AS12389" in page
    _, config = await _source()
    assert config["proxy"] == "http://user:secret@10.0.0.1:3128"


async def test_check_failure_is_shown(client, monkeypatch):
    async def broken_probe(url):
        raise httpx.ProxyError("407 Proxy Authentication Required")
    monkeypatch.setattr("app.web.routes_admin.probe", broken_probe)
    await client.post("/admin/proxies/1/check")
    assert "407 Proxy Authentication Required" in (await client.get("/admin/proxies")).text


async def test_delete_falls_back_to_yaml(client):
    await client.post("/admin/proxies/1/delete")
    source, config = await _source()
    assert source.proxy_id is None and config["proxy"] == "${SCRAPE_PROXY}"

