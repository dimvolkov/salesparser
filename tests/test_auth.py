import os
import time

import httpx
import pytest
from sqlalchemy import text

from app import auth

# --- без базы ---


def test_password_hash():
    stored = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", stored)
    assert not auth.verify_password("wrong", stored)
    assert not auth.verify_password("x", "plain-text")


def test_session_signature_and_expiry():
    token = auth.make_session(42)
    assert auth.read_session(token) == "42"
    body, sig = token.split(".")
    assert auth.read_session(f"{body}.{sig[:-2]}xx") is None
    assert auth.read_session(token, now=time.time() + auth.settings.session_ttl_s + 10) is None


# --- вход и права через приложение (нужен TEST_DATABASE_URL) ---

db = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL не задан")


@pytest.fixture(scope="module")
async def client():
    from app.config import settings
    from app.db import SessionLocal, engine
    from app.main import app
    from app.models import Base, User

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    settings.admin_login, settings.admin_password = "admin", "admin-pass-123"
    async with SessionLocal() as session:
        await auth.ensure_admin(session)
        session.add(User(login="viewer", password_hash=auth.hash_password("viewer-pass"), role="viewer"))
        await session.commit()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def login(c, user, password):
    c.cookies.clear()
    return await c.post("/login", data={"login": user, "password": password, "next": "/"})


@db
async def test_anonymous_redirected(client):
    client.cookies.clear()
    r = await client.get("/?cat=1")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/%3Fcat%3D1"
    assert (await client.get("/admin")).status_code == 303
    assert (await client.get("/login")).status_code == 200
    assert (await client.get("/demo/a/")).status_code == 200  # демо-магазины — для worker, без входа


@db
async def test_wrong_password(client):
    r = await login(client, "admin", "nope")
    assert r.status_code == 303 and "error=1" in r.headers["location"]
    assert auth.COOKIE not in client.cookies


@db
async def test_viewer_sees_catalog_but_not_admin(client):
    r = await login(client, "viewer", "viewer-pass")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/admin", headers={"accept": "text/html"})).status_code == 403
    assert (await client.post("/admin/run-all")).status_code == 403


@db
async def test_admin_manages_users_and_csrf(client):
    await login(client, "admin", "admin-pass-123")
    assert (await client.get("/admin/users")).status_code == 200
    r = await client.post("/admin/users", data={"login": "Manager", "password": "manager-pass", "role": "manager"})
    assert r.status_code == 303 and "error" not in r.headers["location"]
    # POST со стороннего сайта отклоняется даже с действующей сессией
    r = await client.post("/admin/users", data={"login": "evil", "password": "evil-pass-1"},
                          headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    # Себя разжаловать нельзя
    r = await client.post("/admin/users/1", data={"role": "viewer"})
    assert "error=self" in r.headers["location"]
    r = await login(client, "manager", "manager-pass")
    assert r.status_code == 303 and (await client.get("/admin")).status_code == 200
    assert (await client.get("/admin/users")).status_code == 403


@db
async def test_disabled_user_and_emergency_login(client):
    await login(client, "admin", "admin-pass-123")
    await client.post("/admin/users/2", data={"role": "viewer", "disabled": "true"})
    assert "error=1" in (await login(client, "viewer", "viewer-pass")).headers["location"]
    # Аварийный вход из окружения работает, даже если пароль в базе другой
    from app.db import SessionLocal
    async with SessionLocal() as s:
        await s.execute(text("UPDATE users SET password_hash = 'scrypt$00$00' WHERE login = 'admin'"))
        await s.commit()
    r = await login(client, "admin", "admin-pass-123")
    assert r.headers["location"] == "/"
