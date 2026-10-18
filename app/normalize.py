import re
from decimal import Decimal, InvalidOperation

_PRICE_RE = re.compile(r"\d[\d\s.,'  ]*")
# Кириллические буквы, визуально совпадающие с латинскими: в артикулах их часто путают
_LOOKALIKES = str.maketrans("АВЕКМНОРСТХУ", "ABEKMHOPCTXY")


def parse_price(text: str | int | float | Decimal | None) -> Decimal | None:
    """'1 299,00 ₽' / '12.990,00' / '12990.00 руб.' / '1,299.50' -> Decimal."""
    if text is None:
        return None
    if isinstance(text, (int, float, Decimal)):
        return Decimal(str(text)).quantize(Decimal("0.01"))
    m = _PRICE_RE.search(str(text))
    if not m:
        return None
    num = re.sub(r"[\s'  ]", "", m.group(0)).rstrip(".,")
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        parts = num.split(",")
        num = num.replace(",", ".") if len(parts) == 2 and len(parts[1]) in (1, 2) else num.replace(",", "")
    elif "." in num:
        parts = num.split(".")
        if len(parts) > 2 or len(parts[1]) == 3:
            num = num.replace(".", "")
    try:
        return Decimal(num).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def gtin_check_digit(body: str) -> int:
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def normalize_ean(value: str | None) -> str | None:
    """Приводит EAN-8/UPC-A/EAN-13/GTIN-14 к цифровой строке, проверяя контрольную цифру.

    UPC-A (12 цифр) дополняется ведущим нулём до EAN-13, GTIN-14 с ведущим нулём сокращается.
    """
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 12:
        digits = "0" + digits
    if len(digits) == 14 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) not in (8, 13, 14):
        return None
    if gtin_check_digit(digits[:-1]) != int(digits[-1]):
        return None
    return digits


def normalize_sku(value: str | None) -> str | None:
    """'sm a556b' / 'SM-A556B' / 'SМ-А556В' (кириллица) -> 'SMA556B'."""
    if not value:
        return None
    s = str(value).upper().translate(_LOOKALIKES)
    s = re.sub(r"[^0-9A-ZА-ЯЁ]", "", s)
    return s[:100] or None


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace("ё", "е")).strip()


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None
