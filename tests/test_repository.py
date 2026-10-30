"""Integration tests for PostgreSQL repository operations."""

from decimal import Decimal

import psycopg2
import pytest

from ecommerce.database import pooled_connection
from ecommerce.repository import (
    add_product,
    create_order,
    get_customer_orders,
    update_order_status,
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
def test_create_order_rejects_invalid_input(items, message):
    # Validation runs before any database access, so no pool is needed.
    with pytest.raises(ValueError, match=message):
        create_order(None, customer_id=1, items=items)


@pytest.mark.parametrize("customer_id", [0, -5, True, "1", None])
def test_create_order_rejects_invalid_customer_id(customer_id):
    with pytest.raises(ValueError, match="Customer ID"):
        create_order(None, customer_id, [{"product_id": 1, "quantity": 1}])


@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity", "1.999", "abc"])
def test_add_product_rejects_invalid_price(price):
    with pytest.raises(ValueError):
        add_product(None, name="Valid name", price=price)


@pytest.mark.parametrize("name", ["", "   ", None, 5])
def test_add_product_rejects_invalid_name(name):
    with pytest.raises(ValueError, match="Product name"):
        add_product(None, name=name, price="1.00")


def test_add_product_rejects_non_dict_metadata():
    with pytest.raises(ValueError, match="metadata"):
        add_product(None, name="Valid name", price="1.00", metadata=["red"])


@pytest.mark.parametrize("customer_id", [0, True, "1"])
def test_get_customer_orders_rejects_invalid_customer_id(customer_id):
    with pytest.raises(ValueError, match="Customer ID"):
        get_customer_orders(None, customer_id)


def test_add_product_strips_name_and_defaults_metadata(db_context):
    product = add_product(
        db_context["pool"],
        name=f'  {db_context["product_prefix"]}-plain  ',
        price=3,
    )

    assert product["name"] == f'{db_context["product_prefix"]}-plain'
    assert product["price"] == Decimal("3.00")
    assert product["metadata"] == {}
    assert product["description"] is None


def test_create_order_rejects_missing_customer(db_context):
    with pytest.raises(ValueError, match="Customer 2000000000 does not exist"):
        create_order(
            db_context["pool"],
            customer_id=2_000_000_000,
            items=[{"product_id": db_context["product_id"], "quantity": 1}],
        )


def test_create_order_rejects_missing_product_without_saving(db_context):
    pool = db_context["pool"]
    customer_id = db_context["customer_id"]

    with pytest.raises(ValueError, match=r"Products do not exist: \[2000000000\]"):
        create_order(
            pool,
            customer_id=customer_id,
            items=[
                {"product_id": db_context["product_id"], "quantity": 1},
                {"product_id": 2_000_000_000, "quantity": 1},
            ],
        )

    assert get_customer_orders(pool, customer_id) == []


def test_order_keeps_purchase_price_after_product_price_changes(db_context):
    """order_items.unit_price is a historical snapshot, not the current price."""
    pool = db_context["pool"]
    product_id = db_context["product_id"]

    order = create_order(
        pool,
        customer_id=db_context["customer_id"],
        items=[{"product_id": product_id, "quantity": 2}],
    )

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE products SET price = %s WHERE product_id = %s;",
            ("99.00", product_id),
        )

    [saved] = get_customer_orders(pool, db_context["customer_id"])

    assert saved["order_id"] == order["order_id"]
    assert saved["total_amount"] == Decimal("25.00")
    assert saved["items"][0]["unit_price"] == Decimal("12.50")


def test_create_order_with_multiple_items(db_context):
    pool = db_context["pool"]
    second = add_product(
        pool,
        name=f'{db_context["product_prefix"]}-second',
        price="4.25",
    )

    order = create_order(
        pool,
        customer_id=db_context["customer_id"],
        items=[
            {"product_id": db_context["product_id"], "quantity": 2},
            {"product_id": second["product_id"], "quantity": 4},
        ],
    )

    assert order["status"] == "pending"
    assert order["total_amount"] == Decimal("42.00")

    [saved] = get_customer_orders(pool, db_context["customer_id"])
    line_totals = {item["product_id"]: item["line_total"] for item in saved["items"]}
    assert line_totals == {
        db_context["product_id"]: Decimal("25.00"),
        second["product_id"]: Decimal("17.00"),
    }
    assert all(isinstance(item["unit_price"], Decimal) for item in saved["items"])


def test_get_customer_orders_returns_empty_list_without_orders(db_context):
    assert get_customer_orders(db_context["pool"], db_context["customer_id"]) == []


def test_get_customer_orders_lists_newest_first(db_context):
    pool = db_context["pool"]
    items = [{"product_id": db_context["product_id"], "quantity": 1}]

    first = create_order(pool, db_context["customer_id"], items)
    second = create_order(pool, db_context["customer_id"], items)

    orders = get_customer_orders(pool, db_context["customer_id"])

    assert [order["order_id"] for order in orders] == [
        second["order_id"],
        first["order_id"],
    ]


def test_update_order_status_changes_status(db_context):
    pool = db_context["pool"]
    order = create_order(
        pool,
        customer_id=db_context["customer_id"],
        items=[{"product_id": db_context["product_id"], "quantity": 1}],
    )

    updated = update_order_status(pool, order["order_id"], "paid")

    assert updated == {"order_id": order["order_id"], "status": "paid"}
    orders = get_customer_orders(pool, db_context["customer_id"])
    assert orders[0]["status"] == "paid"


def test_update_order_status_rejects_missing_order(db_context):
    with pytest.raises(ValueError, match="does not exist"):
        update_order_status(db_context["pool"], 2_000_000_000, "paid")


@pytest.mark.parametrize(
    ("order_id", "status"),
    [(1, "refunded"), (1, ""), (0, "paid"), (True, "paid"), ("1", "paid")],
)
def test_update_order_status_rejects_invalid_input(order_id, status):
    # Validation runs before any database access, so no pool is needed.
    with pytest.raises(ValueError):
        update_order_status(None, order_id, status)
