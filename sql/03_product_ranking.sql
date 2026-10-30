
-- Rank products by units sold, with revenue as a secondary metric.
-- Include every product, even if it has no sales.
-- Sales count only paid, shipped, and delivered orders.

WITH valid_sales AS (
    SELECT
        oi.product_id,
        oi.quantity,
        oi.unit_price
    FROM order_items AS oi
    INNER JOIN orders AS o
        ON o.order_id = oi.order_id
    WHERE o.status IN ('paid', 'shipped', 'delivered')
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
    sales_revenue,
    RANK() OVER (ORDER BY units_sold DESC) AS sales_rank
FROM product_sales
ORDER BY sales_rank, product_id;
