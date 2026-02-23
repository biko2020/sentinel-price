# SentinelPrice: High-Throughput E-Commerce Intelligence

> Enterprise-grade competitor price monitoring pipeline — powered by Scrapy, PostgreSQL, and Docker.

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
- [Spider Configuration](#spider-configuration)
- [Database Schema](#database-schema)
- [Scheduling Crawls](#scheduling-crawls)
- [Logging & Debugging](#logging--debugging)
- [Compliance & Best Practices](#compliance--best-practices)
- [Known Limitations & Roadmap](#known-limitations--roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)

---

## Overview

SentinelPrice is an enterprise-grade data extraction pipeline designed for high-speed competitor price monitoring. It automates extraction of structured product data from complex, JavaScript-heavy e-commerce platforms and organizes it into a high-performance, query-optimized PostgreSQL database — ready to power dashboards, dynamic pricing engines, or MAP compliance systems.

---

## Key Features

- **High-Concurrency Extraction:** Built on Scrapy's asynchronous engine for maximum throughput.
- **Anti-Bot Resiliency:** Integrated rotating proxies and user-agent spoofing to bypass rate limits.
- **Relational Storage:** Clean, normalized PostgreSQL schema ready for web-app integration.
- **Containerized Environment:** Fully Dockerized for production-ready, plug-and-play deployment.
- **Historical Tracking:** Timestamped pricing records enable price history analytics out of the box.
- **Configurable Compliance:** `robots.txt` adherence and crawl rate limits are fully configurable.

---

## Business Use Cases

- Competitor price monitoring
- MAP (Minimum Advertised Price) compliance tracking
- Dynamic pricing systems
- E-commerce market intelligence
- Product availability monitoring
- Price history analytics

---

## Architecture

```
       ┌────────────────────────────────────────────────────────┐
       │                 TARGET E-COMMERCE SITES                │
       │          (Amazon, Walmart, Target, etc.)               │
       └───────────────┬────────────────────────┬───────────────┘
                       │                        │
        HTTP Requests  │ (Via Rotating Proxies) │  HTML/JSON Data
     ┌─────────────────▼────────────────────────▼─────────────────┐
     │                                                            │
     │                 SCRAPY EXTRACTION ENGINE                   │
     │      ┌──────────────────────────────────────────────┐      │
     │      │  • Concurrency Control (Async Workers)       │      │
     │      │  • Anti-Bot Middlewares (User-Agent/Cookies) │      │
     │      └──────────────────────┬───────────────────────┘      │
     │                             │                              │
     └─────────────────────────────┼──────────────────────────────┘
                                   │
                    Raw Data (Items) │ (Cleaning & Validation)
     ┌─────────────────────────────▼──────────────────────────────┐
     │                                                            │
     │                  DATA PROCESSING PIPELINE                  │
     │      ┌──────────────────────────────────────────────┐      │
     │      │  • Price Normalization ($12.99 -> 12.99)     │      │
     │      │  • Deduplication (Unique SKU check)          │      │
     │      │  • Timestamping (Execution tracking)         │      │
     │      └──────────────────────┬───────────────────────┘      │
     │                             │                              │
     └─────────────────────────────┼──────────────────────────────┘
                                   │
                    Structured Data  │ (Bulk Insert)
     ┌─────────────────────────────▼──────────────────────────────┐
     │                                                            │
     │                  POSTGRESQL DATABASE                       │
     │      ┌──────────────────────────────────────────────┐      │
     │      │  • Products Table (PK: SKU)                  │      │
     │      │  • Pricing History (Historical Tracking)     │      │
     │      │  • Indices for Fast Web Queries              │      │
     │      └──────────────────────────────────────────────┘      │
     │                                                            │
     └─────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
                   [ WEB APP / CLIENT DASHBOARD ]
                    (Fast, sub-second queries)
```

---

## Project Structure

```
sentinel-price/
├── scrapers/                # Scrapy Project Folder
│   ├── sentinel_spiders/    # Scrapy Spiders
│   │   ├── spiders/
│   │   │   ├── amazon_spider.py
│   │   │   └── walmart_spider.py
│   │   ├── items.py         # Data models
│   │   ├── pipelines.py     # Database insertion & cleaning logic
│   │   ├── settings.py      # Concurrency & Proxy settings
│   │   └── middlewares.py   # Anti-bot bypass logic
│   ├── scrapy.cfg
│   └── requirements.txt
├── database/                # Database initialization scripts
│   └── init.sql             # Pre-defines the schema
├── docker-compose.yml       # Orchestrates Scraper + DB
├── .env                     # API Keys & DB Credentials (git-ignored)
├── .env.example             # Template for environment variables
├── .gitignore
└── README.md
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Scraping Engine | Python 3.11, Scrapy |
| Database | PostgreSQL 15 |
| Orchestration | Docker, Docker Compose |
| Extraction Techniques | CSS/XPath Selectors, Item Loaders, Async Pipelines |
| Anti-Bot | Rotating Proxies, User-Agent Spoofing |

---

## Getting Started

### Prerequisites

Before you begin, make sure you have the following installed and **running** on your machine:

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (v20+)
- [Docker Compose](https://docs.docker.com/compose/) (v2+)

> No local Python or PostgreSQL installation is required — Docker handles everything.

**Recommended system resources:**
- RAM: 4 GB minimum (8 GB recommended for high-concurrency crawls)
- Disk: 2 GB free space minimum

---

### Environment Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Open `.env` and update the following variables:

```env
# PostgreSQL Configuration
POSTGRES_DB=sentinelprice
POSTGRES_USER=your_db_user
POSTGRES_PASSWORD=your_db_password
POSTGRES_HOST=db
POSTGRES_PORT=5432

# Proxy API Configuration (e.g., BrightData, Oxylabs, SmartProxy)
PROXY_API_KEY=your_proxy_api_key
PROXY_ENDPOINT=http://proxy.provider.com:PORT

# Scrapy Settings (optional overrides)
CONCURRENT_REQUESTS=16
DOWNLOAD_DELAY=1
```

> **Security note:** Never commit your `.env` file. It is already listed in `.gitignore`.

---

### Installation & Execution

**1. Clone the repository:**

```bash
git clone https://github.com/biko2020/sentinel-price
cd sentinel-price
```

**2. Build and start the stack:**

```bash
docker-compose up --build
```

This will:
- Pull and build all required Docker images
- Initialize the PostgreSQL database using `database/init.sql`
- Start the scraper container in a ready state

---

### Running a Crawl

Once the stack is running, open a new terminal and execute:

```bash
# Run the Amazon spider
docker-compose run scraper scrapy crawl amazon_spider

# Run the Walmart spider
docker-compose run scraper scrapy crawl walmart_spider
```

---

### Verifying the Output

After a successful crawl, connect to the PostgreSQL database to confirm data was inserted:

```bash
# Open a psql session inside the running db container
docker-compose exec db psql -U your_db_user -d sentinelprice
```

Then run a sample query:

```sql
-- View the latest 10 price records
SELECT name, brand, price_current, currency, availability, timestamp
FROM pricing_history
ORDER BY timestamp DESC
LIMIT 10;

-- Count total products tracked
SELECT COUNT(DISTINCT sku) AS total_products FROM products;
```

You can also connect using a GUI tool like [pgAdmin](https://www.pgadmin.org/) or [DBeaver](https://dbeaver.io/) by pointing it to `localhost:5432` with your credentials from `.env`.

---

### Stopping & Resetting the Stack

**Stop the stack (preserves data volumes):**

```bash
docker-compose down
```

**Stop and wipe all data (full reset — destructive):**

```bash
docker-compose down -v
```

> The `-v` flag removes Docker volumes, including the PostgreSQL data directory. Use this when you want a clean slate.

---

## Spider Configuration

Each spider has a `start_urls` list and optional `product_urls` configuration at the top of the file. To target specific products or categories, edit the relevant spider file:

```python
# scrapers/sentinel_spiders/spiders/amazon_spider.py

class AmazonSpider(scrapy.Spider):
    name = "amazon_spider"

    # Add your target product or category URLs here
    start_urls = [
        "https://www.amazon.com/dp/ASIN_1",
        "https://www.amazon.com/dp/ASIN_2",
        # Add more URLs as needed
    ]
```

Alternatively, you can pass start URLs at runtime via Scrapy's `-a` argument:

```bash
docker-compose run scraper scrapy crawl amazon_spider -a url="https://www.amazon.com/dp/ASIN_1"
```

> **Tip:** For large-scale monitoring, maintain a CSV or database table of target URLs and load them dynamically in the spider's `start_requests` method.

---

## Database Schema

The system outputs a clean, normalized database with the following structure:

**`products` table** — Stores the canonical product catalog:

| Column | Type | Description |
|---|---|---|
| `product_id` | SERIAL PRIMARY KEY | Auto-incremented internal ID |
| `sku` | VARCHAR UNIQUE | Unique product identifier |
| `name` | VARCHAR | Product name |
| `brand` | VARCHAR | Brand name |
| `url` | TEXT | Source URL |
| `created_at` | TIMESTAMP | First seen date |

**`pricing_history` table** — Stores all price snapshots over time:

| Column | Type | Description |
|---|---|---|
| `id` | SERIAL PRIMARY KEY | Auto-incremented record ID |
| `product_id` | INT (FK) | References `products.product_id` |
| `price_current` | NUMERIC(10,2) | Scraped price (normalized) |
| `currency` | CHAR(3) | ISO 4217 currency code (e.g., USD) |
| `availability` | VARCHAR | Stock status (e.g., In Stock) |
| `timestamp` | TIMESTAMP | Time of the price snapshot |

> Indices are pre-defined on `sku`, `product_id`, and `timestamp` for fast query performance.

---

## Scheduling Crawls

SentinelPrice does not include a built-in scheduler, but it integrates easily with several options depending on your deployment environment:

**Option 1 — Cron (simple, Linux/macOS):**

```bash
# Run the Amazon spider every day at 2:00 AM
0 2 * * * cd /path/to/sentinel-price && docker-compose run scraper scrapy crawl amazon_spider >> /var/log/sentinel.log 2>&1
```

**Option 2 — Scrapyd (Scrapy's native job scheduler):**

Deploy your spiders to a [Scrapyd](https://scrapyd.readthedocs.io/) server and trigger them via its HTTP API.

**Option 3 — Apache Airflow (recommended for production):**

For complex workflows, dependency management, and retry logic, [Apache Airflow](https://airflow.apache.org/) is the recommended approach. Each spider can be wrapped in a `BashOperator` or `DockerOperator` DAG task.

---

## Logging & Debugging

Scrapy logs are streamed to stdout by default. To capture them to a file:

```bash
docker-compose run scraper scrapy crawl amazon_spider --logfile=/app/logs/amazon.log --loglevel=INFO
```

Log levels available: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

**Common issues and fixes:**

| Symptom | Likely Cause | Fix |
|---|---|---|
| `Connection refused` on DB | Database container not ready | Wait a few seconds and retry; check `docker-compose ps` |
| Empty database after crawl | Spider found no items | Check `start_urls` and inspect logs for HTTP errors |
| `407 Proxy Auth Required` | Invalid proxy credentials | Verify `PROXY_API_KEY` and `PROXY_ENDPOINT` in `.env` |
| `503 / 429` responses | Rate limiting triggered | Increase `DOWNLOAD_DELAY` in `settings.py` or `.env` |

To inspect the running containers:

```bash
docker-compose ps
docker-compose logs scraper
docker-compose logs db
```

---

## Compliance & Best Practices

SentinelPrice is designed with responsible scraping in mind:

- **`robots.txt`** — Respected by default; configurable via `ROBOTSTXT_OBEY` in `settings.py`.
- **Rate limiting** — `DOWNLOAD_DELAY` and `AUTOTHROTTLE` settings prevent server overload.
- **Polite crawling** — Randomized delays and concurrency caps are enabled by default.
- **Terms of Service** — Users are responsible for ensuring their use of this tool complies with the terms of service of any target website.

> Proxy usage is optional and fully configurable. It is the user's responsibility to use proxies in accordance with applicable laws and the target site's policies.

---

## Known Limitations & Roadmap

**Current Limitations:**

- Only Amazon and Walmart spiders are included in the initial release.
- JavaScript-rendered content requires Scrapy-Playwright integration (not yet included).
- No built-in web dashboard — database querying requires a separate tool or integration.
- No native alerting system for price drops or availability changes.

**Planned Features:**

- [ ] Scrapy-Playwright middleware for JS-heavy pages
- [ ] Built-in REST API layer for dashboard integration
- [ ] Email/Slack alerting for price change events
- [ ] Support for additional retailers (Target, eBay, BestBuy)
- [ ] Airflow DAG templates for production scheduling
- [ ] Grafana dashboard template for price analytics

---

## Contributing

Contributions are welcome! To get started:

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Commit your changes: `git commit -m 'Add some feature'`
4. Push to the branch: `git push origin feature/your-feature-name`
5. Open a Pull Request.

Please ensure your code follows PEP 8 style guidelines and includes relevant docstrings.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## Contact

**AIT OUFKIR BRAHIM**  
Big Data Engineer

- 📧 Email: [aitoufkirbrahimab@gmail.com](mailto:aitoufkirbrahimab@gmail.com)
- 💻 GitHub: [@biko2020](https://github.com/biko2020)
- 💼 LinkedIn: [brahim-aitoufkir](https://linkedin.com/in/brahim-aitoufkir)

Open to freelance data engineering projects, Big Data consulting, BI dashboard development, technical architecture reviews, cloud migration strategies, and team training and mentorship.