"""Integration tests for PostgreSQL repository operations."""

from decimal import Decimal

import psycopg2
import pytest

from ecommerce.repository import (
    add_product,
    create_order,
    get_customer_orders,
)


def test_add_product_returns_saved_product(db_context):
    product = add_product(
        db_context["pool"],
        name=f'{db_context["product_prefix"]}-new',
        price="19.95",
        description="A product created by a test",
        metadata={"category": "testing", "active": True},
    )

    assert product["product_id"] > 0
    assert product["price"] == Decimal("19.95")
    assert product["metadata"]["category"] == "testing"


def test_create_order_and_get_customer_orders(db_context):
    order = create_order(
        db_context["pool"],
        customer_id=db_context["customer_id"],
        items=[
            {
                "product_id": db_context["product_id"],
                "quantity": 2,
            }
        ],
    )

    assert order["order_id"] > 0
    assert order["total_amount"] == Decimal("25.00")

    orders = get_customer_orders(
        db_context["pool"],
        db_context["customer_id"],
    )

    assert len(orders) == 1
    assert orders[0]["order_id"] == order["order_id"]
    assert orders[0]["total_amount"] == Decimal("25.00")
    assert orders[0]["items"][0]["quantity"] == 2


def test_invalid_order_rolls_back_database_changes(db_context):
    pool = db_context["pool"]
    customer_id = db_context["customer_id"]
    product_id = db_context["product_id"]

    before = get_customer_orders(pool, customer_id)

    with pytest.raises(psycopg2.Error):
        create_order(
            pool,
            customer_id=customer_id,
            items=[
                {
                    "product_id": product_id,
                    "quantity": 2_147_483_648,
                }
            ],
        )

    after = get_customer_orders(pool, customer_id)
    assert len(after) == len(before)


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([], "at least one item"),
        (
            [{"product_id": 1, "quantity": 0}],
            "positive integers",
        ),
        (
            [
                {"product_id": 1, "quantity": 1},
                {"product_id": 1, "quantity": 2},
            ],
            "only once",
        ),
    ],
)
def test_create_order_rejects_invalid_input(db_context, items, message):
    with pytest.raises(ValueError, match=message):
        create_order(
            db_context["pool"],
            customer_id=db_context["customer_id"],
            items=items,
        )


@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity", "1.999"])
def test_add_product_rejects_invalid_price(db_context, price):
    with pytest.raises(ValueError):
        add_product(
            db_context["pool"],
            name=f'{db_context["product_prefix"]}-invalid',
            price=price,
        )
