from contextlib import contextmanager
from decimal import Decimal

import pytest

from app.services import commerce


class FakeSession:
    def __init__(self):
        self.rolled_back = False

    @contextmanager
    def begin(self):
        yield

    def rollback(self):
        self.rolled_back = True


def test_money_rounds_to_cents():
    assert commerce.money("12.345") == Decimal("12.35")


@pytest.mark.parametrize("value", ["0", "-1", "not-money", None])
def test_money_rejects_invalid_values(value):
    with pytest.raises(commerce.BusinessError):
        commerce.money(value)


def test_order_amount_comes_from_locked_products(monkeypatch):
    fake_session = FakeSession()
    captured = {"items": [], "stock": [], "inventory": []}
    products = {
        1: {"product_id": 1, "status": "active", "stock_qty": 10, "unit_price": Decimal("9.90"), "product_name": "A"},
        2: {"product_id": 2, "status": "active", "stock_qty": 10, "unit_price": Decimal("2.50"), "product_name": "B"},
    }
    monkeypatch.setattr(commerce.db, "session", fake_session)
    monkeypatch.setattr(commerce.ProductRepository, "lock", lambda product_id: products[product_id])
    assert hasattr(commerce, "InventoryRepository"), "inventory audit repository is required"
    monkeypatch.setattr(
        commerce.ProductRepository,
        "deduct_stock",
        lambda product_id, quantity: captured["stock"].append((product_id, quantity))
        or {"before_qty": 10, "after_qty": 10 - quantity},
    )
    monkeypatch.setattr(
        commerce.InventoryRepository,
        "record",
        lambda **values: captured["inventory"].append(values),
    )
    monkeypatch.setattr(commerce.OrderRepository, "create", lambda order_no, customer_id, total, remark, operator_id: captured.update(total=total) or 99)
    monkeypatch.setattr(commerce.OrderRepository, "add_item", lambda *args: captured["items"].append(args))
    monkeypatch.setattr(commerce, "audit", lambda *args, **kwargs: None)

    result = commerce.OrderService.create(7, [
        {"product_id": 1, "quantity": 2}, {"product_id": 2, "quantity": 3}
    ], "", 1)

    assert result == 99
    assert captured["total"] == Decimal("27.30")
    assert captured["stock"] == [(1, 2), (2, 3)]
    assert [row["quantity_delta"] for row in captured["inventory"]] == [-2, -3]
    assert {row["change_type"] for row in captured["inventory"]} == {"sale"}


def test_insufficient_stock_rolls_back(monkeypatch):
    fake_session = FakeSession()
    monkeypatch.setattr(commerce.db, "session", fake_session)
    monkeypatch.setattr(commerce.ProductRepository, "lock", lambda product_id: {
        "product_id": product_id, "status": "active", "stock_qty": 1,
        "unit_price": Decimal("20.00"), "product_name": "库存商品",
    })

    with pytest.raises(commerce.BusinessError, match="库存不足"):
        commerce.OrderService.create(1, [{"product_id": 1, "quantity": 2}], "", 1)
    assert fake_session.rolled_back is True
