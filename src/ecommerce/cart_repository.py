"""MongoDB repository operations for shopping-cart sessions."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.collection import Collection

# An untouched cart session expires and is removed by the TTL index.
CART_SESSION_TTL = timedelta(hours=24)


def _validate_positive_integer(value: object, field_name: str) -> None:
    """Reject booleans, non-integers, and non-positive integer values."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def _activity_fields() -> dict[str, datetime]:
    """Return fields that record activity and extend the session's expiry."""
    now = _utc_now()
    return {"updated_at": now, "expires_at": now + CART_SESSION_TTL}


def create_cart(
    collection: Collection,
    customer_id: int,
) -> dict[str, Any]:
    """Create an active, empty cart session for a customer."""
    _validate_positive_integer(customer_id, "customer_id")

    activity = _activity_fields()
    document = {
        "session_id": uuid4().hex,
        "customer_id": customer_id,
        "items": [],
        "status": "active",
        "created_at": activity["updated_at"],
        **activity,
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

    # First, increment the product if it already exists.
    updated_cart = collection.find_one_and_update(
        {
            "customer_id": customer_id,
            "status": "active",
            "items.product_id": product_id,
        },
        {
            "$inc": {"items.$.quantity": quantity},
            "$set": _activity_fields(),
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated_cart is not None:
        return True

    # Otherwise, append it only if it is still absent.
    updated_cart = collection.find_one_and_update(
        {
            "customer_id": customer_id,
            "status": "active",
            "items.product_id": {"$ne": product_id},
        },
        {
            "$push": {"items": {"product_id": product_id, "quantity": quantity}},
            "$set": _activity_fields(),
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated_cart is not None:
        return True

    # Another request may have inserted the item between our two operations.
    # Retry the increment once to handle that race.
    updated_cart = collection.find_one_and_update(
        {
            "customer_id": customer_id,
            "status": "active",
            "items.product_id": product_id,
        },
        {
            "$inc": {"items.$.quantity": quantity},
            "$set": _activity_fields(),
        },
        return_document=ReturnDocument.AFTER,
    )
    return updated_cart is not None


def update_quantity(
    collection: Collection,
    customer_id: int,
    product_id: int,
    quantity: int,
) -> bool:
    """Set the quantity of a product already in the customer's active cart."""
    _validate_positive_integer(customer_id, "customer_id")
    _validate_positive_integer(product_id, "product_id")
    _validate_positive_integer(quantity, "quantity")

    result = collection.update_one(
        {
            "customer_id": customer_id,
            "status": "active",
            "items.product_id": product_id,
        },
        {"$set": {"items.$.quantity": quantity, **_activity_fields()}},
    )
    return result.matched_count > 0


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
            "$set": _activity_fields(),
        },
    )
    return result.modified_count > 0


def clear_cart(
    collection: Collection,
    customer_id: int,
) -> bool:
    """Remove every item from the customer's active cart."""
    _validate_positive_integer(customer_id, "customer_id")

    result = collection.update_one(
        {"customer_id": customer_id, "status": "active"},
        {"$set": {"items": [], **_activity_fields()}},
    )
    return result.matched_count > 0


def ensure_cart_indexes(collection: Collection) -> None:
    """Create lookup, active-cart uniqueness, and session-expiry indexes."""
    collection.create_index("customer_id")
    collection.create_index(
        "customer_id",
        unique=True,
        partialFilterExpression={"status": "active"},
        name="unique_active_cart_per_customer",
    )
    # MongoDB deletes a document once its expires_at time has passed.
    collection.create_index(
        "expires_at",
        expireAfterSeconds=0,
        name="expire_inactive_cart_sessions",
    )
