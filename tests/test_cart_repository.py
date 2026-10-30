"""Tests for MongoDB cart repository operations."""

from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from pymongo import ReturnDocument

from ecommerce import cart_repository


@pytest.fixture
def collection():
    """Provide a mocked MongoDB collection."""
    return MagicMock()


def test_create_cart_inserts_active_cart(collection):
    """A new cart should contain its customer and timestamps."""
    result = cart_repository.create_cart(collection, customer_id=42)
    document = collection.insert_one.call_args.args[0]

    assert document["customer_id"] == 42
    assert document["items"] == []
    assert document["status"] == "active"
    assert document["created_at"] == document["updated_at"]
    assert document["expires_at"] - document["updated_at"] == (
        cart_repository.CART_SESSION_TTL
    )
    assert len(document["session_id"]) == 32
    assert result["_id"] == collection.insert_one.return_value.inserted_id


@pytest.mark.parametrize("customer_id", [0, -1, True, "42", None])
def test_create_cart_rejects_invalid_customer_id(collection, customer_id):
    """Customer IDs must be positive integers."""
    with pytest.raises(ValueError, match="customer_id"):
        cart_repository.create_cart(collection, customer_id)

    collection.insert_one.assert_not_called()


def test_get_cart_returns_active_cart(collection):
    """Retrieve a customer's active cart."""
    expected = {"_id": ObjectId(), "customer_id": 42, "items": []}
    collection.find_one.return_value = expected

    result = cart_repository.get_cart(collection, 42)

    collection.find_one.assert_called_once_with({"customer_id": 42, "status": "active"})
    assert result == expected


def test_add_item_increments_existing_product_atomically(collection):
    """Increment an existing product with one atomic database operation."""
    collection.find_one_and_update.return_value = {
        "_id": ObjectId(),
        "customer_id": 42,
        "items": [{"product_id": 101, "quantity": 5}],
    }

    result = cart_repository.add_item(collection, 42, 101, 3)

    collection.find_one_and_update.assert_called_once()
    query, update = collection.find_one_and_update.call_args.args[:2]
    assert query["items.product_id"] == 101
    assert update["$inc"]["items.$.quantity"] == 3
    assert (
        collection.find_one_and_update.call_args.kwargs["return_document"]
        == ReturnDocument.AFTER
    )
    assert result is True


def test_add_item_pushes_new_product_when_not_present(collection):
    """Append a new product when the atomic increment finds no match."""
    collection.find_one_and_update.side_effect = [
        None,
        {"_id": ObjectId(), "customer_id": 42},
    ]

    result = cart_repository.add_item(collection, 42, 101, 2)

    assert collection.find_one_and_update.call_count == 2
    query, update = collection.find_one_and_update.call_args.args[:2]
    assert query["items.product_id"] == {"$ne": 101}
    assert update["$push"]["items"] == {"product_id": 101, "quantity": 2}
    assert result is True


def test_add_item_returns_false_when_cart_does_not_exist(collection):
    """Do not report success when there is no active cart."""
    collection.find_one_and_update.return_value = None

    result = cart_repository.add_item(collection, 42, 101, 1)

    assert result is False
    assert collection.find_one_and_update.call_count == 3


@pytest.mark.parametrize(
    ("customer_id", "product_id", "quantity"),
    [
        (0, 101, 1),
        (42, 0, 1),
        (42, 101, 0),
        (True, 101, 1),
        (42, True, 1),
        (42, 101, True),
        (42, -1, 1),
        (42, 101, -1),
    ],
)
def test_add_item_rejects_invalid_values(collection, customer_id, product_id, quantity):
    """Reject invalid IDs and quantities before querying MongoDB."""
    with pytest.raises(ValueError):
        cart_repository.add_item(collection, customer_id, product_id, quantity)

    collection.find_one_and_update.assert_not_called()


def test_remove_item_removes_product_atomically(collection):
    """Remove a product using a targeted atomic update."""
    collection.update_one.return_value.modified_count = 1

    result = cart_repository.remove_item(collection, 42, 101)

    query, update = collection.update_one.call_args.args[:2]
    assert query["items.product_id"] == 101
    assert update["$pull"]["items"] == {"product_id": 101}
    assert result is True


def test_remove_item_returns_false_when_product_is_missing(collection):
    """Report false when no matching cart item was removed."""
    collection.update_one.return_value.modified_count = 0

    result = cart_repository.remove_item(collection, 42, 101)

    assert result is False


def test_ensure_cart_indexes_creates_lookup_unique_and_ttl_indexes(collection):
    """Create customer lookup, unique active-cart, and session-expiry indexes."""
    cart_repository.ensure_cart_indexes(collection)

    assert collection.create_index.call_count == 3
    collection.create_index.assert_any_call("customer_id")
    collection.create_index.assert_any_call(
        "customer_id",
        unique=True,
        partialFilterExpression={"status": "active"},
        name="unique_active_cart_per_customer",
    )
    collection.create_index.assert_any_call(
        "expires_at",
        expireAfterSeconds=0,
        name="expire_inactive_cart_sessions",
    )


def test_cart_activity_extends_session_expiry(collection):
    """Every cart change refreshes the session's expiry time."""
    cart_repository.add_item(collection, 42, 101, 1)

    update = collection.find_one_and_update.call_args.args[1]
    assert update["$set"]["expires_at"] - update["$set"]["updated_at"] == (
        cart_repository.CART_SESSION_TTL
    )


def test_update_quantity_sets_existing_item(collection):
    """Set a product's quantity with a targeted positional update."""
    collection.update_one.return_value.matched_count = 1

    result = cart_repository.update_quantity(collection, 42, 101, 4)

    query, update = collection.update_one.call_args.args[:2]
    assert query["items.product_id"] == 101
    assert update["$set"]["items.$.quantity"] == 4
    assert result is True


def test_update_quantity_returns_false_when_item_missing(collection):
    collection.update_one.return_value.matched_count = 0

    assert cart_repository.update_quantity(collection, 42, 101, 4) is False


@pytest.mark.parametrize("quantity", [0, -2, True, 1.5])
def test_update_quantity_rejects_invalid_quantity(collection, quantity):
    with pytest.raises(ValueError, match="quantity"):
        cart_repository.update_quantity(collection, 42, 101, quantity)

    collection.update_one.assert_not_called()


def test_clear_cart_empties_items(collection):
    collection.update_one.return_value.matched_count = 1

    result = cart_repository.clear_cart(collection, 42)

    query, update = collection.update_one.call_args.args[:2]
    assert query == {"customer_id": 42, "status": "active"}
    assert update["$set"]["items"] == []
    assert result is True
