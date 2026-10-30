"""Unit tests for converting MongoDB carts into PostgreSQL orders."""

from unittest.mock import MagicMock

import pytest
from bson import ObjectId

from ecommerce import checkout


@pytest.fixture
def cart():
    return {
        "_id": ObjectId(),
        "customer_id": 42,
        "status": "checking_out",
        "items": [
            {"product_id": 101, "quantity": 2},
            {"product_id": 202, "quantity": 1},
        ],
    }


def test_checkout_places_order_and_closes_cart(monkeypatch, cart):
    collection = MagicMock()
    collection.find_one_and_update.return_value = cart
    create_order = MagicMock(return_value={"order_id": 7, "status": "pending"})
    monkeypatch.setattr(checkout, "create_order", create_order)

    order = checkout.checkout_cart(collection, pool="pool", customer_id=42)

    assert order["order_id"] == 7
    create_order.assert_called_once_with("pool", 42, cart["items"])

    claim_query, claim_update = collection.find_one_and_update.call_args.args[:2]
    assert claim_query["status"] == "active"
    assert claim_query["items.0"] == {"$exists": True}
    assert claim_update["$set"]["status"] == "checking_out"

    close_query, close_update = collection.update_one.call_args.args[:2]
    assert close_query == {"_id": cart["_id"]}
    assert close_update["$set"]["status"] == "checked_out"
    assert close_update["$set"]["order_id"] == 7
    assert "expires_at" in close_update["$unset"]


def test_checkout_restores_cart_when_order_fails(monkeypatch, cart):
    collection = MagicMock()
    collection.find_one_and_update.return_value = cart
    monkeypatch.setattr(
        checkout,
        "create_order",
        MagicMock(side_effect=ValueError("Products do not exist: [202]")),
    )

    with pytest.raises(ValueError, match="Products do not exist"):
        checkout.checkout_cart(collection, pool="pool", customer_id=42)

    restore_query, restore_update = collection.update_one.call_args.args[:2]
    assert restore_query == {"_id": cart["_id"], "status": "checking_out"}
    assert restore_update["$set"]["status"] == "active"


def test_checkout_rejects_missing_or_empty_cart(monkeypatch):
    collection = MagicMock()
    collection.find_one_and_update.return_value = None
    create_order = MagicMock()
    monkeypatch.setattr(checkout, "create_order", create_order)

    with pytest.raises(ValueError, match="no active cart with items"):
        checkout.checkout_cart(collection, pool="pool", customer_id=42)

    create_order.assert_not_called()


@pytest.mark.parametrize("customer_id", [0, -1, True, "42"])
def test_checkout_rejects_invalid_customer_id(customer_id):
    collection = MagicMock()

    with pytest.raises(ValueError, match="customer_id"):
        checkout.checkout_cart(collection, pool="pool", customer_id=customer_id)

    collection.find_one_and_update.assert_not_called()
