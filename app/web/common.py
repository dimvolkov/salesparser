from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Category, Source

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def money(value: Decimal | float | None) -> str:
    if value is None:
        return "—"
    value = Decimal(value)
    s = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return s[:-3] if s.endswith(",00") else s


def signed(value: Decimal | float | None) -> str:
    if value is None or value == 0:
        return ""
    return ("+" if value > 0 else "−") + money(abs(value))


def localdt(value: datetime | None) -> str:
    return value.astimezone(settings.tz).strftime("%d.%m.%Y %H:%M") if value else ""


def product_meta(p) -> str:
    parts = [p.brand, p.ean and f"EAN {p.ean}", p.sku and f"арт. {p.sku}"]
    return " · ".join(x for x in parts if x)


templates.env.filters["money"] = money
templates.env.filters["localdt"] = localdt
templates.env.filters["product_meta"] = product_meta
templates.env.filters["signed"] = signed


async def load_sources(session: AsyncSession, enabled_only: bool = True) -> list[Source]:
    q = select(Source).order_by(Source.is_master.desc(), Source.position, Source.name)
    if enabled_only:
        q = q.where(Source.enabled)
    return list((await session.scalars(q)).all())


async def category_tree(session: AsyncSession) -> tuple[list[dict], dict[int, dict]]:
    """Дерево единых категорий в виде вложенных словарей + индекс по id."""
    cats = (await session.scalars(select(Category).order_by(Category.position, Category.name))).all()
    nodes = {c.id: {"id": c.id, "name": c.name, "parent_id": c.parent_id, "children": []} for c in cats}
    roots = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"])
        (parent["children"] if parent else roots).append(node)
    return roots, nodes


def descendants(nodes: dict[int, dict], cat_id: int) -> list[int]:
    out, stack = [], [nodes[cat_id]]
    while stack:
        n = stack.pop()
        out.append(n["id"])
        stack.extend(n["children"])
    return out


def flat_tree(roots: list[dict], depth: int = 0) -> list[tuple[int, dict]]:
    out = []
    for n in roots:
        out.append((depth, n))
        out.extend(flat_tree(n["children"], depth + 1))
    return out


def breadcrumbs(nodes: dict[int, dict], cat_id: int | None) -> list[dict]:
    out = []
    while cat_id and cat_id in nodes and len(out) < 50:
        out.append(nodes[cat_id])
        cat_id = nodes[cat_id]["parent_id"]
    return list(reversed(out))
