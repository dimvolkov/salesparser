import asyncio
import logging
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class BlockedByRobots(Exception):
    pass


class Fetcher:
    """HTTP-клиент одного источника: пауза между запросами, ретраи, учёт robots.txt."""

    def __init__(self, delay: float = 1.0, retries: int = 3, timeout: float = 30.0,
                 respect_robots: bool = True, headers: dict | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.delay = delay
        self.retries = retries
        self.respect_robots = respect_robots
        self._client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, transport=transport,
            headers={"User-Agent": settings.user_agent, "Accept-Language": "ru,en;q=0.8", **(headers or {})},
        )
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._robots: dict[str, RobotFileParser | None] = {}

    async def _throttle(self):
        async with self._lock:
            wait = self._last + self.delay - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    async def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = None
            try:
                r = await self._client.get(urljoin(origin, "/robots.txt"))
                if r.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(r.text.splitlines())
            except httpx.HTTPError:
                pass
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(settings.user_agent, url)

    async def get(self, url: str) -> str:
        if not await self._allowed(url):
            raise BlockedByRobots(url)
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            await self._throttle()
            try:
                r = await self._client.get(url)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r.text
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500 and e.response.status_code != 429:
                    raise
                last_exc = e
            except httpx.TransportError as e:
                last_exc = e
            log.warning("%s: попытка %d/%d не удалась: %s", url, attempt, self.retries, last_exc)
            await asyncio.sleep(2 ** attempt)
        raise last_exc  # type: ignore[misc]

    async def close(self):
        await self._client.aclose()
