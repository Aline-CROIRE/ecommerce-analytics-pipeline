-- Rank products by units sold, with revenue as a secondary metric.

WITH product_sales AS (
    SELECT
        p.product_id,
        p.name AS product_name,
        COALESCE(SUM(oi.quantity), 0) AS units_sold,
        COALESCE(
            SUM(oi.quantity * oi.unit_price),
            0
        ) AS sales_revenue
    FROM products AS p
    LEFT JOIN order_items AS oi
        ON oi.product_id = p.product_id
    LEFT JOIN orders AS o
        ON o.order_id = oi.order_id
       AND o.status <> 'cancelled'
    WHERE oi.order_id IS NULL OR o.order_id IS NOT NULL
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
