from app.demo.data import ean
from app.normalize import normalize_ean, normalize_sku
from app.scraping.registry import build_adapter
from app.sources import load_source_files


async def crawl(name, transport):
    cfg = next(s for s in load_source_files("sources") if s["name"] == name)
    adapter = build_adapter(name, "generic", cfg["base_url"], {**cfg["config"], "delay": 0}, transport=transport)
    try:
        categories = await adapter.categories()
        products = [p for c in adapter.categories_to_crawl(categories) async for p in adapter.products(c)]
    finally:
        await adapter.close()
    return categories, products


async def test_master_shop_tree_and_listing(demo_transport):
    categories, products = await crawl("demo-a", demo_transport)
    by_ext = {c.external_id: c for c in categories}
    assert by_ext["/demo/a/c/smartphones"].parent_external_id == "/demo/a/c/electronics"
    assert len(categories) == 7
    assert len(products) == 19  # пагинация пройдена целиком
    samsung = next(p for p in products if "Galaxy A55" in p.name)
    assert samsung.ean == ean(1) and samsung.sku == "SM-A556B" and samsung.brand == "Samsung"
    assert samsung.price and samsung.price > 30000


async def test_jsonld_detail(demo_transport):
    _, products = await crawl("demo-b", demo_transport)
    samsung = next(p for p in products if "Galaxy A55" in p.name)
    assert normalize_ean(samsung.ean) == ean(1)
    assert samsung.brand == "Samsung"
    nameless = next(p for p in products if "стеклянный" in p.name)
    assert nameless.ean is None and nameless.sku is None


async def test_flat_tree_and_dirty_sku(demo_transport):
    categories, products = await crawl("demo-c", demo_transport)
    assert all(c.parent_external_id is None for c in categories)
    samsung = next(p for p in products if "Galaxy A55" in p.name)
    assert normalize_sku(samsung.sku) == "SMA556B"
