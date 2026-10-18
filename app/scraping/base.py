from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class CategoryItem:
    external_id: str
    name: str
    url: str
    parent_external_id: str | None = None


@dataclass
class ProductItem:
    external_id: str
    url: str
    name: str
    category_external_id: str
    price: Decimal | None = None
    old_price: Decimal | None = None
    currency: str = "RUB"
    in_stock: bool = True
    sku: str | None = None
    ean: str | None = None
    brand: str | None = None


class SourceAdapter(ABC):
    """Адаптер сайта-источника: отдаёт дерево категорий и товары категории.

    Своя реализация под сложный сайт подключается в YAML как `adapter: "package.module:ClassName"`.
    """

    def __init__(self, name: str, base_url: str, config: dict):
        self.name = name
        self.base_url = base_url
        self.config = config

    @abstractmethod
    async def categories(self) -> list[CategoryItem]: ...

    @abstractmethod
    def products(self, category: CategoryItem) -> AsyncIterator[ProductItem]: ...

    def categories_to_crawl(self, categories: list[CategoryItem]) -> list[CategoryItem]:
        """По умолчанию товары собираются только с листовых категорий, чтобы не ловить дубли из родителей."""
        if not self.config.get("leaf_only", True):
            return categories
        parents = {c.parent_external_id for c in categories if c.parent_external_id}
        return [c for c in categories if c.external_id not in parents]

    async def close(self) -> None:
        pass
