"""Tests for MongoDB connection management."""

from unittest.mock import MagicMock, patch

import pytest
from pymongo.errors import ConfigurationError

from ecommerce import mongodb


def test_create_mongo_client_uses_configured_uri(monkeypatch):
    """The client should use the configured MongoDB URI."""
    monkeypatch.setenv(
        "MONGO_URI",
        "mongodb://test-user:test-password@localhost:27017/?authSource=admin",
    )

    with patch.object(mongodb, "MongoClient") as mock_client:
        result = mongodb.create_mongo_client()

    mock_client.assert_called_once_with(
        "mongodb://test-user:test-password@localhost:27017/?authSource=admin",
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )
    assert result == mock_client.return_value


def test_create_mongo_client_rejects_missing_uri(monkeypatch):
    """Missing connection configuration should fail clearly."""
    monkeypatch.delenv("MONGO_URI", raising=False)

    with pytest.raises(RuntimeError, match="MONGO_URI"):
        mongodb.create_mongo_client()


def test_get_database_uses_configured_name(monkeypatch):
    """The configured database should be selected."""
    monkeypatch.setenv("MONGO_DATABASE", "ecommerce_test")
    client = MagicMock()

    database = mongodb.get_database(client)

    client.__getitem__.assert_called_once_with("ecommerce_test")
    assert database == client.__getitem__.return_value


def test_get_database_rejects_missing_name(monkeypatch):
    """Missing database configuration should fail clearly."""
    monkeypatch.delenv("MONGO_DATABASE", raising=False)

    with pytest.raises(RuntimeError, match="MONGO_DATABASE"):
        mongodb.get_database(MagicMock())


def test_get_database_wraps_configuration_error(monkeypatch):
    """Invalid database selection should raise a clear application error."""
    monkeypatch.setenv("MONGO_DATABASE", "ecommerce_test")
    client = MagicMock()
    client.__getitem__.side_effect = ConfigurationError("Invalid database")

    with pytest.raises(RuntimeError, match="Invalid MongoDB database"):
        mongodb.get_database(client)


def test_verify_mongo_connection_pings_server():
    """Connection verification should ping MongoDB."""
    client = MagicMock()

    result = mongodb.verify_mongo_connection(client)

    client.admin.command.assert_called_once_with("ping")
    assert result is True
