"""Load benchmark data and measure query performance with and without indexes."""

import argparse
import sys
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ecommerce.analytics import benchmark_index_plans, load_sql  # noqa: E402
from ecommerce.database import create_pool, pooled_connection  # noqa: E402

TABLES = ("customers", "products", "orders", "order_items")


def run_sql_file(pool, filename: str) -> None:
    """
    Execute a SQL script, then VACUUM the tables.

    Bulk deletes and rolled-back loads leave dead rows that slow sequential
    scans, which would exaggerate the benefit of the indexes. VACUUM cannot run
    inside a transaction, so this connection briefly uses autocommit.
    """
    with pooled_connection(pool) as connection:
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                cursor.execute(load_sql(filename))
                for table in TABLES:
                    # Table names come from a fixed tuple, not from user input.
                    cursor.execute(f"VACUUM (ANALYZE) {table};")
        finally:
            connection.autocommit = False


def print_table_sizes(pool) -> None:
    """Print row counts so results can be read in context."""
    with (
        pooled_connection(pool) as connection,
        connection,
        connection.cursor() as cursor,
    ):
        for table in TABLES:
            # Table names come from a fixed tuple, not from user input.
            cursor.execute(f"SELECT COUNT(*) FROM {table};")
            print(f"  {table}: {cursor.fetchone()[0]:,} rows")


def print_results(results, runs: int) -> None:
    """Print a Markdown table comparing the before and after measurements."""
    print(f"\n| Query | Stage | Scan | Index used | Rows | Median of {runs} (ms) |")
    print("|---|---|---|---|---:|---:|")
    for query in ("customer_orders", "product_metadata"):
        for stage in ("before_indexes", "after_indexes"):
            result = results[stage][query]
            print(
                f"| {query} | {stage.replace('_', ' ')} "
                f"| {' + '.join(result['scan_types'])} "
                f"| {', '.join(result['indexes_used']) or 'none'} "
                f"| {result['rows']} | {result['median_execution_ms']} |"
            )

    for query in ("customer_orders", "product_metadata"):
        before = results["before_indexes"][query]["median_execution_ms"]
        after = results["after_indexes"][query]["median_execution_ms"]
        speedup = before / after if after else float("inf")
        print(f"\n{query}: {before} ms -> {after} ms ({speedup:.1f}x faster)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--load", action="store_true", help="load sql/08_benchmark_data.sql first"
    )
    parser.add_argument(
        "--remove", action="store_true", help="remove the benchmark data and exit"
    )
    parser.add_argument("--runs", type=int, default=5, help="measured runs per query")
    args = parser.parse_args()

    pool = create_pool()
    try:
        if args.remove:
            run_sql_file(pool, "09_remove_benchmark_data.sql")
            print("Benchmark data removed.")
            return

        if args.load:
            print("Loading benchmark data (this can take a minute)...")
            run_sql_file(pool, "08_benchmark_data.sql")

        print("Table sizes:")
        print_table_sizes(pool)
        print_results(benchmark_index_plans(pool, runs=args.runs), args.runs)

    finally:
        pool.closeall()


if __name__ == "__main__":
    try:
        main()
    except (psycopg2.Error, OSError, RuntimeError, ValueError) as error:
        print(f"Benchmark failed: {error}", file=sys.stderr)
        sys.exit(1)
