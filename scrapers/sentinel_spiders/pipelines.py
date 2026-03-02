# =============================================================================
#  SentinelPrice · Scrapy Pipelines
# =============================================================================
#  Item processing pipeline executed in order after each spider yields an item.
#
#  Pipeline stages (order defined in settings.py):
#    100 · ValidationPipeline      — Drop incomplete or malformed items
#    200 · DeduplicationPipeline   — Skip already-seen SKUs within a crawl
#    300 · CleaningPipeline        — Normalize, enrich, and finalize fields
#    400 · PostgreSQLPipeline      — Upsert products, insert price snapshots
#    500 · AlertPipeline           — Detect price changes and dispatch notifications
#
#  Docs: https://docs.scrapy.org/en/latest/topics/item-pipeline.html
# =============================================================================

import hashlib
import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from itemloaders.processors import MapCompose
from psycopg2 import OperationalError, IntegrityError
from scrapy.exceptions import DropItem, NotConfigured

from sentinel_spiders.items import ProductItem, PriceSnapshotItem, compute_discount


logger = logging.getLogger(__name__)


# =============================================================================
#  1. ValidationPipeline
# =============================================================================

class ValidationPipeline:
    """
    Validates every item before it enters the rest of the pipeline.
    Drops items that are missing required fields or contain invalid values.

    Required fields:
      ProductItem       — source, sku, name, url
      PriceSnapshotItem — source, sku, price_current, currency
    """

    PRODUCT_REQUIRED_FIELDS       = {"source", "sku", "name", "url"}
    PRICE_SNAPSHOT_REQUIRED_FIELDS = {"source", "sku", "price_current", "currency"}

    def process_item(self, item, spider):
        if isinstance(item, ProductItem):
            return self._validate(item, self.PRODUCT_REQUIRED_FIELDS, spider)

        if isinstance(item, PriceSnapshotItem):
            item = self._validate(item, self.PRICE_SNAPSHOT_REQUIRED_FIELDS, spider)
            return self._validate_price(item, spider)

        # Unknown item type — pass through
        return item

    def _validate(self, item, required_fields, spider):
        """Drop item if any required field is missing or empty."""
        missing = [
            field for field in required_fields
            if not item.get(field)
        ]
        if missing:
            raise DropItem(
                f"[{spider.name}] Missing required fields {missing} "
                f"for SKU={item.get('sku', 'N/A')}"
            )
        return item

    def _validate_price(self, item, spider):
        """Drop item if price is zero, negative, or non-numeric."""
        try:
            price = float(item.get("price_current", 0))
        except (TypeError, ValueError):
            raise DropItem(
                f"[{spider.name}] Non-numeric price_current for SKU={item.get('sku')}"
            )

        if price <= 0:
            raise DropItem(
                f"[{spider.name}] Invalid price_current={price} for SKU={item.get('sku')}"
            )

        return item


# =============================================================================
#  2. DeduplicationPipeline
# =============================================================================

class DeduplicationPipeline:
    """
    Prevents duplicate items within a single crawl session.

    Uses an in-memory set of fingerprints built from (source, sku).
    Resets on each spider open — different crawl runs can re-insert
    price snapshots (which is the desired behaviour for history tracking).

    Only deduplicates ProductItems — PriceSnapshotItems are always
    inserted to preserve the full pricing history.
    """

    def open_spider(self, spider):
        self._seen: set[str] = set()
        logger.debug("DeduplicationPipeline ready for spider: %s", spider.name)

    def process_item(self, item, spider):
        if not isinstance(item, ProductItem):
            return item   # Always let price snapshots through

        fingerprint = self._make_fingerprint(item)

        if fingerprint in self._seen:
            raise DropItem(
                f"Duplicate ProductItem dropped: source={item.get('source')} "
                f"sku={item.get('sku')}"
            )

        self._seen.add(fingerprint)
        return item

    def _make_fingerprint(self, item) -> str:
        key = f"{item.get('source', '')}:{item.get('sku', '')}".encode("utf-8")
        return hashlib.blake2b(key, digest_size=16).hexdigest()


# =============================================================================
#  3. CleaningPipeline
# =============================================================================

class CleaningPipeline:
    """
    Normalizes and enriches items before database insertion.

    Responsibilities:
      - Truncate oversized string fields to fit DB column limits
      - Uppercase currency codes (e.g. 'usd' → 'USD')
      - Clamp rating to valid range [0.0 – 5.0]
      - Compute discount_pct from price_current and price_original
      - Set scraped_at timestamp on PriceSnapshotItems
      - Default availability to 'unknown' if not set
    """

    # Max lengths matching the PostgreSQL schema in init.sql
    STRING_LIMITS = {
        "sku":      255,
        "name":     None,    # TEXT — no hard limit but truncate extreme values
        "brand":    255,
        "category": 255,
        "currency": 3,
    }
    NAME_MAX_LENGTH = 1000   # Practical cap for product names

    def process_item(self, item, spider):
        if isinstance(item, ProductItem):
            return self._clean_product(item)

        if isinstance(item, PriceSnapshotItem):
            return self._clean_snapshot(item)

        return item

    # -------------------------------------------------------------------------

    def _clean_product(self, item):
        item["sku"]      = self._truncate(item.get("sku"),      255)
        item["name"]     = self._truncate(item.get("name"),     self.NAME_MAX_LENGTH)
        item["brand"]    = self._truncate(item.get("brand"),    255)
        item["category"] = self._truncate(item.get("category"), 255)
        item["url"]      = (item.get("url") or "").strip()
        return item

    def _clean_snapshot(self, item):
        # Currency
        currency = item.get("currency") or "USD"
        item["currency"] = currency.upper().strip()[:3]

        # Availability default
        if not item.get("availability"):
            item["availability"] = "unknown"

        # Rating clamp
        rating = item.get("rating")
        if rating is not None:
            try:
                item["rating"] = round(min(max(float(rating), 0.0), 5.0), 1)
            except (TypeError, ValueError):
                item["rating"] = None

        # Compute discount if not already set by the spider
        if not item.get("discount_pct"):
            item["discount_pct"] = compute_discount(item)

        # Timestamp — always set server-side to UTC
        item["scraped_at"] = datetime.now(timezone.utc)

        return item

    # -------------------------------------------------------------------------

    @staticmethod
    def _truncate(value, max_len):
        if value is None:
            return None
        s = str(value).strip()
        return s[:max_len] if max_len and len(s) > max_len else s


# =============================================================================
#  4. PostgreSQLPipeline
# =============================================================================

class PostgreSQLPipeline:
    """
    Persists items to PostgreSQL using psycopg2.

    ProductItem       → UPSERT into `products` (update metadata on conflict)
    PriceSnapshotItem → INSERT into `pricing_history` (always append)

    Uses a persistent connection opened per-spider and closed on finish.
    Inserts are committed after every item for durability. For higher
    throughput, switch to batch commits using a buffer + flush strategy.
    """

    def __init__(self, db_settings: dict):
        self.db_settings = db_settings
        self.connection  = None
        self.cursor      = None

    @classmethod
    def from_crawler(cls, crawler):
        db = crawler.settings.get("DATABASE")
        if not db:
            raise NotConfigured("DATABASE settings not found. Check settings.py.")
        return cls(db)

    # -------------------------------------------------------------------------
    #  Connection lifecycle
    # -------------------------------------------------------------------------

    def open_spider(self, spider):
        try:
            self.connection = psycopg2.connect(**self.db_settings)
            self.connection.autocommit = False
            self.cursor = self.connection.cursor()
            logger.info(
                "PostgreSQL connection established for spider: %s | db=%s host=%s",
                spider.name,
                self.db_settings.get("dbname"),
                self.db_settings.get("host"),
            )
        except OperationalError as e:
            raise NotConfigured(
                f"Could not connect to PostgreSQL: {e}\n"
                f"Check your DATABASE settings and ensure the db container is running."
            )

    def close_spider(self, spider):
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        logger.info("PostgreSQL connection closed for spider: %s", spider.name)

    # -------------------------------------------------------------------------
    #  Item routing
    # -------------------------------------------------------------------------

    def process_item(self, item, spider):
        if isinstance(item, ProductItem):
            self._upsert_product(item, spider)

        elif isinstance(item, PriceSnapshotItem):
            self._insert_price_snapshot(item, spider)

        return item

    # -------------------------------------------------------------------------
    #  ProductItem → products table
    # -------------------------------------------------------------------------

    def _upsert_product(self, item, spider):
        """
        Insert a new product or update its metadata if it already exists.
        Conflict is resolved on (sku, source_id) — the unique constraint
        defined in init.sql.
        """
        sql = """
            WITH source AS (
                SELECT source_id
                FROM   sources
                WHERE  name = %(source)s
                LIMIT  1
            )
            INSERT INTO products (source_id, sku, name, brand, category, url, image_url)
            SELECT
                source.source_id,
                %(sku)s,
                %(name)s,
                %(brand)s,
                %(category)s,
                %(url)s,
                %(image_url)s
            FROM source
            ON CONFLICT (sku, source_id) DO UPDATE SET
                name       = EXCLUDED.name,
                brand      = EXCLUDED.brand,
                category   = EXCLUDED.category,
                url        = EXCLUDED.url,
                image_url  = EXCLUDED.image_url,
                updated_at = NOW();
        """
        self._execute(sql, dict(item), spider)

    # -------------------------------------------------------------------------
    #  PriceSnapshotItem → pricing_history table
    # -------------------------------------------------------------------------

    def _insert_price_snapshot(self, item, spider):
        """
        Resolve the product_id from (sku, source), then append a price snapshot.
        Skips the insert (with a warning) if the product is not yet in the DB.
        """
        sql = """
            WITH product AS (
                SELECT p.product_id
                FROM   products p
                JOIN   sources  s ON s.source_id = p.source_id
                WHERE  p.sku  = %(sku)s
                  AND  s.name = %(source)s
                LIMIT  1
            )
            INSERT INTO pricing_history (
                product_id,
                price_current,
                price_original,
                discount_pct,
                currency,
                availability,
                rating,
                review_count,
                scraped_at
            )
            SELECT
                product.product_id,
                %(price_current)s,
                %(price_original)s,
                %(discount_pct)s,
                %(currency)s,
                %(availability)s,
                %(rating)s,
                %(review_count)s,
                %(scraped_at)s
            FROM product
            WHERE product.product_id IS NOT NULL;
        """
        params = {
            "sku":            item.get("sku"),
            "source":         item.get("source"),
            "price_current":  item.get("price_current"),
            "price_original": item.get("price_original"),
            "discount_pct":   item.get("discount_pct"),
            "currency":       item.get("currency", "USD"),
            "availability":   item.get("availability", "unknown"),
            "rating":         item.get("rating"),
            "review_count":   item.get("review_count"),
            "scraped_at":     item.get("scraped_at"),
        }
        self._execute(sql, params, spider)

    # -------------------------------------------------------------------------
    #  Execution helper
    # -------------------------------------------------------------------------

    def _execute(self, sql: str, params: dict, spider):
        """Execute a query and commit. On error, rollback and log."""
        try:
            self.cursor.execute(sql, params)
            self.connection.commit()
        except IntegrityError as e:
            self.connection.rollback()
            logger.warning(
                "IntegrityError for SKU=%s source=%s: %s",
                params.get("sku"), params.get("source"), e,
            )
        except Exception as e:
            self.connection.rollback()
            logger.error(
                "Database error for SKU=%s source=%s: %s",
                params.get("sku"), params.get("source"), e,
                exc_info=True,
            )
            raise DropItem(f"Database insert failed for SKU={params.get('sku')}: {e}")


# =============================================================================
#  5. AlertPipeline
# =============================================================================

class AlertPipeline:
    """
    Detects significant price changes and dispatches Email / Slack alerts.
    Runs after PostgreSQLPipeline (stage 500) so the new snapshot is already
    persisted before we query for the previous one.

    Enabled only when at least one alert channel is configured in .env:
        ALERT_EMAIL_ENABLED=true
        ALERT_SLACK_ENABLED=true

    Thresholds (configurable via .env):
        ALERT_PRICE_DROP_THRESHOLD     — default 5%
        ALERT_PRICE_INCREASE_THRESHOLD — default 10%
    """

    def __init__(self, db_settings: dict):
        self.db_settings   = db_settings
        self.alert_manager = None

    @classmethod
    def from_crawler(cls, crawler):
        db = crawler.settings.get("DATABASE")
        if not db:
            raise NotConfigured("DATABASE settings not found.")
        return cls(db)

    def open_spider(self, spider):
        import os
        email_on = os.environ.get("ALERT_EMAIL_ENABLED", "false").lower() == "true"
        slack_on = os.environ.get("ALERT_SLACK_ENABLED", "false").lower() == "true"

        if not email_on and not slack_on:
            logger.info("AlertPipeline: no channels enabled — skipping.")
            return

        from sentinel_spiders.alerts.alert_manager import AlertManager
        self.alert_manager = AlertManager(self.db_settings, {})
        logger.info("AlertPipeline ready — channels: %s",
            ", ".join([
                *( ["email"] if email_on else []),
                *( ["slack"] if slack_on else []),
            ])
        )

    def process_item(self, item, spider):
        if self.alert_manager and isinstance(item, PriceSnapshotItem):
            self.alert_manager.check_and_alert(item)
        return item