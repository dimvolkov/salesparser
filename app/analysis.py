"""Анализ цен: актуальное предложение каждого источника по позиции, минимум/максимум, разброс, динамика."""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

# Последний снапшот каждого товара источника (не старше stale_days) и предыдущий для динамики
_OFFERS_CTE = """
WITH latest AS (
    SELECT DISTINCT ON (ps.source_product_id) ps.source_product_id, ps.price, ps.old_price,
           ps.in_stock, ps.scraped_on
    FROM price_snapshot ps
    WHERE ps.scraped_on >= :since AND ps.price IS NOT NULL
    ORDER BY ps.source_product_id, ps.scraped_on DESC
),
prev AS (
    SELECT DISTINCT ON (ps.source_product_id) ps.source_product_id, ps.price
    FROM price_snapshot ps JOIN latest l USING (source_product_id)
    WHERE ps.scraped_on < l.scraped_on AND ps.price IS NOT NULL
    ORDER BY ps.source_product_id, ps.scraped_on DESC
),
offers AS (
    SELECT sp.product_id, sp.source_id, sp.id AS sp_id, sp.url, l.price, l.old_price, l.in_stock,
           l.scraped_on, pv.price AS prev_price
    FROM source_product sp
    JOIN latest l ON l.source_product_id = sp.id
    JOIN source s ON s.id = sp.source_id AND s.enabled
    LEFT JOIN prev pv ON pv.source_product_id = sp.id
    WHERE sp.product_id IS NOT NULL
),
agg AS (
    SELECT product_id,
           MIN(price) FILTER (WHERE in_stock) AS min_price,
           MAX(price) FILTER (WHERE in_stock) AS max_price,
           COUNT(DISTINCT source_id) AS sources_count,
           COUNT(DISTINCT source_id) FILTER (WHERE in_stock) AS stock_count
    FROM offers GROUP BY product_id
)
"""

SORTS = {
    "name": "p.name ASC",
    "price": "a.min_price ASC NULLS LAST, p.name",
    "price_desc": "a.min_price DESC NULLS LAST, p.name",
    "spread": "spread DESC NULLS LAST, p.name",
    "sources": "a.sources_count DESC, p.name",
}


@dataclass
class Offer:
    source_id: int
    sp_id: int
    url: str
    price: Decimal
    old_price: Decimal | None
    in_stock: bool
    prev_price: Decimal | None
    css: str = ""

    @property
    def delta(self) -> Decimal | None:
        return self.price - self.prev_price if self.prev_price is not None else None


@dataclass
class Row:
    id: int
    name: str
    brand: str | None
    ean: str | None
    sku: str | None
    category_id: int | None
    min_price: Decimal | None
    max_price: Decimal | None
    sources_count: int
    spread: Decimal | None
    offers: dict[int, Offer] = field(default_factory=dict)
    min_delta: Decimal | None = None


def classify(row: Row) -> None:
    """Подсветка: минимум среди источников в наличии — min (все при равенстве), максимум — max, нет в наличии — oos."""
    for o in row.offers.values():
        if not o.in_stock:
            o.css = "oos"
        elif row.min_price is not None and o.price == row.min_price:
            o.css = "min"
        elif row.max_price is not None and o.price == row.max_price and row.max_price > row.min_price:
            o.css = "max"
    prev = [o.prev_price if o.prev_price is not None else o.price for o in row.offers.values() if o.in_stock]
    if row.min_price is not None and prev and any(o.prev_price is not None for o in row.offers.values()):
        row.min_delta = row.min_price - min(prev)


def _since():
    return settings.today() - timedelta(days=settings.stale_days)


async def catalog(session: AsyncSession, *, category_ids: list[int] | None = None, uncategorized: bool = False,
                  search: str | None = None, only_diff: bool = False, only_stock: bool = False,
                  sort: str = "name", page: int = 1, per_page: int = 50,
                  product_ids: list[int] | None = None) -> tuple[list[Row], int]:
    where, params = [], {"since": _since()}
    if uncategorized:
        where.append("p.category_id IS NULL")
    elif category_ids:
        where.append("p.category_id = ANY(:cats)")
        params["cats"] = category_ids
    if product_ids is not None:
        where.append("p.id = ANY(:pids)")
        params["pids"] = product_ids
    if search:
        where.append("(p.name ILIKE :q OR p.brand ILIKE :q OR p.ean = :qe OR p.sku ILIKE :q)")
        params["q"], params["qe"] = f"%{search}%", search.strip()
    if only_diff:
        where.append("a.max_price > a.min_price")
    if only_stock:
        where.append("a.stock_count > 0")
    limit = ""
    if per_page:
        limit = "LIMIT :limit OFFSET :offset"
        params["limit"], params["offset"] = per_page, (max(page, 1) - 1) * per_page
    sql = _OFFERS_CTE + f"""
        SELECT p.id, p.name, p.brand, p.ean, p.sku, p.category_id, a.min_price, a.max_price, a.sources_count,
               CASE WHEN a.min_price > 0 THEN ROUND((a.max_price - a.min_price) / a.min_price * 100, 1) END AS spread,
               COUNT(*) OVER () AS total
        FROM product p JOIN agg a ON a.product_id = p.id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY {SORTS.get(sort, SORTS["name"])}
        {limit}
    """
    result = (await session.execute(text(sql), params)).mappings().all()
    total = result[0]["total"] if result else 0
    rows = {r["id"]: Row(**{k: v for k, v in r.items() if k != "total"}) for r in result}
    if rows:
        offers = (await session.execute(
            text(_OFFERS_CTE + "SELECT * FROM offers WHERE product_id = ANY(:ids) ORDER BY price"),
            {"since": params["since"], "ids": list(rows)},
        )).mappings().all()
        for o in offers:
            row = rows[o["product_id"]]
            current = row.offers.get(o["source_id"])
            # Если у источника несколько карточек одной позиции — берём лучшую (в наличии, дешевле)
            if current is None or (o["in_stock"] and not current.in_stock):
                row.offers[o["source_id"]] = Offer(o["source_id"], o["sp_id"], o["url"], o["price"], o["old_price"],
                                                   o["in_stock"], o["prev_price"])
        for row in rows.values():
            classify(row)
    return list(rows.values()), total


async def price_history(session: AsyncSession, product_id: int, days: int = 90) -> dict[int, list[tuple]]:
    rows = (await session.execute(text("""
        SELECT sp.source_id, ps.scraped_on, MIN(ps.price) AS price
        FROM price_snapshot ps JOIN source_product sp ON sp.id = ps.source_product_id
        WHERE sp.product_id = :pid AND ps.scraped_on >= :since AND ps.price IS NOT NULL
        GROUP BY sp.source_id, ps.scraped_on ORDER BY ps.scraped_on
    """), {"pid": product_id, "since": settings.today() - timedelta(days=days)})).all()
    history: dict[int, list[tuple]] = {}
    for source_id, day, price in rows:
        history.setdefault(source_id, []).append((day.isoformat(), float(price)))
    return history


async def source_summary(session: AsyncSession) -> dict[int, dict]:
    """По каждому источнику: сколько позиций, в скольких он самый дешёвый."""
    rows = (await session.execute(text(_OFFERS_CTE + """
        SELECT o.source_id, COUNT(DISTINCT o.product_id) AS products,
               COUNT(DISTINCT o.product_id) FILTER (WHERE o.in_stock AND o.price = a.min_price) AS cheapest
        FROM offers o JOIN agg a USING (product_id) GROUP BY o.source_id
    """), {"since": _since()})).mappings().all()
    return {r["source_id"]: dict(r) for r in rows}
