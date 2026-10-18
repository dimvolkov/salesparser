"""Сквозной тест: обход трёх демо-магазинов -> матчинг -> единый каталог. Нужен TEST_DATABASE_URL."""
import os

import pytest
from sqlalchemy import func, select, text

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL не задан")


@pytest.fixture(scope="module")
async def crawled(demo_transport):
    from app import analysis
    from app.db import SessionLocal, engine
    from app.models import Base, Source
    from app.runner import run_source
    from app.sources import sync_sources

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await sync_sources(session, "sources")
        await session.execute(text("UPDATE source SET config = jsonb_set(config, '{delay}', '0')"))
        await session.commit()
        ids = (await session.scalars(select(Source.id).order_by(Source.is_master.desc()))).all()
    for _ in range(2):  # второй прогон в тот же день не должен создавать дубли
        for sid in ids:
            run = await run_source(sid, trigger="test", adapter_kwargs={"transport": demo_transport})
            assert run.status == "ok", run.error_log
    yield SessionLocal, analysis


async def test_matching(crawled):
    SessionLocal, _ = crawled
    from app.models import PriceSnapshot, Product, SourceProduct
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(Product)) == 21
        assert await s.scalar(select(func.count()).select_from(SourceProduct)
                              .where(SourceProduct.product_id.is_(None))) == 1
        assert await s.scalar(select(func.count()).select_from(PriceSnapshot)) == 19 + 19 + 18
        samsung = await s.scalar(select(Product).where(Product.sku == "SMA556B"))
        offers = await s.scalar(select(func.count()).select_from(SourceProduct)
                                .where(SourceProduct.product_id == samsung.id))
        assert offers == 3


async def test_catalog_highlight(crawled):
    SessionLocal, analysis = crawled
    async with SessionLocal() as s:
        rows, total = await analysis.catalog(s, per_page=None)
    assert total == 21
    for row in rows:
        in_stock = [o.price for o in row.offers.values() if o.in_stock]
        if in_stock:
            mins = [o for o in row.offers.values() if o.css == "min"]
            assert mins and all(o.price == min(in_stock) for o in mins)


async def test_manual_category_mapping(crawled):
    SessionLocal, _ = crawled
    from app.catalog import refresh_products
    from app.models import Category, Product, Source, SourceCategory
    async with SessionLocal() as s:
        nothing = await s.scalar(select(Product).where(Product.sku == "A142"))
        assert nothing.category_id is None
        phones = await s.scalar(select(Category).where(Category.name == "Смартфоны"))
        sc = await s.scalar(select(SourceCategory).join(Source)
                            .where(Source.name == "demo-c", SourceCategory.name == "Мобильные телефоны"))
        sc.category_id, sc.mapping_locked = phones.id, True
        await s.flush()
        await refresh_products(s)
        await s.commit()
        await s.refresh(nothing)
        assert nothing.category_id == phones.id
