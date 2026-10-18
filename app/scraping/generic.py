"""Универсальный адаптер, описываемый CSS/XPath-селекторами в YAML.

Обход: стартовая страница -> ссылки категорий -> на страницах категорий ссылки подкатегорий
(дерево строится по тому, где ссылка найдена) -> листинги листовых категорий с пагинацией ->
при необходимости карточка товара (селекторы и/или schema.org JSON-LD).
"""
import json
import re
from collections.abc import AsyncIterator
from urllib.parse import urljoin, urlsplit

from parsel import Selector
from pydantic import BaseModel

from app.normalize import clean_text, parse_price
from app.scraping.base import CategoryItem, ProductItem, SourceAdapter
from app.scraping.http import Fetcher
from app.scraping.proxy import resolve_proxy


class FieldSpec(BaseModel):
    # CSS-селектор (parsel: поддерживает ::text и ::attr(name)) или "xpath:..."
    sel: str
    # Регулярка для извлечения значения из текста; берётся первая группа, если она есть
    re: str | None = None


Field = str | FieldSpec


class CategoriesConfig(BaseModel):
    start_url: str = ""
    link: str
    sub_link: str | None = None
    max_depth: int = 5
    exclude: str | None = None  # регулярка по URL категорий, которые нужно пропустить


class ListingConfig(BaseModel):
    item: str
    fields: dict[str, Field]
    next_page: str | None = None
    max_pages: int = 200


class DetailConfig(BaseModel):
    enabled: bool = False
    fields: dict[str, Field] = {}
    jsonld: bool = True
    # Карточка запрашивается только если после листинга не хватает этих полей
    when_missing: list[str] = ["ean", "sku"]


class GenericConfig(BaseModel):
    categories: CategoriesConfig
    listing: ListingConfig
    detail: DetailConfig = DetailConfig()
    in_stock_pattern: str | None = None
    out_of_stock_pattern: str | None = None
    currency: str = "RUB"
    delay: float = 1.0
    respect_robots: bool = True
    headers: dict[str, str] = {}
    # http(s)://, socks5:// или socks5h://; секреты — через "${ИМЯ_ПЕРЕМЕННОЙ}" (см. app/scraping/proxy.py)
    proxy: str | None = None
    leaf_only: bool = True


def extract(sel: Selector, spec: Field | None) -> str | None:
    if spec is None:
        return None
    if isinstance(spec, str):
        spec = FieldSpec(sel=spec)
    if spec.sel.startswith("xpath:"):
        values = sel.xpath(spec.sel[6:]).getall()
    elif "::" in spec.sel:
        values = sel.css(spec.sel).getall()
    else:
        # Без ::text/::attr берём полный текст элемента вместе с вложенными
        values = [node.xpath("string()").get() for node in sel.css(spec.sel)]
    text = clean_text(" ".join(v for v in values if v and v.strip()))
    if text and spec.re:
        m = re.search(spec.re, text)
        text = (m.group(1) if m.groups() else m.group(0)) if m else None
    return text


def _iter_jsonld_products(data):
    if isinstance(data, list):
        for d in data:
            yield from _iter_jsonld_products(d)
    elif isinstance(data, dict):
        types = data.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Product" in types:
            yield data
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in data:
                yield from _iter_jsonld_products(data[key])


def extract_jsonld(sel: Selector) -> dict:
    """Достаёт поля товара из schema.org Product (JSON-LD)."""
    for raw in sel.css('script[type="application/ld+json"]::text').getall():
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for p in _iter_jsonld_products(data):
            offers = p.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if offers.get("@type") == "AggregateOffer":
                offers = {**offers, "price": offers.get("lowPrice", offers.get("price"))}
            brand = p.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            availability = str(offers.get("availability") or "")
            result = {
                "name": p.get("name"),
                "sku": p.get("sku") or p.get("mpn"),
                "ean": next((p.get(k) for k in ("gtin13", "gtin", "gtin12", "gtin14", "gtin8") if p.get(k)), None),
                "brand": brand,
                "price": offers.get("price"),
                "currency": offers.get("priceCurrency"),
                "in_stock": ("InStock" in availability) if availability else None,
            }
            return {k: (str(v) if v is not None and not isinstance(v, bool) else v)
                    for k, v in result.items() if v not in (None, "")}
    return {}


def external_id(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


class GenericAdapter(SourceAdapter):
    fetcher_class = Fetcher

    def __init__(self, name: str, base_url: str, config: dict, transport=None):
        super().__init__(name, base_url, config)
        self.cfg = GenericConfig.model_validate(config)
        self.proxy = resolve_proxy(self.cfg.proxy)
        self.fetcher = self.make_fetcher(transport)
        self._page_cache: dict[str, str] = {}

    def make_fetcher(self, transport=None):
        return Fetcher(delay=self.cfg.delay, respect_robots=self.cfg.respect_robots,
                       headers=self.cfg.headers, transport=transport, proxy=self.proxy)

    async def fetch(self, url: str) -> Selector:
        html = self._page_cache.pop(url, None)
        if html is None:
            html = await self.fetcher.get(url)
        return Selector(text=html, base_url=url)

    def _links(self, page: Selector, selector: str, page_url: str) -> list[tuple[str, str]]:
        out, seen = [], set()
        for a in page.css(selector):
            href = a.attrib.get("href")
            name = clean_text(a.xpath("string()").get())
            if not href or not name or href.startswith(("#", "javascript:")):
                continue
            url = urljoin(page_url, href).split("#")[0]
            if self.cfg.categories.exclude and re.search(self.cfg.categories.exclude, url):
                continue
            if url not in seen:
                seen.add(url)
                out.append((url, name))
        return out

    async def categories(self) -> list[CategoryItem]:
        cc = self.cfg.categories
        start = urljoin(self.base_url, cc.start_url)
        page = await self.fetch(start)
        result: dict[str, CategoryItem] = {}
        queue: list[tuple[str, str, str | None, int]] = [
            (url, name, None, 1) for url, name in self._links(page, cc.link, start)
        ]
        while queue:
            url, name, parent, depth = queue.pop(0)
            ext = external_id(url)
            if ext in result:
                continue
            result[ext] = CategoryItem(external_id=ext, name=name, url=url, parent_external_id=parent)
            if not cc.sub_link or depth >= cc.max_depth:
                continue
            html = await self.fetcher.get(url)
            # Первая страница листинга понадобится при сборе товаров — не качаем её второй раз
            self._page_cache[url] = html
            sub = Selector(text=html, base_url=url)
            for sub_url, sub_name in self._links(sub, cc.sub_link, url):
                if external_id(sub_url) not in result:
                    queue.append((sub_url, sub_name, ext, depth + 1))
        return list(result.values())

    def categories_to_crawl(self, categories: list[CategoryItem]) -> list[CategoryItem]:
        crawl = super().categories_to_crawl(categories)
        keep = {c.url for c in crawl}
        for url in list(self._page_cache):
            if url not in keep:
                del self._page_cache[url]
        return crawl

    def _in_stock(self, text: str | None, configured: bool) -> bool:
        if not configured:
            return True
        if text is None:
            return False if self.cfg.in_stock_pattern else True
        if self.cfg.out_of_stock_pattern and re.search(self.cfg.out_of_stock_pattern, text, re.I):
            return False
        if self.cfg.in_stock_pattern:
            return bool(re.search(self.cfg.in_stock_pattern, text, re.I))
        return True

    async def products(self, category: CategoryItem) -> AsyncIterator[ProductItem]:
        lc = self.cfg.listing
        url: str | None = category.url
        seen_pages: set[str] = set()
        while url and url not in seen_pages and len(seen_pages) < lc.max_pages:
            seen_pages.add(url)
            page = await self.fetch(url)
            for node in page.css(lc.item):
                raw = {key: extract(node, spec) for key, spec in lc.fields.items()}
                href = raw.get("url")
                if not href:
                    continue
                product_url = urljoin(url, href)
                if self.cfg.detail.enabled and any(not raw.get(f) for f in self.cfg.detail.when_missing):
                    raw = {**await self._detail(product_url), **{k: v for k, v in raw.items() if v}}
                item = self._build(raw, product_url, category)
                if item:
                    yield item
            nxt = extract(page, lc.next_page) if lc.next_page else None
            url = urljoin(url, nxt) if nxt else None

    async def _detail(self, url: str) -> dict:
        page = await self.fetch(url)
        data = extract_jsonld(page) if self.cfg.detail.jsonld else {}
        for key, spec in self.cfg.detail.fields.items():
            value = extract(page, spec)
            if value:
                data[key] = value
        return data

    def _build(self, raw: dict, url: str, category: CategoryItem) -> ProductItem | None:
        name = clean_text(raw.get("name"))
        if not name:
            return None
        in_stock = raw.get("in_stock")
        if not isinstance(in_stock, bool):
            in_stock = self._in_stock(in_stock, "in_stock" in self.cfg.listing.fields
                                      or "in_stock" in self.cfg.detail.fields)
        return ProductItem(
            external_id=external_id(url), url=url, name=name,
            category_external_id=category.external_id,
            price=parse_price(raw.get("price")),
            old_price=parse_price(raw.get("old_price")),
            currency=(raw.get("currency") or self.cfg.currency)[:3].upper(),
            in_stock=in_stock,
            sku=clean_text(raw.get("sku")),
            ean=clean_text(raw.get("ean")),
            brand=clean_text(raw.get("brand")),
        )

    async def close(self):
        await self.fetcher.close()
