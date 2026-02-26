# =============================================================================
#  SentinelPrice · Walmart Spider
# =============================================================================
#  Extracts product metadata and pricing data from Walmart product pages.
#
#  Yields:
#    · ProductItem       — static product metadata (name, brand, category …)
#    · PriceSnapshotItem — price observation (price, availability, rating …)
#
#  Usage:
#    scrapy crawl walmart_spider
#    scrapy crawl walmart_spider -a url="https://www.walmart.com/ip/product-name/ITEM_ID"
#    scrapy crawl walmart_spider -a item_id="123456789"
#
#  Configuration:
#    Add target item IDs or URLs to START_ITEM_IDS / start_urls below.
#    For large-scale monitoring, load URLs from the database instead —
#    see the _load_urls_from_db() method stub at the bottom.
#
#  Notes:
#    Walmart heavily relies on Next.js — product data is embedded in a
#    __NEXT_DATA__ JSON blob inside the HTML. This spider extracts data
#    from that blob first, then falls back to CSS selectors for resilience.
# =============================================================================

import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import scrapy
from itemloaders import ItemLoader

from sentinel_spiders.items import ProductItem, PriceSnapshotItem


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
#  Target item IDs — add your products here
# -----------------------------------------------------------------------------

START_ITEM_IDS = [
    # "123456789",    # Product name / description for reference
    # "987654321",
]

WALMART_BASE_URL  = "https://www.walmart.com"
PRODUCT_URL_TPL   = "https://www.walmart.com/ip/{item_id}"


# =============================================================================
#  WalmartSpider
# =============================================================================

class WalmartSpider(scrapy.Spider):
    name            = "walmart_spider"
    allowed_domains = ["walmart.com", "www.walmart.com"]
    custom_settings = {
        "DOWNLOAD_DELAY":                   2.0,
        "RANDOMIZE_DOWNLOAD_DELAY":         True,
        "CONCURRENT_REQUESTS_PER_DOMAIN":   2,
        "ROBOTSTXT_OBEY":                   False,   # walmart.com/robots.txt blocks scrapers
        "COOKIES_ENABLED":                  True,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language":  "en-US,en;q=0.9",
            "Accept-Encoding":  "gzip, deflate, br",
            "Connection":       "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        },
    }

    # -------------------------------------------------------------------------
    #  Initialization
    # -------------------------------------------------------------------------

    def __init__(self, url=None, item_id=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.start_urls = []

        # Single URL passed via -a url=...
        if url:
            self.start_urls.append(self._normalize_url(url))

        # Single item ID passed via -a item_id=...
        elif item_id:
            self.start_urls.append(PRODUCT_URL_TPL.format(item_id=item_id.strip()))

        # Bulk item IDs defined in START_ITEM_IDS
        elif START_ITEM_IDS:
            self.start_urls = [
                PRODUCT_URL_TPL.format(item_id=i.strip()) for i in START_ITEM_IDS
            ]

        else:
            logger.warning(
                "No start URLs configured. "
                "Add item IDs to START_ITEM_IDS or pass -a url=... / -a item_id=..."
            )

    # -------------------------------------------------------------------------
    #  Request generation
    # -------------------------------------------------------------------------

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(
                url,
                callback = self.parse_product,
                errback  = self.handle_error,
                meta     = {
                    "handle_httpstatus_list": [404, 410, 503],
                },
                headers  = self._browser_headers(),
            )

    # -------------------------------------------------------------------------
    #  Main parser
    # -------------------------------------------------------------------------

    def parse_product(self, response):
        """
        Parse a single Walmart product page.

        Strategy:
          1. Extract data from the __NEXT_DATA__ JSON blob (most reliable).
          2. Fall back to CSS selectors if JSON is absent or incomplete.
          3. Yield a ProductItem and a PriceSnapshotItem.
        """
        if self._is_blocked(response):
            logger.warning("Bot block detected on %s — skipping.", response.url)
            self.crawler.stats.inc_value("sentinel/blocked_responses")
            return

        if response.status in (404, 410):
            logger.info("Product not found [%s]: %s", response.status, response.url)
            return

        item_id = self._extract_item_id(response.url)
        if not item_id:
            logger.warning("Could not extract item ID from URL: %s", response.url)
            return

        logger.debug("Parsing product page: %s | item_id=%s", response.url, item_id)

        # Attempt JSON extraction first
        next_data = self._extract_next_data(response)

        product  = self._parse_product_item(response, item_id, next_data)
        snapshot = self._parse_price_snapshot(response, item_id, next_data)

        if product:
            yield product
        if snapshot:
            yield snapshot

    # -------------------------------------------------------------------------
    #  __NEXT_DATA__ extraction
    # -------------------------------------------------------------------------

    def _extract_next_data(self, response) -> dict:
        """
        Walmart embeds all page data in a <script id="__NEXT_DATA__"> JSON blob.
        Extracting from this is far more reliable than CSS selectors alone,
        as it is the canonical source the page renders from.

        Returns an empty dict if the blob is absent or malformed.
        """
        raw = response.css("script#__NEXT_DATA__::text").get()
        if not raw:
            logger.debug("__NEXT_DATA__ not found on %s", response.url)
            return {}
        try:
            data = json.loads(raw)
            # Navigate to the product node: props → pageProps → initialData → data → product
            return (
                data
                .get("props", {})
                .get("pageProps", {})
                .get("initialData", {})
                .get("data", {})
                .get("product", {})
            )
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning("Failed to parse __NEXT_DATA__ on %s: %s", response.url, e)
            return {}

    # -------------------------------------------------------------------------
    #  ProductItem extraction
    # -------------------------------------------------------------------------

    def _parse_product_item(self, response, item_id: str, nd: dict):
        loader = ItemLoader(item=ProductItem(), selector=response)

        loader.add_value("source", "walmart")
        loader.add_value("sku",    item_id)
        loader.add_value("url",    self._canonical_url(item_id))

        # --- Name ------------------------------------------------------------
        name = (
            nd.get("name")
            or nd.get("title")
            or response.css(
                "h1.prod-ProductTitle::text, "
                '[itemprop="name"]::text, '
                "h1[data-automation-id='product-title']::text"
            ).get()
        )
        loader.add_value("name", name)

        # --- Brand -----------------------------------------------------------
        brand = (
            nd.get("brand", {}).get("name")
            if isinstance(nd.get("brand"), dict)
            else nd.get("brand")
            or response.css(
                ".prod-brandName a::text, "
                '[itemprop="brand"] [itemprop="name"]::text, '
                "a[data-automation-id='brand-link']::text"
            ).get()
        )
        loader.add_value("brand", brand)

        # --- Category --------------------------------------------------------
        category = self._extract_category(response, nd)
        loader.add_value("category", category)

        # --- Image -----------------------------------------------------------
        image_url = (
            self._deep_get(nd, "imageInfo", "thumbnailUrl")
            or self._deep_get(nd, "imageInfo", "allImages", 0, "url")
            or response.css(
                'img[data-automation-id="product-image"]::attr(src), '
                ".prod-hero-image img::attr(src)"
            ).get()
        )
        loader.add_value("image_url", image_url)

        item = loader.load_item()

        if not item.get("name"):
            logger.warning("Could not extract product name for item_id=%s", item_id)
            return None

        return item

    # -------------------------------------------------------------------------
    #  PriceSnapshotItem extraction
    # -------------------------------------------------------------------------

    def _parse_price_snapshot(self, response, item_id: str, nd: dict):
        loader = ItemLoader(item=PriceSnapshotItem(), selector=response)

        loader.add_value("source", "walmart")
        loader.add_value("sku",    item_id)

        # --- Current price ---------------------------------------------------
        price_current = (
            self._deep_get(nd, "priceInfo", "currentPrice", "price")
            or self._deep_get(nd, "priceInfo", "price")
            or self._extract_price_css(response, [
                "[data-automation-id='product-price'] span.price-characteristic",
                ".prod-PriceHero .price-characteristic",
                "[itemprop='price']::attr(content)",
                ".display-price",
            ])
        )
        loader.add_value("price_current", str(price_current) if price_current else None)

        # --- Original price --------------------------------------------------
        price_original = (
            self._deep_get(nd, "priceInfo", "wasPrice", "price")
            or self._deep_get(nd, "priceInfo", "listPrice", "price")
            or self._extract_price_css(response, [
                ".prod-PriceHero .price-group .arrange-fill .price-characteristic",
                "span.price-old .price-characteristic",
                "[data-automation-id='product-was-price'] .price-characteristic",
            ])
        )
        loader.add_value("price_original", str(price_original) if price_original else None)

        # --- Currency --------------------------------------------------------
        currency = (
            self._deep_get(nd, "priceInfo", "currentPrice", "currencyUnit")
            or "USD"
        )
        loader.add_value("currency", currency)

        # --- Availability ----------------------------------------------------
        availability_raw = (
            self._deep_get(nd, "availabilityStatus")
            or self._deep_get(nd, "fulfillmentStatus")
            or response.css(
                "[data-automation-id='fulfillment-shipping-text']::text, "
                ".prod-fulfillment-content .fulfillment-fulfillment-text::text, "
                "[data-automation-id='product-fulfillment-speed-text']::text"
            ).get()
            or "unknown"
        )
        loader.add_value("availability", str(availability_raw))

        # --- Rating ----------------------------------------------------------
        rating = (
            self._deep_get(nd, "averageRating")
            or self._deep_get(nd, "rating", "averageRating")
            or response.css(
                "[data-automation-id='product-rating'] .average-rating::text, "
                ".stars-reviews-count-node span.f7::text"
            ).get()
        )
        loader.add_value("rating", str(rating) if rating else None)

        # --- Review count ----------------------------------------------------
        review_count = (
            self._deep_get(nd, "numberOfReviews")
            or self._deep_get(nd, "rating", "numberOfReviews")
            or response.css(
                "[data-automation-id='product-rating'] .stars-reviews-count::text, "
                ".stars-reviews-count-node span.f7 + span::text"
            ).get()
        )
        loader.add_value("review_count", str(review_count) if review_count else None)

        item = loader.load_item()
        item["scraped_at"] = datetime.now(timezone.utc)

        if not item.get("price_current"):
            logger.info(
                "No price found for item_id=%s — product may be unavailable.", item_id
            )

        return item

    # -------------------------------------------------------------------------
    #  Extraction helpers
    # -------------------------------------------------------------------------

    def _extract_price_css(self, response, selectors: list) -> str | None:
        """Try each selector in order, return first value with numeric content."""
        for selector in selectors:
            # Handle attribute selectors vs text selectors
            if "::attr(" in selector:
                val = response.css(selector).get()
            else:
                val = response.css(f"{selector}::text").get()
            if val:
                cleaned = val.strip()
                if cleaned and any(c.isdigit() for c in cleaned):
                    return cleaned
        return None

    def _extract_category(self, response, nd: dict) -> str | None:
        """
        Extract the deepest breadcrumb category.
        Tries __NEXT_DATA__ breadcrumbs first, then CSS.
        """
        # From JSON breadcrumbs
        breadcrumbs = nd.get("breadcrumb", []) or []
        if breadcrumbs and isinstance(breadcrumbs, list):
            try:
                return breadcrumbs[-1].get("name") or breadcrumbs[-2].get("name")
            except (IndexError, AttributeError):
                pass

        # From page breadcrumb nav
        crumbs = response.css(
            "nav[aria-label='breadcrumb'] li:last-child a::text, "
            ".breadcrumb-list li:last-child span::text"
        ).getall()
        return crumbs[-1].strip() if crumbs else None

    def _extract_item_id(self, url: str) -> str | None:
        """
        Extract Walmart item ID from URL.
        Handles formats:
          /ip/product-name/123456789
          /ip/123456789
          /ip/product-name/123456789?param=value
        """
        match = re.search(r"/ip/(?:[^/]+/)?(\d+)", url)
        return match.group(1) if match else None

    def _canonical_url(self, item_id: str) -> str:
        return f"{WALMART_BASE_URL}/ip/{item_id}"

    def _normalize_url(self, url: str) -> str:
        """Strip query parameters from a Walmart URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def _deep_get(self, data, *keys):
        """
        Safely traverse a nested dict/list using a sequence of keys/indices.
        Returns None if any key is missing or type is unexpected.

        Example:
            _deep_get(nd, "priceInfo", "currentPrice", "price")
        """
        current = data
        for key in keys:
            if current is None:
                return None
            try:
                if isinstance(current, list):
                    current = current[int(key)]
                elif isinstance(current, dict):
                    current = current.get(key)
                else:
                    return None
            except (KeyError, IndexError, ValueError, TypeError):
                return None
        return current

    # -------------------------------------------------------------------------
    #  Bot detection
    # -------------------------------------------------------------------------

    def _is_blocked(self, response) -> bool:
        """
        Detect Walmart bot-block patterns:
          - CAPTCHA / robot check pages
          - Redirect to identity verification
          - 503 service unavailable
        """
        if response.status == 503:
            return True

        title = response.css("title::text").get(default="").lower()
        body  = response.text[:5000].lower()   # Check only start of body for speed

        block_signals = [
            "robot or human",
            "captcha",
            "access denied",
            "blocked",
            "verify you are human",
            "please verify",
            "security check",
        ]
        return any(signal in title or signal in body for signal in block_signals)

    # -------------------------------------------------------------------------
    #  Request headers
    # -------------------------------------------------------------------------

    def _browser_headers(self) -> dict:
        return {
            "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language":           "en-US,en;q=0.9",
            "Accept-Encoding":           "gzip, deflate, br",
            "Connection":                "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest":            "document",
            "Sec-Fetch-Mode":            "navigate",
            "Sec-Fetch-Site":            "none",
            "Sec-Fetch-User":            "?1",
        }

    # -------------------------------------------------------------------------
    #  Error handler
    # -------------------------------------------------------------------------

    def handle_error(self, failure):
        logger.error(
            "Request failed: %s | %s: %s",
            failure.request.url,
            failure.type.__name__,
            failure.getErrorMessage(),
        )
        self.crawler.stats.inc_value("sentinel/request_errors")

    # -------------------------------------------------------------------------
    #  DB-driven URL loading (stub for large-scale monitoring)
    # -------------------------------------------------------------------------

    def _load_urls_from_db(self) -> list[str]:
        """
        Optional: load target product URLs from the `products` table
        instead of a hard-coded list. Useful for large-scale monitoring
        where target URLs are managed in the database.

        Usage:
            self.start_urls = self._load_urls_from_db()

        Requires DATABASE settings to be accessible at spider init time.
        """
        import psycopg2
        from sentinel_spiders.settings import DATABASE

        try:
            conn = psycopg2.connect(**DATABASE)
            cur  = conn.cursor()
            cur.execute(
                "SELECT url FROM products "
                "JOIN sources ON products.source_id = sources.source_id "
                "WHERE sources.name = 'walmart'"
            )
            urls = [row[0] for row in cur.fetchall()]
            cur.close()
            conn.close()
            logger.info("Loaded %d Walmart URLs from the database.", len(urls))
            return urls
        except Exception as e:
            logger.error("Failed to load URLs from DB: %s", e)
            return []