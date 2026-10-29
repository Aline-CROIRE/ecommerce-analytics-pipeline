"""Execute SQL analytics reports and compare PostgreSQL query plans."""

from pathlib import Path
from typing import Any

from psycopg2.extras import Json, RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from ecommerce.database import pooled_connection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = PROJECT_ROOT / "sql"

REPORT_FILES = (
    "02_customer_revenue.sql",
    "03_product_ranking.sql",
    "04_monthly_revenue.sql",
)


def load_sql(filename: str) -> str:
    """Read a SQL file from the project's SQL directory."""
    path = SQL_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"SQL file not found: {path}")
    return path.read_text(encoding="utf-8")


def run_analytics(pool: ThreadedConnectionPool) -> dict[str, list[dict[str, Any]]]:
    """Execute all analytics reports and return their rows."""
    results = {}

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor(cursor_factory=RealDictCursor) as cursor,
    ):
        for filename in REPORT_FILES:
            cursor.execute(load_sql(filename))
            results[filename] = [dict(row) for row in cursor.fetchall()]

    return results


def _explain_query(
    cursor: Any,
    label: str,
    query: str,
    parameters: tuple[Any, ...],
) -> str:
    """Execute a query with EXPLAIN ANALYZE and return its text plan."""
    cursor.execute(
        f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {query}",
        parameters,
    )
    plan = "\n".join(row[0] for row in cursor.fetchall())
    print(f"\n--- {label} ---\n{plan}")
    return plan


def compare_index_plans(pool: ThreadedConnectionPool) -> dict[str, dict[str, str]]:
    """Compare query plans before and after creating the project indexes."""
    customer_query = """
        SELECT order_id, order_date, status
        FROM orders
        WHERE customer_id = %s;
    """
    metadata_query = """
        SELECT product_id, name, price
        FROM products
        WHERE metadata @> %s::jsonb;
    """

    plans: dict[str, dict[str, str]] = {
        "before_indexes": {},
        "after_indexes": {},
    }

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT customer_id FROM customers ORDER BY customer_id LIMIT 1;"
        )
        customer_row = cursor.fetchone()
        customer_id = customer_row[0] if customer_row else -1

        # Remove only the two indexes this project is measuring.
        cursor.execute("DROP INDEX IF EXISTS idx_orders_customer_id;")
        cursor.execute("DROP INDEX IF EXISTS idx_products_metadata_gin;")
        cursor.execute("ANALYZE orders;")
        cursor.execute("ANALYZE products;")

        plans["before_indexes"]["customer_orders"] = _explain_query(
            cursor,
            "Customer orders — before indexes",
            customer_query,
            (customer_id,),
        )
        plans["before_indexes"]["product_metadata"] = _explain_query(
            cursor,
            "Product metadata — before indexes",
            metadata_query,
            (Json({"category": "accessories"}),),
        )

        cursor.execute("""
            CREATE INDEX idx_orders_customer_id
            ON orders (customer_id);
            """)
        cursor.execute("""
            CREATE INDEX idx_products_metadata_gin
            ON products USING GIN (metadata);
            """)
        cursor.execute("ANALYZE orders;")
        cursor.execute("ANALYZE products;")

        plans["after_indexes"]["customer_orders"] = _explain_query(
            cursor,
            "Customer orders — after indexes",
            customer_query,
            (customer_id,),
        )
        plans["after_indexes"]["product_metadata"] = _explain_query(
            cursor,
            "Product metadata — after indexes",
            metadata_query,
            (Json({"category": "accessories"}),),
        )

    return plans
