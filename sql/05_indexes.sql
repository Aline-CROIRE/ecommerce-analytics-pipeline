-- Improve customer order lookups and JSONB metadata searches.

CREATE INDEX IF NOT EXISTS idx_orders_customer_id
    ON orders (customer_id);

CREATE INDEX IF NOT EXISTS idx_products_metadata_gin
    ON products USING GIN (metadata);
