"""HTTP adapter for the isolated price-trend feature."""

from fastapi import APIRouter, Query

from backend.price_trend import get_price_trends
from backend.price_trend.models import PriceTrendResponse


router = APIRouter(prefix="/api/price-trends", tags=["price-trend"])


@router.get("", response_model=PriceTrendResponse)
def price_trends(
    refresh: bool = Query(default=False, description="Bypass the 15-minute scan cache"),
) -> PriceTrendResponse:
    return get_price_trends(force=refresh)
