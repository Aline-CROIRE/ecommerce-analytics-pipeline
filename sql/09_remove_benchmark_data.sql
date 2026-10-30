-- Remove the benchmark dataset created by 08_benchmark_data.sql.
-- Order items are removed by ON DELETE CASCADE from orders.

BEGIN;

DELETE FROM orders
WHERE customer_id IN (
    SELECT customer_id
    FROM customers
    WHERE email LIKE 'bench.%@benchmark.test'
);

DELETE FROM customers
WHERE email LIKE 'bench.%@benchmark.test';

DELETE FROM products AS p
WHERE p.name LIKE 'Benchmark Product %'
  AND NOT EXISTS (
      SELECT 1 FROM order_items AS oi WHERE oi.product_id = p.product_id
  );

COMMIT;

ANALYZE customers;
ANALYZE products;
ANALYZE orders;
ANALYZE order_items;
