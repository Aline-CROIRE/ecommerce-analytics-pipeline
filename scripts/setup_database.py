
"""Apply the PostgreSQL schema and verify the database tables."""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = PROJECT_ROOT / "sql" / "01_schema.sql"

load_dotenv(PROJECT_ROOT / ".env")


def setup_database() -> None:
    """Apply the SQL schema and list the created tables."""
    required_variables = (
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    )
    missing = [
        name for name in required_variables if not os.getenv(name)
    ]

    if missing:
        raise RuntimeError(
            f"Missing environment variables: {', '.join(missing)}"
        )

    if not SCHEMA_FILE.is_file():
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_FILE}")

    connection = psycopg2.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host="localhost",
        port=5432,
        connect_timeout=5,
    )

    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    SCHEMA_FILE.read_text(encoding="utf-8")
                )

                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name;
                    """
                )
                tables = cursor.fetchall()

        print("Database schema applied successfully.")
        print("Tables found:")
        for (table_name,) in tables:
            print(f"  - {table_name}")

    finally:
        connection.close()


if __name__ == "__main__":
    try:
        setup_database()
    except (psycopg2.Error, OSError, RuntimeError) as error:
        print(f"Database setup failed: {error}", file=sys.stderr)
        sys.exit(1)
