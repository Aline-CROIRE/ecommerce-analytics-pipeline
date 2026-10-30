"""Convert a MongoDB shopping cart into a PostgreSQL order."""

from typing import Any

from psycopg2.pool import ThreadedConnectionPool
from pymongo import ReturnDocument
from pymongo.collection import Collection

from ecommerce.cart_repository import _utc_now, _validate_positive_integer
from ecommerce.repository import create_order


def checkout_cart(
    collection: Collection,
    pool: ThreadedConnectionPool,
    customer_id: int,
) -> dict[str, Any]:
    """
    Place an order for the customer's active cart and close the cart.

    The cart is first claimed atomically so two checkouts cannot both use it.
    PostgreSQL remains the source of truth: if the order fails, the cart is
    restored to active. MongoDB and PostgreSQL do not share a transaction, so a
    crash after the order commits can leave the cart in "checking_out".
    """
    _validate_positive_integer(customer_id, "customer_id")

    cart = collection.find_one_and_update(
        {
            "customer_id": customer_id,
            "status": "active",
            "items.0": {"$exists": True},
        },
        {"$set": {"status": "checking_out", "updated_at": _utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    if cart is None:
        raise ValueError(f"Customer {customer_id} has no active cart with items.")

    items = [
        {"product_id": item["product_id"], "quantity": item["quantity"]}
        for item in cart["items"]
    ]

    try:
        order = create_order(pool, customer_id, items)
    except Exception:
        collection.update_one(
            {"_id": cart["_id"], "status": "checking_out"},
            {"$set": {"status": "active", "updated_at": _utc_now()}},
        )
        raise

    # A completed cart is order history, so it no longer expires.
    collection.update_one(
        {"_id": cart["_id"]},
        {
            "$set": {
                "status": "checked_out",
                "order_id": order["order_id"],
                "checked_out_at": _utc_now(),
            },
            "$unset": {"expires_at": ""},
        },
    )
    return order
