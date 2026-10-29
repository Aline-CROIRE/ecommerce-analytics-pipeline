"""Load deterministic sample customers, products, orders, and items."""

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ecommerce.database import create_pool, pooled_connection  # noqa: E402

CUSTOMERS = [
    ("Sample Customer One", "sample.one@example.com"),
    ("Sample Customer Two", "sample.two@example.com"),
    ("Sample Customer Three", "sample.three@example.com"),
]

PRODUCTS = [
    ("Wireless Mouse", Decimal("25.00"), {"category": "accessories"}),
    ("Mechanical Keyboard", Decimal("75.00"), {"category": "accessories"}),
    ("USB-C Hub", Decimal("40.00"), {"category": "electronics"}),
]

SAMPLE_ORDERS = [
    {
        "email": "sample.one@example.com",
        "date": datetime(2026, 10, 20, 10, 0, tzinfo=UTC),
        "status": "delivered",
        "items": [("Wireless Mouse", 2), ("USB-C Hub", 1)],
    },
    {
        "email": "sample.two@example.com",
        "date": datetime(2026, 10, 21, 11, 30, tzinfo=UTC),
        "status": "shipped",
        "items": [("Mechanical Keyboard", 1)],
    },
    {
        "email": "sample.three@example.com",
        "date": datetime(2026, 10, 22, 14, 15, tzinfo=UTC),
        "status": "paid",
        "items": [("Wireless Mouse", 1), ("Mechanical Keyboard", 1)],
    },
]


def load_sample_data() -> None:
    """Insert sample data in one transaction."""
    pool = create_pool()
    try:
        with (
            pooled_connection(pool) as connection,
            connection,
            connection.cursor() as cursor,
        ):
            customer_ids = {}
            for full_name, email in CUSTOMERS:
                cursor.execute(
                    """
                    INSERT INTO customers (full_name, email)
                    VALUES (%s, %s)
                    ON CONFLICT (email)
                    DO UPDATE SET full_name = EXCLUDED.full_name
                    RETURNING customer_id;
                    """,
                    (full_name, email),
                )
                customer_ids[email] = cursor.fetchone()[0]

            product_ids = {}
            for name, price, metadata in PRODUCTS:
                cursor.execute(
                    """
                    SELECT product_id
                    FROM products
                    WHERE name = %s
                    ORDER BY product_id
                    LIMIT 1;
                    """,
                    (name,),
                )
                row = cursor.fetchone()

                if row:
                    product_ids[name] = row[0]
                else:
                    cursor.execute(
                        """
                        INSERT INTO products (name, price, metadata)
                        VALUES (%s, %s, %s)
                        RETURNING product_id;
                        """,
                        (name, price, Json(metadata)),
                    )
                    product_ids[name] = cursor.fetchone()[0]

            for sample_order in SAMPLE_ORDERS:
                customer_id = customer_ids[sample_order["email"]]
                order_date = sample_order["date"]

                cursor.execute(
                    """
                    SELECT order_id
                    FROM orders
                    WHERE customer_id = %s AND order_date = %s;
                    """,
                    (customer_id, order_date),
                )
                row = cursor.fetchone()

                if row:
                    order_id = row[0]
                else:
                    cursor.execute(
                        """
                        INSERT INTO orders
                            (customer_id, order_date, status)
                        VALUES (%s, %s, %s)
                        RETURNING order_id;
                        """,
                        (
                            customer_id,
                            order_date,
                            sample_order["status"],
                        ),
                    )
                    order_id = cursor.fetchone()[0]

                for product_name, quantity in sample_order["items"]:
                    product_id = product_ids[product_name]
                    cursor.execute(
                        """
                        INSERT INTO order_items
                            (order_id, product_id, quantity, unit_price)
                        SELECT %s, %s, %s, price
                        FROM products
                        WHERE product_id = %s
                        ON CONFLICT (order_id, product_id)
                        DO UPDATE SET
                            quantity = EXCLUDED.quantity,
                            unit_price = EXCLUDED.unit_price;
                        """,
                        (
                            order_id,
                            product_id,
                            quantity,
                            product_id,
                        ),
                    )

        print("Sample data loaded successfully.")
        print(f"Customers: {len(CUSTOMERS)}")
        print(f"Products: {len(PRODUCTS)}")
        print(f"Sample orders: {len(SAMPLE_ORDERS)}")

    finally:
        pool.closeall()


if __name__ == "__main__":
    try:
        load_sample_data()
    except (psycopg2.Error, OSError, RuntimeError, ValueError) as error:
        print(f"Sample data loading failed: {error}", file=sys.stderr)
        sys.exit(1)
