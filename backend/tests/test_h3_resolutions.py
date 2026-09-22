"""Local H3 resolution choices exposed to Production and managed runs."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_local_resolution_route_exposes_exact_768_and_1080_dimensions() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/h3-ref2va/resolutions")

    assert response.status_code == 200
    choices = {item["id"]: item for item in response.json()["presets"]}
    assert (choices["landscape-768"]["width"], choices["landscape-768"]["height"]) == (1376, 768)
    assert (choices["portrait-1080"]["width"], choices["portrait-1080"]["height"]) == (1088, 1920)
