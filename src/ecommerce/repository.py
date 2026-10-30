"""Database operations for customers, products, orders, and order history."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from psycopg2 import errors
from psycopg2.extras import Json, RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from ecommerce.cache import invalidate_top_selling_products
from ecommerce.database import pooled_connection

# Must match chk_orders_status in sql/01_schema.sql.
ORDER_STATUSES = frozenset({"pending", "paid", "shipped", "delivered", "cancelled"})


def _validate_positive_id(value: object, label: str) -> None:
    """Reject booleans, non-integers, and IDs that are not positive."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} must be a positive integer.")


def _normalize_email(email: object) -> str:
    """Return a trimmed, lower-case email address or raise ValueError."""
    if not isinstance(email, str) or "@" not in email.strip():
        raise ValueError("Email must be a valid email address.")
    return email.strip().lower()


def add_customer(
    pool: ThreadedConnectionPool,
    full_name: str,
    email: str,
) -> dict[str, Any]:
    """Register a customer; the email address must not already be in use."""
    if not isinstance(full_name, str) or not full_name.strip():
        raise ValueError("Customer name must be a non-empty string.")
    normalized_email = _normalize_email(email)

    try:
        with (
            pooled_connection(pool) as connection,
            connection,
            connection.cursor(cursor_factory=RealDictCursor) as cursor,
        ):
            cursor.execute(
                """
                INSERT INTO customers (full_name, email)
                VALUES (%s, %s)
                RETURNING customer_id, full_name, email, created_at;
                """,
                (full_name.strip(), normalized_email),
            )
            return dict(cursor.fetchone())
    except errors.UniqueViolation as error:
        raise ValueError(
            f"A customer with email {normalized_email} already exists."
        ) from error


def get_customer_by_email(
    pool: ThreadedConnectionPool,
    email: str,
) -> dict[str, Any] | None:
    """Return the customer with this email address, or None."""
    normalized_email = _normalize_email(email)

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor(cursor_factory=RealDictCursor) as cursor,
    ):
        cursor.execute(
            """
            SELECT customer_id, full_name, email, created_at
            FROM customers
            WHERE lower(email) = %s;
            """,
            (normalized_email,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_products(
    pool: ThreadedConnectionPool,
    product_ids: Sequence[int],
) -> dict[int, dict[str, Any]]:
    """Return the requested products keyed by ID; unknown IDs are left out."""
    for product_id in product_ids:
        _validate_positive_id(product_id, "Product ID")
    if not product_ids:
        return {}

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor(cursor_factory=RealDictCursor) as cursor,
    ):
        cursor.execute(
            """
            SELECT product_id, name, description, price, metadata
            FROM products
            WHERE product_id = ANY(%s);
            """,
            (list(product_ids),),
        )
        return {row["product_id"]: dict(row) for row in cursor.fetchall()}


def add_product(
    pool: ThreadedConnectionPool,
    name: str,
    price: Decimal | int | str,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
    cache: Any | None = None,
) -> dict[str, Any]:
    """
    Insert a product and return its database record.

    If a Redis client is given, cached top-selling lists are cleared after the
    insert commits, because a new product can enter a partly filled ranking.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Product name must be a non-empty string.")

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
        product = dict(cursor.fetchone())

    if cache is not None:
        invalidate_top_selling_products(cache)

    return product


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
    _validate_positive_id(customer_id, "Customer ID")

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

        _validate_positive_id(product_id, "Product ID")

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
    cache: Any | None = None,
) -> dict[str, Any]:
    """
    Change an order's status, for example to mark it paid or cancelled.

    Status changes decide whether an order counts as a sale, so if a Redis
    client is given, cached top-selling lists are cleared after the commit.
    """
    _validate_positive_id(order_id, "Order ID")

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

    if cache is not None:
        invalidate_top_selling_products(cache)

    return dict(row)


def get_customer_orders(
    pool: ThreadedConnectionPool,
    customer_id: int,
) -> list[dict[str, Any]]:
    """
    Return a customer's orders, newest first, with totals and item details.

    Money values inside the items JSON are sent as text and converted to
    Decimal, because JSON numbers would otherwise be decoded as floats.
    """
    _validate_positive_id(customer_id, "Customer ID")

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
                            'unit_price', oi.unit_price::TEXT,
                            'line_total', (oi.quantity * oi.unit_price)::TEXT
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
        orders = [dict(row) for row in cursor.fetchall()]

    for order in orders:
        for item in order["items"]:
            item["unit_price"] = Decimal(item["unit_price"])
            item["line_total"] = Decimal(item["line_total"])

    return orders
