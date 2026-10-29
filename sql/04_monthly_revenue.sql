-- Calculate monthly order revenue, excluding cancelled orders.

WITH monthly_order_totals AS (
    SELECT
        DATE_TRUNC('month', o.order_date)::DATE AS revenue_month,
        o.order_id,
        SUM(oi.quantity * oi.unit_price) AS order_total
    FROM orders AS o
    INNER JOIN order_items AS oi
        ON oi.order_id = o.order_id
    WHERE o.status <> 'cancelled'
    GROUP BY DATE_TRUNC('month', o.order_date)::DATE, o.order_id
)
SELECT
    revenue_month,
    COUNT(order_id) AS orders_count,
    SUM(order_total) AS total_revenue
FROM monthly_order_totals
GROUP BY revenue_month
ORDER BY revenue_month;
