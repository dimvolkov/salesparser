"""Тот же generic-адаптер, но страницы рендерятся в Chromium (для сайтов, собирающих каталог на JS).

Требует образ, собранный с WITH_PLAYWRIGHT=1 (см. Dockerfile).
"""
import asyncio
import time

from app.config import settings
from app.scraping.generic import GenericAdapter


class PlaywrightFetcher:
    def __init__(self, delay: float, wait_for: str | None, headers: dict):
        self.delay = delay
        self.wait_for = wait_for
        self.headers = headers
        self._pw = self._browser = self._context = None
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def _ensure(self):
        if self._context:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise RuntimeError("Playwright не установлен: соберите образ с WITH_PLAYWRIGHT=1") from e
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            user_agent=settings.user_agent, locale="ru-RU", extra_http_headers=self.headers)

    async def get(self, url: str) -> str:
        async with self._lock:
            await self._ensure()
            wait = self._last + self.delay - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            page = await self._context.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=60_000)
                if self.wait_for:
                    await page.wait_for_selector(self.wait_for, timeout=30_000)
                return await page.content()
            finally:
                await page.close()
                self._last = time.monotonic()

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()


class PlaywrightAdapter(GenericAdapter):
    def make_fetcher(self, transport=None):
        return PlaywrightFetcher(self.cfg.delay, self.config.get("wait_for"), self.cfg.headers)
