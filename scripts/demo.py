"""
Console shop for trying out the project.

Run it with: python scripts/demo.py
"""

import sys
import time
from pathlib import Path
from typing import Any

import psycopg2
import redis
from pymongo.errors import PyMongoError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ecommerce.analytics import benchmark_index_plans, run_analytics  # noqa: E402
from ecommerce.cache import (  # noqa: E402
    CACHE_KEY_PREFIX,
    DEFAULT_PRODUCT_LIMIT,
    create_redis_client,
    get_top_selling_products,
)
from ecommerce.cart_repository import (  # noqa: E402
    add_item,
    create_cart,
    ensure_cart_indexes,
    get_cart,
    remove_item,
    update_quantity,
)
from ecommerce.checkout import checkout_cart  # noqa: E402
from ecommerce.database import create_pool  # noqa: E402
from ecommerce.mongodb import create_mongo_client, get_database  # noqa: E402
from ecommerce.repository import (  # noqa: E402
    add_customer,
    add_product,
    find_products_by_metadata,
    find_products_with_attribute,
    get_customer_by_email,
    get_customer_orders,
    get_products,
    update_order_status,
)

CART_COLLECTION = "carts"
# Metadata attributes that hold lists, e.g. {"sizes": ["S", "M"]}.
LIST_ATTRIBUTES = {"sizes", "colors", "ports"}
CUSTOMER_STATUSES = {"p": "paid", "c": "cancelled"}
ADMIN_STATUSES = {"s": "shipped", "d": "delivered"}

MENU = """

E-Commerce Shop {signed_in}

   1  Sign in or register
   2  Best sellers
   3  Search products
   4  Add item to cart
   5  View cart
   6  Change quantity or remove item
   7  Checkout
   8  My orders
   9  Pay, cancel, ship or deliver an order
  10  Add a new product
  11  Sales reports
  12  Index performance check
   0  Exit
"""


class Shop:
    """Holds the service connections and the signed-in customer."""

    def __init__(self, pool, cache, carts) -> None:
        self.pool = pool
        self.cache = cache
        self.carts = carts
        self.customer: dict[str, Any] | None = None

    @staticmethod
    def ask(prompt: str) -> str:
        return input(f"{prompt}: ").strip()

    def ask_int(self, prompt: str) -> int:
        value = self.ask(prompt)
        try:
            return int(value)
        except ValueError as error:
            raise ValueError(f"'{value}' is not a whole number.") from error

    def require_customer(self) -> dict[str, Any]:
        if self.customer is None:
            raise ValueError("Please sign in first (option 1).")
        return self.customer

    @staticmethod
    def parse_attributes(text: str) -> dict[str, Any]:
        """Turn 'category=clothing sizes=M,L' into a metadata dictionary."""
        attributes: dict[str, Any] = {}
        for pair in text.split():
            key, separator, value = pair.partition("=")
            if not separator or not key or not value:
                raise ValueError(f"'{pair}' should look like key=value.")
            if key in LIST_ATTRIBUTES:
                attributes[key] = value.split(",")
            elif value.isdigit():
                attributes[key] = int(value)
            else:
                attributes[key] = value
        return attributes

    def sign_in(self) -> None:
        email = self.ask("Email")
        customer = get_customer_by_email(self.pool, email)
        if customer is None:
            print("No account with that email yet. Let's create one.")
            customer = add_customer(self.pool, self.ask("Full name"), email)
            print(f"Registered customer #{customer['customer_id']}.")
        self.customer = customer
        print(f"Welcome, {customer['full_name']}!")

    def best_sellers(self) -> None:
        cache_key = f"{CACHE_KEY_PREFIX}:{DEFAULT_PRODUCT_LIMIT}"
        cached = bool(self.cache.exists(cache_key))
        started = time.perf_counter()
        products = get_top_selling_products(self.pool, self.cache)
        elapsed_ms = (time.perf_counter() - started) * 1000

        source = "Redis cache" if cached else "PostgreSQL (now cached in Redis)"
        print(f"\nTop {len(products)} best sellers, served from {source}")
        print(
            f"in {elapsed_ms:.1f} ms. Cache expires in {self.cache.ttl(cache_key)} s."
        )
        print(f"\n {'ID':>7}  {'Product':<28} {'Units':>6} {'Revenue':>10}")
        for product in products:
            print(
                f" {product['product_id']:>7}  {product['product_name'][:28]:<28} "
                f"{product['units_sold']:>6} {product['sales_revenue']:>10}"
            )

    def search(self) -> None:
        print("Search by attributes, e.g.  category=clothing sizes=M colors=white")
        print("or type ?key to list products that have an attribute, e.g.  ?ports")
        query = self.ask("Search")
        if query.startswith("?"):
            products = find_products_with_attribute(self.pool, query[1:])
            rows = [(p, p["attribute_value"]) for p in products]
        else:
            products = find_products_by_metadata(
                self.pool, self.parse_attributes(query)
            )
            rows = [(p, p["metadata"]) for p in products]

        print(
            f"\n{len(rows)} products found" + (", showing 15" if len(rows) > 15 else "")
        )
        for product, detail in rows[:15]:
            print(
                f" {product['product_id']:>7}  {product['name'][:28]:<28} "
                f"{product['price']:>8}  {detail}"
            )

    def add_to_cart(self) -> None:
        customer = self.require_customer()
        product_id = self.ask_int("Product ID")
        product = get_products(self.pool, [product_id]).get(product_id)
        if product is None:
            raise ValueError(f"Product {product_id} does not exist.")
        quantity = self.ask_int("Quantity")

        if get_cart(self.carts, customer["customer_id"]) is None:
            create_cart(self.carts, customer["customer_id"])
            print("Started a new cart session.")
        add_item(self.carts, customer["customer_id"], product_id, quantity)
        print(f"Added {quantity} x {product['name']} to your cart.")

    def view_cart(self) -> None:
        customer = self.require_customer()
        cart = get_cart(self.carts, customer["customer_id"])
        if cart is None or not cart["items"]:
            print("Your cart is empty.")
            return

        products = get_products(
            self.pool, [item["product_id"] for item in cart["items"]]
        )
        total = 0
        print(f"\nCart session {cart['session_id'][:8]}...")
        for item in cart["items"]:
            product = products.get(item["product_id"])
            name = product["name"] if product else "(product no longer exists)"
            line = product["price"] * item["quantity"] if product else 0
            total += line
            print(
                f" {item['product_id']:>7}  {name[:28]:<28} "
                f"x{item['quantity']:<4} {line:>10}"
            )
        print(f" {'Estimated total (current prices)':>45} {total:>10}")
        print(
            f"Cart expires if untouched until {cart['expires_at']:%Y-%m-%d %H:%M} UTC."
        )

    def change_cart(self) -> None:
        customer = self.require_customer()
        product_id = self.ask_int("Product ID")
        quantity = self.ask_int("New quantity (0 removes the item)")
        if quantity == 0:
            changed = remove_item(self.carts, customer["customer_id"], product_id)
        else:
            changed = update_quantity(
                self.carts, customer["customer_id"], product_id, quantity
            )
        print("Cart updated." if changed else "That product is not in your cart.")

    def checkout(self) -> None:
        customer = self.require_customer()
        order = checkout_cart(self.carts, self.pool, customer["customer_id"])
        print(
            f"Order #{order['order_id']} placed: total {order['total_amount']}, "
            f"status '{order['status']}'."
        )
        print("Pay for it with option 9 so it counts as a sale.")

    def my_orders(self) -> None:
        customer = self.require_customer()
        orders = get_customer_orders(self.pool, customer["customer_id"])
        if not orders:
            print("You have no orders yet.")
            return
        for order in orders:
            print(
                f"\nOrder #{order['order_id']}  {order['order_date']:%Y-%m-%d %H:%M}  "
                f"{order['status']:<10} total {order['total_amount']}"
            )
            for item in order["items"]:
                print(
                    f"   {item['product_name'][:28]:<28} x{item['quantity']:<4} "
                    f"at {item['unit_price']} = {item['line_total']}"
                )

    def change_order_status(self) -> None:
        customer = self.require_customer()
        order_id = self.ask_int("Order ID")
        own_orders = {
            order["order_id"]
            for order in get_customer_orders(self.pool, customer["customer_id"])
        }
        if order_id not in own_orders:
            raise ValueError(f"Order {order_id} is not one of your orders.")

        choice = self.ask("[p]ay, [c]ancel, or (admin) [s]hip / [d]eliver").lower()
        status = {**CUSTOMER_STATUSES, **ADMIN_STATUSES}.get(choice)
        if status is None:
            raise ValueError("Please choose p, c, s, or d.")
        update_order_status(self.pool, order_id, status, cache=self.cache)
        print(f"Order #{order_id} is now '{status}'. Best-seller cache refreshed.")

    def new_product(self) -> None:
        name = self.ask("Product name")
        price = self.ask("Price (e.g. 19.99)")
        description = self.ask("Description (optional)") or None
        print("Attributes, e.g.  category=clothing sizes=S,M,L colors=navy")
        metadata = self.parse_attributes(self.ask("Attributes (optional)"))
        product = add_product(
            self.pool, name, price, description, metadata, cache=self.cache
        )
        print(
            f"Added product #{product['product_id']} {product['name']} "
            f"at {product['price']} with metadata {product['metadata']}."
        )

    def reports(self) -> None:
        reports = run_analytics(self.pool)

        print("\nTop 10 customers by revenue (CTEs + RANK; ties share a rank)")
        for row in reports["02_customer_revenue.sql"][:10]:
            print(
                f" rank {row['spending_rank']:<4} {row['full_name'][:26]:<26} "
                f"{row['total_orders']:>4} orders {row['total_revenue']:>12}"
            )

        print("\nTop 10 products by units sold (RANK)")
        for row in reports["03_product_ranking.sql"][:10]:
            print(
                f" rank {row['sales_rank']:<4} {row['product_name'][:26]:<26} "
                f"{row['units_sold']:>6} units {row['sales_revenue']:>11}"
            )

        print("\nRevenue for the last 6 months")
        for row in reports["04_monthly_revenue.sql"][-6:]:
            print(
                f" {row['revenue_month']:%Y-%m}  {row['orders_count']:>7,} orders "
                f"{row['total_revenue']:>14}"
            )
        print("\nOnly paid, shipped, and delivered orders count as revenue.")

    def index_performance(self) -> None:
        print("Running each query without and with the indexes (median of 3)...")
        results = benchmark_index_plans(self.pool, runs=3)
        for query in ("customer_orders", "product_metadata"):
            before = results["before_indexes"][query]
            after = results["after_indexes"][query]
            index_names = ", ".join(after["indexes_used"]) or "none"
            print(f"\n{query}")
            print(
                f"  without index: {before['scan_types'][0]:<18} "
                f"{before['median_execution_ms']:>8} ms"
            )
            print(
                f"  with index:    {after['scan_types'][-1]:<18} "
                f"{after['median_execution_ms']:>8} ms  (index: {index_names})"
            )
        print(
            "\nOn a small dataset PostgreSQL rightly prefers Seq Scans. Load the "
            "benchmark data\nwith 'python scripts/run_benchmark.py --load' to see "
            "the indexes pay off."
        )

    def run(self) -> None:
        actions = {
            "1": self.sign_in,
            "2": self.best_sellers,
            "3": self.search,
            "4": self.add_to_cart,
            "5": self.view_cart,
            "6": self.change_cart,
            "7": self.checkout,
            "8": self.my_orders,
            "9": self.change_order_status,
            "10": self.new_product,
            "11": self.reports,
            "12": self.index_performance,
        }
        while True:
            signed_in = (
                f"(signed in as {self.customer['email']})" if self.customer else ""
            )
            print(MENU.format(signed_in=signed_in))
            try:
                choice = self.ask("Choose an option")
            except EOFError:
                choice = "0"

            if choice == "0":
                print("Goodbye!")
                return
            action = actions.get(choice)
            if action is None:
                print("Unknown option, please choose from the menu.")
                continue

            try:
                action()
            except ValueError as error:
                # Validation errors from the project modules are shown, not fatal.
                print(f"Sorry: {error}")
            except EOFError:
                print("\nGoodbye!")
                return


def main() -> None:
    pool = create_pool()
    cache = create_redis_client()
    mongo_client = create_mongo_client()
    try:
        cache.ping()
        mongo_client.admin.command("ping")
        carts = get_database(mongo_client)[CART_COLLECTION]
        ensure_cart_indexes(carts)
        Shop(pool, cache, carts).run()
    finally:
        pool.closeall()
        cache.close()
        mongo_client.close()


if __name__ == "__main__":
    try:
        main()
    except (psycopg2.Error, redis.RedisError, PyMongoError, RuntimeError) as error:
        print(f"\nThe shop could not start: {error}", file=sys.stderr)
        print("Are the services running? Try: docker compose up -d", file=sys.stderr)
        sys.exit(1)
