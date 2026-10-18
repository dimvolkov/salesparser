"""Вход, выход и личный профиль."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.auth import Principal
from app.db import get_session
from app.models import User
from app.web.common import templates

router = APIRouter()


def _safe_next(target: str) -> str:
    return target if target.startswith("/") and not target.startswith("//") else "/"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/", error: bool = False):
    if await auth.identify(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": error})


@router.post("/login")
async def login(request: Request, login: str = Form(...), password: str = Form(...), next: str = Form("/"),
                session: AsyncSession = Depends(get_session)):
    auth.check_csrf(request)
    user = await auth.authenticate(session, login, password)
    if not user:
        return RedirectResponse(f"/login?error=1&next={next}", status_code=303)
    resp = RedirectResponse(_safe_next(next), status_code=303)
    auth.set_session_cookie(resp, request, user.id)
    return resp


@router.post("/logout")
async def logout(request: Request):
    auth.check_csrf(request)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, saved: bool = False, error: str = "",
                  who: Principal = Depends(auth.viewer)):
    return templates.TemplateResponse(request, "profile.html", {"saved": saved, "error": error})


@router.post("/profile")
async def update_profile(full_name: str = Form(""), current_password: str = Form(""), password: str = Form(""),
                         who: Principal = Depends(auth.viewer), session: AsyncSession = Depends(get_session)):
    user = await session.get(User, who.id)
    user.full_name = full_name.strip()
    if password:
        if len(password) < 8:
            return RedirectResponse("/profile?error=short", status_code=303)
        if not auth.verify_password(current_password, user.password_hash):
            return RedirectResponse("/profile?error=current", status_code=303)
        user.password_hash = auth.hash_password(password)
    await session.commit()
    return RedirectResponse("/profile?saved=1", status_code=303)
