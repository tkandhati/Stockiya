"""HTTP adapter for the isolated price-trend feature."""

from fastapi import APIRouter, HTTPException, Query

from backend.price_trend import get_price_trends, lookup_price_trend
from backend.price_trend.models import PriceTrendLookupResponse, PriceTrendResponse


router = APIRouter(prefix="/api/price-trends", tags=["price-trend"])


@router.get("", response_model=PriceTrendResponse)
def price_trends(
    refresh: bool = Query(default=False, description="Bypass the 15-minute scan cache"),
) -> PriceTrendResponse:
    return get_price_trends(force=refresh)


@router.get("/lookup", response_model=PriceTrendLookupResponse)
def price_trend_lookup(
    symbol: str = Query(..., min_length=1, max_length=30),
) -> PriceTrendLookupResponse:
    try:
        return lookup_price_trend(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
