"""Tests for AVS data helpers – subscription-scoped pricing path."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from az_scout_avs_sku.avs_data import (
    _apply_subscription_prices,
    _build_price_index,
    _fetch_subscription_price_sheet,
    _prices_cache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_retail_item(
    meter_id: str = "meter-1",
    meter_name: str = "AV36 Node",
    sku_name: str = "AV36 Node",
    retail_price: float = 10.0,
    item_type: str = "Consumption",
    reservation_term: str = "",
    effective_start: str = "2025-01-01",
    currency: str = "USD",
) -> dict[str, Any]:
    """Build a Retail Prices API item dict."""
    item: dict[str, Any] = {
        "meterId": meter_id,
        "meterName": meter_name,
        "skuName": sku_name,
        "retailPrice": retail_price,
        "type": item_type,
        "effectiveStartDate": effective_start,
        "currencyCode": currency,
    }
    if reservation_term:
        item["reservationTerm"] = reservation_term
    return item


def _make_pricesheet_item(
    meter_id: str = "meter-1",
    meter_name: str = "AV36 Node",
    unit_price: float = 8.0,
    meter_category: str = "Specialized Compute",
    meter_subcategory: str = "Azure VMware Solution",
) -> dict[str, Any]:
    """Build a Consumption Price Sheet item dict."""
    return {
        "meterId": meter_id,
        "meterName": meter_name,
        "unitPrice": unit_price,
        "currencyCode": "USD",
        "meterCategory": meter_category,
        "meterSubCategory": meter_subcategory,
        "unitOfMeasure": "1 Hour",
    }


# ---------------------------------------------------------------------------
# _fetch_subscription_price_sheet
# ---------------------------------------------------------------------------


@patch("az_scout_avs_sku.avs_data._get_headers", return_value={"Authorization": "Bearer tok"})
@patch("az_scout_avs_sku.avs_data.requests.get")
def test_fetch_subscription_price_sheet_returns_avs_meters(
    mock_get: MagicMock,
    _mock_headers: MagicMock,
) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "properties": {
            "pricesheets": [
                _make_pricesheet_item(meter_id="aaa", unit_price=8.5),
                _make_pricesheet_item(
                    meter_id="bbb",
                    meter_category="Virtual Machines",
                    meter_subcategory="Dv5 Series",
                    unit_price=0.5,
                ),
            ],
            "nextLink": None,
        },
    }
    mock_get.return_value = mock_resp

    result = _fetch_subscription_price_sheet("sub-123")

    assert result == {"aaa": 8.5}
    _mock_headers.assert_called_once()


@patch("az_scout_avs_sku.avs_data._get_headers", return_value={"Authorization": "Bearer tok"})
@patch("az_scout_avs_sku.avs_data.requests.get")
def test_fetch_subscription_price_sheet_follows_pagination(
    mock_get: MagicMock,
    _mock_headers: MagicMock,
) -> None:
    page1 = MagicMock()
    page1.status_code = 200
    page1.raise_for_status = MagicMock()
    page1.json.return_value = {
        "properties": {
            "pricesheets": [_make_pricesheet_item(meter_id="m1", unit_price=5.0)],
            "nextLink": "https://next-page",
        },
    }

    page2 = MagicMock()
    page2.status_code = 200
    page2.raise_for_status = MagicMock()
    page2.json.return_value = {
        "properties": {
            "pricesheets": [_make_pricesheet_item(meter_id="m2", unit_price=6.0)],
            "nextLink": None,
        },
    }

    mock_get.side_effect = [page1, page2]

    result = _fetch_subscription_price_sheet("sub-456")

    assert result == {"m1": 5.0, "m2": 6.0}
    assert mock_get.call_count == 2


@patch("az_scout_avs_sku.avs_data._get_headers", return_value={"Authorization": "Bearer tok"})
@patch("az_scout_avs_sku.avs_data.requests.get")
def test_fetch_subscription_price_sheet_raises_on_404(
    mock_get: MagicMock,
    _mock_headers: MagicMock,
) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_get.return_value = mock_resp

    with pytest.raises(ValueError, match="Enterprise Agreement"):
        _fetch_subscription_price_sheet("sub-no-ea")


# ---------------------------------------------------------------------------
# _apply_subscription_prices
# ---------------------------------------------------------------------------


@patch("az_scout_avs_sku.avs_data._fetch_subscription_price_sheet")
def test_apply_subscription_prices_overrides_retail(
    mock_fetch: MagicMock,
) -> None:
    mock_fetch.return_value = {"meter-a": 7.5}

    items = [
        _make_retail_item(meter_id="meter-a", retail_price=10.0),
        _make_retail_item(meter_id="meter-b", retail_price=12.0),
    ]
    result = _apply_subscription_prices(items, "sub-1")

    assert len(result) == 2
    assert result[0]["retailPrice"] == 7.5
    assert result[1]["retailPrice"] == 12.0  # unchanged


@patch("az_scout_avs_sku.avs_data._fetch_subscription_price_sheet")
def test_apply_subscription_prices_raises_on_failure(
    mock_fetch: MagicMock,
) -> None:
    mock_fetch.side_effect = RuntimeError("auth failed")

    items = [_make_retail_item(retail_price=10.0)]
    with pytest.raises(RuntimeError, match="auth failed"):
        _apply_subscription_prices(items, "sub-1")


@patch("az_scout_avs_sku.avs_data._fetch_subscription_price_sheet")
def test_apply_subscription_prices_raises_when_no_meters(
    mock_fetch: MagicMock,
) -> None:
    mock_fetch.return_value = {}

    items = [_make_retail_item(retail_price=10.0)]
    with pytest.raises(ValueError, match="No AVS meters found"):
        _apply_subscription_prices(items, "sub-1")


@patch("az_scout_avs_sku.avs_data._fetch_subscription_price_sheet")
def test_apply_subscription_prices_case_insensitive_meter_id(
    mock_fetch: MagicMock,
) -> None:
    mock_fetch.return_value = {"meter-abc": 5.0}

    items = [_make_retail_item(meter_id="METER-ABC", retail_price=10.0)]
    result = _apply_subscription_prices(items, "sub-1")

    assert result[0]["retailPrice"] == 5.0


# ---------------------------------------------------------------------------
# _build_price_index – subscription routing
# ---------------------------------------------------------------------------


@patch("az_scout_avs_sku.avs_data._apply_subscription_prices")
@patch("az_scout_avs_sku.avs_data._fetch_regional_price_items")
def test_build_price_index_calls_subscription_path(
    mock_fetch_public: MagicMock,
    mock_apply_sub: MagicMock,
) -> None:
    _prices_cache.clear()

    public_items = [
        _make_retail_item(meter_id="m1", sku_name="AV36 Node", retail_price=10.0),
    ]
    mock_fetch_public.return_value = public_items
    mock_apply_sub.return_value = [
        _make_retail_item(meter_id="m1", sku_name="AV36 Node", retail_price=7.0),
    ]

    result = _build_price_index(
        region="eastus",
        byol=False,
        pricing_source="subscription",
        subscription_id="sub-1",
    )

    mock_apply_sub.assert_called_once_with(public_items, "sub-1")
    assert result["AV36"]["payg_hour"] == 7.0


@patch("az_scout_avs_sku.avs_data._apply_subscription_prices")
@patch("az_scout_avs_sku.avs_data._fetch_regional_price_items")
def test_build_price_index_skips_subscription_for_public(
    mock_fetch_public: MagicMock,
    mock_apply_sub: MagicMock,
) -> None:
    _prices_cache.clear()

    mock_fetch_public.return_value = [
        _make_retail_item(meter_id="m1", sku_name="AV36 Node", retail_price=10.0),
    ]

    result = _build_price_index(
        region="eastus",
        byol=False,
        pricing_source="public",
        subscription_id="",
    )

    mock_apply_sub.assert_not_called()
    assert result["AV36"]["payg_hour"] == 10.0
