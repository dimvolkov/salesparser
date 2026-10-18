"""Прокси источника: `config.proxy` в YAML, например `proxy: "${LENTA_PROXY}"`.

Ссылка на переменную окружения раскрывается только при создании адаптера, поэтому логин и пароль
прокси не попадают ни в git, ни в базу (config источника хранится в таблице sources как есть).
"""
import os
import re
from urllib.parse import unquote, urlsplit

_VAR = re.compile(r"\$\{(\w+)\}")
SCHEMES = ("http", "https", "socks5", "socks5h")


def resolve_proxy(value: str | None) -> str | None:
    """Раскрывает `${VAR}`; пустая строка (переменная не задана) — значит без прокси."""
    if not value:
        return None
    url = _VAR.sub(lambda m: os.environ.get(m.group(1), ""), value).strip()
    if not url:
        return None
    scheme = urlsplit(url).scheme
    if scheme not in SCHEMES:
        raise ValueError(f"Прокси должен начинаться с {', '.join(s + '://' for s in SCHEMES)}: {mask(url)}")
    return url


def mask(url: str) -> str:
    """Адрес прокси для логов, без пароля."""
    parts = urlsplit(url)
    if parts.password is None:
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
