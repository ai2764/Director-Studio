from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app


def test_mobile_path_serves_the_spa_entrypoint(tmp_path, monkeypatch):
    frontend_dist = tmp_path / "frontend" / "dist"
    frontend_dist.mkdir(parents=True)
    (frontend_dist / "index.html").write_text(
        '<!doctype html><div id="root"></div>',
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "frontend_dist", frontend_dist)

    with TestClient(create_app()) as client:
        response = client.get("/mobile")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
