"""Обход одного источника и запись результатов."""
import logging
import traceback
from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import automap_categories, refresh_products, sync_master_categories
from app.config import settings
from app.db import SessionLocal
from app.matching import match_products
from app.models import PriceSnapshot, Proxy, ScrapeRun, Source, SourceCategory, SourceProduct
from app.normalize import normalize_ean, normalize_sku
from app.scraping.base import CategoryItem, ProductItem
from app.scraping.proxy import mask
from app.scraping.registry import build_adapter

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_categories(session, source_id: int, items: list[CategoryItem]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for position, item in enumerate(items):
        stmt = insert(SourceCategory).values(
            source_id=source_id, external_id=item.external_id, name=item.name[:300], url=item.url,
            position=position, last_seen_at=_now(),
        ).on_conflict_do_update(
            index_elements=[SourceCategory.source_id, SourceCategory.external_id],
            set_={"name": item.name[:300], "url": item.url, "position": position, "last_seen_at": _now()},
        ).returning(SourceCategory.id)
        ids[item.external_id] = (await session.execute(stmt)).scalar_one()
    for item in items:
        await session.execute(update(SourceCategory).where(SourceCategory.id == ids[item.external_id])
                              .values(parent_id=ids.get(item.parent_external_id)))
    return ids


async def upsert_products(session, source_id: int, items: list[ProductItem], cat_ids: dict[str, int]) -> None:
    today = settings.today()
    for item in items:
        values = {
            "url": item.url, "name": item.name[:500], "brand": item.brand and item.brand[:200],
            "sku_raw": item.sku and item.sku[:200], "sku": normalize_sku(item.sku),
            "ean": normalize_ean(item.ean), "source_category_id": cat_ids.get(item.category_external_id),
            "last_seen_at": _now(),
        }
        stmt = insert(SourceProduct).values(source_id=source_id, external_id=item.external_id[:1000], **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SourceProduct.source_id, SourceProduct.external_id], set_=values,
        ).returning(SourceProduct.id)
        sp_id = (await session.execute(stmt)).scalar_one()
        price = {"price": item.price, "old_price": item.old_price, "currency": item.currency,
                 "in_stock": item.in_stock, "scraped_at": _now()}
        await session.execute(
            insert(PriceSnapshot).values(source_product_id=sp_id, scraped_on=today, **price)
            .on_conflict_do_update(index_elements=[PriceSnapshot.source_product_id, PriceSnapshot.scraped_on],
                                   set_=price)
        )


async def post_process() -> None:
    """Матчинг и пересчёт категорий после обхода (общий для всех источников)."""
    async with SessionLocal() as session:
        # Источники обходятся параллельно — матчинг выполняем строго по одному
        await session.execute(text("SELECT pg_advisory_xact_lock(724501)"))
        await automap_categories(session)
        matched, unmatched = await match_products(session)
        await refresh_products(session)
        await session.commit()
    log.info("Матчинг: сопоставлено %d, без ключа %d", matched, unmatched)


async def adapter_config(session: AsyncSession, source: Source) -> dict:
    """Config источника для адаптера: прокси, выбранный в админке, заменяет config.proxy из YAML."""
    config = dict(source.config)
    if source.proxy_id:
        proxy = await session.get(Proxy, source.proxy_id)
        if proxy:
            config["proxy"] = proxy.url
    return config


async def run_source(source_id: int, run_id: int | None = None, trigger: str = "schedule",
                     adapter_kwargs: dict | None = None, match: bool = True) -> ScrapeRun:
    async with SessionLocal() as session:
        source = await session.get(Source, source_id)
        config = await adapter_config(session, source)
        run = await session.get(ScrapeRun, run_id) if run_id else None
        if run is None:
            run = ScrapeRun(source_id=source_id, trigger=trigger)
            session.add(run)
        run.status, run.started_at = "running", _now()
        await session.commit()
        run_id = run.id

    errors: list[str] = []
    categories_found = items_found = 0
    status = "ok"
    adapter = None
    try:
        # Внутри try: ошибка конфига (селекторы, прокси) должна попасть в журнал, а не оставить запуск в running
        adapter = build_adapter(source.name, source.adapter, source.base_url, config, **(adapter_kwargs or {}))
        if getattr(adapter, "proxy", None):
            log.info("%s: через прокси %s", source.name, mask(adapter.proxy))
        categories = await adapter.categories()
        categories_found = len(categories)
        if not categories:
            raise RuntimeError("Не найдено ни одной категории — проверьте селекторы categories.link")
        async with SessionLocal() as session:
            cat_ids = await upsert_categories(session, source.id, categories)
            if source.is_master:
                await sync_master_categories(session, source)
            await session.commit()

        for category in adapter.categories_to_crawl(categories):
            try:
                batch = [item async for item in adapter.products(category)]
            except Exception as e:  # одна сломанная категория не должна ронять весь обход
                errors.append(f"{category.url}: {e!r}")
                log.warning("%s: ошибка категории %s: %r", source.name, category.url, e)
                continue
            async with SessionLocal() as session:
                await upsert_products(session, source.id, batch, cat_ids)
                await session.commit()
            items_found += len(batch)
        if errors:
            status = "partial"
    except Exception:
        status = "failed"
        errors.append(traceback.format_exc())
        log.exception("%s: обход завершился ошибкой", source.name)
    finally:
        if adapter:
            await adapter.close()

    if match and status != "failed":
        try:
            await post_process()
        except Exception:
            errors.append("post_process: " + traceback.format_exc())
            status = "partial"

    async with SessionLocal() as session:
        run = await session.get(ScrapeRun, run_id)
        run.status, run.finished_at = status, _now()
        run.categories_found, run.items_found = categories_found, items_found
        run.errors_count = len(errors)
        run.error_log = "\n\n".join(errors)[-20000:] or None
        await session.commit()
    log.info("%s: %s, категорий %d, товаров %d, ошибок %d",
             source.name, status, categories_found, items_found, len(errors))
    return run


async def enabled_source_ids() -> list[int]:
    async with SessionLocal() as session:
        # Ведущий источник первым: его дерево нужно для автомаппинга остальных
        return list((await session.scalars(
            select(Source.id).where(Source.enabled).order_by(Source.is_master.desc(), Source.position)
        )).all())
