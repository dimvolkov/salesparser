from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.auth import Principal
from app.catalog import refresh_products
from app.config import settings
from app.db import get_session
from app.models import Product, Proxy, ScrapeRun, Source, SourceCategory, SourceProduct, User
from app.normalize import normalize_ean, normalize_sku
from app.scraping.proxy import mask, probe, resolve_proxy
from app.web.common import category_tree, flat_tree, load_sources, templates

router = APIRouter(prefix="/admin", dependencies=[Depends(auth.manager)])


def back(request: Request, default: str) -> RedirectResponse:
    target = request.headers.get("referer") or default
    return RedirectResponse(target, status_code=303)


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    sources = await load_sources(session, enabled_only=False)
    last_runs = {}
    for s in sources:
        last_runs[s.id] = await session.scalar(
            select(ScrapeRun).where(ScrapeRun.source_id == s.id).order_by(ScrapeRun.id.desc()).limit(1))
    counts = dict((await session.execute(
        select(SourceProduct.source_id, func.count()).group_by(SourceProduct.source_id))).all())
    unmapped = dict((await session.execute(
        select(SourceCategory.source_id, func.count()).where(SourceCategory.category_id.is_(None))
        .group_by(SourceCategory.source_id))).all())
    unmatched = await session.scalar(select(func.count()).select_from(SourceProduct)
                                     .where(SourceProduct.product_id.is_(None)))
    products = await session.scalar(select(func.count()).select_from(Product))
    proxies = (await session.scalars(select(Proxy).order_by(Proxy.name))).all()
    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "sources": sources, "last_runs": last_runs, "counts": counts, "unmapped": unmapped,
        "unmatched": unmatched, "products": products, "settings": settings, "proxies": proxies,
    })


@router.post("/sources/{source_id}/run")
async def queue_run(request: Request, source_id: int, session: AsyncSession = Depends(get_session)):
    await _queue(session, [source_id])
    return back(request, "/admin")


@router.post("/run-all")
async def queue_all(request: Request, session: AsyncSession = Depends(get_session)):
    ids = (await session.scalars(select(Source.id).where(Source.enabled))).all()
    await _queue(session, list(ids))
    return back(request, "/admin")


async def _queue(session: AsyncSession, ids: list[int]):
    busy = set((await session.scalars(select(ScrapeRun.source_id)
                                      .where(ScrapeRun.status.in_(["queued", "running"])))).all())
    for sid in ids:
        if sid not in busy:
            session.add(ScrapeRun(source_id=sid, status="queued", trigger="manual"))
    await session.commit()


@router.post("/sources/{source_id}/toggle")
async def toggle(request: Request, source_id: int, session: AsyncSession = Depends(get_session)):
    source = await session.get(Source, source_id)
    if source:
        source.enabled = not source.enabled
        await session.commit()
    return back(request, "/admin")


@router.post("/sources/{source_id}/proxy")
async def set_source_proxy(request: Request, source_id: int, proxy_id: str = Form(""),
                           session: AsyncSession = Depends(get_session)):
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(404)
    if proxy_id.isdigit() and await session.get(Proxy, int(proxy_id)) is None:
        raise HTTPException(400, "Прокси не найден")
    source.proxy_id = int(proxy_id) if proxy_id.isdigit() else None
    await session.commit()
    return back(request, "/admin")


# ── Прокси ─────────────────────────────────────────────────────────────

@router.get("/proxies", response_class=HTMLResponse)
async def proxies(request: Request, error: str = "", session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Proxy).order_by(Proxy.name))).all()
    used = {}
    for source in await load_sources(session, enabled_only=False):
        if source.proxy_id:
            used.setdefault(source.proxy_id, []).append(source.title)
    return templates.TemplateResponse(request, "admin/proxies.html", {
        "rows": rows, "used": used, "error": error, "mask": mask})


def _valid_proxy_url(url: str) -> bool:
    try:
        resolve_proxy(url)
    except ValueError:
        return False
    # "${ПЕРЕМЕННАЯ}" допустима, даже если в этом процессе она не задана
    return bool(url) and (url.startswith("${") or "://" in url)


@router.post("/proxies")
async def create_proxy(name: str = Form(""), url: str = Form(""), note: str = Form(""),
                       session: AsyncSession = Depends(get_session)):
    name, url = name.strip(), url.strip()
    if not name or not _valid_proxy_url(url):
        return RedirectResponse("/admin/proxies?error=invalid", status_code=303)
    if await session.scalar(select(Proxy.id).where(Proxy.name == name)):
        return RedirectResponse("/admin/proxies?error=exists", status_code=303)
    session.add(Proxy(name=name, url=url, note=note.strip()))
    await session.commit()
    return RedirectResponse("/admin/proxies", status_code=303)


@router.post("/proxies/{proxy_id}")
async def update_proxy(proxy_id: int, name: str = Form(""), url: str = Form(""), note: str = Form(""),
                       session: AsyncSession = Depends(get_session)):
    proxy = await session.get(Proxy, proxy_id)
    if proxy is None:
        raise HTTPException(404)
    name, url = name.strip(), url.strip()
    if not name or (url and not _valid_proxy_url(url)):
        return RedirectResponse("/admin/proxies?error=invalid", status_code=303)
    if await session.scalar(select(Proxy.id).where(Proxy.name == name, Proxy.id != proxy_id)):
        return RedirectResponse("/admin/proxies?error=exists", status_code=303)
    # Пустой адрес — оставить прежний: в форме он показывается без пароля
    if url and url != proxy.url:
        proxy.url, proxy.checked_at, proxy.check_ok, proxy.check_result = url, None, None, None
    proxy.name, proxy.note = name, note.strip()
    await session.commit()
    return RedirectResponse("/admin/proxies", status_code=303)


@router.post("/proxies/{proxy_id}/delete")
async def delete_proxy(proxy_id: int, session: AsyncSession = Depends(get_session)):
    proxy = await session.get(Proxy, proxy_id)
    if proxy:
        # Источники с этим прокси переходят на config.proxy из YAML (или напрямую)
        await session.delete(proxy)
        await session.commit()
    return RedirectResponse("/admin/proxies", status_code=303)


@router.post("/proxies/{proxy_id}/check")
async def check_proxy(proxy_id: int, session: AsyncSession = Depends(get_session)):
    proxy = await session.get(Proxy, proxy_id)
    if proxy is None:
        raise HTTPException(404)
    try:
        url = resolve_proxy(proxy.url)
        if url is None:
            raise ValueError("переменная окружения не задана")
        proxy.check_result, proxy.check_ok = await probe(url), True
    except Exception as e:
        proxy.check_result, proxy.check_ok = (f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)[:500], False
    proxy.checked_at = func.now()
    await session.commit()
    return RedirectResponse("/admin/proxies", status_code=303)


@router.get("/runs", response_class=HTMLResponse)
async def runs(request: Request, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(ScrapeRun, Source.title).join(Source).order_by(ScrapeRun.id.desc()).limit(100))).all()
    return templates.TemplateResponse(request, "admin/runs.html", {"rows": rows})


@router.get("/categories", response_class=HTMLResponse)
async def categories(request: Request, source: int | None = None, only_unmapped: bool = False,
                     session: AsyncSession = Depends(get_session)):
    sources = [s for s in await load_sources(session, enabled_only=False) if not s.is_master]
    current = next((s for s in sources if s.id == source), sources[0] if sources else None)
    roots, _ = await category_tree(session)
    rows = []
    if current:
        q = select(SourceCategory).where(SourceCategory.source_id == current.id).order_by(SourceCategory.position)
        if only_unmapped:
            q = q.where(SourceCategory.category_id.is_(None))
        rows = (await session.scalars(q)).all()
    by_id = {r.id: r for r in rows}
    return templates.TemplateResponse(request, "admin/categories.html", {
        "sources": sources, "current": current, "rows": rows, "by_id": by_id,
        "options": flat_tree(roots), "only_unmapped": only_unmapped,
    })


@router.post("/categories/{sc_id}")
async def map_category(request: Request, sc_id: int, category_id: str = Form(""),
                       session: AsyncSession = Depends(get_session)):
    sc = await session.get(SourceCategory, sc_id)
    if sc is None:
        raise HTTPException(404)
    if (await session.get(Source, sc.source_id)).is_master:
        raise HTTPException(400, "Категории ведущего источника и есть единое дерево")
    sc.category_id = int(category_id) if category_id.isdigit() else None
    sc.mapping_locked = True
    await session.flush()
    await refresh_products(session)
    await session.commit()
    return back(request, "/admin/categories")


@router.get("/unmatched", response_class=HTMLResponse)
async def unmatched(request: Request, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(SourceProduct, Source.title).join(Source).where(SourceProduct.product_id.is_(None))
        .order_by(Source.title, SourceProduct.name).limit(300))).all()
    candidates = {}
    for sp, _ in rows:
        candidates[sp.id] = (await session.execute(text("""
            SELECT id, name, similarity(name, :n) AS score FROM product
            WHERE similarity(name, :n) > 0.2 ORDER BY score DESC LIMIT 3
        """), {"n": sp.name})).mappings().all()
    return templates.TemplateResponse(request, "admin/unmatched.html", {"rows": rows, "candidates": candidates})


@router.post("/unmatched/{sp_id}/link")
async def link_product(request: Request, sp_id: int, product_id: str = Form(""),
                       session: AsyncSession = Depends(get_session)):
    sp = await session.get(SourceProduct, sp_id)
    if sp is None:
        raise HTTPException(404)
    if product_id.strip().isdigit():
        product = await session.get(Product, int(product_id))
    else:
        product = None
    if product is None:
        raise HTTPException(400, "Позиция с таким ID не найдена")
    sp.product_id, sp.match_locked = product.id, True
    await session.flush()
    await refresh_products(session)
    await session.commit()
    return back(request, "/admin/unmatched")


@router.post("/unmatched/{sp_id}/create")
async def create_product(request: Request, sp_id: int, session: AsyncSession = Depends(get_session)):
    sp = await session.get(SourceProduct, sp_id)
    if sp is None:
        raise HTTPException(404)
    if sp.product_id is not None:
        raise HTTPException(400, "Товар уже сопоставлен — сначала отвяжите его")
    ean, sku = normalize_ean(sp.ean), normalize_sku(sp.sku)
    taken_ean = ean and await session.scalar(select(Product.id).where(Product.ean == ean))
    taken_sku = sku and await session.scalar(select(Product.id).where(Product.sku == sku))
    product = Product(name=sp.name, brand=sp.brand, ean=None if taken_ean else ean, sku=None if taken_sku else sku)
    session.add(product)
    await session.flush()
    sp.product_id, sp.match_locked = product.id, True
    await session.flush()
    await refresh_products(session)
    await session.commit()
    return back(request, "/admin/unmatched")


@router.post("/source-products/{sp_id}/unlink")
async def unlink(request: Request, sp_id: int, session: AsyncSession = Depends(get_session)):
    """Отвязать ошибочно сопоставленный товар источника: он уйдёт в «Несопоставленные»."""
    sp = await session.get(SourceProduct, sp_id)
    if sp:
        sp.product_id, sp.match_locked = None, True
        await session.commit()
    return back(request, "/admin/unmatched")


# ── Пользователи (только администратор) ───────────────────────────────

@router.get("/users", response_class=HTMLResponse)
async def users(request: Request, error: str = "", who: Principal = Depends(auth.admin),
                session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(User).order_by(User.created_at))).all()
    return templates.TemplateResponse(request, "admin/users.html", {"rows": rows, "roles": auth.ROLES,
                                                                     "error": error})


@router.post("/users")
async def create_user(login: str = Form(""), password: str = Form(""), full_name: str = Form(""),
                      role: str = Form("viewer"), who: Principal = Depends(auth.admin),
                      session: AsyncSession = Depends(get_session)):
    login = login.strip().lower()
    if not login or len(password) < 8:
        return RedirectResponse("/admin/users?error=short", status_code=303)
    if role not in auth.ROLES:
        raise HTTPException(400, "Неизвестная роль")
    if await session.scalar(select(User.id).where(User.login == login)):
        return RedirectResponse("/admin/users?error=exists", status_code=303)
    session.add(User(login=login, password_hash=auth.hash_password(password), full_name=full_name.strip(),
                     role=role))
    await session.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}")
async def update_user(user_id: int, role: str = Form(...), disabled: bool = Form(False),
                      full_name: str = Form(""), password: str = Form(""),
                      who: Principal = Depends(auth.admin), session: AsyncSession = Depends(get_session)):
    if role not in auth.ROLES:
        raise HTTPException(400, "Неизвестная роль")
    if user_id == who.id and (disabled or role != "admin"):
        return RedirectResponse("/admin/users?error=self", status_code=303)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404)
    if password:
        if len(password) < 8:
            return RedirectResponse("/admin/users?error=short", status_code=303)
        user.password_hash = auth.hash_password(password)
    user.role, user.disabled, user.full_name = role, disabled, full_name.strip()
    await session.commit()
    return RedirectResponse("/admin/users", status_code=303)
