"""Данные демо-магазинов. Цены детерминированно «плавают» по дням, чтобы была видна динамика."""
import hashlib
from dataclasses import dataclass
from datetime import date

from app.normalize import gtin_check_digit


def ean(n: int) -> str:
    body = f"460700{n:06d}"
    return body + str(gtin_check_digit(body))


@dataclass
class DemoProduct:
    id: int
    brand: str
    sku: str
    name: str
    base_price: int
    cat: str
    shops: str = "abc"
    has_ean: bool = True


PRODUCTS = [
    DemoProduct(1, "Samsung", "SM-A556B", "Смартфон Samsung Galaxy A55 8/256 ГБ", 38990, "phones"),
    DemoProduct(2, "Apple", "MTP03", "Смартфон Apple iPhone 15 128 ГБ", 79990, "phones"),
    DemoProduct(3, "Xiaomi", "2312DRA50G", "Смартфон Xiaomi Redmi Note 13 Pro 8/256 ГБ", 27990, "phones"),
    DemoProduct(4, "realme", "RMX3890", "Смартфон realme 12 Pro 8/256 ГБ", 29990, "phones", "ab"),
    DemoProduct(5, "Honor", "5109BBYL", "Смартфон Honor 200 12/512 ГБ", 39990, "phones", "ac"),
    DemoProduct(6, "Nothing", "A142", "Смартфон Nothing Phone (2a) 12/256 ГБ", 33990, "phones", "c"),
    DemoProduct(7, "Lenovo", "82XV00BLRK", "Ноутбук Lenovo IdeaPad Slim 3 15IRH8", 54990, "laptops"),
    DemoProduct(8, "ASUS", "X1504VA-BQ287", "Ноутбук ASUS Vivobook 15 X1504VA", 49990, "laptops"),
    DemoProduct(9, "Apple", "MRXN3", "Ноутбук Apple MacBook Air 13 M3 8/256 ГБ", 129990, "laptops", "ab"),
    DemoProduct(10, "HP", "7P6M2EA", "Ноутбук HP 250 G10", 45990, "laptops", "abc"),
    DemoProduct(11, "Sony", "WH-1000XM5", "Наушники Sony WH-1000XM5", 32990, "headphones"),
    DemoProduct(12, "Apple", "MTJV3", "Наушники Apple AirPods Pro 2 USB-C", 22990, "headphones"),
    DemoProduct(13, "JBL", "JBLT520BTBLK", "Наушники JBL Tune 520BT чёрные", 3990, "headphones", "bc"),
    DemoProduct(14, "Dreame", "RLL22GA", "Робот-пылесос Dreame L10s Ultra", 59990, "vacuums"),
    DemoProduct(15, "Xiaomi", "BHR5771EU", "Робот-пылесос Xiaomi Robot Vacuum S10+", 34990, "vacuums"),
    DemoProduct(16, "Dyson", "446986-01", "Пылесос Dyson V15 Detect Absolute", 69990, "vacuums", "ab"),
    DemoProduct(17, "Tefal", "TY6837WO", "Пылесос Tefal X-Pert 6.60", 14990, "vacuums"),
    DemoProduct(18, "Bosch", "TWK3P420", "Чайник Bosch TWK3P420", 4290, "kettles"),
    DemoProduct(19, "Xiaomi", "BHR5927EU", "Чайник Xiaomi Mi Electric Kettle 2", 2990, "kettles"),
    DemoProduct(20, "Polaris", "PWK1725CGLD", "Чайник Polaris PWK 1725CGLD", 2490, "kettles", "abc"),
    DemoProduct(21, "Kitfort", "KT-6125", "Чайник Kitfort KT-6125", 3490, "kettles", "ac"),
    DemoProduct(22, "", "", "Чайник электрический стеклянный 1,7 л", 1290, "kettles", "b", has_ean=False),
]

# Дерево категорий каждого магазина: slug -> (название, родитель, ключ товарной категории)
SHOPS = {
    "a": {
        "title": "ТехноМаркет",
        "bias": 0.0,
        "tree": [
            ("electronics", "Электроника", None, None),
            ("smartphones", "Смартфоны", "electronics", "phones"),
            ("laptops", "Ноутбуки", "electronics", "laptops"),
            ("headphones", "Наушники", "electronics", "headphones"),
            ("appliances", "Бытовая техника", None, None),
            ("vacuums", "Пылесосы", "appliances", "vacuums"),
            ("kettles", "Чайники", "appliances", "kettles"),
        ],
    },
    "b": {
        "title": "Электрон",
        "bias": -0.03,
        "tree": [
            ("gadgets", "Телефоны и гаджеты", None, None),
            ("phones", "Смартфоны", "gadgets", "phones"),
            ("audio", "Наушники", "gadgets", "headphones"),
            ("computers", "Компьютеры", None, None),
            ("notebooks", "Ноутбуки", "computers", "laptops"),
            ("home", "Для дома", None, None),
            ("cleaning", "Пылесосы", "home", "vacuums"),
            ("kitchen", "Чайники", "home", "kettles"),
        ],
    },
    "c": {
        "title": "МегаДом",
        "bias": 0.02,
        "tree": [
            ("mobile", "Мобильные телефоны", None, "phones"),
            ("notebooks", "Ноутбуки", None, "laptops"),
            ("audio", "Аудио", None, "headphones"),
            ("cleaning", "Уборка", None, "vacuums"),
            ("kitchen", "Техника для кухни", None, "kettles"),
        ],
    },
}


def _rand(*parts) -> float:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(h[:4], "big") / 2**32


def offer(shop: str, p: DemoProduct, day: date) -> tuple[int, int | None, bool]:
    """(цена, старая цена, в наличии) для товара в магазине на дату."""
    r = _rand(shop, p.id) - 0.5
    daily = _rand(shop, p.id, day.isoformat()) - 0.5
    price = p.base_price * (1 + SHOPS[shop]["bias"] + r * 0.12 + daily * 0.04)
    price = int(round(price / 10) * 10 - 10) if price > 1000 else int(round(price))
    old = int(round(price * 1.15 / 100) * 100 - 10) if _rand(shop, p.id, "sale") > 0.7 else None
    in_stock = _rand(shop, p.id, "stock", day.isoformat()[:7]) > 0.12
    return price, old, in_stock
