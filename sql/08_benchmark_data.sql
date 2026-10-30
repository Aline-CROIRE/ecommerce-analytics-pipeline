-- Deterministic benchmark dataset for index measurements:
-- 20,000 customers, 50,000 products, 200,000 orders, and 400,000 order items.
-- Values are derived from generate_series, so every load is identical.
-- The script is idempotent; remove the data with 09_remove_benchmark_data.sql.

BEGIN;

INSERT INTO customers (full_name, email)
SELECT 'Benchmark Customer ' || g, 'bench.' || g || '@benchmark.test'
FROM generate_series(1, 20000) AS g
ON CONFLICT (email) DO NOTHING;

-- Refresh statistics so the next insert is planned for the new row counts.
ANALYZE customers;

-- 500 brands give a selective metadata filter (about 100 products each).
INSERT INTO products (name, price, metadata)
SELECT
    'Benchmark Product ' || g,
    5 + (g % 200) + 0.99,
    jsonb_build_object(
        'category',
        (ARRAY['accessories', 'electronics', 'clothing', 'home', 'sports'])
            [1 + g % 5],
        'brand', 'brand_' || (g % 500),
        'colors', jsonb_build_array(
            (ARRAY['black', 'white', 'red', 'blue', 'green'])[1 + g % 5],
            (ARRAY['grey', 'navy', 'yellow'])[1 + g % 3]
        ),
        'sizes', CASE WHEN g % 5 = 2 THEN '["S", "M", "L"]'::JSONB END
    ) - CASE WHEN g % 5 = 2 THEN '' ELSE 'sizes' END
FROM generate_series(1, 50000) AS g
WHERE NOT EXISTS (
    SELECT 1 FROM products WHERE name = 'Benchmark Product 1'
);

-- Refresh statistics so the next insert is planned for the new row counts.
ANALYZE products;

-- About 10 orders per customer, spread over 2025 with mixed statuses.
WITH bench_customers AS (
    SELECT customer_id, ROW_NUMBER() OVER (ORDER BY customer_id) AS rn
    FROM customers
    WHERE email LIKE 'bench.%@benchmark.test'
)
INSERT INTO orders (customer_id, order_date, status)
SELECT
    bc.customer_id,
    TIMESTAMPTZ '2025-01-01 00:00+00'
        + (g % 365) * INTERVAL '1 day'
        + (g % 1440) * INTERVAL '1 minute',
    (ARRAY['paid', 'shipped', 'delivered', 'delivered', 'pending', 'cancelled'])
        [1 + g % 6]
FROM generate_series(1, 200000) AS g
INNER JOIN bench_customers AS bc
    ON bc.rn = 1 + (g % 20000)
WHERE NOT EXISTS (
    SELECT 1
    FROM orders AS o
    INNER JOIN customers AS c
        ON c.customer_id = o.customer_id
    WHERE c.email = 'bench.1@benchmark.test'
);

-- Refresh statistics so the next insert is planned for the new row counts.
ANALYZE orders;

-- Two distinct products per order, priced at the product's current price.
WITH bench_orders AS (
    SELECT o.order_id, ROW_NUMBER() OVER (ORDER BY o.order_id) AS rn
    FROM orders AS o
    INNER JOIN customers AS c
        ON c.customer_id = o.customer_id
    WHERE c.email LIKE 'bench.%@benchmark.test'
),
bench_products AS (
    SELECT product_id, price, ROW_NUMBER() OVER (ORDER BY product_id) AS rn
    FROM products
    WHERE name LIKE 'Benchmark Product %'
)
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT bo.order_id, bp.product_id, 1 + (bo.rn + k) % 3, bp.price
FROM bench_orders AS bo
CROSS JOIN generate_series(0, 1) AS k
INNER JOIN bench_products AS bp
    ON bp.rn = 1 + ((bo.rn * 7 + k * 13) % 50000)
ON CONFLICT (order_id, product_id) DO NOTHING;

COMMIT;

-- Refresh planner statistics after the bulk load.
ANALYZE customers;
ANALYZE products;
ANALYZE orders;
ANALYZE order_items;
