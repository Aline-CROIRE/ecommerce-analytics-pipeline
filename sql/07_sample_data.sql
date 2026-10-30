-- Deterministic sample data for customers, products, orders, and order items.
-- The script is idempotent: running it again does not create duplicates.

BEGIN;

INSERT INTO customers (full_name, email)
VALUES
    ('Sample Customer One', 'sample.one@example.com'),
    ('Sample Customer Two', 'sample.two@example.com'),
    ('Sample Customer Three', 'sample.three@example.com')
ON CONFLICT (email)
DO UPDATE SET full_name = EXCLUDED.full_name;

-- Flexible, category-specific attributes live in JSONB metadata.
WITH sample_products (name, price, metadata) AS (
    VALUES
        (
            'Wireless Mouse',
            25.00,
            '{"category": "accessories", "colors": ["black", "white"],
              "connectivity": "bluetooth"}'::JSONB
        ),
        (
            'Mechanical Keyboard',
            75.00,
            '{"category": "accessories", "colors": ["black"],
              "layout": "US", "switches": "brown"}'::JSONB
        ),
        (
            'USB-C Hub',
            40.00,
            '{"category": "electronics", "ports": ["HDMI", "USB-A", "SD"],
              "power_delivery_watts": 100}'::JSONB
        ),
        (
            'Cotton T-Shirt',
            15.00,
            '{"category": "clothing", "sizes": ["S", "M", "L", "XL"],
              "colors": ["navy", "grey"]}'::JSONB
        )
),
updated_products AS (
    UPDATE products AS p
    SET metadata = sp.metadata
    FROM sample_products AS sp
    WHERE p.name = sp.name
    RETURNING p.name
)
INSERT INTO products (name, price, metadata)
SELECT sp.name, sp.price, sp.metadata
FROM sample_products AS sp
WHERE sp.name NOT IN (SELECT name FROM updated_products);

WITH sample_orders (email, order_date, status) AS (
    VALUES
        ('sample.one@example.com', TIMESTAMPTZ '2026-10-20 10:00+00', 'delivered'),
        ('sample.two@example.com', TIMESTAMPTZ '2026-10-21 11:30+00', 'shipped'),
        ('sample.three@example.com', TIMESTAMPTZ '2026-10-22 14:15+00', 'paid'),
        ('sample.two@example.com', TIMESTAMPTZ '2026-10-23 09:45+00', 'cancelled')
)
INSERT INTO orders (customer_id, order_date, status)
SELECT c.customer_id, so.order_date, so.status
FROM sample_orders AS so
INNER JOIN customers AS c
    ON c.email = so.email
WHERE NOT EXISTS (
    SELECT 1
    FROM orders AS o
    WHERE o.customer_id = c.customer_id
      AND o.order_date = so.order_date
);

-- unit_price copies the product's current price at the time of purchase.
WITH sample_items (email, order_date, product_name, quantity) AS (
    VALUES
        ('sample.one@example.com', TIMESTAMPTZ '2026-10-20 10:00+00', 'Wireless Mouse', 2),
        ('sample.one@example.com', TIMESTAMPTZ '2026-10-20 10:00+00', 'USB-C Hub', 1),
        ('sample.two@example.com', TIMESTAMPTZ '2026-10-21 11:30+00', 'Mechanical Keyboard', 1),
        ('sample.three@example.com', TIMESTAMPTZ '2026-10-22 14:15+00', 'Wireless Mouse', 1),
        ('sample.three@example.com', TIMESTAMPTZ '2026-10-22 14:15+00', 'Mechanical Keyboard', 1),
        ('sample.two@example.com', TIMESTAMPTZ '2026-10-23 09:45+00', 'Cotton T-Shirt', 3)
)
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT o.order_id, p.product_id, si.quantity, p.price
FROM sample_items AS si
INNER JOIN customers AS c
    ON c.email = si.email
INNER JOIN orders AS o
    ON o.customer_id = c.customer_id
   AND o.order_date = si.order_date
INNER JOIN LATERAL (
    SELECT product_id, price
    FROM products
    WHERE name = si.product_name
    ORDER BY product_id
    LIMIT 1
) AS p ON TRUE
ON CONFLICT (order_id, product_id) DO NOTHING;

COMMIT;
