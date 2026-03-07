"""API routes for AVS SKU and pricing data."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from az_scout_avs_sku.avs_data import get_avs_skus_for_region

router = APIRouter()


@router.get("/skus", response_model=None)
async def skus(
    region: str = "",
    byol: bool = True,
    sku: str = "",
    pricing_source: str = "public",
    subscription_id: str = "",
) -> dict[str, object] | JSONResponse:
    """Return AVS SKUs with technical data and optional regional pricing."""
    normalized_region = region.strip().lower()
    normalized_sku = sku.strip()
    normalized_pricing_source = pricing_source.strip().lower() or "public"
    normalized_subscription_id = subscription_id.strip()

    try:
        return get_avs_skus_for_region(
            region=normalized_region,
            byol=byol,
            sku=normalized_sku,
            pricing_source=normalized_pricing_source,
            subscription_id=normalized_subscription_id,
        )
    except ValueError as exc:
        message = f"Failed to load AVS SKU data for region '{normalized_region}': {exc}"
        return JSONResponse(
            status_code=422,
            content={"error": message, "detail": message},
        )
    except Exception as exc:  # noqa: BLE001
        message = f"Failed to load AVS SKU data for region '{normalized_region}': {exc}"
        return JSONResponse(
            status_code=502,
            content={"error": message, "detail": message},
        )
