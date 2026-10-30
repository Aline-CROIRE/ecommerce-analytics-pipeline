"""Integration tests for JSONB product metadata queries."""

import pytest
from psycopg2.extras import Json

from ecommerce.database import pooled_connection
from ecommerce.repository import (
    add_product,
    find_products_by_metadata,
    find_products_with_attribute,
)


@pytest.fixture
def metadata_products(db_context):
    """Add products with distinct metadata, tagged with a unique test marker."""
    pool = db_context["pool"]
    prefix = db_context["product_prefix"]
    tag = {"test_run": prefix}

    shirt = add_product(
        pool,
        name=f"{prefix}-shirt",
        price="15.00",
        metadata={**tag, "category": "clothing", "sizes": ["S", "M"]},
    )
    jacket = add_product(
        pool,
        name=f"{prefix}-jacket",
        price="80.00",
        metadata={**tag, "category": "clothing", "sizes": ["L"]},
    )
    cable = add_product(
        pool,
        name=f"{prefix}-cable",
        price="9.99",
        metadata={**tag, "category": "electronics", "length_m": 2},
    )
    return {
        "pool": pool,
        "tag": tag,
        "ids": {
            "shirt": shirt["product_id"],
            "jacket": jacket["product_id"],
            "cable": cable["product_id"],
        },
    }


def test_find_products_by_metadata_matches_containment(metadata_products):
    pool, tag, ids = (
        metadata_products["pool"],
        metadata_products["tag"],
        metadata_products["ids"],
    )

    clothing = find_products_by_metadata(pool, {**tag, "category": "clothing"})
    size_m = find_products_by_metadata(pool, {**tag, "sizes": ["M"]})

    assert [row["product_id"] for row in clothing] == [ids["shirt"], ids["jacket"]]
    assert all(row["category"] == "clothing" for row in clothing)
    assert [row["product_id"] for row in size_m] == [ids["shirt"]]


def test_find_products_with_attribute_matches_key(metadata_products):
    pool, tag, ids = (
        metadata_products["pool"],
        metadata_products["tag"],
        metadata_products["ids"],
    )

    with_sizes = find_products_with_attribute(pool, "sizes")
    ours = [row for row in with_sizes if row["product_id"] in ids.values()]

    assert [row["product_id"] for row in ours] == [ids["shirt"], ids["jacket"]]
    assert ours[0]["attribute_value"] == ["S", "M"]
    assert not any(row["product_id"] == ids["cable"] for row in with_sizes)
    assert find_products_by_metadata(pool, {**tag, "category": "toys"}) == []


@pytest.mark.parametrize("criteria", [{}, None, ["category"], "clothing"])
def test_find_products_by_metadata_rejects_invalid_criteria(criteria):
    with pytest.raises(ValueError):
        find_products_by_metadata(None, criteria)


@pytest.mark.parametrize("attribute", ["", "   ", None, 5])
def test_find_products_with_attribute_rejects_invalid_attribute(attribute):
    with pytest.raises(ValueError):
        find_products_with_attribute(None, attribute)


@pytest.mark.parametrize(
    ("condition", "parameter"),
    [
        ("metadata @> %s", Json({"category": "clothing"})),
        ("metadata ? %s", "sizes"),
    ],
)
def test_metadata_queries_can_use_gin_index(db_context, condition, parameter):
    """
    The GIN index supports both query operators.

    Sequential scans are disabled for this transaction only, because on a small
    table PostgreSQL correctly prefers a sequential scan.
    """
    with (
        pooled_connection(db_context["pool"]) as connection,
        connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL enable_seqscan = off;")
        cursor.execute(
            f"EXPLAIN SELECT product_id FROM products WHERE {condition};",
            (parameter,),
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "idx_products_metadata_gin" in plan
