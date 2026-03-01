# =============================================================================
#  SentinelPrice · Target Spider
# =============================================================================
#  Extracts product metadata and pricing data from Target product pages.
#
#  Yields:
#    · ProductItem       — static product metadata (name, brand, category …)
#    · PriceSnapshotItem — price observation (price, availability, rating …)
#
#  Usage:
#    scrapy crawl target_spider
#    scrapy crawl target_spider -a url="https://www.target.com/p/-/A-XXXXXXXX"
#    scrapy crawl target_spider -a tcin="XXXXXXXX"
#
#  Notes:
#    Target embeds product data in a __TGT_DATA__ / __PRELOADED_STATE__ JSON
#    blob. This spider extracts from that first, then falls back to CSS/JSON-LD.
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
#  Target product TCINs — add your products here
# -----------------------------------------------------------------------------

START_TCINS = [
    # "XXXXXXXX",   # Product name / description
]

TARGET_BASE_URL  = "https://www.target.com"
PRODUCT_URL_TPL  = "https://www.target.com/p/-/A-{tcin}"


# =============================================================================
#  TargetSpider
# =============================================================================

class TargetSpider(scrapy.Spider):
    name            = "target_spider"
    allowed_domains = ["target.com", "www.target.com"]
    custom_settings = {
        "DOWNLOAD_DELAY":                   2.0,
        "RANDOMIZE_DOWNLOAD_DELAY":         True,
        "CONCURRENT_REQUESTS_PER_DOMAIN":   2,
        "ROBOTSTXT_OBEY":                   False,
        "COOKIES_ENABLED":                  True,
    }

    # -------------------------------------------------------------------------
    #  Initialization
    # -------------------------------------------------------------------------

    def __init__(self, url=None, tcin=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = []

        if url:
            self.start_urls.append(self._normalize_url(url))
        elif tcin:
            self.start_urls.append(PRODUCT_URL_TPL.format(tcin=tcin.strip()))
        elif START_TCINS:
            self.start_urls = [
                PRODUCT_URL_TPL.format(tcin=t.strip()) for t in START_TCINS
            ]
        else:
            logger.warning(
                "No start URLs configured. "
                "Add TCINs to START_TCINS or pass -a url=... / -a tcin=..."
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
                meta     = {"handle_httpstatus_list": [404, 410, 503]},
                headers  = self._browser_headers(),
            )

    # -------------------------------------------------------------------------
    #  Main parser
    # -------------------------------------------------------------------------

    def parse_product(self, response):
        if self._is_blocked(response):
            logger.warning("Bot block detected on %s — skipping.", response.url)
            self.crawler.stats.inc_value("sentinel/blocked_responses")
            return

        if response.status in (404, 410):
            logger.info("Product not found [%s]: %s", response.status, response.url)
            return

        tcin = self._extract_tcin(response.url)
        if not tcin:
            logger.warning("Could not extract TCIN from URL: %s", response.url)
            return

        logger.debug("Parsing Target product: %s | TCIN=%s", response.url, tcin)

        preloaded = self._extract_preloaded_state(response)

        product  = self._parse_product_item(response, tcin, preloaded)
        snapshot = self._parse_price_snapshot(response, tcin, preloaded)

        if product:
            yield product
        if snapshot:
            yield snapshot

    # -------------------------------------------------------------------------
    #  __PRELOADED_STATE__ / JSON-LD extraction
    # -------------------------------------------------------------------------

    def _extract_preloaded_state(self, response) -> dict:
        """
        Target injects product data into window.__PRELOADED_STATE__ or
        a <script type="application/ld+json"> JSON-LD block.
        Returns the product node or an empty dict.
        """
        # Try __PRELOADED_STATE__
        raw = response.css("script:contains('__PRELOADED_STATE__')::text").get()
        if raw:
            try:
                match = re.search(r"__PRELOADED_STATE__\s*=\s*({.*?});?\s*</script>", raw, re.DOTALL)
                if not match:
                    match = re.search(r"__PRELOADED_STATE__\s*=\s*({.+})", raw, re.DOTALL)
                if match:
                    data = json.loads(match.group(1))
                    return (
                        data.get("product", {})
                        or data.get("pdp", {}).get("product", {})
                        or {}
                    )
            except (json.JSONDecodeError, AttributeError):
                pass

        # Try JSON-LD
        jsonld_raw = response.css('script[type="application/ld+json"]::text').getall()
        for blob in jsonld_raw:
            try:
                data = json.loads(blob)
                if isinstance(data, list):
                    data = next((d for d in data if d.get("@type") == "Product"), {})
                if data.get("@type") == "Product":
                    return data
            except (json.JSONDecodeError, AttributeError):
                pass

        return {}

    # -------------------------------------------------------------------------
    #  ProductItem extraction
    # -------------------------------------------------------------------------

    def _parse_product_item(self, response, tcin: str, ps: dict):
        loader = ItemLoader(item=ProductItem(), selector=response)

        loader.add_value("source", "target")
        loader.add_value("sku",    tcin)
        loader.add_value("url",    self._canonical_url(tcin))

        # Name
        name = (
            ps.get("name")
            or ps.get("title")
            or response.css(
                "h1[data-test='product-title']::text, "
                "h1.Heading__StyledHeading::text, "
                "[itemprop='name']::text"
            ).get()
        )
        loader.add_value("name", name)

        # Brand
        brand = (
            ps.get("brand", {}).get("name") if isinstance(ps.get("brand"), dict)
            else ps.get("brand")
            or response.css(
                "a[data-test='product-brand-link']::text, "
                "[itemprop='brand'] [itemprop='name']::text"
            ).get()
        )
        loader.add_value("brand", brand)

        # Category — from breadcrumb
        category = (
            response.css(
                "nav[data-test='breadcrumb'] li:last-child a::text, "
                ".Breadcrumbs__StyledBreadcrumbs li:last-child a::text"
            ).get()
        )
        loader.add_value("category", category)

        # Image
        image_url = (
            ps.get("image")
            or response.css(
                "[data-test='product-image'] img::attr(src), "
                ".slideDeckSlide img::attr(src)"
            ).get()
        )
        loader.add_value("image_url", image_url)

        item = loader.load_item()
        if not item.get("name"):
            logger.warning("Could not extract product name for TCIN=%s", tcin)
            return None
        return item

    # -------------------------------------------------------------------------
    #  PriceSnapshotItem extraction
    # -------------------------------------------------------------------------

    def _parse_price_snapshot(self, response, tcin: str, ps: dict):
        loader = ItemLoader(item=PriceSnapshotItem(), selector=response)

        loader.add_value("source", "target")
        loader.add_value("sku",    tcin)

        # Current price
        price_current = (
            self._deep_get(ps, "offers", "price")
            or self._deep_get(ps, "price", "current_retail")
            or response.css(
                "[data-test='product-price']::text, "
                "[data-test='current-price']::text, "
                "[itemprop='price']::attr(content)"
            ).get()
        )
        loader.add_value("price_current", str(price_current) if price_current else None)

        # Original price
        price_original = (
            self._deep_get(ps, "price", "reg_retail")
            or response.css(
                "[data-test='product-regular-price']::text, "
                "span.styles__StrikeThrough::text"
            ).get()
        )
        loader.add_value("price_original", str(price_original) if price_original else None)

        # Currency
        currency = self._deep_get(ps, "offers", "priceCurrency") or "USD"
        loader.add_value("currency", currency)

        # Availability
        avail_raw = (
            self._deep_get(ps, "offers", "availability")
            or response.css(
                "[data-test='fulfillment-cell'] button::text, "
                "[data-test='orderPickup-stockLevel']::text, "
                "[data-test='shippingMessage']::text"
            ).get()
            or "unknown"
        )
        loader.add_value("availability", str(avail_raw))

        # Rating
        rating = (
            ps.get("aggregateRating", {}).get("ratingValue")
            or response.css("[data-test='ratings'] span.RatingStars::attr(aria-label)::text").get()
        )
        loader.add_value("rating", str(rating) if rating else None)

        # Review count
        review_count = (
            ps.get("aggregateRating", {}).get("reviewCount")
            or response.css("[data-test='rating-count']::text").get()
        )
        loader.add_value("review_count", str(review_count) if review_count else None)

        item = loader.load_item()
        item["scraped_at"] = datetime.now(timezone.utc)
        return item

    # -------------------------------------------------------------------------
    #  Helpers
    # -------------------------------------------------------------------------

    def _extract_tcin(self, url: str):
        match = re.search(r"/A-(\d+)", url)
        return match.group(1) if match else None

    def _canonical_url(self, tcin: str) -> str:
        return f"{TARGET_BASE_URL}/p/-/A-{tcin}"

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def _deep_get(self, data, *keys):
        current = data
        for key in keys:
            if not isinstance(current, (dict, list)):
                return None
            try:
                current = current[key] if isinstance(current, list) else current.get(key)
            except (KeyError, IndexError, TypeError):
                return None
        return current

    def _is_blocked(self, response) -> bool:
        if response.status == 503:
            return True
        sample = response.text[:5000].lower()
        return any(s in sample for s in ["captcha", "access denied", "verify you are human"])

    def _browser_headers(self) -> dict:
        return {
            "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language":           "en-US,en;q=0.9",
            "Accept-Encoding":           "gzip, deflate, br",
            "Connection":                "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    def handle_error(self, failure):
        logger.error("Request failed: %s | %s", failure.request.url, failure.getErrorMessage())
        self.crawler.stats.inc_value("sentinel/request_errors")

    def _load_urls_from_db(self) -> list:
        import psycopg2
        from sentinel_spiders.settings import DATABASE
        try:
            conn = psycopg2.connect(**DATABASE)
            cur  = conn.cursor()
            cur.execute(
                "SELECT url FROM products JOIN sources ON products.source_id = sources.source_id "
                "WHERE sources.name = 'target'"
            )
            urls = [row[0] for row in cur.fetchall()]
            cur.close(); conn.close()
            return urls
        except Exception as e:
            logger.error("Failed to load URLs from DB: %s", e)
            return []