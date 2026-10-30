"""Database operations for products, orders, and customer history."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from psycopg2.extras import Json, RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from ecommerce.database import pooled_connection

# Must match chk_orders_status in sql/01_schema.sql.
ORDER_STATUSES = frozenset({"pending", "paid", "shipped", "delivered", "cancelled"})


def add_product(
    pool: ThreadedConnectionPool,
    name: str,
    price: Decimal | int | str,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a product and return its database record."""
    if not name.strip():
        raise ValueError("Product name cannot be empty.")

    try:
        product_price = Decimal(str(price))
    except (ValueError, ArithmeticError) as error:
        raise ValueError("Price must be a valid decimal amount.") from error

    if not product_price.is_finite() or product_price < 0:
        raise ValueError("Price must be finite and non-negative.")

    if product_price.as_tuple().exponent < -2:
        raise ValueError("Price cannot have more than two decimal places.")

    product_metadata = metadata if metadata is not None else {}
    if not isinstance(product_metadata, dict):
        raise ValueError("Product metadata must be a dictionary.")

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor(cursor_factory=RealDictCursor) as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO products (name, description, price, metadata)
            VALUES (%s, %s, %s, %s)
            RETURNING product_id, name, description, price, metadata,
                      created_at;
            """,
            (
                name.strip(),
                description,
                product_price,
                Json(product_metadata),
            ),
        )
        return dict(cursor.fetchone())


def find_products_by_metadata(
    pool: ThreadedConnectionPool,
    criteria: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Return products whose JSONB metadata contains all the given attributes.

    Uses the @> containment operator, which idx_products_metadata_gin supports.
    Example: {"category": "clothing", "sizes": ["M"]} matches clothing in size M.
    """
    if not isinstance(criteria, dict) or not criteria:
        raise ValueError("Metadata criteria must be a non-empty dictionary.")

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor(cursor_factory=RealDictCursor) as cursor,
    ):
        cursor.execute(
            """
            SELECT
                product_id,
                name,
                price,
                metadata ->> 'category' AS category,
                metadata
            FROM products
            WHERE metadata @> %s
            ORDER BY product_id;
            """,
            (Json(criteria),),
        )
        return [dict(row) for row in cursor.fetchall()]


def find_products_with_attribute(
    pool: ThreadedConnectionPool,
    attribute: str,
) -> list[dict[str, Any]]:
    """
    Return products whose metadata defines the given top-level attribute.

    Uses the ? key-exists operator, which idx_products_metadata_gin supports.
    """
    if not isinstance(attribute, str) or not attribute.strip():
        raise ValueError("Attribute must be a non-empty string.")

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor(cursor_factory=RealDictCursor) as cursor,
    ):
        cursor.execute(
            """
            SELECT
                product_id,
                name,
                price,
                metadata -> %s AS attribute_value
            FROM products
            WHERE metadata ? %s
            ORDER BY product_id;
            """,
            (attribute, attribute),
        )
        return [dict(row) for row in cursor.fetchall()]


def create_order(
    pool: ThreadedConnectionPool,
    customer_id: int,
    items: Sequence[Mapping[str, int]],
) -> dict[str, Any]:
    """
    Create an order and its items atomically.

    Each item must contain product_id and quantity. Product prices are read
    from PostgreSQL and copied into order_items as historical purchase prices.
    """
    if not items:
        raise ValueError("An order must contain at least one item.")

    normalized_items: list[tuple[int, int]] = []
    seen_products: set[int] = set()

    for item in items:
        try:
            product_id = item["product_id"]
            quantity = item["quantity"]
        except (KeyError, TypeError) as error:
            raise ValueError("Each item needs product_id and quantity.") from error

        if (
            not isinstance(product_id, int)
            or isinstance(product_id, bool)
            or product_id <= 0
        ):
            raise ValueError("Product IDs must be positive integers.")

        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise ValueError("Quantities must be positive integers.")

        if product_id in seen_products:
            raise ValueError("Each product can appear only once per order.")

        seen_products.add(product_id)
        normalized_items.append((product_id, quantity))

    product_ids = [product_id for product_id, _ in normalized_items]

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor(cursor_factory=RealDictCursor) as cursor,
    ):
        cursor.execute(
            """
            SELECT customer_id
            FROM customers
            WHERE customer_id = %s;
            """,
            (customer_id,),
        )
        if cursor.fetchone() is None:
            raise ValueError(f"Customer {customer_id} does not exist.")

        cursor.execute(
            """
            SELECT product_id, price
            FROM products
            WHERE product_id = ANY(%s)
            FOR SHARE;
            """,
            (product_ids,),
        )
        prices = {row["product_id"]: row["price"] for row in cursor.fetchall()}

        missing_ids = set(product_ids) - prices.keys()
        if missing_ids:
            raise ValueError(f"Products do not exist: {sorted(missing_ids)}")

        cursor.execute(
            """
            INSERT INTO orders (customer_id)
            VALUES (%s)
            RETURNING order_id, order_date, status;
            """,
            (customer_id,),
        )
        order = dict(cursor.fetchone())

        total = Decimal("0.00")
        for product_id, quantity in normalized_items:
            unit_price = prices[product_id]
            cursor.execute(
                """
                INSERT INTO order_items
                    (order_id, product_id, quantity, unit_price)
                VALUES (%s, %s, %s, %s);
                """,
                (order["order_id"], product_id, quantity, unit_price),
            )
            total += unit_price * quantity

        order["total_amount"] = total
        return order


def update_order_status(
    pool: ThreadedConnectionPool,
    order_id: int,
    status: str,
) -> dict[str, Any]:
    """Change an order's status, for example to mark it paid or cancelled."""
    if not isinstance(order_id, int) or isinstance(order_id, bool) or order_id <= 0:
        raise ValueError("Order ID must be a positive integer.")

    if status not in ORDER_STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(sorted(ORDER_STATUSES))}.")

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor(cursor_factory=RealDictCursor) as cursor,
    ):
        cursor.execute(
            """
            UPDATE orders
            SET status = %s
            WHERE order_id = %s
            RETURNING order_id, status;
            """,
            (status, order_id),
        )
        row = cursor.fetchone()

        if row is None:
            raise ValueError(f"Order {order_id} does not exist.")

        return dict(row)


def get_customer_orders(
    pool: ThreadedConnectionPool,
    customer_id: int,
) -> list[dict[str, Any]]:
    """Return a customer's orders, totals, and item details."""
    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor(cursor_factory=RealDictCursor) as cursor,
    ):
        cursor.execute(
            """
            SELECT
                o.order_id,
                o.order_date,
                o.status,
                COALESCE(
                    SUM(oi.quantity * oi.unit_price),
                    0
                ) AS total_amount,
                COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'product_id', p.product_id,
                            'product_name', p.name,
                            'quantity', oi.quantity,
                            'unit_price', oi.unit_price,
                            'line_total', oi.quantity * oi.unit_price
                        )
                        ORDER BY p.name
                    ) FILTER (WHERE oi.order_item_id IS NOT NULL),
                    '[]'::jsonb
                ) AS items
            FROM orders AS o
            LEFT JOIN order_items AS oi
                ON oi.order_id = o.order_id
            LEFT JOIN products AS p
                ON p.product_id = oi.product_id
            WHERE o.customer_id = %s
            GROUP BY o.order_id, o.order_date, o.status
            ORDER BY o.order_date DESC, o.order_id DESC;
            """,
            (customer_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
