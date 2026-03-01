# =============================================================================
#  SentinelPrice · BestBuy Spider
# =============================================================================
#  Extracts product metadata and pricing data from Best Buy product pages.
#
#  Yields:
#    · ProductItem       — static product metadata (name, brand, category …)
#    · PriceSnapshotItem — price observation (price, availability, rating …)
#
#  Usage:
#    scrapy crawl bestbuy_spider
#    scrapy crawl bestbuy_spider -a url="https://www.bestbuy.com/site/-/XXXXXXX.p"
#    scrapy crawl bestbuy_spider -a sku="XXXXXXX"
#
#  Notes:
#    Best Buy embeds product data in multiple locations:
#      1. window.__INITIAL_STATE__ JSON blob (most complete)
#      2. JSON-LD Product schema
#      3. CSS selectors as final fallback
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
#  BestBuy SKUs — add your products here
# -----------------------------------------------------------------------------

START_SKUS = [
    # "XXXXXXX",   # Product name / description
]

BESTBUY_BASE_URL = "https://www.bestbuy.com"
PRODUCT_URL_TPL  = "https://www.bestbuy.com/site/-/{sku}.p"


# =============================================================================
#  BestBuySpider
# =============================================================================

class BestBuySpider(scrapy.Spider):
    name            = "bestbuy_spider"
    allowed_domains = ["bestbuy.com", "www.bestbuy.com"]
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

    def __init__(self, url=None, sku=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = []

        if url:
            self.start_urls.append(self._normalize_url(url))
        elif sku:
            self.start_urls.append(PRODUCT_URL_TPL.format(sku=sku.strip()))
        elif START_SKUS:
            self.start_urls = [
                PRODUCT_URL_TPL.format(sku=s.strip()) for s in START_SKUS
            ]
        else:
            logger.warning(
                "No start URLs configured. "
                "Add SKUs to START_SKUS or pass -a url=... / -a sku=..."
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

        sku = self._extract_sku(response.url)
        if not sku:
            logger.warning("Could not extract SKU from URL: %s", response.url)
            return

        logger.debug("Parsing BestBuy product: %s | SKU=%s", response.url, sku)

        initial_state = self._extract_initial_state(response)
        jsonld        = self._extract_jsonld(response)

        product  = self._parse_product_item(response, sku, initial_state, jsonld)
        snapshot = self._parse_price_snapshot(response, sku, initial_state, jsonld)

        if product:
            yield product
        if snapshot:
            yield snapshot

    # -------------------------------------------------------------------------
    #  __INITIAL_STATE__ and JSON-LD extraction
    # -------------------------------------------------------------------------

    def _extract_initial_state(self, response) -> dict:
        """
        Best Buy embeds product data in window.__INITIAL_STATE__.
        Navigate to the product node inside the state tree.
        """
        scripts = response.css("script::text").getall()
        for script in scripts:
            if "__INITIAL_STATE__" in script:
                try:
                    match = re.search(r"__INITIAL_STATE__\s*=\s*({.+?});?\s*(?:window\.|</script>)", script, re.DOTALL)
                    if match:
                        data = json.loads(match.group(1))
                        # Navigate to product details node
                        return (
                            data.get("productDetail", {}).get("summary", {})
                            or data.get("pdp", {}).get("product", {})
                            or {}
                        )
                except (json.JSONDecodeError, AttributeError):
                    pass
        return {}

    def _extract_jsonld(self, response) -> dict:
        for raw in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(raw)
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

    def _parse_product_item(self, response, sku: str, st: dict, jld: dict):
        loader = ItemLoader(item=ProductItem(), selector=response)

        loader.add_value("source", "bestbuy")
        loader.add_value("sku",    sku)
        loader.add_value("url",    self._canonical_url(sku))

        # Name
        name = (
            st.get("name")
            or jld.get("name")
            or response.css(
                "h1.heading-5.v-fw-regular::text, "
                "[itemprop='name']::text, "
                ".sku-title h1::text"
            ).get()
        )
        loader.add_value("name", name)

        # Brand
        brand = (
            st.get("brand")
            or (jld.get("brand", {}).get("name") if isinstance(jld.get("brand"), dict) else None)
            or response.css(
                "[itemprop='brand'] [itemprop='name']::text, "
                ".sku-brand::text"
            ).get()
        )
        loader.add_value("brand", brand)

        # Category
        category = response.css(
            "nav.breadcrumb-container li:last-child a::text, "
            ".breadcrumb li:last-child span::text"
        ).get()
        loader.add_value("category", category)

        # Image
        image_url = (
            jld.get("image")
            or response.css(
                "img.primary-image::attr(src), "
                "[data-testid='carousel-image-0'] img::attr(src)"
            ).get()
        )
        loader.add_value("image_url", image_url)

        item = loader.load_item()
        if not item.get("name"):
            logger.warning("Could not extract product name for SKU=%s", sku)
            return None
        return item

    # -------------------------------------------------------------------------
    #  PriceSnapshotItem extraction
    # -------------------------------------------------------------------------

    def _parse_price_snapshot(self, response, sku: str, st: dict, jld: dict):
        loader = ItemLoader(item=PriceSnapshotItem(), selector=response)

        loader.add_value("source", "bestbuy")
        loader.add_value("sku",    sku)

        # Current price
        price_current = (
            st.get("currentPrice")
            or st.get("regularPrice")
            or self._deep_get(jld, "offers", "price")
            or response.css(
                "[itemprop='price']::attr(content), "
                ".priceView-customer-price span[aria-hidden='true']::text, "
                ".priceView-purchase-price .priceView-customer-price span::text, "
                ".price-box .priceView-customer-price span::text"
            ).get()
        )
        loader.add_value("price_current", str(price_current) if price_current else None)

        # Original price
        price_original = (
            st.get("regularPrice")
            or response.css(
                ".pricing-price__regular-price .screen-reader-only::text, "
                "s.price-was::text, "
                "[data-testid='regular-price']::text"
            ).get()
        )
        loader.add_value("price_original", str(price_original) if price_original else None)

        # Currency
        currency = (
            self._deep_get(jld, "offers", "priceCurrency")
            or "USD"
        )
        loader.add_value("currency", currency)

        # Availability
        avail_raw = (
            st.get("availability")
            or self._deep_get(jld, "offers", "availability")
            or response.css(
                ".fulfillment-add-to-cart-button button::text, "
                ".add-to-cart-button::text, "
                "[data-button-state]::attr(data-button-state)"
            ).get()
            or "unknown"
        )
        loader.add_value("availability", str(avail_raw))

        # Rating
        rating = (
            st.get("customerReviewAverage")
            or self._deep_get(jld, "aggregateRating", "ratingValue")
            or response.css(
                "[itemprop='ratingValue']::attr(content), "
                ".ugc-ratings-reviews span.c-review-average::text"
            ).get()
        )
        loader.add_value("rating", str(rating) if rating else None)

        # Review count
        review_count = (
            st.get("customerReviewCount")
            or self._deep_get(jld, "aggregateRating", "reviewCount")
            or response.css(
                "[itemprop='reviewCount']::attr(content), "
                ".c-total-reviews::text"
            ).get()
        )
        loader.add_value("review_count", str(review_count) if review_count else None)

        item = loader.load_item()
        item["scraped_at"] = datetime.now(timezone.utc)
        return item

    # -------------------------------------------------------------------------
    #  Helpers
    # -------------------------------------------------------------------------

    def _extract_sku(self, url: str):
        match = re.search(r"/(\d+)\.p", url)
        return match.group(1) if match else None

    def _canonical_url(self, sku: str) -> str:
        return f"{BESTBUY_BASE_URL}/site/-/{sku}.p"

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def _deep_get(self, data, *keys):
        current = data
        for key in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    def _is_blocked(self, response) -> bool:
        if response.status == 503:
            return True
        sample = response.text[:5000].lower()
        return any(s in sample for s in ["captcha", "access denied", "verify you are human", "robot check"])

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
                "WHERE sources.name = 'bestbuy'"
            )
            urls = [row[0] for row in cur.fetchall()]
            cur.close(); conn.close()
            return urls
        except Exception as e:
            logger.error("Failed to load URLs from DB: %s", e)
            return []