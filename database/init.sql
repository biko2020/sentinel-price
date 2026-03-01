-- =============================================================================
--  SentinelPrice · Database Initialization Script
-- =============================================================================
--  This script is executed automatically by the PostgreSQL Docker container
--  on first startup (mounted via docker-compose.yml as an init script).
--
--  To run manually:
--    psql -U <your_db_user> -d sentinelprice -f database/init.sql
--
--  Schema overview:
--    · sources          — tracked e-commerce platforms (Amazon, Walmart, etc.)
--    · products         — canonical product catalog (one row per unique SKU)
--    · pricing_history  — append-only price snapshots over time
-- =============================================================================


-- -----------------------------------------------------------------------------
--  EXTENSIONS
-- -----------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid() support


-- -----------------------------------------------------------------------------
--  TYPES
-- -----------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE availability_status AS ENUM (
        'in_stock',
        'out_of_stock',
        'limited_stock',
        'preorder',
        'discontinued',
        'unknown'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;  -- Skip if already exists (idempotent re-runs)
END $$;


-- -----------------------------------------------------------------------------
--  TABLE: sources
-- -----------------------------------------------------------------------------
--  Tracks the e-commerce platforms being monitored.
--  Each spider maps to one source row.

CREATE TABLE IF NOT EXISTS sources (
    source_id   SERIAL          PRIMARY KEY,
    name        VARCHAR(100)    NOT NULL UNIQUE,   -- e.g. 'amazon', 'walmart'
    base_url    TEXT            NOT NULL,           -- e.g. 'https://www.amazon.com'
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Seed default sources
INSERT INTO sources (name, base_url) VALUES
    ('amazon',  'https://www.amazon.com'),
    ('walmart', 'https://www.walmart.com'),
    ('target',  'https://www.target.com'),
    ('ebay',    'https://www.ebay.com'),
    ('bestbuy', 'https://www.bestbuy.com')
ON CONFLICT (name) DO NOTHING;


-- -----------------------------------------------------------------------------
--  TABLE: products
-- -----------------------------------------------------------------------------
--  Canonical product catalog. One row per unique (sku, source) combination.
--  Updated in place when product metadata changes (name, brand, url).

CREATE TABLE IF NOT EXISTS products (
    product_id      SERIAL          PRIMARY KEY,
    source_id       INT             NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    sku             VARCHAR(255)    NOT NULL,
    name            TEXT            NOT NULL,
    brand           VARCHAR(255),
    category        VARCHAR(255),
    url             TEXT            NOT NULL,
    image_url       TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_sku_source UNIQUE (sku, source_id)
);

-- Index for fast SKU lookups
CREATE INDEX IF NOT EXISTS idx_products_sku
    ON products (sku);

-- Index for filtering by source
CREATE INDEX IF NOT EXISTS idx_products_source_id
    ON products (source_id);


-- -----------------------------------------------------------------------------
--  TABLE: pricing_history
-- -----------------------------------------------------------------------------
--  Append-only log of price snapshots. One row per crawl per product.
--  Never updated — only inserted. This preserves full price history.

CREATE TABLE IF NOT EXISTS pricing_history (
    id              BIGSERIAL           PRIMARY KEY,
    product_id      INT                 NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    price_current   NUMERIC(12, 2)      NOT NULL,
    price_original  NUMERIC(12, 2),                 -- Original / crossed-out price if available
    discount_pct    NUMERIC(5, 2),                  -- Computed discount percentage
    currency        CHAR(3)             NOT NULL DEFAULT 'USD',  -- ISO 4217
    availability    availability_status NOT NULL DEFAULT 'unknown',
    rating          NUMERIC(3, 1),                  -- e.g. 4.5
    review_count    INT,
    scraped_at      TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

-- Index for fast per-product history queries
CREATE INDEX IF NOT EXISTS idx_pricing_history_product_id
    ON pricing_history (product_id);

-- Index for time-range queries (price over time, recent snapshots)
CREATE INDEX IF NOT EXISTS idx_pricing_history_scraped_at
    ON pricing_history (scraped_at DESC);

-- Composite index for the most common query pattern:
-- "latest price for product X"
CREATE INDEX IF NOT EXISTS idx_pricing_history_product_time
    ON pricing_history (product_id, scraped_at DESC);


-- -----------------------------------------------------------------------------
--  FUNCTION & TRIGGER: auto-update products.updated_at
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_products_updated_at ON products;
CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();


-- -----------------------------------------------------------------------------
--  VIEW: latest_prices
-- -----------------------------------------------------------------------------
--  Convenience view returning the most recent price snapshot per product.
--  Use this for dashboards and real-time monitoring queries.

CREATE OR REPLACE VIEW latest_prices AS
SELECT DISTINCT ON (ph.product_id)
    p.product_id,
    s.name          AS source,
    p.sku,
    p.name          AS product_name,
    p.brand,
    p.category,
    p.url,
    ph.price_current,
    ph.price_original,
    ph.discount_pct,
    ph.currency,
    ph.availability,
    ph.rating,
    ph.review_count,
    ph.scraped_at
FROM pricing_history ph
JOIN products p  ON p.product_id = ph.product_id
JOIN sources  s  ON s.source_id  = p.source_id
ORDER BY ph.product_id, ph.scraped_at DESC;


-- -----------------------------------------------------------------------------
--  VIEW: price_changes
-- -----------------------------------------------------------------------------
--  Detects price changes by comparing each snapshot to the previous one.
--  Useful for alerting pipelines and change-log dashboards.

CREATE OR REPLACE VIEW price_changes AS
SELECT
    product_id,
    sku,
    product_name,
    source,
    currency,
    prev_price,
    price_current,
    ROUND(((price_current - prev_price) / NULLIF(prev_price, 0)) * 100, 2) AS change_pct,
    scraped_at
FROM (
    SELECT
        ph.product_id,
        p.sku,
        p.name          AS product_name,
        s.name          AS source,
        ph.currency,
        ph.price_current,
        LAG(ph.price_current) OVER (
            PARTITION BY ph.product_id
            ORDER BY ph.scraped_at
        )               AS prev_price,
        ph.scraped_at
    FROM pricing_history ph
    JOIN products p ON p.product_id = ph.product_id
    JOIN sources  s ON s.source_id  = p.source_id
) sub
WHERE prev_price IS NOT NULL
  AND price_current <> prev_price;


-- =============================================================================
--  Schema initialized successfully.
-- =============================================================================