# =============================================================================
#  SentinelPrice · eBay Spider
# =============================================================================
#  Extracts product metadata and pricing data from eBay listing pages.
#
#  Yields:
#    · ProductItem       — static product metadata (name, brand, category …)
#    · PriceSnapshotItem — price observation (price, availability, rating …)
#
#  Usage:
#    scrapy crawl ebay_spider
#    scrapy crawl ebay_spider -a url="https://www.ebay.com/itm/XXXXXXXXXXXX"
#    scrapy crawl ebay_spider -a item_id="XXXXXXXXXXXX"
#
#  Notes:
#    eBay exposes rich microdata (itemprop) and JSON-LD on listing pages,
#    making extraction more reliable than pure CSS selectors.
#    Both Buy It Now and auction listings are supported.
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
#  eBay item IDs — add your listings here
# -----------------------------------------------------------------------------

START_ITEM_IDS = [
    # "XXXXXXXXXXXX",   # Listing title / description
]

EBAY_BASE_URL   = "https://www.ebay.com"
PRODUCT_URL_TPL = "https://www.ebay.com/itm/{item_id}"


# =============================================================================
#  EbaySpider
# =============================================================================

class EbaySpider(scrapy.Spider):
    name            = "ebay_spider"
    allowed_domains = ["ebay.com", "www.ebay.com"]
    custom_settings = {
        "DOWNLOAD_DELAY":                   1.5,
        "RANDOMIZE_DOWNLOAD_DELAY":         True,
        "CONCURRENT_REQUESTS_PER_DOMAIN":   4,   # eBay is more permissive
        "ROBOTSTXT_OBEY":                   False,
        "COOKIES_ENABLED":                  True,
    }

    # -------------------------------------------------------------------------
    #  Initialization
    # -------------------------------------------------------------------------

    def __init__(self, url=None, item_id=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = []

        if url:
            self.start_urls.append(self._normalize_url(url))
        elif item_id:
            self.start_urls.append(PRODUCT_URL_TPL.format(item_id=item_id.strip()))
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
            logger.info("Listing not found [%s]: %s", response.status, response.url)
            return

        item_id = self._extract_item_id(response.url)
        if not item_id:
            logger.warning("Could not extract item ID from URL: %s", response.url)
            return

        logger.debug("Parsing eBay listing: %s | item_id=%s", response.url, item_id)

        jsonld = self._extract_jsonld(response)

        product  = self._parse_product_item(response, item_id, jsonld)
        snapshot = self._parse_price_snapshot(response, item_id, jsonld)

        if product:
            yield product
        if snapshot:
            yield snapshot

    # -------------------------------------------------------------------------
    #  JSON-LD extraction
    # -------------------------------------------------------------------------

    def _extract_jsonld(self, response) -> dict:
        """eBay includes a Product JSON-LD block on all listing pages."""
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

    def _parse_product_item(self, response, item_id: str, jld: dict):
        loader = ItemLoader(item=ProductItem(), selector=response)

        loader.add_value("source", "ebay")
        loader.add_value("sku",    item_id)
        loader.add_value("url",    self._canonical_url(item_id))

        # Name — JSON-LD is most reliable on eBay
        name = (
            jld.get("name")
            or response.css(
                "h1[itemprop='name']::text, "
                "h1.x-item-title__mainTitle span::text, "
                "#itemTitle::text"
            ).get()
        )
        loader.add_value("name", name)

        # Brand
        brand = (
            jld.get("brand", {}).get("name") if isinstance(jld.get("brand"), dict)
            else jld.get("brand")
            or response.css(
                "span[itemprop='brand']::text, "
                "[data-testid='x-item-brand'] span::text"
            ).get()
        )
        loader.add_value("brand", brand)

        # Category
        category = response.css(
            "nav[aria-label='Breadcrumb'] li:last-child a::text, "
            ".seo-breadcrumb-text:last-child::text"
        ).get()
        loader.add_value("category", category)

        # Image
        image_url = (
            jld.get("image")
            or response.css(
                "#icImg::attr(src), "
                ".ux-image-carousel-item img::attr(src), "
                "[data-testid='ux-image-magnify'] img::attr(src)"
            ).get()
        )
        loader.add_value("image_url", image_url)

        item = loader.load_item()
        if not item.get("name"):
            logger.warning("Could not extract listing name for item_id=%s", item_id)
            return None
        return item

    # -------------------------------------------------------------------------
    #  PriceSnapshotItem extraction
    # -------------------------------------------------------------------------

    def _parse_price_snapshot(self, response, item_id: str, jld: dict):
        loader = ItemLoader(item=PriceSnapshotItem(), selector=response)

        loader.add_value("source", "ebay")
        loader.add_value("sku",    item_id)

        # Current price — covers Buy It Now and auction current bid
        price_current = (
            self._deep_get(jld, "offers", "price")
            or response.css(
                "[itemprop='price']::attr(content), "
                ".x-price-primary .ux-textspans::text, "
                "#prcIsum::attr(content), "
                "#prcIsum_bidPrice::text, "
                ".display-price::text"
            ).get()
        )
        loader.add_value("price_current", str(price_current) if price_current else None)

        # Original / was price (strike-through)
        price_original = response.css(
            ".x-additional-info .ux-textspans--STRIKETHROUGH::text, "
            "#orgPrc::text, "
            ".vi-originalPrice .ux-textspans::text"
        ).get()
        loader.add_value("price_original", price_original)

        # Currency
        currency = (
            self._deep_get(jld, "offers", "priceCurrency")
            or response.css("[itemprop='priceCurrency']::attr(content)").get()
            or "USD"
        )
        loader.add_value("currency", currency)

        # Availability — eBay JSON-LD uses schema.org URLs
        avail_raw = (
            self._deep_get(jld, "offers", "availability")
            or response.css(
                "[itemprop='availability']::attr(href), "
                ".d-quantity__availability::text, "
                "#qtySubTxt span::text"
            ).get()
            or "unknown"
        )
        # Normalize schema.org availability URLs
        avail_map = {
            "InStock":    "in_stock",
            "OutOfStock": "out_of_stock",
            "SoldOut":    "out_of_stock",
            "PreOrder":   "preorder",
        }
        avail_str = str(avail_raw).split("/")[-1]  # Extract last segment of schema.org URL
        avail_normalized = avail_map.get(avail_str, avail_str)
        loader.add_value("availability", avail_normalized)

        # Rating
        rating = (
            self._deep_get(jld, "aggregateRating", "ratingValue")
            or response.css(
                "[itemprop='ratingValue']::attr(content), "
                ".ebay-review-start-rating::text"
            ).get()
        )
        loader.add_value("rating", str(rating) if rating else None)

        # Review count
        review_count = (
            self._deep_get(jld, "aggregateRating", "reviewCount")
            or response.css(
                "[itemprop='reviewCount']::attr(content), "
                ".ebay-review-item-reviews span::text"
            ).get()
        )
        loader.add_value("review_count", str(review_count) if review_count else None)

        item = loader.load_item()
        item["scraped_at"] = datetime.now(timezone.utc)
        return item

    # -------------------------------------------------------------------------
    #  Helpers
    # -------------------------------------------------------------------------

    def _extract_item_id(self, url: str):
        match = re.search(r"/itm/(?:[^/]+/)?(\d+)", url)
        return match.group(1) if match else None

    def _canonical_url(self, item_id: str) -> str:
        return f"{EBAY_BASE_URL}/itm/{item_id}"

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
                "WHERE sources.name = 'ebay'"
            )
            urls = [row[0] for row in cur.fetchall()]
            cur.close(); conn.close()
            return urls
        except Exception as e:
            logger.error("Failed to load URLs from DB: %s", e)
            return []