"""Run e-commerce SQL reports and query-plan comparisons."""

import sys
from pathlib import Path
from pprint import pprint

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ecommerce.analytics import compare_index_plans, run_analytics  # noqa: E402
from ecommerce.database import create_pool  # noqa: E402

PREVIEW_ROWS = 10


def main() -> None:
    """Run analytics reports and compare query execution plans."""
    pool = create_pool()

    try:
        print("\nAnalytics reports")
        reports = run_analytics(pool)

        for filename, rows in reports.items():
            print(f"\n--- {filename} ---")
            print(f"Showing {min(len(rows), PREVIEW_ROWS)} of {len(rows)} rows")
            pprint(rows[:PREVIEW_ROWS], sort_dicts=False)

        print("\nQuery plan comparison")
        compare_index_plans(pool)

        print("\nAnalytics and query-plan comparison completed.")

    finally:
        pool.closeall()


if __name__ == "__main__":
    try:
        main()
    except (psycopg2.Error, OSError, RuntimeError, ValueError) as error:
        print(f"Analytics failed: {error}", file=sys.stderr)
        sys.exit(1)
