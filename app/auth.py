"""Кто спрашивает: сотрудник по сессии.

Порт app/auth.py из icebreaker: пароли scrypt, сессия — подписанная HMAC
HttpOnly-cookie, пользователь перечитывается из базы на каждом запросе,
запись по cookie — только со своей страницы (проверка Origin).
"""
import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import User

SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LEN = 16384, 8, 1, 64
COOKIE = "sp_session"
ROLES = {"admin": "Администратор", "manager": "Менеджер", "viewer": "Наблюдатель"}


# --- пароли ---

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                             n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN)
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Чужой формат и пустая строка — это «не подошёл»."""
    if not password or not stored or not stored.startswith("scrypt$"):
        return False
    parts = stored.split("$")
    if len(parts) != 3:
        return False
    try:
        salt, expected = bytes.fromhex(parts[1]), bytes.fromhex(parts[2])
    except ValueError:
        return False
    actual = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=len(expected))
    return hmac.compare_digest(expected, actual)


# --- сессия: base64url(json).HMAC-SHA256 ---

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    key = hashlib.sha256(("salesparser-session|" + settings.secret_key).encode()).digest()
    return _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def make_session(user_id: int, now: float | None = None) -> str:
    payload = {"uid": str(user_id), "exp": int((now or time.time()) + settings.session_ttl_s)}
    body = _b64(json.dumps(payload).encode())
    return f"{body}.{_sign(body)}"


def read_session(value: str, now: float | None = None) -> str | None:
    """id пользователя из cookie или None, если подпись чужая или срок вышел."""
    if not value or value.count(".") != 1:
        return None
    body, sig = value.split(".")
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        payload = json.loads(_unb64(body))
    except ValueError:
        return None
    if int(payload.get("exp", 0)) < (now or time.time()):
        return None
    return payload.get("uid")


def set_session_cookie(response, request: Request, user_id: int) -> None:
    response.set_cookie(COOKIE, make_session(user_id), max_age=settings.session_ttl_s,
                        httponly=True, samesite="lax", secure=request.url.scheme == "https")


# --- кто спрашивает ---

@dataclass
class Principal:
    id: int
    login: str
    name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def can_edit(self) -> bool:
        return self.role in ("admin", "manager")

    @property
    def role_title(self) -> str:
        return ROLES.get(self.role, self.role)


async def identify(request: Request) -> Principal | None:
    if hasattr(request.state, "user"):
        return request.state.user
    who = None
    uid = read_session(request.cookies.get(COOKIE, ""))
    if uid and uid.isdigit():
        async with SessionLocal() as session:
            user = await session.get(User, int(uid))
        if user and not user.disabled:
            who = Principal(user.id, user.login, user.full_name or user.login, user.role)
    request.state.user = who
    return who


def check_csrf(request: Request) -> None:
    """Запись по cookie — только со своей страницы.

    SameSite=Lax уже не пускает чужие POST с cookie, но формы и fetch со
    сторонних сайтов ловим ещё и по Origin: он ставится браузером, подделать
    его из страницы нельзя.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin")
    if not origin:
        return
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    if origin.split("://", 1)[-1].rstrip("/") != host:
        raise HTTPException(403, "Запрос с чужой страницы")


async def viewer(request: Request) -> Principal:
    """Любой вошедший сотрудник; неавторизованных отправляем на вход."""
    who = await identify(request)
    if not who:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise HTTPException(303, headers={"Location": f"/login?next={quote(target)}"})
    check_csrf(request)
    return who


async def manager(who: Principal = Depends(viewer)) -> Principal:
    if not who.can_edit:
        raise HTTPException(403, "Нужны права менеджера")
    return who


async def admin(who: Principal = Depends(viewer)) -> Principal:
    if not who.is_admin:
        raise HTTPException(403, "Нужны права администратора")
    return who


async def ensure_admin(session: AsyncSession) -> None:
    """Пустая таблица пользователей — заводим администратора из окружения."""
    if await session.scalar(select(func.count()).select_from(User)) or not settings.admin_password:
        return
    session.add(User(login=settings.admin_login.lower(), password_hash=hash_password(settings.admin_password),
                     full_name="Администратор", role="admin"))
    await session.commit()


async def authenticate(session: AsyncSession, login: str, password: str) -> User | None:
    login = (login or "").strip().lower()
    user = await session.scalar(select(User).where(User.login == login))
    if user and not user.disabled and verify_password(password, user.password_hash):
        user.last_login_at = func.now()
        await session.commit()
        return user
    # Аварийный вход из окружения работает, даже если пароль в базе забыт.
    if (settings.admin_password and login == settings.admin_login.lower()
            and hmac.compare_digest(password.encode(), settings.admin_password.encode())):
        if user is None:
            user = User(login=login, password_hash=hash_password(password), full_name="Администратор")
            session.add(user)
        user.role, user.disabled, user.last_login_at = "admin", False, func.now()
        await session.commit()
        return user
    return None
