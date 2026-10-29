"""Integration tests for SQL analytics and query-plan comparisons."""

from decimal import Decimal

import pytest

from ecommerce.analytics import (
    REPORT_FILES,
    compare_index_plans,
    load_sql,
    run_analytics,
)
from ecommerce.repository import create_order


def test_load_sql_reads_existing_report():
    """Load a real analytics query from the project's SQL directory."""
    sql = load_sql("02_customer_revenue.sql")

    assert "customer_revenue" in sql
    assert "RANK()" in sql


def test_load_sql_raises_for_missing_file():
    """Report a useful error when a SQL file does not exist."""
    with pytest.raises(FileNotFoundError, match="SQL file not found"):
        load_sql("missing_report.sql")


def test_run_analytics_returns_all_reports(db_context):
    """Execute all reports and verify their results for isolated test data."""
    pool = db_context["pool"]
    customer_id = db_context["customer_id"]
    product_id = db_context["product_id"]

    order = create_order(
        pool,
        customer_id=customer_id,
        items=[{"product_id": product_id, "quantity": 2}],
    )

    reports = run_analytics(pool)

    assert set(reports) == set(REPORT_FILES)

    customer_rows = reports["02_customer_revenue.sql"]
    customer = next(row for row in customer_rows if row["customer_id"] == customer_id)

    assert customer["total_orders"] == 1
    assert customer["total_revenue"] == Decimal("25.00")
    assert customer["spending_rank"] >= 1

    product_rows = reports["03_product_ranking.sql"]
    product = next(row for row in product_rows if row["product_id"] == product_id)

    assert product["units_sold"] == 2
    assert product["sales_revenue"] == Decimal("25.00")
    assert product["sales_rank"] >= 1

    monthly_rows = reports["04_monthly_revenue.sql"]
    assert monthly_rows

    # The report aggregates all valid orders in each month, including
    # other sample data, so verify the report contains valid totals.
    assert all(row["orders_count"] >= 1 for row in monthly_rows)
    assert all(row["total_revenue"] >= Decimal("0") for row in monthly_rows)

    assert order["total_amount"] == Decimal("25.00")


def test_compare_index_plans_returns_before_and_after_results(db_context):
    """Verify that both query-plan comparisons are returned."""
    plans = compare_index_plans(db_context["pool"])

    assert set(plans) == {"before_indexes", "after_indexes"}

    for stage in ("before_indexes", "after_indexes"):
        assert set(plans[stage]) == {"customer_orders", "product_metadata"}

        for query_plan in plans[stage].values():
            assert "Execution Time" in query_plan
