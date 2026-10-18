from decimal import Decimal as D

from app.analysis import Offer, Row, classify


def make_row(*offers):
    in_stock = [o.price for o in offers if o.in_stock]
    row = Row(id=1, name="x", brand=None, ean=None, sku=None, category_id=None,
              min_price=min(in_stock), max_price=max(in_stock), sources_count=len(offers), spread=None)
    row.offers = {o.source_id: o for o in offers}
    classify(row)
    return row


def test_min_max_and_out_of_stock():
    row = make_row(Offer(1, 1, "u", D(100), None, True, D(110)),
                   Offer(2, 2, "u", D(120), None, True, None),
                   Offer(3, 3, "u", D(50), None, False, None))
    assert [row.offers[i].css for i in (1, 2, 3)] == ["min", "max", "oos"]
    assert row.offers[1].delta == D(-10)
    assert row.min_delta == D(-10)


def test_ties_are_all_highlighted():
    row = make_row(Offer(1, 1, "u", D(100), None, True, None),
                   Offer(2, 2, "u", D(100), None, True, None))
    assert {o.css for o in row.offers.values()} == {"min"}
