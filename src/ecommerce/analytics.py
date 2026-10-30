"""Execute SQL analytics reports and compare PostgreSQL query plans."""

import statistics
from collections.abc import Callable, Iterator
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

# The two query patterns the project indexes are designed to support.
INDEXED_QUERIES = {
    "customer_orders": """
        SELECT order_id, order_date, status
        FROM orders
        WHERE customer_id = %s
    """,
    "product_metadata": """
        SELECT product_id, name, price
        FROM products
        WHERE metadata @> %s::jsonb
    """,
}

PlanMeasure = Callable[[Any, str, str, tuple[Any, ...]], Any]


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


def _query_parameters(cursor: Any) -> dict[str, tuple[Any, ...]]:
    """
    Pick realistic, selective parameters from the data being measured.

    The busiest customer has a typical order history, and a single brand
    matches few products, which is the case an index helps.
    Without benchmark data, fall back to the sample-data category.
    """
    cursor.execute("""
        SELECT customer_id
        FROM orders
        GROUP BY customer_id
        ORDER BY COUNT(*) DESC, customer_id
        LIMIT 1;
        """)
    customer_row = cursor.fetchone()

    cursor.execute("""
        SELECT metadata -> 'brand'
        FROM products
        WHERE metadata ? 'brand'
        ORDER BY product_id
        LIMIT 1;
        """)
    brand_row = cursor.fetchone()
    criteria = {"brand": brand_row[0]} if brand_row else {"category": "accessories"}

    return {
        "customer_orders": (customer_row[0] if customer_row else -1,),
        "product_metadata": (Json(criteria),),
    }


def _compare_with_and_without_indexes(
    pool: ThreadedConnectionPool,
    measure: PlanMeasure,
) -> dict[str, dict[str, Any]]:
    """
    Measure each indexed query without, then with, the project indexes.

    Everything runs in one transaction that ends with the indexes recreated.
    """
    results: dict[str, dict[str, Any]] = {
        "before_indexes": {},
        "after_indexes": {},
    }

    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor() as cursor,
    ):
        parameters = _query_parameters(cursor)

        # Remove only the two indexes this project is measuring.
        cursor.execute("DROP INDEX IF EXISTS idx_orders_customer_id;")
        cursor.execute("DROP INDEX IF EXISTS idx_products_metadata_gin;")
        cursor.execute("ANALYZE orders;")
        cursor.execute("ANALYZE products;")

        for name, query in INDEXED_QUERIES.items():
            results["before_indexes"][name] = measure(
                cursor, f"{name} - before indexes", query, parameters[name]
            )

        cursor.execute(load_sql("05_indexes.sql"))
        cursor.execute("ANALYZE orders;")
        cursor.execute("ANALYZE products;")

        for name, query in INDEXED_QUERIES.items():
            results["after_indexes"][name] = measure(
                cursor, f"{name} - after indexes", query, parameters[name]
            )

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
    """Compare text query plans before and after creating the project indexes."""
    return _compare_with_and_without_indexes(pool, _explain_query)


def _plan_nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield a JSON plan node and all of its child nodes."""
    yield plan
    for child in plan.get("Plans", []):
        yield from _plan_nodes(child)


def summarize_plan(plans: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize repeated EXPLAIN (FORMAT JSON) results for one query."""
    nodes = list(_plan_nodes(plans[-1]["Plan"]))
    return {
        "median_execution_ms": round(
            statistics.median(plan["Execution Time"] for plan in plans), 3
        ),
        "median_planning_ms": round(
            statistics.median(plan["Planning Time"] for plan in plans), 3
        ),
        "scan_types": [
            node["Node Type"] for node in nodes if "Scan" in node["Node Type"]
        ],
        "indexes_used": sorted(
            {node["Index Name"] for node in nodes if "Index Name" in node}
        ),
        "rows": plans[-1]["Plan"]["Actual Rows"],
        "runs": len(plans),
    }


def benchmark_index_plans(
    pool: ThreadedConnectionPool,
    runs: int = 5,
) -> dict[str, dict[str, dict[str, Any]]]:
    """
    Benchmark the indexed queries before and after creating the indexes.

    Each query runs once to warm the cache, then `runs` more times; the
    median execution time is reported to reduce measurement noise.
    """
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 1:
        raise ValueError("runs must be a positive integer")

    def measure(cursor, label, query, parameters):
        explain = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}"
        cursor.execute(explain, parameters)  # Warm-up run, not measured.
        plans = []
        for _ in range(runs):
            cursor.execute(explain, parameters)
            plans.append(cursor.fetchone()[0][0])
        return summarize_plan(plans)

    return _compare_with_and_without_indexes(pool, measure)
