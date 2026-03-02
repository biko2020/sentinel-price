# =============================================================================
#  SentinelPrice · Scrapy Settings
# =============================================================================
#  All values are loaded from environment variables (via .env) with sensible
#  fallbacks so the project runs out of the box without configuration.
#
#  Docs: https://docs.scrapy.org/en/latest/topics/settings.html
# =============================================================================

import os
from dotenv import load_dotenv

load_dotenv()


# -----------------------------------------------------------------------------
#  PROJECT IDENTITY
# -----------------------------------------------------------------------------

BOT_NAME            = "sentinel_spiders"
SPIDER_MODULES      = ["sentinel_spiders.spiders"]
NEWSPIDER_MODULE    = "sentinel_spiders.spiders"


# -----------------------------------------------------------------------------
#  POSTGRESQL DATABASE
# -----------------------------------------------------------------------------

DATABASE = {
    "dbname":   os.environ.get("POSTGRES_DB",       "sentinelprice"),
    "user":     os.environ.get("POSTGRES_USER",     "sentinel_user"),
    "password": os.environ.get("POSTGRES_PASSWORD", ""),
    "host":     os.environ.get("POSTGRES_HOST",     "db"),
    "port":     os.environ.get("POSTGRES_PORT",     "5432"),
}


# -----------------------------------------------------------------------------
#  CONCURRENCY & RATE LIMITING
# -----------------------------------------------------------------------------
#  These settings control crawl speed. Tune carefully per target site.
#  AutoThrottle dynamically adjusts delay based on server latency —
#  it is the recommended approach over a fixed DOWNLOAD_DELAY alone.

CONCURRENT_REQUESTS                 = int(os.environ.get("CONCURRENT_REQUESTS",            16))
CONCURRENT_REQUESTS_PER_DOMAIN      = int(os.environ.get("CONCURRENT_REQUESTS_PER_DOMAIN",  4))
CONCURRENT_REQUESTS_PER_IP          = 0      # 0 = disabled (use PER_DOMAIN instead)

DOWNLOAD_DELAY                      = float(os.environ.get("DOWNLOAD_DELAY", 1.5))
RANDOMIZE_DOWNLOAD_DELAY            = True   # Adds ±50% jitter to DOWNLOAD_DELAY

# AutoThrottle — adjusts delay dynamically based on server response time
AUTOTHROTTLE_ENABLED                = os.environ.get("AUTOTHROTTLE_ENABLED", "true").lower() == "true"
AUTOTHROTTLE_START_DELAY            = 1.0    # Initial delay before throttling kicks in
AUTOTHROTTLE_MAX_DELAY              = 30.0   # Never wait longer than this
AUTOTHROTTLE_TARGET_CONCURRENCY     = float(os.environ.get("AUTOTHROTTLE_TARGET_CONCURRENCY", 2.0))
AUTOTHROTTLE_DEBUG                  = False  # Set True to log throttle decisions


# -----------------------------------------------------------------------------
#  COMPLIANCE
# -----------------------------------------------------------------------------

ROBOTSTXT_OBEY  = os.environ.get("ROBOTSTXT_OBEY",   "true").lower() == "true"
COOKIES_ENABLED = os.environ.get("COOKIES_ENABLED",  "true").lower() == "true"


# -----------------------------------------------------------------------------
#  HTTP & RETRY
# -----------------------------------------------------------------------------

DEFAULT_REQUEST_HEADERS = {
    "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate, br",
}

DOWNLOAD_TIMEOUT    = 30                    # Seconds before a request times out
DOWNLOAD_MAXSIZE    = 10 * 1024 * 1024      # 10 MB max response size

# Retry failed requests (connection errors, 500s, 429s)
RETRY_ENABLED           = True
RETRY_TIMES             = 3
RETRY_HTTP_CODES        = [429, 500, 502, 503, 504, 522, 524, 408]
RETRY_BACKOFF_BASE      = 2.0   # Exponential backoff multiplier
RETRY_PRIORITY_ADJUST   = -1    # Retry requests at lower priority


# -----------------------------------------------------------------------------
#  MIDDLEWARES
# -----------------------------------------------------------------------------
#  Execution order: lower number = runs first for requests, last for responses.

DOWNLOADER_MIDDLEWARES = {
    # Built-in middlewares replaced by custom implementations
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware":    None,
    "scrapy.downloadermiddlewares.retry.RetryMiddleware":            None,

    # Custom middlewares (defined in middlewares.py)
    "sentinel_spiders.middlewares.RandomUserAgentMiddleware":        400,
    "sentinel_spiders.middlewares.ProxyMiddleware":                  410,
    "sentinel_spiders.middlewares.RateLimitRetryMiddleware":         420,
    "sentinel_spiders.middlewares.PlaywrightMiddleware":             430,
    "sentinel_spiders.middlewares.SentinelRetryMiddleware":          550,
}

SPIDER_MIDDLEWARES = {
    "sentinel_spiders.middlewares.SentinelSpiderMiddleware":         543,
}


# -----------------------------------------------------------------------------
#  ITEM PIPELINES
# -----------------------------------------------------------------------------
#  Execution order: lower number = runs first.

ITEM_PIPELINES = {
    "sentinel_spiders.pipelines.ValidationPipeline":        100,    # Drop invalid items
    "sentinel_spiders.pipelines.DeduplicationPipeline":     200,    # Skip already-seen SKUs
    "sentinel_spiders.pipelines.CleaningPipeline":          300,    # Normalize & enrich fields
    "sentinel_spiders.pipelines.PostgreSQLPipeline":        400,    # Persist to database
    "sentinel_spiders.pipelines.AlertPipeline":             500,    # Price change notifications
}


# -----------------------------------------------------------------------------
#  PROXY
# -----------------------------------------------------------------------------

PROXY_ENABLED   = os.environ.get("PROXY_ENABLED",  "false").lower() == "true"
PROXY_ENDPOINT  = os.environ.get("PROXY_ENDPOINT", "")
PROXY_USERNAME  = os.environ.get("PROXY_USERNAME", "")
PROXY_PASSWORD  = os.environ.get("PROXY_PASSWORD", "")
PROXY_API_KEY   = os.environ.get("PROXY_API_KEY",  "")


# -----------------------------------------------------------------------------
#  PLAYWRIGHT (JS RENDERING)
# -----------------------------------------------------------------------------
#  Used for product pages with JavaScript-rendered prices or lazy-loading.
#  Requires scrapy-playwright and browser binaries:
#    playwright install chromium
#
#  DOWNLOAD_HANDLERS registers Playwright as the HTTP/S handler.
#  Without this, scrapy-playwright crashes the middleware stack on startup.

DOWNLOAD_HANDLERS = {
    "http":  "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}

# Playwright browser contexts — proxy is configured here, not via request.meta.
# Playwright ignores Scrapy's meta["proxy"]; it must be set at the context level.
_proxy_enabled  = os.environ.get("PROXY_ENABLED",  "false").lower() == "true"
_proxy_endpoint = os.environ.get("PROXY_ENDPOINT", "")
_proxy_username = os.environ.get("PROXY_USERNAME", "")
_proxy_password = os.environ.get("PROXY_PASSWORD", "")

PLAYWRIGHT_CONTEXTS = {
    "default": {
        "proxy": {
            "server":   _proxy_endpoint,
            "username": _proxy_username,
            "password": _proxy_password,
        } if _proxy_enabled and _proxy_endpoint else None,
        "java_script_enabled": True,
        "ignore_https_errors": True,
    }
}

PLAYWRIGHT_ENABLED               = os.environ.get("PLAYWRIGHT_ENABLED",              "true").lower()  == "true"
PLAYWRIGHT_BROWSER_TYPE          = os.environ.get("PLAYWRIGHT_BROWSER_TYPE",          "chromium")
PLAYWRIGHT_AUTO_UPGRADE_ON_BLOCK = os.environ.get("PLAYWRIGHT_AUTO_UPGRADE_ON_BLOCK", "true").lower() == "true"

# These remain hardcoded — no value in making them env-driven
PLAYWRIGHT_DEFAULT_WAIT_FOR             = None      # Override per-request via meta["playwright_wait_for"]
PLAYWRIGHT_DEFAULT_TIMEOUT              = 30_000    # ms
PLAYWRIGHT_MAX_PAGES_PER_CONTEXT        = 4
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT   = 30_000    # ms
PLAYWRIGHT_LAUNCH_OPTIONS               = {
    "headless": True,
    "args": [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ],
}


# -----------------------------------------------------------------------------
#  FEEDS  (optional export — uncomment to enable)
# -----------------------------------------------------------------------------
#  Exports scraped data to a file in addition to PostgreSQL.
#  Useful for auditing and debugging individual crawl runs.

# FEEDS = {
#     "output/%(name)s_%(time)s.jsonl": {
#         "format":    "jsonlines",
#         "encoding":  "utf-8",
#         "overwrite": False,
#     },
# }


# -----------------------------------------------------------------------------
#  LOGGING
# -----------------------------------------------------------------------------

LOG_LEVEL   = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT  = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
LOG_FILE    = os.environ.get("LOG_FILE") or None   # None = stream to stdout only


# -----------------------------------------------------------------------------
#  TELEMETRY & INTERNALS
# -----------------------------------------------------------------------------

TELEMETRY_ENABLED                       = False
REQUEST_FINGERPRINTER_IMPLEMENTATION    = "2.7"
TWISTED_REACTOR                         = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"