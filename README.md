# SentinelPrice: High-Throughput E-Commerce Price Intelligence

> Enterprise-grade competitor price monitoring pipeline — powered by Scrapy, PostgreSQL, FastAPI, Apache Airflow, and Docker.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Business Use Cases](#business-use-cases)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Environment Configuration](#environment-configuration)
  - [Installation & Execution](#installation--execution)
  - [Running a Crawl](#running-a-crawl)
  - [Verifying the Output](#verifying-the-output)
  - [Stopping & Resetting the Stack](#stopping--resetting-the-stack)
- [Supported Retailers](#supported-retailers)
- [Spider Configuration](#spider-configuration)
- [REST API](#rest-api)
- [Database Schema](#database-schema)
- [Production Scheduling — Apache Airflow](#production-scheduling--apache-airflow)
- [Email & Slack Alerting](#email--slack-alerting)
- [Windows Automation — sentinel.bat](#-windows-automation--sentinelbat)
- [Logging & Debugging](#logging--debugging)
- [Compliance & Best Practices](#compliance--best-practices)
- [Known Limitations & Roadmap](#known-limitations--roadmap)
- [License](#license)
- [Contact](#contact)

---

## Overview

SentinelPrice is an enterprise-grade data extraction pipeline designed for high-speed competitor price monitoring. It automates extraction of structured product data from complex, JavaScript-heavy e-commerce platforms across five major retailers and organizes it into a high-performance, query-optimized PostgreSQL database — ready to power dashboards, dynamic pricing engines, REST APIs, or MAP compliance systems.

---

## Key Features

- **Multi-Retailer Coverage:** Five production spiders — Amazon, Walmart, Target, eBay, Best Buy.
- **High-Concurrency Extraction:** Built on Scrapy's asynchronous engine with autothrottle for maximum throughput.
- **Anti-Bot Resiliency:** Integrated rotating proxies (Zyte), randomized user-agents, and rate limiting.
- **Relational Storage:** Clean, normalized PostgreSQL schema with historical price tracking, views, and triggers.
- **REST API Layer:** FastAPI service exposing price data for dashboard and application integration.
- **Price Change Alerting:** Configurable Email and Slack notifications on price drops and increases.
- **Production Scheduling:** Apache Airflow DAGs for daily crawls, high-frequency monitoring, and weekly maintenance.
- **Containerized Environment:** Fully Dockerized with Docker Compose for plug-and-play deployment.

---

## Business Use Cases

- Competitor price monitoring and benchmarking
- MAP (Minimum Advertised Price) compliance tracking
- Dynamic pricing system data feeds
- E-commerce market intelligence dashboards
- Product availability monitoring
- Price history analytics and trend detection
- Automated deal and flash sale alerting

---

## Architecture

```
       ┌─────────────────────────────────────────────────────────────┐
       │                  TARGET E-COMMERCE SITES                    │
       │       Amazon · Walmart · Target · eBay · Best Buy           │
       └──────────────────────┬──────────────────────────────────────┘
                              │  HTTP Requests (via Zyte Proxy)
       ┌──────────────────────▼──────────────────────────────────────┐
       │               SCRAPY EXTRACTION ENGINE                      │
       │   • Async workers · Anti-bot middlewares · ItemLoaders      │
       └──────────────────────┬──────────────────────────────────────┘
                              │  Raw Items
       ┌──────────────────────▼──────────────────────────────────────┐
       │              DATA PROCESSING PIPELINE                       │
       │   Validation → Deduplication → Cleaning → PostgreSQL        │
       │                                         → Alert Detection   │
       └──────────┬──────────────────────────────┬───────────────────┘
                  │                              │
       ┌──────────▼──────────┐      ┌────────────▼──────────────────┐
       │  POSTGRESQL DB      │      │  NOTIFICATIONS                │
       │  products           │      │  • Email (HTML SMTP)          │
       │  pricing_history    │      │  • Slack (Block Kit webhook)  │
       │  latest_prices view │      └───────────────────────────────┘
       └──────────┬──────────┘
                  │
       ┌──────────▼──────────┐      ┌───────────────────────────────┐
       │  FASTAPI REST API   │      │  APACHE AIRFLOW               │
       │  /api/v1/prices     │      │  • Daily crawl DAG            │
       │  /api/v1/products   │      │  • High-frequency DAG         │
       │  /api/v1/stats      │      │  • Weekly maintenance DAG     │
       └──────────┬──────────┘      └───────────────────────────────┘
                  │
       ┌──────────▼──────────┐
       │  DASHBOARD / CLIENT │
       └─────────────────────┘
```

---

## Project Structure

```
sentinel-price/
├── scrapers/                          # Scrapy project
│   ├── sentinel_spiders/
│   │   ├── spiders/
│   │   │   ├── amazon_spider.py       # ASIN extraction, price cascade selectors
│   │   │   ├── walmart_spider.py      # __NEXT_DATA__ JSON extraction
│   │   │   ├── target_spider.py       # __PRELOADED_STATE__ + JSON-LD
│   │   │   ├── ebay_spider.py         # JSON-LD microdata, Buy It Now + auctions
│   │   │   └── bestbuy_spider.py      # __INITIAL_STATE__ + JSON-LD
│   │   ├── alerts/
│   │   │   ├── __init__.py
│   │   │   └── alert_manager.py       # AlertEvent, EmailChannel, SlackChannel
│   │   ├── items.py                   # ProductItem, PriceSnapshotItem
│   │   ├── pipelines.py               # Validation → Dedup → Cleaning → DB → Alerts
│   │   ├── settings.py                # Env-driven concurrency, proxy, Playwright config
│   │   └── middlewares.py             # RandomUserAgent, Proxy, RateLimit, Playwright, Retry
│   ├── scrapy.cfg
│   ├── Dockerfile
│   └── requirements.txt
├── database/
│   └── init.sql                       # Schema, views, triggers, source seeding
├── airflow/
│   ├── Dockerfile                     # Airflow + Docker CLI + psql client
│   └── dags/
│       ├── daily_crawl.py             # All 5 spiders in parallel — 08:00 UTC daily
│       ├── high_frequency_crawl.py    # Amazon + eBay — every 4 hours
│       └── weekly_maintenance.py      # Archive, vacuum, coverage report — Sundays
├── api/
│   ├── main.py                        # FastAPI app, CORS, lifespan, health check
│   ├── database.py                    # Async PostgreSQL via databases + asyncpg
│   ├── models.py                      # Pydantic v2 response schemas
│   ├── requirements.txt
│   ├── Dockerfile
│   └── routers/
│       ├── products.py                # GET /products, GET /products/{sku}
│       ├── prices.py                  # GET /prices/latest, /history/{sku}, /changes
│       └── stats.py                   # GET /stats
├── docker-compose.yml                 # db · scraper · api · airflow
├── .env                               # Active credentials (never commit)
├── .env.example                       # Documented template
├── .gitignore
├── test_alerts.py                     # Manual alert channel verification script
├── sentinel.bat                       # Windows one-click automation
└── README.md
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Scraping Engine | Python 3.11, Scrapy |
| JS Rendering | Scrapy-Playwright + Chromium (optional) |
| Proxy Layer | Zyte Smart Proxy Manager |
| Database | PostgreSQL 15 |
| REST API | FastAPI, Uvicorn, asyncpg |
| Alerting | SMTP Email, Slack Incoming Webhooks |
| Scheduling | Apache Airflow 2.9 (LocalExecutor) |
| Orchestration | Docker, Docker Compose |
| Anti-Bot | Rotating Proxies, User-Agent Spoofing, AutoThrottle |

---

## Getting Started

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) v20+
- [Docker Compose](https://docs.docker.com/compose/) v2+

> No local Python or PostgreSQL installation required.

**Recommended resources:** 8 GB RAM · 5 GB free disk (includes Airflow image)

---

### Environment Configuration

```bash
cp .env.example .env
```

Open `.env` and fill in your credentials. Minimum required fields:

```env
# Database
POSTGRES_DB=sentinelprice
POSTGRES_USER=sentinel_user
POSTGRES_PASSWORD=your_password

# Proxy (required for Amazon and Walmart)
PROXY_ENABLED=true
PROXY_ENDPOINT=http://api.zyte.com:8011
PROXY_USERNAME=your_zyte_api_key
PROXY_PASSWORD=

# Airflow
AIRFLOW_FERNET_KEY=   # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
AIRFLOW_SECRET_KEY=   # python -c "import secrets; print(secrets.token_hex(32))"
```

---

### Installation & Execution

**1. Clone the repository:**

```bash
git clone https://github.com/biko2020/sentinel-price
cd sentinel-price
```

**2. Build and start the full stack:**

```bash
docker-compose up --build
```

This starts four services: `db`, `scraper`, `api`, and `airflow`.

| Service | URL |
|---|---|
| REST API | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| Airflow UI | http://localhost:8080 |
| PostgreSQL | localhost:5432 |

---

### Running a Crawl

```bash
# All retailers (Windows)
.\sentinel.bat

# Single retailer
docker-compose run scraper scrapy crawl amazon_spider
docker-compose run scraper scrapy crawl walmart_spider
docker-compose run scraper scrapy crawl target_spider
docker-compose run scraper scrapy crawl ebay_spider
docker-compose run scraper scrapy crawl bestbuy_spider

# Pass a specific URL at runtime
docker-compose run scraper scrapy crawl amazon_spider -a url="https://www.amazon.com/dp/B0XXXXXXXX"
docker-compose run scraper scrapy crawl ebay_spider -a item_id="123456789012"
```

---

### Verifying the Output

```bash
docker-compose exec db psql -U sentinel_user -d sentinelprice
```

```sql
-- Latest prices across all retailers
SELECT source, product_name, price_current, currency, availability, scraped_at
FROM latest_prices
ORDER BY scraped_at DESC
LIMIT 20;

-- Price history for a specific SKU
SELECT price_current, availability, scraped_at
FROM pricing_history ph
JOIN products p ON p.product_id = ph.product_id
WHERE p.sku = 'B0CX23V2ZK'
ORDER BY scraped_at DESC;

-- Coverage by retailer
SELECT source, COUNT(*) AS products FROM latest_prices GROUP BY source;
```

Or call the API:

```bash
curl http://localhost:8000/api/v1/prices/latest?source=amazon&limit=10
curl http://localhost:8000/api/v1/stats
```

---

### Stopping & Resetting the Stack

```bash
# Stop (preserve data)
docker-compose down

# Full reset — wipes all data
docker-compose down -v
```

---

## Supported Retailers

| Retailer | Spider | Extraction Method | SKU Format |
|---|---|---|---|
| Amazon | `amazon_spider` | CSS selectors + price cascade | ASIN (`B0XXXXXXXX`) |
| Walmart | `walmart_spider` | `__NEXT_DATA__` JSON | Item ID |
| Target | `target_spider` | `__PRELOADED_STATE__` + JSON-LD | TCIN (`A-XXXXXXXX`) |
| eBay | `ebay_spider` | JSON-LD microdata | Item ID (numeric) |
| Best Buy | `bestbuy_spider` | `__INITIAL_STATE__` + JSON-LD | SKU (numeric) |

---

## Spider Configuration

Add target products to `START_*` lists at the top of each spider file:

```python
# scrapers/sentinel_spiders/spiders/amazon_spider.py
START_ASINS = [
    "B0CX23V2ZK",   # Apple AirPods Pro 2nd Gen
    "B09BR6WFZR",   # Kindle Paperwhite
]

# scrapers/sentinel_spiders/spiders/ebay_spider.py
START_ITEM_IDS = [
    "123456789012",
]
```

Or pass at runtime:

```bash
docker-compose run scraper scrapy crawl target_spider -a tcin="12345678"
docker-compose run scraper scrapy crawl bestbuy_spider -a sku="6525768"
```

---

## REST API

The FastAPI service runs at `http://localhost:8000`. Full interactive documentation at `/docs`.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Health check |
| GET | `/api/v1/products` | List all tracked products |
| GET | `/api/v1/products/{sku}` | Get product by SKU |
| GET | `/api/v1/prices/latest` | Latest price per product |
| GET | `/api/v1/prices/history/{sku}` | Full price history for a SKU |
| GET | `/api/v1/prices/changes` | Detected price changes |
| GET | `/api/v1/stats` | Crawl and coverage statistics |

All list endpoints support query filters:

```bash
# Filter by retailer and price range
curl "http://localhost:8000/api/v1/prices/latest?source=amazon&min_price=100&max_price=500"

# Price changes above 10%
curl "http://localhost:8000/api/v1/prices/changes?min_change=10"
```

---

## Database Schema

**`products`** — canonical product catalog:

| Column | Type | Description |
|---|---|---|
| `product_id` | SERIAL PK | Internal ID |
| `source_id` | INT FK | References `sources` table |
| `sku` | VARCHAR | Retailer-specific product ID |
| `name` | VARCHAR | Product name |
| `brand` | VARCHAR | Brand name |
| `category` | VARCHAR | Product category |
| `url` | TEXT | Canonical product URL |
| `image_url` | TEXT | Product image URL |
| `created_at` | TIMESTAMPTZ | First seen |
| `updated_at` | TIMESTAMPTZ | Last updated |

**`pricing_history`** — all price snapshots:

| Column | Type | Description |
|---|---|---|
| `id` | SERIAL PK | Record ID |
| `product_id` | INT FK | References `products` |
| `price_current` | NUMERIC(10,2) | Current price |
| `price_original` | NUMERIC(10,2) | Pre-discount price |
| `discount_pct` | NUMERIC(5,2) | Calculated discount % |
| `currency` | CHAR(3) | ISO 4217 code |
| `availability` | ENUM | `in_stock` · `out_of_stock` · `preorder` |
| `rating` | NUMERIC(3,2) | Star rating |
| `review_count` | INTEGER | Number of reviews |
| `scraped_at` | TIMESTAMPTZ | Snapshot timestamp |

**Views:** `latest_prices` (most recent snapshot per product) · `price_changes` (detected changes between snapshots)

---

## Production Scheduling — Apache Airflow

Three DAG templates are included for production scheduling.

### Airflow Dashboard

![Airflow DAG Overview](airflow/Dashboard/sentinelprice_airflow_dag_overview.png)

### Daily Crawl Pipeline

![Daily Crawl DAG](airflow/Dashboard/sentinelprice_daily_crawl_pipeline.png)

Runs every day at **08:00 UTC**. All five retailer spiders execute in parallel inside a TaskGroup.

```
start → db_health_check → [amazon | walmart | target | ebay | bestbuy] → verify_data → log_summary → end
```

### High-Frequency Crawl Pipeline

![High Frequency DAG](airflow/Dashboard/sentinelprice_high_frequency_pipeline.png)

Runs every **4 hours** — Amazon and eBay only (most volatile pricing).

### Weekly Maintenance Pipeline

![Weekly Maintenance DAG](airflow/Dashboard/sentinelprice_weekly_maintenance_pipeline.png)

Runs every **Sunday at 02:00 UTC**. Archives snapshots older than 90 days, runs `VACUUM ANALYZE`, and emits a per-retailer coverage report.

### Starting Airflow

```bash
docker-compose up --build airflow
```

Open `http://localhost:8080` — login with your configured `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD`.

Trigger a manual run:

```bash
docker-compose exec airflow airflow dags trigger sentinelprice_daily_crawl
```

---

## Email & Slack Alerting

`AlertPipeline` (stage 500) automatically detects price changes after every crawl and dispatches notifications to configured channels.

### Configuration

```env
# Thresholds (global)
ALERT_PRICE_DROP_THRESHOLD=5        # Alert on ≥5% price drop
ALERT_PRICE_INCREASE_THRESHOLD=10   # Alert on ≥10% price increase

# Slack
ALERT_SLACK_ENABLED=true
ALERT_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL

# Email
ALERT_EMAIL_ENABLED=true
ALERT_EMAIL_RECIPIENT=you@example.com
ALERT_EMAIL_SMTP_HOST=smtp.gmail.com
ALERT_EMAIL_SMTP_PORT=587
ALERT_EMAIL_SMTP_USER=you@gmail.com
ALERT_EMAIL_SMTP_PASSWORD=your_app_password
```

> Gmail users: use an [App Password](https://myaccount.google.com/apppasswords) — your regular password won't work over SMTP.
> Slack users: create a webhook at [api.slack.com/apps](https://api.slack.com/apps) → Incoming Webhooks.

### Testing alerts without running a crawl

```bash
# Test both channels
docker-compose run scraper python test_alerts.py

# Test Slack only with a price increase simulation
docker-compose run scraper python test_alerts.py --channel slack --type increase
```

---

## 🖥️ Windows Automation — `sentinel.bat`

A one-click entry point for Windows users automating the full pipeline.

### Commands

| Command | Action |
|---|---|
| `.\sentinel.bat` | Full run — starts stack, crawls all retailers, shows results |
| `.\sentinel.bat amazon` | Crawl Amazon only |
| `.\sentinel.bat walmart` | Crawl Walmart only |
| `.\sentinel.bat target` | Crawl Target only |
| `.\sentinel.bat ebay` | Crawl eBay only |
| `.\sentinel.bat bestbuy` | Crawl Best Buy only |
| `.\sentinel.bat query` | Show latest prices without crawling |
| `.\sentinel.bat reset` | Wipe all data (asks for confirmation) |

---

## Logging & Debugging

```bash
# Stream logs per service
docker-compose logs -f scraper
docker-compose logs -f api
docker-compose logs -f airflow

# Write spider logs to file
docker-compose run scraper scrapy crawl amazon_spider --logfile=/app/logs/amazon.log --loglevel=INFO

# Check alert pipeline activity
docker-compose logs scraper | grep -i alert
```

**Common issues:**

| Symptom | Cause | Fix |
|---|---|---|
| `407 Proxy Auth Required` | Invalid Zyte credentials | Rotate API key in `.env` |
| `503 / 429` responses | Rate limiting | Increase `DOWNLOAD_DELAY` in `.env` |
| `Connection refused` on DB | Container not ready | Wait and retry; check `docker-compose ps` |
| Empty DB after crawl | No matching selectors | Check `start_urls` and spider logs |
| Airflow `service "db" is not running` | Wrong compose context | DAGs use direct `psql://` — ensure DB container is up |
| Alert not firing | Threshold not crossed | Set `ALERT_PRICE_DROP_THRESHOLD=0` temporarily to test |

---

## Compliance & Best Practices

- **`robots.txt`** — Respected by default; toggle via `ROBOTSTXT_OBEY` in `.env`.
- **Rate limiting** — AutoThrottle and `DOWNLOAD_DELAY` prevent server overload.
- **Proxy rotation** — Zyte handles IP rotation and bot fingerprint masking.
- **Terms of Service** — Users are responsible for compliance with target site ToS.

---

## Known Limitations & Roadmap

**Current Limitations:**

- Playwright JS rendering is disabled when using Zyte (Zyte renders JS server-side).
- No built-in web dashboard — use the REST API or connect a BI tool directly to PostgreSQL.

**Planned:**
- [ ] Grafana dashboard template for price analytics

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## Contact

**AIT OUFKIR BRAHIM**  
Big Data Engineer

- 📧 [aitoufkirbrahimab@gmail.com](mailto:aitoufkirbrahimab@gmail.com)
- 💻 [github.com/biko2020](https://github.com/biko2020)
- 💼 [linkedin.com/in/brahim-aitoufkir](https://linkedin.com/in/brahim-aitoufkir)

Open to freelance data engineering projects, Big Data consulting, BI dashboard development, technical architecture reviews, cloud migration strategies, and team training and mentorship.