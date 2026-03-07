from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from az_scout_avs_sku.routes import router


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/plugins/avs-sku")
    return TestClient(app)


def test_skus_route_returns_payload() -> None:
    payload = {
        "region": "eastus",
        "byol": False,
        "sku_filter": "AV64",
        "pricing_source": "subscription",
        "subscription_id": "sub-1",
        "source": {"technical": "https://example.test/sku.json", "pricing": "https://example.test"},
        "items": [],
    }

    with patch("az_scout_avs_sku.routes.get_avs_skus_for_region", return_value=payload) as mocked:
        client = _build_client()
        response = client.get(
            "/plugins/avs-sku/skus",
            params={
                "region": " EastUS ",
                "byol": "false",
                "sku": " AV64 ",
                "pricing_source": " Subscription ",
                "subscription_id": " sub-1 ",
            },
        )

    assert response.status_code == 200
    assert response.json() == payload
    mocked.assert_called_once_with(
        region="eastus",
        byol=False,
        sku="AV64",
        pricing_source="subscription",
        subscription_id="sub-1",
    )


def test_skus_route_wraps_upstream_errors() -> None:
    with patch(
        "az_scout_avs_sku.routes.get_avs_skus_for_region",
        side_effect=RuntimeError("boom"),
    ):
        client = _build_client()
        response = client.get("/plugins/avs-sku/skus", params={"region": "eastus"})

    assert response.status_code == 502
    body = response.json()
    assert "Failed to load AVS SKU data" in body["error"]
    assert "eastus" in body["error"]
    assert body["detail"] == body["error"]


def test_skus_route_returns_422_for_value_errors() -> None:
    with patch(
        "az_scout_avs_sku.routes.get_avs_skus_for_region",
        side_effect=ValueError("No AVS meters found"),
    ):
        client = _build_client()
        response = client.get("/plugins/avs-sku/skus", params={"region": "eastus"})

    assert response.status_code == 422
    body = response.json()
    assert "No AVS meters found" in body["error"]
    assert body["detail"] == body["error"]
