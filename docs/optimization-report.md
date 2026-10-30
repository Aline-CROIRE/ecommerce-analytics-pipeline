# Database Query Optimization Report

## 1. Objective

Measure whether the project's two indexes improve the queries they were
designed for, using PostgreSQL's `EXPLAIN ANALYZE` on a realistically sized
dataset.

| Index | Type | Supports |
|---|---|---|
| `idx_orders_customer_id` on `orders (customer_id)` | B-tree | Customer order history (`get_customer_orders()`) |
| `idx_products_metadata_gin` on `products USING GIN (metadata)` | GIN (`jsonb_ops`) | JSONB containment `@>` and key-exists `?` (`find_products_by_metadata()`, `find_products_with_attribute()`) |

Both indexes are defined in [`sql/05_indexes.sql`](../sql/05_indexes.sql) and
are applied by `scripts/setup_database.py`.

## 2. Method

### Dataset

The original sample data has only 3 customers and 4 products. At that size,
PostgreSQL correctly chooses sequential scans because reading one table page is
cheaper than using an index, so it cannot show an index's benefit.
[`sql/08_benchmark_data.sql`](../sql/08_benchmark_data.sql) therefore generates
a deterministic dataset with `generate_series`:

| Table | Rows |
|---|---:|
| customers | 20,003 |
| products | 50,004 |
| orders | 200,004 |
| order_items | 400,006 |

Each benchmark customer has about 10 orders. Products have varied JSONB
metadata (`category`, `brand`, `colors`, and `sizes` for clothing). There are
500 brands, so a single brand matches 100 products (0.2% of the table): a
selective filter, which is the case an index is meant for.

### Queries measured

```sql
-- Customer order history
SELECT order_id, order_date, status FROM orders WHERE customer_id = %s;

-- Product metadata search
SELECT product_id, name, price FROM products WHERE metadata @> %s::jsonb;
```

The customer is the one with the most orders (10), and the metadata criterion
is `{"brand": "brand_1"}`.

### Procedure

`benchmark_index_plans()` in [`src/ecommerce/analytics.py`](../src/ecommerce/analytics.py),
run by `scripts/run_benchmark.py`:

1. Drop the two project indexes and run `ANALYZE`.
2. For each query, run `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` once as a
   warm-up, then 7 measured times; record the **median** execution time and the
   scan types and index names from the plan.
3. Recreate the indexes from `sql/05_indexes.sql`, run `ANALYZE`, and repeat.

Everything runs in one transaction that ends with the indexes in place. Tables
are vacuumed after loading data, so dead rows do not slow the "before" scans.

Environment: PostgreSQL 17 (`postgres:17-alpine`) in Docker on Windows 11, with
the data in memory (`shared hit` buffers) for both stages.

### Reproduce

```powershell
python scripts/run_benchmark.py --load --runs 7   # load data, then benchmark
python scripts/run_benchmark.py --runs 7          # benchmark again
python scripts/run_benchmark.py --remove          # remove the benchmark data
```

## 3. Results

Median of 7 runs, from two separate benchmark runs:

| Query | Without index | With index | Run 1 (ms) | Run 2 (ms) | Speedup |
|---|---|---|---|---|---:|
| Customer orders | Seq Scan | Index Scan on `idx_orders_customer_id` | 10.192 → 0.053 | 10.159 → 0.043 | ~190–240× |
| Product metadata | Seq Scan | Bitmap Index Scan on `idx_products_metadata_gin` | 16.234 → 1.227 | 11.848 → 0.914 | ~13× |

Both queries returned the same rows before and after (10 orders, 100 products).

### Query plans

**Customer orders, without the index:** every row is read and 199,994 are
discarded.

```text
Seq Scan on orders  (cost=0.00..4075.05 rows=10 width=24) (actual time=0.009..10.473 rows=10 loops=1)
  Filter: (customer_id = 40530)
  Rows Removed by Filter: 199994
  Buffers: shared hit=1575
Execution Time: 10.485 ms
```

**Customer orders, with the index:** the B-tree goes straight to the 10 rows.

```text
Index Scan using idx_orders_customer_id on orders  (cost=0.29..8.47 rows=10 width=24) (actual time=0.071..0.075 rows=10 loops=1)
  Index Cond: (customer_id = 40530)
  Buffers: shared hit=4 read=2
Execution Time: 0.108 ms
```

**Product metadata, without the index:** every product's JSONB is checked.

```text
Seq Scan on products  (cost=0.00..2745.05 rows=5 width=37) (actual time=0.010..14.124 rows=100 loops=1)
  Filter: (metadata @> '{"brand": "brand_1"}'::jsonb)
  Rows Removed by Filter: 49904
  Buffers: shared hit=2120
Execution Time: 14.149 ms
```

**Product metadata, with the index:** the GIN index finds the matching rows,
then only those heap pages are read.

```text
Bitmap Heap Scan on products  (cost=21.51..40.84 rows=5 width=37) (actual time=1.034..1.162 rows=100 loops=1)
  Recheck Cond: (metadata @> '{"brand": "brand_1"}'::jsonb)
  Heap Blocks: exact=100
  Buffers: shared hit=119
  ->  Bitmap Index Scan on idx_products_metadata_gin  (cost=0.00..21.50 rows=5 width=0) (actual time=1.005..1.005 rows=100 loops=1)
        Index Cond: (metadata @> '{"brand": "brand_1"}'::jsonb)
        Buffers: shared hit=19
Execution Time: 1.241 ms
```

## 4. Interpretation

- **B-tree on `orders.customer_id`:** pages read fell from 1,575 to 6 and the
  query became two orders of magnitude faster. This is the access pattern of
  `get_customer_orders()`, which runs for every customer history request.
- **GIN on `products.metadata`:** pages read fell from 2,120 to 119 and the
  query became about 13× faster. The gain is smaller than for the B-tree
  because the 100 matching products sit on 100 different pages, all of which
  must still be read and rechecked.
- **Planner estimates for JSONB:** PostgreSQL estimated 5 matching products but
  found 100. It keeps no detailed statistics inside JSONB values, so it uses a
  default selectivity for `@>`. The index was still chosen, but for less
  selective criteria the estimate could lead to a worse plan.
- **Small data:** on the 4-product sample dataset, both queries use sequential
  scans even with the indexes in place, and that is the correct choice. The
  automated tests therefore check index *support* by disabling sequential scans
  for one transaction (`tests/test_analytics.py`,
  `tests/test_metadata_queries.py`), and use the benchmark for *performance*.
- **Dead rows matter:** an earlier run made just after a bulk delete measured
  the sequential scan at 154 ms instead of about 10 ms, because the scan had to
  read past deleted rows. The benchmark now vacuums first so the comparison is
  fair; in production, autovacuum handles this.

## 5. Costs and recommendations

- Indexes slow writes slightly and use storage. Both are justified here because
  they serve frequent read paths (customer history and product search).
- A GIN index helps only operators it supports (`@>`, `?`, `?|`, `?&`). A query
  such as `metadata ->> 'brand' = 'brand_1'` would not use it; use `@>` instead,
  or add an expression B-tree index if that pattern is common.
- Frequently filtered attributes such as `category` could become relational
  columns with ordinary B-tree indexes and accurate planner statistics.
- `order_items.product_id` has no dedicated index. The product ranking reports
  aggregate all rows anyway, but per-product lookups would benefit from one.
- Re-run the benchmark after schema or data-volume changes rather than
  assuming these results still hold.

## 6. Conclusion

On a 670,000-row dataset, both indexes are used by the planner and give a
measurable improvement: about 200× for customer order lookups (B-tree) and
about 13× for JSONB metadata searches (GIN), with the same results returned.
On the tiny sample dataset, sequential scans remain the better plan, which is
why the measurement uses realistic data volumes.
