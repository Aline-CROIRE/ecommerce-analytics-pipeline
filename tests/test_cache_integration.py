"""Live PostgreSQL and Redis tests for top-selling cache invalidation."""

import pytest
import redis

from ecommerce.cache import (
    CACHE_KEY_PREFIX,
    create_redis_client,
    get_top_selling_products,
    invalidate_top_selling_products,
)
from ecommerce.repository import create_order, update_order_status

CACHE_KEY = f"{CACHE_KEY_PREFIX}:10"


@pytest.fixture
def redis_client():
    """Provide a live Redis client, skipping when Redis is unavailable."""
    client = create_redis_client()
    try:
        client.ping()
    except redis.RedisError as error:
        pytest.skip(f"Redis is unavailable: {error}")

    invalidate_top_selling_products(client)
    try:
        yield client
    finally:
        # Do not leave rankings that include test data in the cache.
        invalidate_top_selling_products(client)
        client.close()


def test_status_change_clears_stale_top_sellers(db_context, redis_client):
    pool = db_context["pool"]
    product_id = db_context["product_id"]

    # A large pending order is not a sale yet, so it is not ranked first.
    order = create_order(
        pool,
        customer_id=db_context["customer_id"],
        items=[{"product_id": product_id, "quantity": 100_000}],
    )
    before = get_top_selling_products(pool, redis_client)
    assert redis_client.exists(CACHE_KEY)
    assert before[0]["product_id"] != product_id

    update_order_status(pool, order["order_id"], "paid", cache=redis_client)

    assert not redis_client.exists(CACHE_KEY)
    after = get_top_selling_products(pool, redis_client)
    assert after[0]["product_id"] == product_id
    assert after[0]["units_sold"] == 100_000


def test_failed_status_change_keeps_cache(db_context, redis_client):
    pool = db_context["pool"]
    get_top_selling_products(pool, redis_client)

    with pytest.raises(ValueError, match="does not exist"):
        update_order_status(pool, 2_000_000_000, "paid", cache=redis_client)

    assert redis_client.exists(CACHE_KEY)
