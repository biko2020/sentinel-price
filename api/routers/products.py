
# =============================================================================
#  SentinelPrice · Products Router
# =============================================================================

from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from api.database import database
from api.models import ProductResponse

router = APIRouter()


@router.get(
    "/products",
    response_model     = list[ProductResponse],
    summary            = "List all tracked products",
    response_description = "Paginated list of products across all sources",
)
async def list_products(
    source:   Optional[str] = Query(None,  description="Filter by source: amazon | walmart"),
    brand:    Optional[str] = Query(None,  description="Filter by brand name (case-insensitive)"),
    limit:    int           = Query(50,    ge=1, le=500, description="Results per page"),
    offset:   int           = Query(0,     ge=0,         description="Pagination offset"),
):
    query = """
        SELECT
            p.product_id, s.name AS source, p.sku, p.name,
            p.brand, p.category, p.url, p.image_url,
            p.created_at, p.updated_at
        FROM products p
        JOIN sources s ON s.source_id = p.source_id
        WHERE 1=1
    """
    params = {}

    if source:
        query += " AND s.name = :source"
        params["source"] = source.lower()

    if brand:
        query += " AND LOWER(p.brand) LIKE :brand"
        params["brand"] = f"%{brand.lower()}%"

    query += " ORDER BY p.updated_at DESC LIMIT :limit OFFSET :offset"
    params["limit"]  = limit
    params["offset"] = offset

    rows = await database.fetch_all(query=query, values=params)
    return [dict(r) for r in rows]


@router.get(
    "/products/{sku}",
    response_model     = ProductResponse,
    summary            = "Get a product by SKU",
)
async def get_product(
    sku:    str,
    source: Optional[str] = Query(None, description="Source platform (amazon | walmart)"),
):
    query = """
        SELECT
            p.product_id, s.name AS source, p.sku, p.name,
            p.brand, p.category, p.url, p.image_url,
            p.created_at, p.updated_at
        FROM products p
        JOIN sources s ON s.source_id = p.source_id
        WHERE p.sku = :sku
    """
    params = {"sku": sku}

    if source:
        query += " AND s.name = :source"
        params["source"] = source.lower()

    query += " LIMIT 1"
    row = await database.fetch_one(query=query, values=params)

    if not row:
        raise HTTPException(status_code=404, detail=f"Product SKU '{sku}' not found.")

    return dict(row)