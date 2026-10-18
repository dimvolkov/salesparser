"""Прокси источника: выбранный в админке (таблица proxy) или `config.proxy` в YAML.

Адрес можно задать ссылкой на переменную окружения — `"${LENTA_PROXY}"`, тогда пароль хранится
только в окружении worker и api.
"""
import os
import re
from urllib.parse import unquote, urlsplit

import httpx

_VAR = re.compile(r"\$\{(\w+)\}")
SCHEMES = ("http", "https", "socks5", "socks5h")


def resolve_proxy(value: str | None) -> str | None:
    """Раскрывает `${VAR}`; пустая строка (переменная не задана) — значит без прокси."""
    if not value:
        return None
    url = _VAR.sub(lambda m: os.environ.get(m.group(1), ""), value).strip()
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in SCHEMES or not parts.hostname:
        raise ValueError(f"Прокси должен начинаться с {', '.join(s + '://' for s in SCHEMES)}: {mask(url)}")
    return url


def mask(url: str) -> str:
    """Адрес прокси для логов и админки, без пароля."""
    parts = urlsplit(url)
    if parts.password is None or not parts.hostname:
        return url
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return parts._replace(netloc=f"{parts.username}:***@{host}").geturl()


def playwright_proxy(url: str) -> dict:
    """Формат `proxy` для chromium.launch(): логин и пароль отдельно от адреса."""
    parts = urlsplit(url)
    if parts.scheme.startswith("socks") and parts.username:
        raise ValueError("Chromium не поддерживает SOCKS5 с паролем — используйте HTTP-прокси")
    # DNS через прокси Chromium делает и так, socks5h он не знает
    scheme = "socks5" if parts.scheme == "socks5h" else parts.scheme
    server = f"{scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    proxy = {"server": server}
    if parts.username:
        proxy["username"] = unquote(parts.username)
        proxy["password"] = unquote(parts.password or "")
    return proxy


async def probe(url: str, timeout: float = 20.0, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Внешний IP, страна и сеть, какими их видят сайты через этот прокси, например «RU · 1.2.3.4 · AS… Rostelecom»."""
    # С transport (в тестах) прокси не подключаем: httpx отдал бы запрос прокси, а не transport
    async with httpx.AsyncClient(proxy=None if transport else url, timeout=timeout, transport=transport) as client:
        r = await client.get("https://ipinfo.io/json")
        r.raise_for_status()
        data = r.json()
    return " · ".join(str(data[k]) for k in ("country", "ip", "org") if data.get(k))
