"""Apply the PostgreSQL schema and verify the database tables."""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_FILES = (
    PROJECT_ROOT / "sql" / "01_schema.sql",
    PROJECT_ROOT / "sql" / "05_indexes.sql",
)

load_dotenv(PROJECT_ROOT / ".env")


def setup_database() -> None:
    """Apply the SQL schema and indexes, then list the tables and indexes."""
    required_variables = (
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    )
    missing = [name for name in required_variables if not os.getenv(name)]

    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    for sql_file in SETUP_FILES:
        if not sql_file.is_file():
            raise FileNotFoundError(f"SQL file not found: {sql_file}")

    connection = psycopg2.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        connect_timeout=5,
    )

    try:
        with connection, connection.cursor() as cursor:
            for sql_file in SETUP_FILES:
                cursor.execute(sql_file.read_text(encoding="utf-8"))

            cursor.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name;
                """)
            tables = cursor.fetchall()

            cursor.execute("""
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname LIKE 'idx_%'
                ORDER BY indexname;
                """)
            indexes = cursor.fetchall()

        print("Database schema and indexes applied successfully.")
        print("Tables found:")
        for (table_name,) in tables:
            print(f"  - {table_name}")
        print("Performance indexes found:")
        for (index_name,) in indexes:
            print(f"  - {index_name}")

    finally:
        connection.close()


if __name__ == "__main__":
    try:
        setup_database()
    except (psycopg2.Error, OSError, RuntimeError) as error:
        print(f"Database setup failed: {error}", file=sys.stderr)
        sys.exit(1)
