# =============================================================================
#  SentinelPrice · Amazon Spider
# =============================================================================
#  Extracts product metadata and pricing data from Amazon product pages.
#
#  Yields:
#    · ProductItem       — static product metadata (name, brand, category …)
#    · PriceSnapshotItem — price observation (price, availability, rating …)
#
#  Usage:
#    scrapy crawl amazon_spider
#    scrapy crawl amazon_spider -a url="https://www.amazon.com/dp/B0XXXXXXXX"
#    scrapy crawl amazon_spider -a asin="B0XXXXXXXX" 
#
#  Configuration:
#    Add target ASINs or URLs to START_ASINS / start_urls below.
#    For large-scale monitoring, load URLs from the database instead —
#    see the _load_urls_from_db() method stub at the bottom.
# =============================================================================

import re
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs

import scrapy
from itemloaders import ItemLoader
from itemloaders.processors import TakeFirst

from sentinel_spiders.items import ProductItem, PriceSnapshotItem


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
#  Target ASINs — add your products here 
# -----------------------------------------------------------------------------

START_ASINS = [
    # "B0XXXXXXXX",   # Product name / description for reference
    # "B0YYYYYYYY",
]

AMAZON_BASE_URL  = "https://www.amazon.com"
PRODUCT_URL_TPL  = "https://www.amazon.com/dp/{asin}"


# =============================================================================
#  AmazonSpider
# =============================================================================

class AmazonSpider(scrapy.Spider):
    name              = "amazon_spider"
    allowed_domains   = ["amazon.com", "www.amazon.com"]
    custom_settings   = {
        # Amazon is aggressive — be conservative
        "DOWNLOAD_DELAY":               2.5,
        "RANDOMIZE_DOWNLOAD_DELAY":     True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "ROBOTSTXT_OBEY":               False,   # amazon.com/robots.txt blocks all crawlers
        "COOKIES_ENABLED":              True,
    }

    # -------------------------------------------------------------------------
    #  Initialization
    # -------------------------------------------------------------------------

    def __init__(self, url=None, asin=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.start_urls = []

        # Single URL passed via -a url=...
        if url:
            self.start_urls.append(self._normalize_url(url))

        # Single ASIN passed via -a asin=...
        elif asin:
            self.start_urls.append(PRODUCT_URL_TPL.format(asin=asin.strip()))

        # Bulk ASINs defined in START_ASINS
        elif START_ASINS:
            self.start_urls = [
                PRODUCT_URL_TPL.format(asin=a.strip()) for a in START_ASINS
            ]

        else:
            logger.warning(
                "No start URLs configured. "
                "Add ASINs to START_ASINS or pass -a url=... / -a asin=..."
            )

    # -------------------------------------------------------------------------
    #  Request generation
    # -------------------------------------------------------------------------

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(
                url,
                callback    = self.parse_product,
                errback     = self.handle_error,
                meta        = {
                    "dont_redirect":    False,
                    "handle_httpstatus_list": [404, 503],
                },
                headers     = self._browser_headers(),
            )

    # -------------------------------------------------------------------------
    #  Main parser
    # -------------------------------------------------------------------------

    def parse_product(self, response):
        """
        Parse a single Amazon product (ASIN) page.
        Yields a ProductItem and a PriceSnapshotItem for the same SKU.
        """
        # Guard: detect bot-block / CAPTCHA pages
        if self._is_blocked(response):
            logger.warning("Bot block detected on %s — skipping.", response.url)
            self.crawler.stats.inc_value("sentinel/blocked_responses")
            return

        # Guard: product not found
        if response.status == 404 or self._is_unavailable(response):
            logger.info("Product not found: %s", response.url)
            return

        asin = self._extract_asin(response.url)
        if not asin:
            logger.warning("Could not extract ASIN from URL: %s", response.url)
            return

        logger.debug("Parsing product page: %s | ASIN=%s", response.url, asin)

        # --- Product metadata ------------------------------------------------
        product = self._parse_product_item(response, asin)
        if product:
            yield product

        # --- Price snapshot --------------------------------------------------
        snapshot = self._parse_price_snapshot(response, asin)
        if snapshot:
            yield snapshot

    # -------------------------------------------------------------------------
    #  ProductItem extraction
    # -------------------------------------------------------------------------

    def _parse_product_item(self, response, asin: str):
        loader = ItemLoader(item=ProductItem(), response=response)

        loader.add_value("source", "amazon")
        loader.add_value("sku",    asin)
        loader.add_value("url",    self._canonical_url(asin))

        # Product title
        loader.add_css(
            "name",
            "#productTitle::text, "
            "span#productTitle::text"
        )

        # Brand — multiple possible locations
        loader.add_css(
            "brand",
            "#bylineInfo::text, "
            "#bylineInfo a::text, "
            ".po-brand .po-break-word::text"
        )

        # Category — breadcrumb
        loader.add_css(
            "category",
            "#wayfinding-breadcrumbs_feature_div ul li:last-child a::text, "
            ".a-breadcrumb .a-list-item:last-child a::text"
        )

        # Main product image
        loader.add_css(
            "image_url",
            "#imgTagWrapperId img::attr(src), "
            "#landingImage::attr(src), "
            "#imgBlkFront::attr(src)"
        )

        item = loader.load_item()

        if not item.get("name"):
            logger.warning("Could not extract product name for ASIN=%s", asin)
            return None

        return item

    # -------------------------------------------------------------------------
    #  PriceSnapshotItem extraction
    # -------------------------------------------------------------------------

    def _parse_price_snapshot(self, response, asin: str):
        loader = ItemLoader(item=PriceSnapshotItem(), response=response)

        loader.add_value("source", "amazon")
        loader.add_value("sku",    asin)

        # --- Current price ---------------------------------------------------
        # Amazon uses multiple price containers depending on product type
        price_current = (
            self._extract_price(response, [
                "#priceblock_ourprice",
                "#priceblock_dealprice",
                "#priceblock_saleprice",
                ".a-price.aok-align-center .a-offscreen",
                ".a-price:not(.a-text-price) .a-offscreen",
                "#corePrice_feature_div .a-price .a-offscreen",
                "#apex_offerDisplay_desktop .a-price .a-offscreen",
                "#sns-base-price",
                "#newBuyBoxPrice",
                "#price_inside_buybox",
                "#price",
            ])
        )
        loader.add_value("price_current", price_current)

        # --- Original / strike-through price ---------------------------------
        price_original = self._extract_price(response, [
            ".a-text-price .a-offscreen",
            "#priceblock_listprice",
            "#listPrice",
            ".basisPrice .a-offscreen",
            "#rrpPrice .a-offscreen",
        ])
        loader.add_value("price_original", price_original)

        # --- Currency --------------------------------------------------------
        currency = self._extract_currency(response)
        loader.add_value("currency", currency)

        # --- Availability ----------------------------------------------------
        availability_raw = response.css(
            "#availability span::text, "
            "#outOfStock #availability span::text, "
            "#availability .a-size-medium::text"
        ).getall()
        availability_text = " ".join(availability_raw).strip()
        loader.add_value("availability", availability_text or "unknown")

        # --- Rating ----------------------------------------------------------
        rating_text = response.css(
            "span[data-hook='rating-out-of-text'] .a-icon-alt::text, "
            "#acrPopover .a-icon-alt::text, "
            "i[data-hook='average-star-rating'] .a-icon-alt::text"
        ).get(default="")
        rating = self._parse_rating(rating_text)
        loader.add_value("rating", rating)

        # --- Review count ----------------------------------------------------
        review_count_text = response.css(
            "#acrCustomerReviewText::text, "
            "span[data-hook='total-review-count']::text"
        ).get(default="")
        loader.add_value("review_count", review_count_text)

        item = loader.load_item()

        if not item.get("price_current"):
            logger.info(
                "No price found for ASIN=%s — product may be unavailable or "
                "price requires login.",
                asin,
            )
            # Still yield a snapshot with price=None so availability is tracked
            item["price_current"] = None
            item["scraped_at"]    = datetime.now(timezone.utc)

        return item

    # -------------------------------------------------------------------------
    #  Extraction helpers
    # -------------------------------------------------------------------------

    def _extract_price(self, response, selectors: list) -> str | None:
        """
        Try each CSS selector in order and return the first non-empty price string.
        Uses .a-offscreen spans which contain the raw machine-readable price text.
        """
        for selector in selectors:
            values = response.css(f"{selector}::text").getall()
            for val in values:
                cleaned = val.strip()
                if cleaned and any(c.isdigit() for c in cleaned):
                    return cleaned
        return None

    def _extract_currency(self, response) -> str:
        """
        Detect currency from the price symbol on the page.
        Falls back to USD if not detectable.
        """
        symbol_map = {
            "$": "USD", "£": "GBP", "€": "EUR",
            "¥": "JPY", "₹": "INR", "C$": "CAD",
            "A$": "AUD", "₩": "KRW",
        }
        price_text = response.css(
            ".a-price .a-price-symbol::text"
        ).get(default="$")

        for symbol, code in symbol_map.items():
            if symbol in price_text:
                return code
        return "USD"

    def _extract_asin(self, url: str) -> str | None:
        """Extract ASIN from URL path (e.g. /dp/B0XXXXXXXX)."""
        match = re.search(r"/dp/([A-Z0-9]{10})", url)
        return match.group(1) if match else None

    def _parse_rating(self, text: str) -> float | None:
        """Parse '4.5 out of 5 stars' → 4.5"""
        if not text:
            return None
        match = re.search(r"([\d.]+)\s+out\s+of", text)
        try:
            return float(match.group(1)) if match else None
        except (ValueError, AttributeError):
            return None

    def _canonical_url(self, asin: str) -> str:
        """Return a clean, canonical product URL for storage."""
        return f"{AMAZON_BASE_URL}/dp/{asin}"

    def _normalize_url(self, url: str) -> str:
        """Strip tracking parameters from an Amazon URL."""
        parsed = urlparse(url)
        # Keep only the /dp/ASIN path, strip all query params
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path.split('?')[0]}"
        return clean

    # -------------------------------------------------------------------------
    #  Bot detection
    # -------------------------------------------------------------------------

    def _is_blocked(self, response) -> bool:
        """
        Detect common Amazon bot-block patterns:
          - CAPTCHA page (title or body contains 'captcha' / 'robot check')
          - Empty body
          - Redirect to sign-in
        """
        title = response.css("title::text").get(default="").lower()
        body  = response.text.lower()

        blocked_signals = [
            "captcha",
            "robot check",
            "enter the characters you see below",
            "sorry, we just need to make sure you're not a robot",
            "automated access",
        ]
        if any(signal in title or signal in body for signal in blocked_signals):
            return True

        if response.status == 503:
            return True

        return False

    def _is_unavailable(self, response) -> bool:
        """Detect 'this item is no longer available' pages."""
        body = response.text.lower()
        signals = [
            "this item is no longer available",
            "this listing has ended",
            "currently unavailable",
        ]
        return any(s in body for s in signals)

    # -------------------------------------------------------------------------
    #  Request headers
    # -------------------------------------------------------------------------

    def _browser_headers(self) -> dict:
        """
        Return headers that mimic a real browser navigation.
        These supplement the random User-Agent set by RandomUserAgentMiddleware.
        """
        return {
            "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language":  "en-US,en;q=0.9",
            "Accept-Encoding":  "gzip, deflate, br",
            "Connection":       "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "TE":               "Trailers",
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
            conn  = psycopg2.connect(**DATABASE)
            cur   = conn.cursor()
            cur.execute(
                "SELECT url FROM products "
                "JOIN sources ON products.source_id = sources.source_id "
                "WHERE sources.name = 'amazon'"
            )
            urls = [row[0] for row in cur.fetchall()]
            cur.close()
            conn.close()
            logger.info("Loaded %d Amazon URLs from the database.", len(urls))
            return urls
        except Exception as e:
            logger.error("Failed to load URLs from DB: %s", e)
            return []