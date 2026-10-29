-- Calculate order totals, aggregate revenue by customer, and rank customers.

WITH order_totals AS (
    SELECT
        o.order_id,
        o.customer_id,
        SUM(oi.quantity * oi.unit_price) AS order_total
    FROM orders AS o
    INNER JOIN order_items AS oi
        ON oi.order_id = o.order_id
    WHERE o.status <> 'cancelled'
    GROUP BY o.order_id, o.customer_id
),
customer_revenue AS (
    SELECT
        c.customer_id,
        c.full_name,
        c.email,
        COUNT(ot.order_id) AS total_orders,
        COALESCE(SUM(ot.order_total), 0) AS total_revenue
    FROM customers AS c
    LEFT JOIN order_totals AS ot
        ON ot.customer_id = c.customer_id
    GROUP BY c.customer_id, c.full_name, c.email
)
SELECT
    customer_id,
    full_name,
    email,
    total_orders,
    total_revenue,
    RANK() OVER (ORDER BY total_revenue DESC) AS spending_rank
FROM customer_revenue
ORDER BY spending_rank, customer_id;
