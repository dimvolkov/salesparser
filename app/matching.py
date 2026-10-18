"""Сопоставление товаров источников с единой позицией по EAN, затем по нормализованному артикулу."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, SourceProduct


async def match_products(session: AsyncSession) -> tuple[int, int]:
    """Возвращает (сопоставлено, осталось без ключа)."""
    products = (await session.scalars(select(Product))).all()
    by_ean = {p.ean: p for p in products if p.ean}
    by_sku = {p.sku: p for p in products if p.sku}

    pending = (await session.scalars(
        select(SourceProduct).where(SourceProduct.product_id.is_(None), SourceProduct.match_locked.is_(False))
        .order_by(SourceProduct.id)
    )).all()
    matched = unmatched = 0
    for sp in pending:
        product = (by_ean.get(sp.ean) if sp.ean else None) or (by_sku.get(sp.sku) if sp.sku else None)
        if product is None:
            if not (sp.ean or sp.sku):
                unmatched += 1
                continue
            product = Product(name=sp.name, brand=sp.brand, ean=sp.ean, sku=sp.sku)
            session.add(product)
            await session.flush()
        else:
            # Дополняем позицию ключами, которых у неё ещё нет — чтобы находить её и по ним
            if sp.ean and not product.ean and sp.ean not in by_ean:
                product.ean = sp.ean
            if sp.sku and not product.sku and sp.sku not in by_sku:
                product.sku = sp.sku
        if product.ean:
            by_ean[product.ean] = product
        if product.sku:
            by_sku[product.sku] = product
        sp.product_id = product.id
        matched += 1
    await session.flush()
    return matched, unmatched
