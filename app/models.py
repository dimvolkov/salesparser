from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
    CheckConstraint, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Proxy(Base):
    """Прокси, заведённый в админке; выбирается для источника на странице «Источники»."""
    __tablename__ = "proxy"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # http(s)://user:pass@host:port, socks5://..., socks5h://... или "${ПЕРЕМЕННАЯ}"
    url: Mapped[str] = mapped_column(String(1000))
    note: Mapped[str] = mapped_column(String(300), default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_ok: Mapped[bool | None] = mapped_column(Boolean)
    check_result: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Source(Base):
    """Сайт-источник. Конфигурация приходит из sources/*.yaml."""
    __tablename__ = "source"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500))
    adapter: Mapped[str] = mapped_column(String(200), default="generic")
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_master: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Выбирается в админке и важнее config.proxy из YAML; синхронизация YAML его не трогает
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxy.id", ondelete="SET NULL"))


class Category(Base):
    """Единое дерево категорий (строится по ведущему источнику)."""
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("category.id", ondelete="SET NULL"))
    position: Mapped[int] = mapped_column(Integer, default=0)


class SourceCategory(Base):
    __tablename__ = "source_category"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(1000))
    name: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(2000))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("source_category.id", ondelete="SET NULL"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Привязка к единому дереву; mapping_locked = задана вручную в админке
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id", ondelete="SET NULL"), index=True)
    mapping_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    """Единая позиция каталога, объединяющая предложения разных источников."""
    __tablename__ = "product"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(200))
    ean: Mapped[str | None] = mapped_column(String(14), unique=True)
    sku: Mapped[str | None] = mapped_column(String(100), unique=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceProduct(Base):
    """Товар в конкретном источнике."""
    __tablename__ = "source_product"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(1000))
    url: Mapped[str] = mapped_column(String(2000))
    name: Mapped[str] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(200))
    sku_raw: Mapped[str | None] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    ean: Mapped[str | None] = mapped_column(String(14), index=True)
    source_category_id: Mapped[int | None] = mapped_column(ForeignKey("source_category.id", ondelete="SET NULL"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id", ondelete="SET NULL"), index=True)
    # Ручная привязка в админке — автоматический матчинг её не трогает
    match_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PriceSnapshot(Base):
    """Цена товара источника за день; повторный обход в тот же день перезаписывает запись."""
    __tablename__ = "price_snapshot"
    __table_args__ = (UniqueConstraint("source_product_id", "scraped_on"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_product_id: Mapped[int] = mapped_column(ForeignKey("source_product.id", ondelete="CASCADE"))
    scraped_on: Mapped[date] = mapped_column(Date, index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScrapeRun(Base):
    __tablename__ = "scrape_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    # queued -> running -> ok | partial | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="schedule")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    categories_found: Mapped[int] = mapped_column(Integer, default=0)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
    error_log: Mapped[str | None] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'manager', 'viewer')", name="users_role_check"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    full_name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
