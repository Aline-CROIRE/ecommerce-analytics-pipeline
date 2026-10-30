"""Integration tests for the setup and sample-data SQL scripts."""

import pytest

from ecommerce.analytics import load_sql
from ecommerce.database import create_pool, pooled_connection

SAMPLE_COUNTS_QUERY = """
    SELECT
        (SELECT COUNT(*) FROM customers
         WHERE email LIKE 'sample.%%@example.com'),
        (SELECT COUNT(*) FROM orders AS o
         INNER JOIN customers AS c ON c.customer_id = o.customer_id
         WHERE c.email LIKE 'sample.%%@example.com'),
        (SELECT COUNT(*) FROM order_items AS oi
         INNER JOIN orders AS o ON o.order_id = oi.order_id
         INNER JOIN customers AS c ON c.customer_id = o.customer_id
         WHERE c.email LIKE 'sample.%%@example.com');
"""


@pytest.fixture
def pool():
    """Provide a connection pool for the configured test database."""
    connection_pool = create_pool()
    try:
        yield connection_pool
    finally:
        connection_pool.closeall()


def _run_script(pool, filename):
    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(load_sql(filename))


def _fetch_one(pool, query, parameters=()):
    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(query, parameters)
        return cursor.fetchone()


def test_setup_scripts_create_tables_and_indexes(pool):
    """Re-running the setup scripts succeeds and creates both indexes."""
    _run_script(pool, "01_schema.sql")
    _run_script(pool, "05_indexes.sql")

    (index_count,) = _fetch_one(
        pool,
        """
        SELECT COUNT(*)
        FROM pg_indexes
        WHERE indexname IN ('idx_orders_customer_id', 'idx_products_metadata_gin');
        """,
    )

    assert index_count == 2


def test_sample_data_script_is_idempotent(pool):
    """Running the sample-data script twice does not duplicate rows."""
    _run_script(pool, "07_sample_data.sql")
    first_counts = _fetch_one(pool, SAMPLE_COUNTS_QUERY)

    _run_script(pool, "07_sample_data.sql")
    second_counts = _fetch_one(pool, SAMPLE_COUNTS_QUERY)

    assert first_counts == second_counts
    assert first_counts == (3, 4, 6)


def test_sample_products_store_jsonb_attributes(pool):
    """Sample products expose flexible attributes such as sizes and colors."""
    _run_script(pool, "07_sample_data.sql")

    (metadata,) = _fetch_one(
        pool,
        """
        SELECT metadata
        FROM products
        WHERE name = %s
        ORDER BY product_id
        LIMIT 1;
        """,
        ("Cotton T-Shirt",),
    )

    assert metadata["category"] == "clothing"
    assert "M" in metadata["sizes"]
    assert "navy" in metadata["colors"]
