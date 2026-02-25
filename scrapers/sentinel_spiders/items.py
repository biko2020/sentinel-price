# =============================================================================
#  SentinelPrice · Scrapy Items
# =============================================================================
#  Defines the data models (Items) that spiders populate and pipelines
#  process before inserting into PostgreSQL.
#
#  Each Item maps directly to a database table:
#    · ProductItem       → products table
#    · PriceSnapshotItem → pricing_history table
#
#  Docs: https://docs.scrapy.org/en/latest/topics/items.html
# =============================================================================

import scrapy
from itemloaders.processors import TakeFirst, MapCompose, Join
from w3lib.html import remove_tags


# -----------------------------------------------------------------------------
#  Helpers
# -----------------------------------------------------------------------------

def to_float(value):
    """Strip currency symbols and formatting, return a float or None."""
    if value is None:
        return None
    try:
        cleaned = (
            str(value)
            .replace(",", "")
            .replace("$", "")
            .replace("€", "")
            .replace("£", "")
            .replace("¥", "")
            .strip()
        )
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def to_int(value):
    """Strip non-numeric characters and return an int or None."""
    if value is None:
        return None
    try:
        cleaned = "".join(filter(str.isdigit, str(value)))
        return int(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def normalize_availability(value):
    """
    Map raw availability strings to availability_status ENUM values.
    Must match the ENUM defined in init.sql.
    """
    if value is None:
        return "unknown"
    mapping = {
        "in stock":       "in_stock",
        "in-stock":       "in_stock",
        "instock":        "in_stock",
        "out of stock":   "out_of_stock",
        "out-of-stock":   "out_of_stock",
        "outofstock":     "out_of_stock",
        "unavailable":    "out_of_stock",
        "limited stock":  "limited_stock",
        "limited":        "limited_stock",
        "only":           "limited_stock",   # "Only 3 left in stock"
        "pre-order":      "preorder",
        "preorder":       "preorder",
        "pre order":      "preorder",
        "coming soon":    "preorder",
        "discontinued":   "discontinued",
    }
    normalized = str(value).lower().strip()
    for key, status in mapping.items():
        if key in normalized:
            return status
    return "unknown"


def clean_text(value):
    """Remove HTML tags and normalize whitespace."""
    if value is None:
        return None
    return " ".join(remove_tags(str(value)).split())


def strip_whitespace(value):
    """Strip leading and trailing whitespace."""
    if value is None:
        return None
    return str(value).strip()


def compute_discount(item):
    """
    Compute discount percentage from price_current and price_original.
    Called in the pipeline after both fields are available.
    """
    try:
        original = float(item.get("price_original") or 0)
        current  = float(item.get("price_current")  or 0)
        if original > 0 and current < original:
            return round(((original - current) / original) * 100, 2)
    except (TypeError, ValueError):
        pass
    return None


# -----------------------------------------------------------------------------
#  ProductItem
# -----------------------------------------------------------------------------
#  Represents a product's static metadata.
#  Maps to the `products` table in PostgreSQL.

class ProductItem(scrapy.Item):

    # --- Source --------------------------------------------------------------
    source      = scrapy.Field(
        input_processor  = MapCompose(strip_whitespace),
        output_processor = TakeFirst(),
    )  # Spider name / platform identifier (e.g. 'amazon', 'walmart')

    # --- Identifiers ---------------------------------------------------------
    sku         = scrapy.Field(
        input_processor  = MapCompose(strip_whitespace),
        output_processor = TakeFirst(),
    )  # Platform-specific product ID (ASIN, item number, etc.)

    # --- Descriptive Metadata ------------------------------------------------
    name        = scrapy.Field(
        input_processor  = MapCompose(clean_text),
        output_processor = TakeFirst(),
    )

    brand       = scrapy.Field(
        input_processor  = MapCompose(clean_text),
        output_processor = TakeFirst(),
    )

    category    = scrapy.Field(
        input_processor  = MapCompose(clean_text),
        output_processor = TakeFirst(),
    )

    # --- URLs ----------------------------------------------------------------
    url         = scrapy.Field(
        input_processor  = MapCompose(strip_whitespace),
        output_processor = TakeFirst(),
    )  # Canonical product page URL

    image_url   = scrapy.Field(
        input_processor  = MapCompose(strip_whitespace),
        output_processor = TakeFirst(),
    )


# -----------------------------------------------------------------------------
#  PriceSnapshotItem
# -----------------------------------------------------------------------------
#  Represents a single price observation captured during a crawl.
#  Maps to the `pricing_history` table in PostgreSQL.

class PriceSnapshotItem(scrapy.Item):

    # --- Foreign Key ---------------------------------------------------------
    sku         = scrapy.Field(
        input_processor  = MapCompose(strip_whitespace),
        output_processor = TakeFirst(),
    )  # Used by the pipeline to resolve product_id

    source      = scrapy.Field(
        input_processor  = MapCompose(strip_whitespace),
        output_processor = TakeFirst(),
    )

    # --- Pricing -------------------------------------------------------------
    price_current   = scrapy.Field(
        input_processor  = MapCompose(to_float),
        output_processor = TakeFirst(),
    )  # Current selling price (normalized to float)

    price_original  = scrapy.Field(
        input_processor  = MapCompose(to_float),
        output_processor = TakeFirst(),
    )  # Original / crossed-out price, if available

    discount_pct    = scrapy.Field(
        output_processor = TakeFirst(),
    )  # Computed by pipeline via compute_discount()

    currency        = scrapy.Field(
        input_processor  = MapCompose(strip_whitespace),
        output_processor = TakeFirst(),
    )  # ISO 4217 currency code (e.g. 'USD', 'EUR')

    # --- Availability --------------------------------------------------------
    availability    = scrapy.Field(
        input_processor  = MapCompose(normalize_availability),
        output_processor = TakeFirst(),
    )  # Normalized to availability_status ENUM values

    # --- Social Proof --------------------------------------------------------
    rating          = scrapy.Field(
        input_processor  = MapCompose(to_float),
        output_processor = TakeFirst(),
    )  # Average rating (e.g. 4.5)

    review_count    = scrapy.Field(
        input_processor  = MapCompose(to_int),
        output_processor = TakeFirst(),
    )  # Total number of reviews

    # --- Timestamp -----------------------------------------------------------
    scraped_at      = scrapy.Field(
        output_processor = TakeFirst(),
    )  # Set automatically by the pipeline (datetime.utcnow())