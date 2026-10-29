# Database Query Optimization Report

## 1. Objective

The objective of this report is to evaluate the impact of database indexes on query performance in the E-Commerce Analytics Data Pipeline. PostgreSQL's `EXPLAIN ANALYZE` is used to compare query plans before and after creating the selected indexes.

## 2. Indexes Evaluated

### Customer orders index

```sql
CREATE INDEX idx_orders_customer_id
ON orders (customer_id);
```

This B-tree index supports queries that filter orders by `customer_id`, such as retrieving a customer's order history.

### Product metadata index

```sql
CREATE INDEX idx_products_metadata_gin
ON products USING GIN (metadata);
```

This GIN index supports containment queries against the `products.metadata` JSONB column.

## 3. Testing Method

The analytics script compares two queries:

1. Retrieving orders belonging to a particular customer.
2. Searching product metadata using a JSONB containment condition.

The script removes the two indexes being evaluated, runs `ANALYZE`, records each query plan with `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)`, creates the indexes, runs `ANALYZE` again, and repeats the queries.

The reported execution times depend on the test environment, database contents, and PostgreSQL's query planner.

## 4. Results

### Customer orders query

| Measurement | Before indexes | After indexes |
|---|---:|---:|
| Query plan | Sequential scan | Sequential scan |
| Actual execution time | 0.049 ms | 0.044 ms |
| Planning time | 1.069 ms | 0.649 ms |

### Product metadata query

| Measurement | Before indexes | After indexes |
|---|---:|---:|
| Query plan | Sequential scan | Sequential scan |
| Actual execution time | 0.068 ms | 0.133 ms |
| Planning time | 0.555 ms | 1.287 ms |

*Note: These are the recorded results from the current small sample dataset. Execution times can vary between runs.*

## 5. Interpretation

The results do not demonstrate a consistent performance improvement from the indexes.

The customer orders query had a slightly lower recorded execution time after index creation. The product metadata query had a higher recorded execution time after index creation. PostgreSQL continued to choose sequential scans for both queries.

This behavior is reasonable because the sample database contains very few records. For small tables, scanning the table can be cheaper than using an index. At this scale, execution-time differences are also sensitive to measurement noise and system overhead.

These results should not be interpreted as evidence that indexes are ineffective. Their benefits should be evaluated with a larger and more representative dataset.

## 6. Recommendations

- Retain indexes when they support important production queries and their benefits justify their storage and write-maintenance costs.
- Benchmark using a substantially larger dataset with representative query patterns.
- Record execution plans, execution times, and buffer statistics before and after indexing.
- Repeat measurements and compare results instead of relying on a single run.
- Continue using `EXPLAIN ANALYZE` to validate query plans and identify bottlenecks.

## 7. Conclusion

The project implements a B-tree index on `orders.customer_id` and a GIN index on `products.metadata`. The query-plan comparison demonstrates how to evaluate indexing using PostgreSQL's diagnostic tools.

With the current small dataset, PostgreSQL chose sequential scans for both evaluated queries, and the measurements showed no consistent performance improvement. A larger dataset and repeated benchmarks are required to assess the indexes' practical benefits more reliably.