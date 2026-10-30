"""PostgreSQL connection pooling and configuration."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extensions import TRANSACTION_STATUS_IDLE
from psycopg2.pool import ThreadedConnectionPool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def create_pool(
    min_connections: int = 1,
    max_connections: int = 5,
) -> ThreadedConnectionPool:
    """Create a pool using the project's PostgreSQL environment settings."""
    if min_connections < 1 or max_connections < min_connections:
        raise ValueError("Invalid connection pool size.")

    required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    return ThreadedConnectionPool(
        minconn=min_connections,
        maxconn=max_connections,
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        connect_timeout=5,
    )


@contextmanager
def pooled_connection(pool: ThreadedConnectionPool) -> Iterator:
    """Borrow a connection and safely return or discard it."""
    connection = pool.getconn()
    discard_connection = False

    try:
        yield connection
    finally:
        if connection.closed:
            discard_connection = True
        else:
            try:
                transaction_status = connection.get_transaction_status()

                if transaction_status != TRANSACTION_STATUS_IDLE:
                    connection.rollback()

            except psycopg2.Error:
                discard_connection = True

        pool.putconn(connection, close=discard_connection)
