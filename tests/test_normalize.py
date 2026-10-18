from decimal import Decimal

import pytest

from app.normalize import normalize_ean, normalize_sku, parse_price


@pytest.mark.parametrize("raw, expected", [
    ("1 299,00 ₽", "1299.00"),
    ("12 990 ₽", "12990.00"),
    ("12.990,00 ₽", "12990.00"),
    ("12990.00 руб.", "12990.00"),
    ("1,299.50", "1299.50"),
    ("1.299", "1299.00"),
    ("от 999 ₽", "999.00"),
    ("99,9", "99.90"),
    (4290, "4290.00"),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [None, "", "Цена по запросу"])
def test_parse_price_empty(raw):
    assert parse_price(raw) is None


def test_ean():
    assert normalize_ean("4607000000014") == "4607000000014"
    assert normalize_ean("4 607000 000014") == "4607000000014"
    assert normalize_ean("4607000000015") is None  # неверная контрольная цифра
    assert normalize_ean("036000291452") == "0036000291452"  # UPC-A -> EAN-13
    assert normalize_ean("00036000291452") == "0036000291452"  # GTIN-14 с ведущим нулём
    assert normalize_ean("12345") is None


def test_sku():
    assert normalize_sku("SM-A556B") == normalize_sku("sm a556b") == "SMA556B"
    # кириллические «двойники» латинских букв
    assert normalize_sku("SМ-А556В") == "SMA556B"
    assert normalize_sku(" - ") is None
