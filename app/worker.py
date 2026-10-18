"""Процесс сбора: ежедневный обход по расписанию + очередь ручных запусков из админки.

    python -m app.worker                 # планировщик (сервис worker)
    python -m app.worker run [--source NAME]  # разовый обход
    python -m app.worker check NAME [--url URL]  # внешний IP и ответ сайта через прокси источника
"""
import argparse
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import ScrapeRun, Source
from app.runner import enabled_source_ids, post_process, run_source
from app.scraping.proxy import mask
from app.scraping.registry import build_adapter
from app.sources import sync_sources

log = logging.getLogger("worker")
_active: set[int] = set()


async def _run_guarded(source_id: int, run_id: int | None, trigger: str, sem: asyncio.Semaphore):
    if source_id in _active:
        log.info("Источник %s уже обходится, пропуск", source_id)
        return
    _active.add(source_id)
    try:
        async with sem:
            await run_source(source_id, run_id=run_id, trigger=trigger)
    finally:
        _active.discard(source_id)


async def run_all(trigger: str = "schedule", only: str | None = None) -> None:
    async with SessionLocal() as session:
        await sync_sources(session)
    ids = await enabled_source_ids()
    if only:
        async with SessionLocal() as session:
            ids = list((await session.scalars(select(Source.id).where(Source.name == only))).all())
        if not ids:
            raise SystemExit(f"Источник {only!r} не найден")
    sem = asyncio.Semaphore(settings.max_parallel_sources)
    await asyncio.gather(*(_run_guarded(i, None, trigger, sem) for i in ids))
    await post_process()


async def process_queue() -> None:
    async with SessionLocal() as session:
        queued = (await session.scalars(
            select(ScrapeRun).where(ScrapeRun.status == "queued").order_by(ScrapeRun.id)
        )).all()
    if not queued:
        return
    sem = asyncio.Semaphore(settings.max_parallel_sources)
    await asyncio.gather(*(_run_guarded(r.source_id, r.id, r.trigger, sem) for r in queued))


async def serve() -> None:
    async with SessionLocal() as session:
        await sync_sources(session)
        # Запуски, прерванные рестартом контейнера, помечаем как упавшие
        for run in (await session.scalars(select(ScrapeRun).where(ScrapeRun.status == "running"))).all():
            run.status, run.error_log = "failed", "Прервано перезапуском worker"
        await session.commit()

    scheduler = AsyncIOScheduler(timezone=settings.tz)
    scheduler.add_job(run_all, CronTrigger.from_crontab(settings.scrape_cron, timezone=settings.tz),
                      id="daily", max_instances=1, coalesce=True, misfire_grace_time=3600)
    scheduler.add_job(process_queue, "interval", seconds=settings.queue_poll_seconds,
                      id="queue", max_instances=1, coalesce=True)
    scheduler.start()
    log.info("Worker запущен, расписание: %s (%s)", settings.scrape_cron, settings.timezone)
    if settings.run_on_start:
        asyncio.create_task(run_all("startup"))
    await asyncio.Event().wait()


async def check_source(name: str, url: str | None) -> None:
    """Проверка доступа без обхода: какой IP видит внешний мир и что отвечает сайт источника."""
    async with SessionLocal() as session:
        await sync_sources(session)
        source = await session.scalar(select(Source).where(Source.name == name))
    if source is None:
        raise SystemExit(f"Источник {name!r} не найден")
    adapter = build_adapter(source.name, source.adapter, source.base_url, source.config)
    fetcher = getattr(adapter, "fetcher", None)
    if fetcher is None:
        raise SystemExit(f"У адаптера {source.adapter} нет fetcher — проверка не поддерживается")
    proxy = getattr(adapter, "proxy", None)
    print("Прокси:", mask(proxy) if proxy else "нет")
    try:
        for target in ("https://api.ipify.org/", url or source.base_url):
            try:
                text = await fetcher.get(target)
            except Exception as e:
                print(f"{target}: ОШИБКА {e!r}")
            else:
                print(f"{target}: {len(text)} символов\n  {' '.join(text.split())[:300]}")
    finally:
        await adapter.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="Разовый обход всех (или одного) источников")
    run.add_argument("--source")
    check = sub.add_parser("check", help="Проверить доступ к сайту источника (и прокси)")
    check.add_argument("source")
    check.add_argument("--url", help="страница для проверки, по умолчанию base_url")
    args = parser.parse_args()
    if args.cmd == "run":
        asyncio.run(run_all("cli", args.source))
    elif args.cmd == "check":
        asyncio.run(check_source(args.source, args.url))
    else:
        asyncio.run(serve())


if __name__ == "__main__":
    main()
