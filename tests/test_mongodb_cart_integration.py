"""Live MongoDB integration tests for shopping-cart sessions and checkout."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pymongo.errors import DuplicateKeyError, PyMongoError

from ecommerce.cart_repository import (
    add_item,
    clear_cart,
    create_cart,
    ensure_cart_indexes,
    get_cart,
    remove_item,
    update_quantity,
)
from ecommerce.checkout import checkout_cart
from ecommerce.mongodb import create_mongo_client, get_database
from ecommerce.repository import get_customer_orders

pytestmark = pytest.mark.mongodb


@pytest.fixture
def carts():
    """Provide a temporary cart collection, skipping if MongoDB is unavailable."""
    try:
        client = create_mongo_client()
        client.admin.command("ping")
    except (RuntimeError, PyMongoError) as error:
        pytest.skip(f"MongoDB is unavailable: {error}")

    collection = get_database(client)[f"cart_integration_{uuid4().hex}"]
    ensure_cart_indexes(collection)
    try:
        yield collection
    finally:
        collection.drop()
        client.close()


def _unique_customer_id():
    return 900_000_000 + int(uuid4().int % 100_000_000)


def test_cart_operations_against_real_mongodb(carts):
    """Verify cart operations and indexes against a live MongoDB server."""
    customer_id = _unique_customer_id()
    create_cart(carts, customer_id)

    assert add_item(carts, customer_id, 101, 2)
    assert add_item(carts, customer_id, 101, 3)
    assert add_item(carts, customer_id, 202, 1)

    cart = get_cart(carts, customer_id)
    quantities = {item["product_id"]: item["quantity"] for item in cart["items"]}
    assert quantities == {101: 5, 202: 1}

    assert update_quantity(carts, customer_id, 202, 4)
    assert remove_item(carts, customer_id, 101)

    cart = get_cart(carts, customer_id)
    assert cart["items"] == [{"product_id": 202, "quantity": 4}]
    assert cart["expires_at"] > cart["created_at"]

    assert clear_cart(carts, customer_id)
    assert get_cart(carts, customer_id)["items"] == []

    with pytest.raises(DuplicateKeyError):
        create_cart(carts, customer_id)


def test_session_expiry_index_exists(carts):
    indexes = carts.index_information()

    assert indexes["expire_inactive_cart_sessions"]["expireAfterSeconds"] == 0


def test_checkout_creates_postgres_order_from_cart(carts, db_context):
    """A MongoDB cart becomes a PostgreSQL order and the cart is closed."""
    pool = db_context["pool"]
    customer_id = db_context["customer_id"]
    product_id = db_context["product_id"]

    create_cart(carts, customer_id)
    add_item(carts, customer_id, product_id, 3)

    order = checkout_cart(carts, pool, customer_id)

    assert order["total_amount"] == Decimal("37.50")
    saved_orders = get_customer_orders(pool, customer_id)
    assert [row["order_id"] for row in saved_orders] == [order["order_id"]]
    assert saved_orders[0]["items"][0]["quantity"] == 3

    closed = carts.find_one({"customer_id": customer_id})
    assert closed["status"] == "checked_out"
    assert closed["order_id"] == order["order_id"]
    assert "expires_at" not in closed
    assert get_cart(carts, customer_id) is None

    # The customer can start a new cart after checkout.
    create_cart(carts, customer_id)


def test_failed_checkout_keeps_cart_active(carts, db_context):
    """An unknown product rolls back the order and leaves the cart usable."""
    pool = db_context["pool"]
    customer_id = db_context["customer_id"]

    create_cart(carts, customer_id)
    add_item(carts, customer_id, 2_000_000_000, 1)

    with pytest.raises(ValueError, match="Products do not exist"):
        checkout_cart(carts, pool, customer_id)

    assert get_customer_orders(pool, customer_id) == []
    assert get_cart(carts, customer_id)["status"] == "active"
