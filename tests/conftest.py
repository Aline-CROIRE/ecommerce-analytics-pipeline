"""Shared fixtures for PostgreSQL integration tests."""

from uuid import uuid4

import pytest
from psycopg2.extras import Json

from ecommerce.database import create_pool, pooled_connection


@pytest.fixture
def db_context():
    """Create isolated customer/product data and clean it up afterward."""
    token = uuid4().hex
    product_prefix = f"pytest-{token}"
    email = f"{token}@example.com"
    pool = create_pool()

    try:
        with (
            pooled_connection(pool) as connection,
            connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                INSERT INTO customers (full_name, email)
                VALUES (%s, %s)
                RETURNING customer_id;
                """,
                ("Pytest Customer", email),
            )
            customer_id = cursor.fetchone()[0]

            cursor.execute(
                """
                INSERT INTO products (name, price, metadata)
                VALUES (%s, %s, %s)
                RETURNING product_id;
                """,
                (
                    f"{product_prefix}-initial",
                    "12.50",
                    Json({"category": "testing"}),
                ),
            )
            product_id = cursor.fetchone()[0]

        yield {
            "pool": pool,
            "customer_id": customer_id,
            "product_id": product_id,
            "product_prefix": product_prefix,
        }

    finally:
        try:
            with (
                pooled_connection(pool) as connection,
                connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "DELETE FROM orders WHERE customer_id = %s;",
                    (locals().get("customer_id"),),
                )
                cursor.execute(
                    "DELETE FROM customers WHERE email = %s;",
                    (email,),
                )
                cursor.execute(
                    """
                    DELETE FROM products
                    WHERE name LIKE %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM order_items
                          WHERE order_items.product_id = products.product_id
                      );
                    """,
                    (f"{product_prefix}%",),
                )
        finally:
            pool.closeall()
