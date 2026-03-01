
# =============================================================================
#  SentinelPrice · Prices Router
# =============================================================================

from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from api.database import database
from api.models import LatestPriceResponse, PriceHistoryResponse, PriceChangeResponse

router = APIRouter()


@router.get(
    "/prices/latest",
    response_model       = list[LatestPriceResponse],
    summary              = "Latest price snapshot per product",
    response_description = "Most recent price observation for each tracked product",
)
async def latest_prices(
    source:       Optional[str]   = Query(None,  description="Filter by source: amazon | walmart"),
    availability: Optional[str]   = Query(None,  description="Filter by availability status"),
    min_price:    Optional[float] = Query(None,  description="Minimum current price"),
    max_price:    Optional[float] = Query(None,  description="Maximum current price"),
    limit:        int             = Query(50,    ge=1, le=500),
    offset:       int             = Query(0,     ge=0),
):
    query = """
        SELECT * FROM latest_prices
        WHERE 1=1
    """
    params = {}

    if source:
        query += " AND source = :source"
        params["source"] = source.lower()

    if availability:
        query += " AND availability = :availability"
        params["availability"] = availability.lower()

    if min_price is not None:
        query += " AND price_current >= :min_price"
        params["min_price"] = min_price

    if max_price is not None:
        query += " AND price_current <= :max_price"
        params["max_price"] = max_price

    query += " ORDER BY scraped_at DESC LIMIT :limit OFFSET :offset"
    params["limit"]  = limit
    params["offset"] = offset

    rows = await database.fetch_all(query=query, values=params)
    return [dict(r) for r in rows]


@router.get(
    "/prices/history/{sku}",
    response_model       = list[PriceHistoryResponse],
    summary              = "Full price history for a SKU",
    response_description = "All price snapshots recorded for the given SKU, newest first",
)
async def price_history(
    sku:    str,
    source: Optional[str] = Query(None,  description="Source platform (amazon | walmart)"),
    limit:  int           = Query(100,   ge=1, le=1000),
    offset: int           = Query(0,     ge=0),
):
    query = """
        SELECT
            ph.id, p.sku, s.name AS source,
            ph.price_current, ph.price_original, ph.discount_pct,
            ph.currency, ph.availability,
            ph.rating, ph.review_count, ph.scraped_at
        FROM pricing_history ph
        JOIN products p ON p.product_id = ph.product_id
        JOIN sources  s ON s.source_id  = p.source_id
        WHERE p.sku = :sku
    """
    params = {"sku": sku}

    if source:
        query += " AND s.name = :source"
        params["source"] = source.lower()

    query += " ORDER BY ph.scraped_at DESC LIMIT :limit OFFSET :offset"
    params["limit"]  = limit
    params["offset"] = offset

    rows = await database.fetch_all(query=query, values=params)

    if not rows:
        raise HTTPException(
            status_code = 404,
            detail      = f"No price history found for SKU '{sku}'."
        )

    return [dict(r) for r in rows]


@router.get(
    "/prices/changes",
    response_model       = list[PriceChangeResponse],
    summary              = "Detected price changes",
    response_description = "Products whose price changed between consecutive crawl snapshots",
)
async def price_changes(
    source:     Optional[str]   = Query(None,  description="Filter by source"),
    min_change: Optional[float] = Query(None,  description="Minimum absolute change percentage"),
    limit:      int             = Query(50,    ge=1, le=500),
    offset:     int             = Query(0,     ge=0),
):
    query = """
        SELECT * FROM price_changes
        WHERE 1=1
    """
    params = {}

    if source:
        query += " AND source = :source"
        params["source"] = source.lower()

    if min_change is not None:
        query += " AND ABS(change_pct) >= :min_change"
        params["min_change"] = min_change

    query += " ORDER BY scraped_at DESC LIMIT :limit OFFSET :offset"
    params["limit"]  = limit
    params["offset"] = offset

    rows = await database.fetch_all(query=query, values=params)
    return [dict(r) for r in rows]