"""Unit tests for Redis product caching."""

import json
from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import redis

from ecommerce import cache


class FakeRedis:
    """Small Redis substitute for testing cache behavior."""

    def __init__(self, value=None):
        self.value = value
        self.data = {}
        self.get_calls = []
        self.setex_calls = []
        self.delete_calls = []

    def get(self, key):
        self.get_calls.append(key)
        return self.value if self.value is not None else self.data.get(key)

    def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self.data[key] = value

    def delete(self, key):
        self.delete_calls.append(key)
        self.data.pop(key, None)


def mock_database(monkeypatch, rows):
    """Replace PostgreSQL access with a mocked connection and cursor."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows

    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    monkeypatch.setattr(
        cache,
        "pooled_connection",
        lambda pool: nullcontext(connection),
    )
    monkeypatch.setattr(cache, "load_sql", lambda filename: "SELECT test")
    return cursor


def test_cache_hit_returns_cached_products_without_querying_database():
    products = [{"product_id": 1, "product_name": "Keyboard", "units_sold": 5}]
    redis_cache = FakeRedis(value=json.dumps(products))

    result = cache.get_top_selling_products(
        pool=None,
        cache=redis_cache,
    )

    assert result == products
    assert redis_cache.get_calls == [f"{cache.CACHE_KEY_PREFIX}:10"]
    assert redis_cache.setex_calls == []


def test_cache_miss_queries_database_and_stores_results(monkeypatch):
    rows = [
        {
            "product_id": 1,
            "product_name": "Keyboard",
            "units_sold": 5,
            "sales_revenue": Decimal("125.00"),
        }
    ]
    cursor = mock_database(monkeypatch, rows)
    redis_cache = FakeRedis()

    result = cache.get_top_selling_products(
        pool=None,
        cache=redis_cache,
        limit=10,
        ttl=120,
    )

    assert result == [
        {
            "product_id": 1,
            "product_name": "Keyboard",
            "units_sold": 5,
            "sales_revenue": "125.00",
        }
    ]
    cursor.execute.assert_called_once_with("SELECT test", (10,))
    assert len(redis_cache.setex_calls) == 1

    key, ttl, payload = redis_cache.setex_calls[0]
    assert key == f"{cache.CACHE_KEY_PREFIX}:10"
    assert ttl == 120
    assert json.loads(payload) == result


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "10", None])
def test_invalid_limit_raises_value_error(limit):
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        cache.get_top_selling_products(
            pool=None,
            cache=FakeRedis(),
            limit=limit,
        )


@pytest.mark.parametrize("ttl", [0, -1, True, 1.5, "300", None])
def test_invalid_ttl_raises_value_error(ttl):
    with pytest.raises(ValueError, match="ttl must be a positive integer"):
        cache.get_top_selling_products(
            pool=None,
            cache=FakeRedis(),
            ttl=ttl,
        )


def test_redis_read_failure_falls_back_to_database(monkeypatch):
    mock_database(monkeypatch, [])

    class UnavailableRedis(FakeRedis):
        def get(self, key):
            raise redis.RedisError("Redis unavailable")

    result = cache.get_top_selling_products(
        pool=None,
        cache=UnavailableRedis(),
    )

    assert result == []


def test_redis_write_failure_does_not_break_database_result(monkeypatch):
    mock_database(monkeypatch, [{"product_id": 1, "product_name": "Mouse"}])

    class UnavailableRedis(FakeRedis):
        def setex(self, key, ttl, value):
            raise redis.RedisError("Redis unavailable")

    result = cache.get_top_selling_products(
        pool=None,
        cache=UnavailableRedis(),
    )

    assert result == [{"product_id": 1, "product_name": "Mouse"}]


def test_create_redis_client_uses_configured_host_and_port(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_HOST", "redis.internal")
    monkeypatch.setenv("REDIS_PORT", "6380")

    client = cache.create_redis_client()
    connection_kwargs = client.connection_pool.connection_kwargs

    assert connection_kwargs["host"] == "redis.internal"
    assert connection_kwargs["port"] == 6380
    assert connection_kwargs["socket_connect_timeout"] == 5


def test_corrupt_cached_json_is_deleted_and_reloaded(monkeypatch):
    mock_database(monkeypatch, [{"product_id": 2, "product_name": "Monitor"}])
    redis_cache = FakeRedis(value="{invalid json")

    result = cache.get_top_selling_products(
        pool=None,
        cache=redis_cache,
    )

    assert result == [{"product_id": 2, "product_name": "Monitor"}]
    assert redis_cache.delete_calls == [f"{cache.CACHE_KEY_PREFIX}:10"]
    assert len(redis_cache.setex_calls) == 1
