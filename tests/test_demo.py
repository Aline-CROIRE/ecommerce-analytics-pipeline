"""End-to-end test: drive the interactive shop by typing menu choices."""

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import redis
from pymongo.errors import PyMongoError

from ecommerce.cache import create_redis_client
from ecommerce.database import pooled_connection
from ecommerce.mongodb import create_mongo_client, get_database
from ecommerce.repository import (
    add_product,
    get_customer_by_email,
    get_customer_orders,
)

DEMO_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo.py"


@pytest.fixture
def mongo_carts():
    """Skip unless Redis and MongoDB are reachable (PostgreSQL is required)."""
    try:
        cache = create_redis_client()
        cache.ping()
        cache.close()
        client = create_mongo_client()
        client.admin.command("ping")
    except (RuntimeError, redis.RedisError, PyMongoError) as error:
        pytest.skip(f"The shop needs all services: {error}")
    try:
        yield get_database(client)["carts"]
    finally:
        client.close()


def run_shop(*inputs: str) -> str:
    completed = subprocess.run(
        [sys.executable, str(DEMO_SCRIPT)],
        input="\n".join(inputs) + "\n",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_customer_can_shop_end_to_end(db_context, mongo_carts):
    pool = db_context["pool"]
    token = uuid4().hex
    email = f"shopper-{token}@example.com"
    product = add_product(
        pool,
        name=f'{db_context["product_prefix"]}-shop',
        price="12.50",
        metadata={"category": f"shop-{token}", "sizes": ["M"]},
    )
    product_id = str(product["product_id"])

    try:
        output = run_shop(
            "5",  # View cart before signing in.
            "1",
            email,
            "Shop Tester",  # Register.
            "3",
            f"category=shop-{token} sizes=M",  # JSONB search.
            "4",
            product_id,
            "3",  # Add 3 to cart.
            "6",
            product_id,
            "2",  # Change quantity to 2.
            "5",  # View cart.
            "7",  # Checkout.
            "8",  # My orders.
            "9",
            "2000000000",  # Someone else's order.
            "2",  # Best sellers.
            "99",  # Unknown option.
            "0",
        )

        assert "Please sign in first" in output
        assert "Registered customer" in output
        assert "1 products found" in output
        assert "Added 3 x" in output
        assert "Cart updated." in output
        assert "Estimated total (current prices)" in output
        assert "placed: total 25.00, status 'pending'" in output
        assert "is not one of your orders" in output
        assert "best sellers, served from" in output
        assert "Unknown option" in output
        assert output.rstrip().endswith("Goodbye!")

        customer = get_customer_by_email(pool, email)
        [order] = get_customer_orders(pool, customer["customer_id"])
        assert order["items"][0]["quantity"] == 2
        closed_cart = mongo_carts.find_one({"customer_id": customer["customer_id"]})
        assert closed_cart["status"] == "checked_out"

    finally:
        customer = get_customer_by_email(pool, email)
        if customer is not None:
            mongo_carts.delete_many({"customer_id": customer["customer_id"]})
            with (
                pooled_connection(pool) as connection,
                connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "DELETE FROM orders WHERE customer_id = %s;",
                    (customer["customer_id"],),
                )
                cursor.execute(
                    "DELETE FROM customers WHERE customer_id = %s;",
                    (customer["customer_id"],),
                )


def test_returning_customer_signs_in_and_can_pay(db_context, mongo_carts):
    pool = db_context["pool"]
    customer_id = db_context["customer_id"]
    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT email FROM customers WHERE customer_id = %s;", (customer_id,)
        )
        email = cursor.fetchone()[0]

    try:
        output = run_shop(
            "1",
            email.upper(),  # Existing account; email is case-insensitive.
            "4",
            str(db_context["product_id"]),
            "1",
            "7",
            "0",
        )
        [order] = get_customer_orders(pool, customer_id)

        output += run_shop("1", email, "9", str(order["order_id"]), "p", "0")

        assert "Welcome, Pytest Customer!" in output
        assert "Registered customer" not in output
        assert f"Order #{order['order_id']} is now 'paid'" in output
        assert get_customer_orders(pool, customer_id)[0]["status"] == "paid"
    finally:
        mongo_carts.delete_many({"customer_id": customer_id})


def test_shop_explains_invalid_input():
    output = run_shop("1", "not-an-email", "3", "category", "0")

    assert "Sorry: Email must be a valid email address." in output
    assert "should look like key=value" in output
