
-- Return the top-selling products based on units sold.
-- Exclude items from cancelled orders and retain products with no sales.

WITH valid_sales AS (
    SELECT
        oi.product_id,
        oi.quantity,
        oi.unit_price
    FROM order_items AS oi
    INNER JOIN orders AS o
        ON o.order_id = oi.order_id
    WHERE o.status <> 'cancelled'
),
product_sales AS (
    SELECT
        p.product_id,
        p.name AS product_name,
        COALESCE(SUM(vs.quantity), 0) AS units_sold,
        COALESCE(
            SUM(vs.quantity * vs.unit_price),
            0
        ) AS sales_revenue
    FROM products AS p
    LEFT JOIN valid_sales AS vs
        ON vs.product_id = p.product_id
    GROUP BY p.product_id, p.name
)
SELECT
    product_id,
    product_name,
    units_sold,
    sales_revenue
FROM product_sales
ORDER BY units_sold DESC, sales_revenue DESC, product_id
LIMIT %s;
