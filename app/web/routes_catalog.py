import csv
import io
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis, auth
from app.db import get_session
from app.models import Product, ScrapeRun
from app.web.common import breadcrumbs, category_tree, descendants, load_sources, money, templates

router = APIRouter(dependencies=[Depends(auth.viewer)])
PER_PAGE = 50


def _filters(request: Request) -> dict:
    q = request.query_params
    cat = q.get("cat", "")
    return {
        "cat": cat,
        "q": q.get("q", "").strip(),
        "diff": q.get("diff") == "1",
        "stock": q.get("stock") == "1",
        "sort": q.get("sort", "name") if q.get("sort") in analysis.SORTS else "name",
        "page": max(1, int(q.get("page", "1"))) if q.get("page", "1").isdigit() else 1,
    }


async def _query(session: AsyncSession, f: dict, nodes: dict, per_page: int | None):
    cat_ids, uncategorized = None, False
    if f["cat"] == "none":
        uncategorized = True
    elif f["cat"].isdigit() and int(f["cat"]) in nodes:
        cat_ids = descendants(nodes, int(f["cat"]))
    return await analysis.catalog(session, category_ids=cat_ids, uncategorized=uncategorized, search=f["q"],
                                  only_diff=f["diff"], only_stock=f["stock"], sort=f["sort"],
                                  page=f["page"], per_page=per_page)


@router.get("/", response_class=HTMLResponse)
async def catalog_page(request: Request, session: AsyncSession = Depends(get_session)):
    f = _filters(request)
    roots, nodes = await category_tree(session)
    sources = await load_sources(session)
    rows, total = await _query(session, f, nodes, PER_PAGE)
    summary = await analysis.source_summary(session)
    last_run = await session.scalar(select(func.max(ScrapeRun.finished_at)).where(ScrapeRun.status != "failed"))
    current = nodes.get(int(f["cat"])) if f["cat"].isdigit() else None
    return templates.TemplateResponse(request, "catalog.html", {
        "f": f, "roots": roots, "sources": sources, "rows": rows, "total": total,
        "pages": max(1, ceil(total / PER_PAGE)), "summary": summary, "last_run": last_run,
        "current": current, "crumbs": breadcrumbs(nodes, current and current["id"]),
    })


@router.get("/export.csv")
async def export_csv(request: Request, session: AsyncSession = Depends(get_session)):
    f = _filters(request)
    roots, nodes = await category_tree(session)
    sources = await load_sources(session)
    rows, _ = await _query(session, f, nodes, None)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["ID", "Наименование", "Бренд", "EAN", "Артикул", "Категория", "Мин. цена", "Макс. цена",
                "Разброс, %", "Лучший источник"] + [s.title for s in sources])
    titles = {s.id: s.title for s in sources}
    for r in rows:
        best = ", ".join(titles[o.source_id] for o in r.offers.values() if o.css == "min")
        cells = []
        for s in sources:
            o = r.offers.get(s.id)
            cells.append("" if o is None else money(o.price) + ("" if o.in_stock else " (нет)"))
        w.writerow([r.id, r.name, r.brand or "", r.ean or "", r.sku or "",
                    nodes[r.category_id]["name"] if r.category_id in nodes else "",
                    money(r.min_price), money(r.max_price), r.spread if r.spread is not None else "", best] + cells)
    data = "﻿" + buf.getvalue()  # BOM — чтобы Excel открыл UTF-8 корректно
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="catalog.csv"'})


@router.get("/product/{product_id}", response_class=HTMLResponse)
async def product_page(request: Request, product_id: int, session: AsyncSession = Depends(get_session)):
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(404)
    _, nodes = await category_tree(session)
    sources = await load_sources(session)
    rows, _ = await analysis.catalog(session, product_ids=[product_id], per_page=None)
    history = await analysis.price_history(session, product_id)
    titles = {s.id: s.title for s in sources}
    series = [{"label": titles[sid], "data": [{"x": d, "y": p} for d, p in points]}
              for sid, points in history.items() if sid in titles]
    return templates.TemplateResponse(request, "product.html", {
        "product": product, "row": rows[0] if rows else None, "sources": sources, "series": series,
        "crumbs": breadcrumbs(nodes, product.category_id), "can_edit": request.state.user.can_edit,
    })
