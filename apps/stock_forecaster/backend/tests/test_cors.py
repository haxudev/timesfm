import pytest
from fastapi.testclient import TestClient

from stock_forecaster.config import Settings
from stock_forecaster.main import create_app


@pytest.fixture
def cors_client(tmp_path):
  settings = Settings(
    cors_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    model_enabled=False,
    research_data_dir=tmp_path / "research",
  )
  with TestClient(create_app(settings)) as client:
    yield client
  assert not settings.research_data_dir.exists()


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_cors_allows_delete_preflight_with_browser_headers(cors_client, origin):
  response = cors_client.options("/api/v2/jobs/" + "a" * 32, headers={
    "Origin": origin,
    "Access-Control-Request-Method": "DELETE",
    "Access-Control-Request-Headers": "content-type,idempotency-key,x-request-id",
  })
  assert response.status_code == 200
  assert response.headers["access-control-allow-origin"] == origin
  assert {"GET", "POST", "DELETE"} <= {
    method.strip() for method in response.headers["access-control-allow-methods"].split(",")
  }
  assert {"content-type", "idempotency-key", "x-request-id"} <= {
    header.strip().lower() for header in response.headers["access-control-allow-headers"].split(",")
  }
  assert "origin" in response.headers["vary"].lower()
  assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("origin", [
  "https://evil.example", "http://localhost:5173.evil.example", "null",
])
def test_cors_rejects_delete_preflight_from_untrusted_origins(cors_client, origin):
  response = cors_client.options("/api/v2/jobs/" + "a" * 32, headers={
    "Origin": origin,
    "Access-Control-Request-Method": "DELETE",
    "Access-Control-Request-Headers": "content-type",
  })
  assert response.status_code == 400
  assert "access-control-allow-origin" not in response.headers
  assert "Disallowed CORS origin" in response.text


def test_cors_does_not_allow_unrelated_methods(cors_client):
  response = cors_client.options("/api/v2/jobs/" + "a" * 32, headers={
    "Origin": "http://localhost:5173",
    "Access-Control-Request-Method": "PATCH",
    "Access-Control-Request-Headers": "content-type",
  })
  assert response.status_code == 400
  assert "PATCH" not in response.headers["access-control-allow-methods"]
  assert "Disallowed CORS method" in response.text