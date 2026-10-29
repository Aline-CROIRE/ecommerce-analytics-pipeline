"""Redis caching for top-selling products."""

import json
import os
from contextlib import suppress
from decimal import Decimal
from typing import Any

import redis
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from ecommerce.analytics import load_sql
from ecommerce.database import pooled_connection

DEFAULT_CACHE_TTL = 300
DEFAULT_PRODUCT_LIMIT = 10
CACHE_KEY_PREFIX = "ecommerce:products:top-selling"


def create_redis_client() -> redis.Redis:
    """Create a Redis client using the configured connection URL."""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    return redis.Redis.from_url(
        redis_url,
        decode_responses=True,
    )


def _normalize_products(
    products: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert database values into JSON-safe, consistent values."""
    normalized = []

    for product in products:
        item = dict(product)

        for key, value in item.items():
            if isinstance(value, Decimal):
                item[key] = str(value)

        normalized.append(item)

    return normalized


def get_top_selling_products(
    pool: ThreadedConnectionPool,
    cache: Any,
    limit: int = DEFAULT_PRODUCT_LIMIT,
    ttl: int = DEFAULT_CACHE_TTL,
) -> list[dict[str, Any]]:
    """Return cached top-selling products or load them from PostgreSQL."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")

    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1:
        raise ValueError("ttl must be a positive integer")

    cache_key = f"{CACHE_KEY_PREFIX}:{limit}"

    try:
        cached_products = cache.get(cache_key)
    except redis.RedisError:
        cached_products = None

    if cached_products is not None:
        try:
            decoded_products = json.loads(cached_products)

            if isinstance(decoded_products, list) and all(
                isinstance(product, dict) for product in decoded_products
            ):
                return decoded_products

        except (json.JSONDecodeError, TypeError):
            pass

        with suppress(redis.RedisError):
            cache.delete(cache_key)

    query = load_sql("06_top_selling_products.sql")

    with (
        pooled_connection(pool) as connection,
        connection.cursor(cursor_factory=RealDictCursor) as cursor,
    ):
        cursor.execute(query, (limit,))
        products = [dict(row) for row in cursor.fetchall()]

    products = _normalize_products(products)
    payload = json.dumps(products)

    with suppress(redis.RedisError):
        cache.setex(cache_key, ttl, payload)

    return products
