"""Единое дерево категорий: синхронизация с ведущим источником и автомаппинг остальных."""
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Source, SourceCategory
from app.normalize import normalize_name


async def sync_master_categories(session: AsyncSession, source: Source) -> None:
    rows = (await session.scalars(
        select(SourceCategory).where(SourceCategory.source_id == source.id).order_by(SourceCategory.position)
    )).all()
    by_id = {sc.id: sc for sc in rows}

    def depth(sc: SourceCategory) -> int:
        d = 0
        while sc.parent_id and sc.parent_id in by_id and d < 50:
            sc, d = by_id[sc.parent_id], d + 1
        return d

    for sc in sorted(rows, key=depth):
        cat = await session.get(Category, sc.category_id) if sc.category_id else None
        if cat is None:
            cat = Category(name=sc.name)
            session.add(cat)
            await session.flush()
            sc.category_id = cat.id
        cat.name = sc.name
        cat.position = sc.position
        parent = by_id.get(sc.parent_id) if sc.parent_id else None
        cat.parent_id = parent.category_id if parent else None
    await session.flush()


async def automap_categories(session: AsyncSession) -> int:
    """Неведущие категории без ручной привязки маппятся на единую категорию с тем же названием (если она одна)."""
    cats = (await session.scalars(select(Category))).all()
    by_name: dict[str, list[int]] = {}
    for c in cats:
        by_name.setdefault(normalize_name(c.name), []).append(c.id)
    rows = (await session.scalars(
        select(SourceCategory).join(Source, Source.id == SourceCategory.source_id).where(
            Source.is_master.is_(False), SourceCategory.category_id.is_(None),
            SourceCategory.mapping_locked.is_(False),
        )
    )).all()
    mapped = 0
    for sc in rows:
        ids = by_name.get(normalize_name(sc.name), [])
        if len(ids) == 1:
            sc.category_id = ids[0]
            mapped += 1
    await session.flush()
    return mapped


async def refresh_products(session: AsyncSession) -> None:
    """Категория и название единой позиции берутся предпочтительно у ведущего источника."""
    await session.execute(text("""
        UPDATE product p SET category_id = sub.category_id
        FROM (
            SELECT DISTINCT ON (sp.product_id) sp.product_id, sc.category_id
            FROM source_product sp
            JOIN source_category sc ON sc.id = sp.source_category_id
            JOIN source s ON s.id = sp.source_id
            WHERE sp.product_id IS NOT NULL AND sc.category_id IS NOT NULL
            ORDER BY sp.product_id, s.is_master DESC, sp.last_seen_at DESC
        ) sub
        WHERE p.id = sub.product_id AND p.category_id IS DISTINCT FROM sub.category_id
    """))
    await session.execute(text("""
        UPDATE product p SET name = sub.name, brand = COALESCE(sub.brand, p.brand)
        FROM (
            SELECT DISTINCT ON (sp.product_id) sp.product_id, sp.name, sp.brand
            FROM source_product sp JOIN source s ON s.id = sp.source_id
            WHERE sp.product_id IS NOT NULL AND s.is_master
            ORDER BY sp.product_id, sp.last_seen_at DESC
        ) sub
        WHERE p.id = sub.product_id AND (p.name <> sub.name OR p.brand IS DISTINCT FROM COALESCE(sub.brand, p.brand))
    """))
    await session.execute(text("""
        UPDATE product p SET brand = sub.brand
        FROM (
            SELECT DISTINCT ON (product_id) product_id, brand FROM source_product
            WHERE product_id IS NOT NULL AND brand IS NOT NULL ORDER BY product_id, last_seen_at DESC
        ) sub
        WHERE p.id = sub.product_id AND p.brand IS NULL
    """))
