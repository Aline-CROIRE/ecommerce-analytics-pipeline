"""Live MongoDB integration tests for shopping cart operations."""

import os
from uuid import uuid4

import pytest
from pymongo.errors import DuplicateKeyError

from ecommerce.cart_repository import (
    add_item,
    create_cart,
    ensure_cart_indexes,
    get_cart,
    remove_item,
)
from ecommerce.mongodb import create_mongo_client, get_database


@pytest.mark.mongodb
@pytest.mark.skipif(
    os.getenv("RUN_MONGODB_INTEGRATION") != "1",
    reason="Set RUN_MONGODB_INTEGRATION=1 to run live MongoDB tests.",
)
def test_cart_operations_against_real_mongodb():
    """Verify cart operations and indexes against a live MongoDB server."""
    client = create_mongo_client()
    collection = None

    try:
        client.admin.command("ping")
        database = get_database(client)
        collection = database[f"cart_integration_{uuid4().hex}"]

        ensure_cart_indexes(collection)

        customer_id = 900_000_000 + int(uuid4().int % 100_000_000)
        create_cart(collection, customer_id)

        assert add_item(collection, customer_id, 101, 2)
        assert add_item(collection, customer_id, 101, 3)
        assert add_item(collection, customer_id, 202, 1)

        cart = get_cart(collection, customer_id)
        assert cart is not None

        quantities = {item["product_id"]: item["quantity"] for item in cart["items"]}
        assert quantities == {101: 5, 202: 1}

        assert remove_item(collection, customer_id, 101)

        cart = get_cart(collection, customer_id)
        assert cart is not None
        assert cart["items"] == [{"product_id": 202, "quantity": 1}]

        with pytest.raises(DuplicateKeyError):
            create_cart(collection, customer_id)

    finally:
        if collection is not None:
            collection.drop()
        client.close()
