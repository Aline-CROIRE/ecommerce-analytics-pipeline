"""Tests for PostgreSQL connection-pool reliability."""

from unittest.mock import MagicMock

import psycopg2
from psycopg2.extensions import TRANSACTION_STATUS_IDLE, TRANSACTION_STATUS_INTRANS

from ecommerce.database import pooled_connection


def test_pooled_connection_returns_healthy_connection():
    """Return an idle connection to the pool without closing it."""
    pool = MagicMock()
    connection = MagicMock()
    connection.closed = 0
    connection.get_transaction_status.return_value = TRANSACTION_STATUS_IDLE
    pool.getconn.return_value = connection

    with pooled_connection(pool) as borrowed:
        assert borrowed is connection

    pool.putconn.assert_called_once_with(connection, close=False)


def test_pooled_connection_rolls_back_unfinished_transaction():
    """Roll back an unfinished transaction before returning the connection."""
    pool = MagicMock()
    connection = MagicMock()
    connection.closed = 0
    connection.get_transaction_status.return_value = TRANSACTION_STATUS_INTRANS
    pool.getconn.return_value = connection

    with pooled_connection(pool):
        pass

    connection.rollback.assert_called_once()
    pool.putconn.assert_called_once_with(connection, close=False)


def test_pooled_connection_discards_closed_connection():
    """Discard a connection that is already closed."""
    pool = MagicMock()
    connection = MagicMock()
    connection.closed = 1
    pool.getconn.return_value = connection

    with pooled_connection(pool):
        pass

    connection.get_transaction_status.assert_not_called()
    pool.putconn.assert_called_once_with(connection, close=True)


def test_pooled_connection_discards_connection_when_rollback_fails():
    """Discard a connection that cannot roll back its transaction."""
    pool = MagicMock()
    connection = MagicMock()
    connection.closed = 0
    connection.get_transaction_status.return_value = TRANSACTION_STATUS_INTRANS
    connection.rollback.side_effect = psycopg2.OperationalError(
        "Connection lost during rollback."
    )
    pool.getconn.return_value = connection

    with pooled_connection(pool):
        pass

    connection.rollback.assert_called_once()
    pool.putconn.assert_called_once_with(connection, close=True)
