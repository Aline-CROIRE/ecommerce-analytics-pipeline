"""Run the top-selling products workflow against PostgreSQL and Redis."""

import sys
from pathlib import Path
from pprint import pprint

import psycopg2
import redis

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ecommerce.cache import (  # noqa: E402
    CACHE_KEY_PREFIX,
    DEFAULT_PRODUCT_LIMIT,
    create_redis_client,
    get_top_selling_products,
)
from ecommerce.database import create_pool  # noqa: E402


def main() -> None:
    """Verify database loading and Redis caching against live services."""
    pool = create_pool()
    client = create_redis_client()

    try:
        client.ping()
        print("Redis connection: OK")

        cache_key = f"{CACHE_KEY_PREFIX}:{DEFAULT_PRODUCT_LIMIT}"
        client.delete(cache_key)

        print("\n=== FIRST REQUEST: DATABASE LOAD ===")
        first_result = get_top_selling_products(pool, client)
        pprint(first_result, sort_dicts=False)

        ttl = client.ttl(cache_key)
        print(f"\nCache TTL after first request: {ttl} seconds")

        print("\n=== SECOND REQUEST: REDIS CACHE HIT ===")
        # None is intentional: a cache hit must not need a database connection.
        second_result = get_top_selling_products(None, client)
        pprint(second_result, sort_dicts=False)

        if first_result != second_result:
            raise RuntimeError("The first and second results do not match.")

        if ttl <= 0:
            raise RuntimeError("The cache entry was not stored with a valid TTL.")

        print("\nIntegration verification passed.")
        print("The first request loaded products from PostgreSQL.")
        print("The second request succeeded without a database connection.")
        print("The Redis cache entry has a valid TTL.")

    finally:
        pool.closeall()
        client.close()


if __name__ == "__main__":
    try:
        main()
    except (
        psycopg2.Error,
        redis.RedisError,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        print(f"Integration verification failed: {error}", file=sys.stderr)
        sys.exit(1)
