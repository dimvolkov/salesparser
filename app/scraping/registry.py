import importlib

from app.scraping.base import SourceAdapter
from app.scraping.generic import GenericAdapter
from app.scraping.playwright import PlaywrightAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {
    "generic": GenericAdapter,
    "playwright": PlaywrightAdapter,
}


def adapter_class(name: str) -> type[SourceAdapter]:
    if name in ADAPTERS:
        return ADAPTERS[name]
    if ":" in name:
        module, cls = name.split(":", 1)
        return getattr(importlib.import_module(module), cls)
    raise ValueError(f"Неизвестный адаптер: {name}")


def build_adapter(name: str, adapter: str, base_url: str, config: dict, **kwargs) -> SourceAdapter:
    return adapter_class(adapter)(name, base_url, config, **kwargs)
