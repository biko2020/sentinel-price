# =============================================================================
#  SentinelPrice · Scrapy Middlewares
# =============================================================================
#  Anti-bot and resilience middlewares for high-volume e-commerce crawling.
#
#  Middlewares (in execution order per settings.py):
#    400 · RandomUserAgentMiddleware   — Rotates browser fingerprints
#    410 · ProxyMiddleware             — Injects rotating proxies
#    420 · RateLimitRetryMiddleware    — Handles 429 / Retry-After headers
#    430 · PlaywrightMiddleware        — JS rendering for bot-blocked pages
#    543 · SentinelSpiderMiddleware    — Spider-level logging & error handling
#    550 · SentinelRetryMiddleware     — Extended retry with exponential backoff
#
#  Docs: https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
# =============================================================================

import base64
import logging
import random
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from scrapy import signals
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.exceptions import IgnoreRequest, NotConfigured
from scrapy.utils.response import response_status_message


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
#  USER-AGENT POOL
# -----------------------------------------------------------------------------
#  A curated list of realistic, modern browser user-agents.
#  Covers Chrome, Firefox, Safari, and Edge across Windows and macOS.
#  Updated periodically — add new agents here to keep the pool fresh.

USER_AGENTS = [
    # Chrome — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome — macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Firefox — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox — macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari — macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    # Edge — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]


# =============================================================================
#  1. RandomUserAgentMiddleware
# =============================================================================

class RandomUserAgentMiddleware:
    """
    Replaces the User-Agent header on every request with a randomly selected
    agent from the USER_AGENTS pool.

    Also injects supporting headers that complete a realistic browser
    fingerprint: Sec-CH-UA, Sec-Fetch-*, and Accept-Language.
    Without these, a rotating User-Agent alone is easy to detect.
    """

    def __init__(self, user_agents: list, fallback_ua: str):
        self.user_agents = user_agents
        self.fallback_ua = fallback_ua

    @classmethod
    def from_crawler(cls, crawler):
        custom_agents = crawler.settings.getlist("USER_AGENTS", [])
        agents = custom_agents if custom_agents else USER_AGENTS
        fallback = agents[0]
        mw = cls(agents, fallback)
        return mw

    def process_request(self, request, spider):
        ua = random.choice(self.user_agents)
        request.headers["User-Agent"] = ua

        # Inject Sec-CH-UA hint that matches Chrome agents
        if "Chrome/" in ua:
            try:
                version = ua.split("Chrome/")[1].split(".")[0]
                request.headers["Sec-CH-UA"] = (
                    f'"Chromium";v="{version}", "Google Chrome";v="{version}", "Not-A.Brand";v="99"'
                )
                request.headers["Sec-CH-UA-Mobile"]   = "?0"
                request.headers["Sec-CH-UA-Platform"]  = '"Windows"' if "Windows" in ua else '"macOS"'
            except (IndexError, ValueError):
                pass

        # Fetch metadata headers — expected by modern servers
        request.headers.setdefault("Sec-Fetch-Dest",   "document")
        request.headers.setdefault("Sec-Fetch-Mode",   "navigate")
        request.headers.setdefault("Sec-Fetch-Site",   "none")
        request.headers.setdefault("Sec-Fetch-User",   "?1")
        request.headers.setdefault("Accept-Language",  "en-US,en;q=0.9")
        request.headers.setdefault("DNT",              "1")


# =============================================================================
#  2. ProxyMiddleware
# =============================================================================

class ProxyMiddleware:
    """
    Injects a rotating proxy into every outgoing request.
    Disabled entirely when PROXY_ENABLED=false (default).

    Supports authenticated proxies via PROXY_USERNAME / PROXY_PASSWORD.
    On proxy failure (407, 503), logs the error and lets SentinelRetryMiddleware
    handle the retry — without a proxy on the next attempt if the pool is empty.
    """

    PROXY_FAILURE_CODES = {407, 503}

    def __init__(self, settings):
        self.enabled    = settings.getbool("PROXY_ENABLED", False)
        self.endpoint   = settings.get("PROXY_ENDPOINT", "")
        self.username   = settings.get("PROXY_USERNAME", "")
        self.password   = settings.get("PROXY_PASSWORD", "")

        if self.enabled and not self.endpoint:
            raise NotConfigured(
                "PROXY_ENABLED is True but PROXY_ENDPOINT is not set. "
                "Check your .env configuration."
            )

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def process_request(self, request, spider):
        if not self.enabled:
            return

        # Skip proxy injection for Playwright requests — Playwright manages
        # its own proxy via PLAYWRIGHT_CONTEXTS in settings.py.
        # Injecting meta["proxy"] into a Playwright request causes Error.
        if request.meta.get("playwright"):
            return

        request.meta["proxy"] = self.endpoint

        if self.username:
            # Zyte and some providers use API key as username with empty password
            credentials = f"{self.username}:{self.password}"
            encoded     = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
            request.headers["Proxy-Authorization"] = f"Basic {encoded}"

        logger.debug(
            "Routing %s via proxy %s",
            request.url,
            urlparse(self.endpoint).hostname,
        )

    def process_response(self, request, response, spider):
        if response.status in self.PROXY_FAILURE_CODES:
            logger.warning(
                "Proxy failure [%s] for %s — will retry.",
                response.status,
                request.url,
            )
            request.meta["proxy_failed"] = True
        return response

    def process_exception(self, request, exception, spider):
        logger.error(
            "Proxy exception for %s: %s",
            request.url,
            type(exception).__name__,
        )
        request.meta["proxy_failed"] = True


# =============================================================================
#  3. RateLimitRetryMiddleware
# =============================================================================

class RateLimitRetryMiddleware:
    """
    Handles HTTP 429 Too Many Requests responses intelligently.

    Behaviour:
      - Reads the Retry-After header (seconds or HTTP date) if present.
      - Falls back to exponential backoff if no header is provided.
      - Sleeps the worker (blocking) for the computed wait period.
      - Re-schedules the request after the wait.

    ⚠  Blocking sleep is intentional here — a 429 means the target server
       wants us to stop. Sleeping prevents hammering the proxy pool.
    """

    MAX_WAIT_SECONDS = 120   # Never sleep longer than this
    BASE_BACKOFF     = 5     # Starting backoff in seconds

    def __init__(self, settings):
        self.max_retry_times = settings.getint("RETRY_TIMES", 3)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def process_response(self, request, response, spider):
        if response.status != 429:
            return response

        retry_count = request.meta.get("retry_times", 0)

        if retry_count >= self.max_retry_times:
            logger.error(
                "Giving up on %s after %d retries (429).",
                request.url, retry_count,
            )
            raise IgnoreRequest(f"Max retries exceeded for {request.url}")

        wait = self._compute_wait(response, retry_count)
        logger.warning(
            "429 Too Many Requests for %s — waiting %.1fs before retry %d/%d.",
            request.url, wait, retry_count + 1, self.max_retry_times,
        )
        time.sleep(wait)

        new_request = request.copy()
        new_request.meta["retry_times"] = retry_count + 1
        new_request.dont_filter = True
        return new_request

    def _compute_wait(self, response, retry_count: int) -> float:
        """Return wait time in seconds from Retry-After header or backoff."""
        retry_after = response.headers.get("Retry-After", b"").decode("utf-8").strip()

        if retry_after:
            # Numeric seconds
            try:
                return min(float(retry_after), self.MAX_WAIT_SECONDS)
            except ValueError:
                pass
            # HTTP date format: "Wed, 21 Oct 2025 07:28:00 GMT"
            try:
                retry_dt = datetime.strptime(retry_after, "%a, %d %b %Y %H:%M:%S %Z")
                wait = (retry_dt - datetime.utcnow()).total_seconds()
                return min(max(wait, 0), self.MAX_WAIT_SECONDS)
            except ValueError:
                pass

        # Exponential backoff with jitter
        backoff = self.BASE_BACKOFF * (2 ** retry_count)
        jitter  = random.uniform(0, backoff * 0.2)
        return min(backoff + jitter, self.MAX_WAIT_SECONDS)



# =============================================================================
#  4. PlaywrightMiddleware
# =============================================================================

class PlaywrightMiddleware:
    """
    Upgrades eligible requests to full browser rendering via Playwright.

    How it works:
      - Runs on every response AFTER the standard HTTP download.
      - If the response is a bot-block / CAPTCHA (detected via signals), or if
        the spider explicitly requests Playwright via meta["use_playwright"],
        the request is re-issued through scrapy-playwright's Chromium engine.
      - Playwright waits for a configurable CSS selector before returning,
        ensuring JS-rendered content (prices, availability) is fully loaded.
      - Falls back silently to the original response if Playwright fails.

    Opt-in per request (spider side):
        yield scrapy.Request(
            url,
            meta={
                "use_playwright":       True,
                "playwright_wait_for":  "#productTitle",   # optional
                "playwright_timeout":   20000,             # optional (ms)
            }
        )

    Auto-upgrade on bot-block (no spider changes needed):
        Any response containing CAPTCHA signals is automatically retried
        via Playwright on the first block — transparent to the spider.

    Settings (settings.py):
        PLAYWRIGHT_ENABLED               = True
        PLAYWRIGHT_DEFAULT_WAIT_FOR      = None     # Global CSS selector to wait for
        PLAYWRIGHT_DEFAULT_TIMEOUT       = 30000    # ms
        PLAYWRIGHT_AUTO_UPGRADE_ON_BLOCK = True     # Auto-retry blocked responses
    """

    BLOCK_SIGNALS = [
        "captcha",
        "robot check",
        "enter the characters you see below",
        "verify you are human",
        "access denied",
        "automated access",
        "security check",
        "please verify",
    ]

    def __init__(self, settings):
        self.enabled         = settings.getbool("PLAYWRIGHT_ENABLED", True)
        self.auto_upgrade    = settings.getbool("PLAYWRIGHT_AUTO_UPGRADE_ON_BLOCK", True)
        self.default_wait    = settings.get("PLAYWRIGHT_DEFAULT_WAIT_FOR", None)
        self.default_timeout = settings.getint("PLAYWRIGHT_DEFAULT_TIMEOUT", 30_000)

        if not self.enabled:
            raise NotConfigured("PlaywrightMiddleware is disabled (PLAYWRIGHT_ENABLED=False).")

        try:
            from scrapy_playwright.page import PageMethod  # noqa: F401
        except ImportError:
            raise NotConfigured(
                "scrapy-playwright is not installed. "
                "Run: pip install scrapy-playwright && playwright install chromium"
            )

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def process_request(self, request, spider):
        """
        If the request has use_playwright=True, enrich it with
        Playwright page methods (wait_for_selector, scroll, etc.).
        """
        if not request.meta.get("use_playwright"):
            return

        from scrapy_playwright.page import PageMethod

        wait_selector = (
            request.meta.get("playwright_wait_for")
            or self.default_wait
        )
        timeout = request.meta.get("playwright_timeout", self.default_timeout)

        page_methods = [
            PageMethod("set_viewport_size", {"width": 1366, "height": 768}),
        ]

        if wait_selector:
            page_methods.append(
                PageMethod(
                    "wait_for_selector",
                    wait_selector,
                    timeout=timeout,
                    state="visible",
                )
            )
        else:
            page_methods.append(
                PageMethod("wait_for_load_state", "networkidle", timeout=timeout)
            )

        # Scroll to trigger lazy-loaded price/availability elements
        page_methods.append(
            PageMethod("evaluate", "window.scrollBy(0, window.innerHeight * 0.6)")
        )

        request.meta["playwright"]               = True
        request.meta["playwright_page_methods"]  = page_methods
        request.meta["playwright_include_page"]  = False

        logger.debug("Playwright: upgrading to browser render: %s", request.url)

    def process_response(self, request, response, spider):
        """
        After a standard HTTP response, check for bot-block.
        If detected and auto-upgrade is on, re-issue via Playwright.
        """
        if request.meta.get("playwright"):
            return response

        if self.auto_upgrade and self._is_blocked(response):
            logger.warning(
                "Bot block on %s — auto-upgrading to Playwright render.",
                response.url,
            )
            spider.crawler.stats.inc_value("sentinel/playwright_upgrades")

            from scrapy_playwright.page import PageMethod

            new_request = request.copy()
            new_request.meta.update({
                "use_playwright":           True,
                "playwright":               True,
                "playwright_include_page":  False,
                "playwright_page_methods":  [
                    PageMethod("set_viewport_size", {"width": 1366, "height": 768}),
                    PageMethod("wait_for_load_state", "networkidle", timeout=self.default_timeout),
                    PageMethod("evaluate", "window.scrollBy(0, window.innerHeight * 0.6)"),
                ],
            })
            new_request.dont_filter = True
            return new_request

        return response

    def process_exception(self, request, exception, spider):
        """
        On Playwright timeout or browser crash, fall back to HTTP request.
        """
        playwright_exceptions = (
            "TimeoutError",
            "TargetClosedError",
            "BrowserContextClosedError",
        )
        exc_name = type(exception).__name__
        if exc_name in playwright_exceptions:
            logger.warning(
                "Playwright [%s] on %s — falling back to HTTP.",
                exc_name, request.url,
            )
            spider.crawler.stats.inc_value("sentinel/playwright_fallbacks")
            fallback = request.copy()
            fallback.meta["use_playwright"] = False
            fallback.meta["playwright"]     = False
            fallback.dont_filter            = True
            return fallback

    def _is_blocked(self, response) -> bool:
        if response.status == 503:
            return True
        sample = response.text[:5000].lower()
        return any(signal in sample for signal in self.BLOCK_SIGNALS)


# =============================================================================
#  5. SentinelRetryMiddleware
# =============================================================================

class SentinelRetryMiddleware(RetryMiddleware):
    """
    Extends Scrapy's built-in RetryMiddleware with:
      - Exponential backoff between retries
      - Per-domain retry counters for observability
      - Detailed structured logging on each retry and final failure

    Inherits RETRY_HTTP_CODES and RETRY_TIMES from settings.py.
    """

    def __init__(self, settings):
        super().__init__(settings)
        self.backoff_base   = settings.getfloat("RETRY_BACKOFF_BASE", 2.0)
        self._domain_stats  = {}   # { domain: { "retries": int, "failures": int } }

    @classmethod
    def from_crawler(cls, crawler):
        mw = cls(crawler.settings)
        crawler.signals.connect(mw.spider_closed, signal=signals.spider_closed)
        return mw

    def process_response(self, request, response, spider):
        if request.meta.get("dont_retry", False):
            return response

        if response.status in self.retry_http_codes:
            reason = response_status_message(response.status)
            return self._retry(request, reason, spider) or response

        return response

    def process_exception(self, request, exception, spider):
        if isinstance(exception, self.EXCEPTIONS_TO_RETRY) and not request.meta.get("dont_retry", False):
            return self._retry(request, exception, spider)

    def _retry(self, request, reason, spider):
        retry_times = request.meta.get("retry_times", 0) + 1
        domain      = urlparse(request.url).netloc

        # Update domain stats
        stats = self._domain_stats.setdefault(domain, {"retries": 0, "failures": 0})
        stats["retries"] += 1

        if retry_times <= self.max_retry_times:
            wait = self.backoff_base ** retry_times + random.uniform(0, 1)
            logger.warning(
                "Retry %d/%d for %s (reason: %s) — backing off %.1fs.",
                retry_times, self.max_retry_times,
                request.url, reason, wait,
            )
            time.sleep(wait)

            new_request = request.copy()
            new_request.meta["retry_times"]  = retry_times
            new_request.dont_filter         = True
            return new_request

        # Max retries exceeded
        stats["failures"] += 1
        logger.error(
            "Dropped %s after %d retries. Reason: %s",
            request.url, self.max_retry_times, reason,
        )
        self.crawler.stats.inc_value("sentinel/dropped_requests")
        return None

    def spider_closed(self, spider):
        """Log per-domain retry summary when the spider finishes."""
        if self._domain_stats:
            logger.info("── Retry Summary ──────────────────────────────────")
            for domain, stats in sorted(self._domain_stats.items()):
                logger.info(
                    "  %-40s retries=%-4d failures=%d",
                    domain, stats["retries"], stats["failures"],
                )
            logger.info("────────────────────────────────────────────────────")


# =============================================================================
#  6. SentinelSpiderMiddleware
# =============================================================================

class SentinelSpiderMiddleware:
    """
    Spider-level middleware for structured logging and error observability.

    Responsibilities:
      - Logs each spider open/close event with item and request counts.
      - Catches and logs unhandled exceptions from spider callbacks.
      - Tracks dropped items and increments Scrapy stats counters.
    """

    @classmethod
    def from_crawler(cls, crawler):
        mw = cls()
        crawler.signals.connect(mw.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(mw.spider_closed, signal=signals.spider_closed)
        return mw

    def spider_opened(self, spider):
        logger.info(
            "Spider opened: %s | start_urls=%d",
            spider.name,
            len(getattr(spider, "start_urls", [])),
        )

    def spider_closed(self, spider):
        stats = spider.crawler.stats.get_stats()
        logger.info(
            "Spider closed: %s | items_scraped=%s | requests_made=%s | "
            "items_dropped=%s | finish_reason=%s",
            spider.name,
            stats.get("item_scraped_count",  0),
            stats.get("downloader/request_count", 0),
            stats.get("item_dropped_count",  0),
            stats.get("finish_reason",       "unknown"),
        )

    def process_spider_output(self, response, result, spider):
        for item in result:
            yield item

    def process_spider_exception(self, response, exception, spider):
        logger.error(
            "Spider exception on %s: [%s] %s",
            response.url,
            type(exception).__name__,
            str(exception),
            exc_info=True,
        )
        return []   # Return empty list to suppress the exception and continue crawling