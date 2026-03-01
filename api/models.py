# =============================================================================
#  SentinelPrice · API Models
# =============================================================================
#  Pydantic response schemas for all API endpoints.
# =============================================================================

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ProductResponse(BaseModel):
    product_id:  int
    source:      str
    sku:         str
    name:        str
    brand:       Optional[str]
    category:    Optional[str]
    url:         str
    image_url:   Optional[str]
    created_at:  datetime
    updated_at:  datetime

    class Config:
        from_attributes = True


class LatestPriceResponse(BaseModel):
    product_id:     int
    source:         str
    sku:            str
    product_name:   str
    brand:          Optional[str]
    category:       Optional[str]
    url:            str
    price_current:  Optional[float]
    price_original: Optional[float]
    discount_pct:   Optional[float]
    currency:       str
    availability:   str
    rating:         Optional[float]
    review_count:   Optional[int]
    scraped_at:     datetime

    class Config:
        from_attributes = True


class PriceHistoryResponse(BaseModel):
    id:             int
    sku:            str
    source:         str
    price_current:  float
    price_original: Optional[float]
    discount_pct:   Optional[float]
    currency:       str
    availability:   str
    rating:         Optional[float]
    review_count:   Optional[int]
    scraped_at:     datetime

    class Config:
        from_attributes = True


class PriceChangeResponse(BaseModel):
    product_id:     int
    sku:            str
    product_name:   str
    source:         str
    currency:       str
    prev_price:     float
    price_current:  float
    change_pct:     Optional[float]
    scraped_at:     datetime

    class Config:
        from_attributes = True


class StatsResponse(BaseModel):
    total_products:     int
    total_snapshots:    int
    sources:            list[dict]
    last_crawl:         Optional[datetime]
    price_changes_24h:  int