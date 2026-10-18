import asyncio
import base64

import httpx
import pytest

from app.scraping.http import Fetcher
from app.scraping.proxy import mask, playwright_proxy, probe, resolve_proxy
from app.scraping.registry import build_adapter

CONFIG = {"categories": {"link": "a"}, "listing": {"item": ".p", "fields": {}}}


def test_resolve_from_env(monkeypatch):
    monkeypatch.setenv("SHOP_PROXY", "http://u:p@10.0.0.1:3128")
    assert resolve_proxy("${SHOP_PROXY}") == "http://u:p@10.0.0.1:3128"
    assert resolve_proxy("http://10.0.0.2:8080") == "http://10.0.0.2:8080"


def test_unset_env_means_direct(monkeypatch):
    monkeypatch.delenv("SHOP_PROXY", raising=False)
    assert resolve_proxy("${SHOP_PROXY}") is None
    assert resolve_proxy(None) is None
    assert resolve_proxy("") is None


def test_bad_scheme_hides_password():
    with pytest.raises(ValueError) as e:
        resolve_proxy("ftp://u:secret@host:21")
    assert "secret" not in str(e.value)


def test_mask():
    assert mask("http://user:secret@host:3128") == "http://user:***@host:3128"
    assert mask("socks5h://user:secret@host") == "socks5h://user:***@host"
    assert mask("http://host:3128") == "http://host:3128"


def test_playwright_format():
    assert playwright_proxy("http://us%40er:p%3Ass@host:3128") == {
        "server": "http://host:3128", "username": "us@er", "password": "p:ss"}
    assert playwright_proxy("socks5h://host:1080") == {"server": "socks5://host:1080"}
    with pytest.raises(ValueError):
        playwright_proxy("socks5://u:p@host:1080")


def test_adapter_resolves_proxy(monkeypatch):
    monkeypatch.setenv("SHOP_PROXY", "http://u:p@10.0.0.1:3128")
    adapter = build_adapter("shop", "generic", "https://shop.example/", {**CONFIG, "proxy": "${SHOP_PROXY}"})
    assert adapter.proxy == "http://u:p@10.0.0.1:3128"
    # В конфиге источника (и в базе) остаётся ссылка на переменную, а не пароль
    assert adapter.config["proxy"] == "${SHOP_PROXY}"


async def test_requests_go_through_proxy():
    seen: list[bytes] = []

    async def handle(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        seen.append(head)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\nConnection: close\r\n\r\nvia-proxy")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    fetcher = Fetcher(delay=0, respect_robots=False, proxy=f"http://user:secret@127.0.0.1:{port}")
    try:
        assert await fetcher.get("http://shop.example/catalog/") == "via-proxy"
    finally:
        await fetcher.close()
        server.close()
    request_line, *lines = seen[0].decode().split("\r\n")
    assert request_line == "GET http://shop.example/catalog/ HTTP/1.1"
    headers = dict(line.split(": ", 1) for line in lines if line)
    headers = {k.lower(): v for k, v in headers.items()}
    assert headers["proxy-authorization"] == "Basic " + base64.b64encode(b"user:secret").decode()


async def test_probe_parses_ipinfo():
    def handler(request):
        assert request.url.host == "ipinfo.io"
        return httpx.Response(200, json={"ip": "1.2.3.4", "country": "RU", "org": "AS12389 Rostelecom"})
    result = await probe("http://10.0.0.1:3128", transport=httpx.MockTransport(handler))
    assert result == "RU · 1.2.3.4 · AS12389 Rostelecom"
