"""MongoDB repository operations for shopping carts."""

from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument
from pymongo.collection import Collection


def _validate_positive_integer(value: object, field_name: str) -> None:
    """Reject booleans, non-integers, and non-positive integer values."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def create_cart(
    collection: Collection,
    customer_id: int,
) -> dict[str, Any]:
    """Create an active, empty cart for a customer."""
    _validate_positive_integer(customer_id, "customer_id")

    now = _utc_now()
    document = {
        "customer_id": customer_id,
        "items": [],
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }

    result = collection.insert_one(document)
    return {"_id": result.inserted_id, **document}


def get_cart(
    collection: Collection,
    customer_id: int,
) -> dict[str, Any] | None:
    """Return a customer's active cart, or None if no cart exists."""
    _validate_positive_integer(customer_id, "customer_id")
    return collection.find_one({"customer_id": customer_id, "status": "active"})


def add_item(
    collection: Collection,
    customer_id: int,
    product_id: int,
    quantity: int,
) -> bool:
    """Atomically add a product or increment its existing quantity."""
    _validate_positive_integer(customer_id, "customer_id")
    _validate_positive_integer(product_id, "product_id")
    _validate_positive_integer(quantity, "quantity")

    now = _utc_now()

    # First try to increment an existing item atomically.
    updated_cart = collection.find_one_and_update(
        {
            "customer_id": customer_id,
            "status": "active",
            "items.product_id": product_id,
        },
        {
            "$inc": {"items.$.quantity": quantity},
            "$set": {"updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated_cart is not None:
        return True

    # If the item does not exist, append it only if it is still absent.
    updated_cart = collection.find_one_and_update(
        {
            "customer_id": customer_id,
            "status": "active",
            "items.product_id": {"$ne": product_id},
        },
        {
            "$push": {"items": {"product_id": product_id, "quantity": quantity}},
            "$set": {"updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
    )
    return updated_cart is not None


def remove_item(
    collection: Collection,
    customer_id: int,
    product_id: int,
) -> bool:
    """Remove a product from a customer's active cart."""
    _validate_positive_integer(customer_id, "customer_id")
    _validate_positive_integer(product_id, "product_id")

    result = collection.update_one(
        {
            "customer_id": customer_id,
            "status": "active",
            "items.product_id": product_id,
        },
        {
            "$pull": {"items": {"product_id": product_id}},
            "$set": {"updated_at": _utc_now()},
        },
    )
    return result.modified_count > 0


def ensure_cart_indexes(collection: Collection) -> None:
    """Create indexes for customer lookup and active-cart uniqueness."""
    collection.create_index("customer_id")
    collection.create_index(
        "customer_id",
        unique=True,
        partialFilterExpression={"status": "active"},
        name="unique_active_cart_per_customer",
    )
