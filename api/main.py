
# =============================================================================
#  SentinelPrice · REST API
# =============================================================================
#  FastAPI application exposing price intelligence data for dashboard
#  integration and external consumers.
#
#  Base URL:  http://localhost:8000
#  Docs:      http://localhost:8000/docs      (Swagger UI)
#  Redoc:     http://localhost:8000/redoc
#
#  Endpoints:
#    GET  /                            — Health check
#    GET  /api/v1/products             — List all tracked products
#    GET  /api/v1/products/{sku}       — Get a single product
#    GET  /api/v1/prices/latest        — Latest price per product
#    GET  /api/v1/prices/history/{sku} — Full price history for a SKU
#    GET  /api/v1/prices/changes       — Detected price changes
#    GET  /api/v1/stats                — Crawl and coverage statistics
# =============================================================================

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.database import database
from api.routers import products, prices, stats


# -----------------------------------------------------------------------------
#  Lifespan — connect / disconnect DB on startup / shutdown
# -----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()
    yield
    await database.disconnect()


# -----------------------------------------------------------------------------
#  App
# -----------------------------------------------------------------------------

app = FastAPI(
    title        = "SentinelPrice API",
    description  = "High-throughput e-commerce price intelligence REST API.",
    version      = "1.0.0",
    lifespan     = lifespan,
    docs_url     = "/docs",
    redoc_url    = "/redoc",
)


# -----------------------------------------------------------------------------
#  CORS — allow all origins by default (restrict in production)
# -----------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


# -----------------------------------------------------------------------------
#  Routers
# -----------------------------------------------------------------------------

app.include_router(products.router, prefix="/api/v1", tags=["Products"])
app.include_router(prices.router,   prefix="/api/v1", tags=["Prices"])
app.include_router(stats.router,    prefix="/api/v1", tags=["Stats"])


# -----------------------------------------------------------------------------
#  Health check
# -----------------------------------------------------------------------------

@app.get("/", tags=["Health"])
async def health():
    return {"status": "ok", "service": "SentinelPrice API", "version": "1.0.0"}