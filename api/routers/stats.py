
# =============================================================================
#  SentinelPrice · Stats Router
# =============================================================================

from fastapi import APIRouter
from api.database import database
from api.models import StatsResponse

router = APIRouter()


@router.get(
    "/stats",
    response_model = StatsResponse,
    summary        = "Crawl and coverage statistics",
)
async def get_stats():

    total_products = await database.fetch_val(
        "SELECT COUNT(DISTINCT product_id) FROM products"
    )

    total_snapshots = await database.fetch_val(
        "SELECT COUNT(*) FROM pricing_history"
    )

    sources = await database.fetch_all("""
        SELECT
            s.name                          AS source,
            COUNT(DISTINCT p.product_id)    AS product_count,
            COUNT(ph.id)                    AS snapshot_count,
            MAX(ph.scraped_at)              AS last_crawl
        FROM sources s
        LEFT JOIN products       p  ON p.source_id  = s.source_id
        LEFT JOIN pricing_history ph ON ph.product_id = p.product_id
        GROUP BY s.name
        ORDER BY s.name
    """)

    last_crawl = await database.fetch_val(
        "SELECT MAX(scraped_at) FROM pricing_history"
    )

    price_changes_24h = await database.fetch_val("""
        SELECT COUNT(*) FROM price_changes
        WHERE scraped_at >= NOW() - INTERVAL '24 hours'
    """)

    return {
        "total_products":    total_products    or 0,
        "total_snapshots":   total_snapshots   or 0,
        "sources":           [dict(r) for r in sources],
        "last_crawl":        last_crawl,
        "price_changes_24h": price_changes_24h or 0,
    }