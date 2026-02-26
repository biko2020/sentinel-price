# =============================================================================
#  SentinelPrice · Scraper Dockerfile
# =============================================================================
#  Builds the Scrapy extraction engine container.
#
#  Build context: ./scrapers (set in docker-compose.yml)
#  Final image:   python:3.11-slim + Scrapy + Playwright (Chromium)
# =============================================================================


# -----------------------------------------------------------------------------
#  Base image
# -----------------------------------------------------------------------------

FROM python:3.11-slim

# Prevent .pyc files and enable unbuffered stdout/stderr (important for logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright


# -----------------------------------------------------------------------------
#  System dependencies
# -----------------------------------------------------------------------------
#  Required by:
#    · psycopg2-binary  — libpq
#    · Playwright       — Chromium runtime libs
#    · general          — gcc, curl, ca-certificates

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        curl \
        ca-certificates \
        # Playwright / Chromium runtime dependencies
        libnss3 \
        libatk-bridge2.0-0 \
        libdrm2 \
        libxkbcommon0 \
        libgbm1 \
        libasound2 \
        libxrandr2 \
        libxdamage1 \
        libxcomposite1 \
        libxfixes3 \
        fonts-liberation \
        libappindicator3-1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*


# -----------------------------------------------------------------------------
#  Working directory
# -----------------------------------------------------------------------------

WORKDIR /app


# -----------------------------------------------------------------------------
#  Python dependencies
# -----------------------------------------------------------------------------

COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# -----------------------------------------------------------------------------
#  Playwright — install Chromium browser binaries
# -----------------------------------------------------------------------------

RUN playwright install chromium


# -----------------------------------------------------------------------------
#  Application source
# -----------------------------------------------------------------------------

COPY . .


# -----------------------------------------------------------------------------
#  Non-root user (security best practice)
# -----------------------------------------------------------------------------

RUN useradd --create-home --shell /bin/bash sentinel \
    && chown -R sentinel:sentinel /app \
    && chown -R sentinel:sentinel /ms-playwright

USER sentinel


# -----------------------------------------------------------------------------
#  Default command
# -----------------------------------------------------------------------------
#  Overridden at runtime by docker-compose run scraper scrapy crawl <spider>

CMD ["scrapy", "list"]