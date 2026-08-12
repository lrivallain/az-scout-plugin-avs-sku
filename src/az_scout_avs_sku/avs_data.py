"""AVS SKU technical data and pricing aggregation helpers."""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from az_scout.azure_api import AZURE_MGMT_URL, ArmNotFoundError, arm_get

from az_scout_avs_sku._log import logger

_SKU_DATA_PATH = Path(__file__).parent / "static" / "data" / "sku.json"
AZURE_PRICES_BASE_URL = "https://prices.azure.com/api/retail/prices"
REQUEST_TIMEOUT_SECONDS = 20
CACHE_TTL = timedelta(minutes=30)
PAYG_MONTH_HOURS = 730
CONSUMPTION_API_VERSION = "2023-05-01"
PRICE_MODES = (
    "payg_hour",
    "payg_month",
    "reservation_1y_month",
    "reservation_3y_month",
    "reservation_5y_month",
)
GENERATION2_AV64_REGIONS = {
    "australiaeast",
    "belgiumcentral",
    "brazilsouth",
    "canadacentral",
    "canadaeast",
    "centralus",
    "eastus",
    "eastus2",
    "francecentral",
    "germanywestcentral",
    "japaneast",
    "malaysiawest",
    "newzealandnorth",
    "northeurope",
    "northcentralus",
    "norwayeast",
    "qatarcentral",
    "southeastasia",
    "southcentralus",
    "switzerlandnorth",
    "switzerlandwest",
    "uksouth",
    "ukwest",
    "westus2",
}
STRETCHED_CLUSTER_REGIONS: dict[str, set[str]] = {
    "AV36": {"uksouth", "westeurope"},
    "AV36P": {"uksouth", "westeurope", "australiaeast", "eastus"},
    "AV48": {"germanywestcentral"},
}

_sku_cache: tuple[datetime, list[dict[str, Any]]] | None = None
_prices_cache: dict[tuple[str, bool, str, str], tuple[datetime, dict[str, dict[str, Any]]]] = {}
_sku_cache_lock = threading.Lock()
_prices_cache_lock = threading.Lock()


def _http_get_json(url: str) -> dict[str, Any] | list[Any]:
    resp = requests.get(
        url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": "az-scout-plugin-avs-sku"},
    )
    resp.raise_for_status()
    parsed: dict[str, Any] | list[Any] = resp.json()
    if not isinstance(parsed, (dict, list)):
        msg = "Unexpected JSON payload type"
        raise ValueError(msg)
    return parsed


def _is_cache_fresh(timestamp: datetime) -> bool:
    return datetime.now(UTC) - timestamp < CACHE_TTL


def _extract_sku_code(value: str) -> str | None:
    match = re.search(r"\bAV\d+[A-Z]*\b", value.upper())
    if not match:
        return None
    return match.group(0)


def _normalize_region_key(region: str) -> str:
    return region.lower().replace(" ", "")


def _get_generation_labels(sku_name: str, region: str) -> list[str]:
    normalized_sku = sku_name.strip().upper()
    if normalized_sku not in {"AV36", "AV36P", "AV48", "AV52", "AV64"}:
        return []

    if normalized_sku == "AV64":
        labels = ["Generation 1"]
        if _normalize_region_key(region) in GENERATION2_AV64_REGIONS:
            labels.append("Generation 2")
        return labels

    return ["Generation 1"]


def _supports_stretched_cluster(sku_name: str, region: str) -> bool:
    regions = STRETCHED_CLUSTER_REGIONS.get(sku_name.strip().upper(), set())
    return _normalize_region_key(region) in regions


def get_avs_sku_technical_data() -> list[dict[str, Any]]:
    """Return AVS SKU technical metadata from the bundled JSON data file."""
    global _sku_cache

    with _sku_cache_lock:
        if _sku_cache and _is_cache_fresh(_sku_cache[0]):
            return _sku_cache[1]

        with open(_SKU_DATA_PATH, encoding="utf-8") as fh:
            payload = json.load(fh)

        if not isinstance(payload, list):
            msg = "Unexpected AVS SKU data format in bundled data file"
            raise ValueError(msg)

        normalized: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            sku_name = str(item.get("name", "")).strip()
            if not sku_name:
                continue
            normalized.append(item)

        normalized.sort(key=lambda sku: str(sku.get("name", "")))
        _sku_cache = (datetime.now(UTC), normalized)
        return normalized


def _build_prices_filter(region: str) -> str:
    safe_region = region.replace("'", "''")
    return (
        "serviceName eq 'Specialized Compute' "
        "and armRegionName eq '"
        f"{safe_region}' "
        "and contains(productName,'Azure VMware Solution')"
    )


def _fetch_regional_price_items(region: str) -> list[dict[str, Any]]:
    query = urlencode({"$filter": _build_prices_filter(region)})
    next_url = f"{AZURE_PRICES_BASE_URL}?{query}"
    items: list[dict[str, Any]] = []

    while next_url:
        payload = _http_get_json(next_url)
        if not isinstance(payload, dict):
            break
        page_items = payload.get("Items", [])
        if isinstance(page_items, list):
            items.extend([item for item in page_items if isinstance(item, dict)])
        next_link = payload.get("NextPageLink")
        next_url = str(next_link) if next_link else ""

    return items


def _fetch_subscription_price_sheet(
    subscription_id: str,
) -> dict[str, float]:
    """Fetch AVS meter prices from the Consumption Price Sheet API.

    Uses az-scout's ``arm_get`` to make authenticated ARM calls with
    built-in retry and error handling.  Returns a mapping of
    ``meterId`` (lowercase) to ``unitPrice`` for AVS-related meters only.
    """
    url: str | None = (
        f"{AZURE_MGMT_URL}/subscriptions/{subscription_id}"
        f"/providers/Microsoft.Consumption/pricesheets/default"
        f"?api-version={CONSUMPTION_API_VERSION}"
    )
    meter_prices: dict[str, float] = {}

    while url:
        try:
            data = arm_get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        except ArmNotFoundError as exc:
            msg = (
                f"Subscription price sheet not available for subscription "
                f"'{subscription_id}'. This API requires an Enterprise Agreement (EA) "
                f"or Microsoft Customer Agreement (MCA) billing account."
            )
            raise ValueError(msg) from exc
        properties = data.get("properties", {})
        pricesheets = properties.get("pricesheets", [])

        for item in pricesheets:
            if not isinstance(item, dict):
                continue
            category = str(item.get("meterCategory", "")).lower()
            subcategory = str(item.get("meterSubCategory", "")).lower()
            if "specialized compute" not in category:
                continue
            if "azure vmware solution" not in subcategory:
                continue

            meter_id = str(item.get("meterId", "")).lower()
            unit_price = item.get("unitPrice")
            if meter_id and isinstance(unit_price, (int, float)):
                meter_prices[meter_id] = float(unit_price)

        url = properties.get("nextLink")

    logger.info(
        "Fetched %d AVS meter prices from subscription price sheet for sub=%s",
        len(meter_prices),
        subscription_id,
    )
    return meter_prices


def _apply_subscription_prices(
    items: list[dict[str, Any]],
    subscription_id: str,
) -> list[dict[str, Any]]:
    """Override retail prices with subscription-specific prices.

    Fetches the subscription's Consumption Price Sheet using az-scout's
    authentication helpers and replaces ``retailPrice`` for matching
    meter IDs.  Raises on any failure so callers get an explicit error
    instead of silently falling back to public prices.
    """
    meter_prices = _fetch_subscription_price_sheet(subscription_id)

    if not meter_prices:
        msg = (
            f"No AVS meters found in the subscription price sheet "
            f"for subscription '{subscription_id}'. "
            f"Ensure the subscription has Azure VMware Solution pricing."
        )
        raise ValueError(msg)

    applied = 0
    result: list[dict[str, Any]] = []
    for item in items:
        meter_id = str(item.get("meterId", "")).lower()
        if meter_id in meter_prices:
            item = {**item, "retailPrice": meter_prices[meter_id]}
            applied += 1
        result.append(item)

    logger.info(
        "Applied %d subscription prices (of %d total items) for sub=%s",
        applied,
        len(items),
        subscription_id,
    )
    return result


def _build_price_index(
    region: str,
    byol: bool,
    pricing_source: str = "public",
    subscription_id: str = "",
) -> dict[str, dict[str, Any]]:
    cache_key = (region, byol, pricing_source, subscription_id)
    with _prices_cache_lock:
        cached = _prices_cache.get(cache_key)
        if cached and _is_cache_fresh(cached[0]):
            return cached[1]

    # Fetch data outside the lock to avoid blocking other threads during network I/O.
    items = _fetch_regional_price_items(region)

    if pricing_source == "subscription" and subscription_id:
        items = _apply_subscription_prices(items, subscription_id)

    prices_by_sku: dict[str, dict[str, Any]] = {}

    def get_or_create_price_entry(sku_code: str) -> dict[str, Any]:
        existing = prices_by_sku.get(sku_code)
        if existing is not None:
            return existing

        created = {
            "currency_code": "USD",
            "payg_hour": None,
            "payg_month": None,
            "reservation_1y_month": None,
            "reservation_3y_month": None,
            "reservation_5y_month": None,
            "_effective": {
                "payg_hour": "",
                "reservation_1y_month": "",
                "reservation_3y_month": "",
                "reservation_5y_month": "",
            },
        }
        prices_by_sku[sku_code] = created
        return created

    for item in items:
        meter_name = str(item.get("meterName", ""))
        sku_name = str(item.get("skuName", ""))
        if "Trial" in meter_name or "Trial" in sku_name:
            continue

        has_byol = "BYOL" in meter_name.upper() or "BYOL" in sku_name.upper()
        if has_byol != byol:
            continue

        sku_code = _extract_sku_code(sku_name) or _extract_sku_code(meter_name)
        if not sku_code:
            continue

        retail_price = item.get("retailPrice")
        if not isinstance(retail_price, (int, float)) or retail_price <= 0:
            continue

        price_entry = get_or_create_price_entry(sku_code)
        price_entry["currency_code"] = item.get("currencyCode", "USD")

        item_type = str(item.get("type", ""))
        candidate_start = str(item.get("effectiveStartDate", ""))

        if item_type == "Consumption":
            current_start = str(price_entry["_effective"]["payg_hour"])
            if candidate_start >= current_start:
                payg_hour = float(retail_price)
                price_entry["payg_hour"] = payg_hour
                price_entry["payg_month"] = round(payg_hour * PAYG_MONTH_HOURS, 2)
                price_entry["_effective"]["payg_hour"] = candidate_start
            continue

        if item_type != "Reservation":
            continue

        reservation_term = str(item.get("reservationTerm", "")).strip().lower()
        mode_key = ""
        months = 0
        if reservation_term == "1 year":
            mode_key = "reservation_1y_month"
            months = 12
        elif reservation_term == "3 years":
            mode_key = "reservation_3y_month"
            months = 36
        elif reservation_term == "5 years":
            mode_key = "reservation_5y_month"
            months = 60
        else:
            continue

        current_start = str(price_entry["_effective"][mode_key])
        if candidate_start >= current_start:
            price_entry[mode_key] = round(float(retail_price) / months, 2)
            price_entry["_effective"][mode_key] = candidate_start

    for price_data in prices_by_sku.values():
        price_data.pop("_effective", None)

    with _prices_cache_lock:
        # Double-check: a concurrent thread may have already populated the cache
        # while we were computing; only write if the entry is still absent or stale.
        existing = _prices_cache.get(cache_key)
        if not existing or not _is_cache_fresh(existing[0]):
            _prices_cache[cache_key] = (datetime.now(UTC), prices_by_sku)
        else:
            prices_by_sku = existing[1]
    return prices_by_sku


def get_avs_skus_for_region(
    region: str | None = None,
    byol: bool = True,
    sku: str | None = None,
    pricing_source: str = "public",
    subscription_id: str | None = None,
) -> dict[str, Any]:
    """Return AVS technical SKU data and optional regional pricing when region is provided."""
    normalized_region = (region or "").strip().lower()
    sku_filter = (sku or "").strip().upper()
    normalized_pricing_source = pricing_source.strip().lower() or "public"
    normalized_subscription_id = (subscription_id or "").strip()
    technical_skus = get_avs_sku_technical_data()
    if normalized_region:
        price_index = _build_price_index(
            region=normalized_region,
            byol=byol,
            pricing_source=normalized_pricing_source,
            subscription_id=normalized_subscription_id,
        )
    else:
        price_index = {}

    rows: list[dict[str, Any]] = []
    for technical in technical_skus:
        sku_name = str(technical.get("name", ""))
        if not sku_name:
            continue
        if sku_filter and sku_filter not in sku_name.upper():
            continue

        generation_labels = _get_generation_labels(sku_name=sku_name, region=normalized_region)
        technical_data = dict(technical)
        if normalized_region and sku_name.upper() == "AV64" and "Generation 2" in generation_labels:
            technical_data["vsan_architecture"] = "OSA or ESA"

        price_data = price_index.get(sku_name)
        price_found = any((price_data or {}).get(mode) is not None for mode in PRICE_MODES)

        stretched = (
            _supports_stretched_cluster(sku_name, normalized_region) if normalized_region else None
        )

        rows.append(
            {
                "name": sku_name,
                "technical": technical_data,
                "generation_labels": generation_labels,
                "stretched_cluster": stretched,
                "available_in_region": price_found if normalized_region else None,
                "price": {
                    "found": price_found,
                    "byol": byol,
                    **(price_data or {}),
                },
            }
        )

    logger.info(
        "Loaded %s AVS SKUs for region=%s byol=%s sku_filter=%s "
        "pricing_source=%s sub=%s (%s prices found)",
        len(rows),
        normalized_region or "<none>",
        byol,
        sku_filter or "<none>",
        normalized_pricing_source,
        normalized_subscription_id or "<none>",
        sum(1 for row in rows if row["price"]["found"]),
    )

    return {
        "region": normalized_region,
        "byol": byol,
        "sku_filter": sku_filter,
        "pricing_source": normalized_pricing_source,
        "subscription_id": normalized_subscription_id,
        "source": {
            "technical": "bundled (az-scout-plugin-avs-sku)",
            "pricing": AZURE_PRICES_BASE_URL if normalized_region else None,
        },
        "items": rows,
    }
