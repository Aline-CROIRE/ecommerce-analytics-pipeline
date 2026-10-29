"""Regression tests for concurrent cart item updates."""

from unittest.mock import MagicMock

from bson import ObjectId

from ecommerce.cart_repository import add_item


def test_add_item_retries_increment_after_concurrent_insert():
    """Retry incrementing when another request inserts the item first."""
    collection = MagicMock()

    # First: the product is absent.
    # Second: another request inserts it before our push operation.
    # Third: our retry finds the newly inserted product.
    collection.find_one_and_update.side_effect = [
        None,
        None,
        {
            "_id": ObjectId(),
            "customer_id": 42,
            "items": [{"product_id": 101, "quantity": 5}],
        },
    ]

    result = add_item(collection, customer_id=42, product_id=101, quantity=2)

    assert result is True
    assert collection.find_one_and_update.call_count == 3

    retry_query, retry_update = collection.find_one_and_update.call_args.args[:2]

    assert retry_query["customer_id"] == 42
    assert retry_query["status"] == "active"
    assert retry_query["items.product_id"] == 101
    assert retry_update["$inc"]["items.$.quantity"] == 2
