"""Load deterministic sample data from sql/07_sample_data.sql."""

import sys
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ecommerce.analytics import load_sql  # noqa: E402
from ecommerce.database import create_pool, pooled_connection  # noqa: E402

SAMPLE_DATA_FILE = "07_sample_data.sql"
COUNTED_TABLES = ("customers", "products", "orders", "order_items")


def load_sample_data() -> None:
    """Run the sample-data SQL script in one transaction and report row counts."""
    pool = create_pool()
    try:
        with (
            pooled_connection(pool) as connection,
            connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(load_sql(SAMPLE_DATA_FILE))

            print("Sample data loaded successfully.")
            for table in COUNTED_TABLES:
                # Table names come from a fixed tuple, not from user input.
                cursor.execute(f"SELECT COUNT(*) FROM {table};")
                print(f"  {table}: {cursor.fetchone()[0]} rows")

    finally:
        pool.closeall()


if __name__ == "__main__":
    try:
        load_sample_data()
    except (psycopg2.Error, OSError, RuntimeError, ValueError) as error:
        print(f"Sample data loading failed: {error}", file=sys.stderr)
        sys.exit(1)
