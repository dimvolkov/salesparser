"""Три демо-магазина с разной вёрсткой: для проверки конвейера без реальных сайтов."""
from datetime import date
from math import ceil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.demo.data import PRODUCTS, SHOPS, ean, offer

router = APIRouter(prefix="/demo", include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
PER_PAGE = 2


def _fmt(shop: str, price: int) -> str:
    if shop == "a":
        return f"{price:,}".replace(",", " ") + " ₽"
    if shop == "b":
        return f"{price}.00"
    return f"{price:,}".replace(",", ".") + ",00 ₽"


def _shop(shop: str) -> dict:
    if shop not in SHOPS:
        raise HTTPException(404)
    return SHOPS[shop]


def _items(shop: str, cat_key: str | None, day: date) -> list[dict]:
    items = []
    for p in PRODUCTS:
        if shop in p.shops and p.cat == cat_key:
            price, old, in_stock = offer(shop, p, day)
            items.append({
                "p": p, "ean": ean(p.id) if p.has_ean else "", "price": _fmt(shop, price),
                "price_raw": price, "old": _fmt(shop, old) if old else None, "in_stock": in_stock,
                "sku_c": p.sku.lower().replace("-", " "),
            })
    return items


@router.get("/{shop}/", response_class=HTMLResponse)
async def home(request: Request, shop: str):
    data = _shop(shop)
    roots = [t for t in data["tree"] if t[2] is None]
    return templates.TemplateResponse(request, f"shop_{shop}.html", {"shop": shop, "data": data,
                                                                     "view": "home", "roots": roots})


@router.get("/{shop}/c/{slug}", response_class=HTMLResponse)
async def category(request: Request, shop: str, slug: str, page: int = 1):
    data = _shop(shop)
    node = next((t for t in data["tree"] if t[0] == slug), None)
    if node is None:
        raise HTTPException(404)
    children = [t for t in data["tree"] if t[2] == slug]
    items = _items(shop, node[3], settings.today()) if node[3] else []
    pages = max(1, ceil(len(items) / PER_PAGE))
    return templates.TemplateResponse(request, f"shop_{shop}.html", {
        "shop": shop, "data": data, "view": "category", "node": node, "children": children,
        "items": items[(page - 1) * PER_PAGE: page * PER_PAGE], "page": page, "pages": pages,
    })


@router.get("/{shop}/p/{pid}", response_class=HTMLResponse)
async def product(request: Request, shop: str, pid: int):
    data = _shop(shop)
    p = next((p for p in PRODUCTS if p.id == pid and shop in p.shops), None)
    if p is None:
        raise HTTPException(404)
    item = next(i for i in _items(shop, p.cat, settings.today()) if i["p"].id == pid)
    return templates.TemplateResponse(request, f"shop_{shop}.html", {"shop": shop, "data": data,
                                                                     "view": "product", "item": item})
