"""
Unit Tests for Secure FastAPI Scraper & Ingestion Endpoints.
Validates:
1. Authentication (X-API-Key and Bearer tokens)
2. Rate limiting (429 Too Many Requests enforcement)
3. Input validation (strict Pydantic bounds and state whitelist)
4. Job lifecycle and operational dashboard metrics
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app, JOBS_STORE
from app.operations.api_security import (
    DEFAULT_DEV_KEY,
    scrape_rate_limiter,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_limiter_and_jobs():
    """Reset rate limiter and job store before each test."""
    scrape_rate_limiter.reset()
    JOBS_STORE.clear()


def test_public_health_endpoints():
    """Verify that root and health endpoints are public and return 200."""
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert res_root.json()["status"] == "online"

    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "healthy"


def test_auth_rejection_missing_credentials():
    """Ensure POST /scrape rejects calls without authentication header with 401."""
    res = client.post("/scrape", json={"state": "NSW", "max_records": 100})
    assert res.status_code == 401
    assert "Invalid or missing API authentication credentials" in res.json()["detail"]


def test_auth_rejection_invalid_api_key():
    """Ensure POST /scrape rejects invalid API keys with 401."""
    headers = {"X-API-Key": "wrong-secret-token"}
    res = client.post("/scrape", json={"state": "NSW", "max_records": 100}, headers=headers)
    assert res.status_code == 401


def test_auth_success_valid_api_key():
    """Ensure valid X-API-Key allows triggering scrape job."""
    headers = {"X-API-Key": DEFAULT_DEV_KEY}
    payload = {
        "state": "NSW",
        "suburbs": ["Sydney", "Parramatta"],
        "max_records": 50,
        "batch_size": 25,
        "workers": 2,
        "dry_run": True
    }
    res = client.post("/scrape", json=payload, headers=headers)
    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "queued"
    assert "job_id" in data
    assert data["job_id"].startswith("job_")


def test_auth_success_bearer_token():
    """Ensure Authorization: Bearer <key> is accepted."""
    headers = {"Authorization": f"Bearer {DEFAULT_DEV_KEY}"}
    payload = {"state": "VIC", "max_records": 20, "dry_run": True}
    res = client.post("/scrape", json=payload, headers=headers)
    assert res.status_code == 202
    assert res.json()["status"] == "queued"


def test_input_validation_invalid_state():
    """Ensure invalid Australian state code triggers 422 Unprocessable Entity."""
    headers = {"X-API-Key": DEFAULT_DEV_KEY}
    payload = {"state": "CALIFORNIA", "max_records": 50}
    res = client.post("/scrape", json=payload, headers=headers)
    assert res.status_code == 422


def test_input_validation_negative_or_exceeded_records():
    """Ensure max_records bounds (1 to 100,000) are enforced."""
    headers = {"X-API-Key": DEFAULT_DEV_KEY}

    # Zero records
    res_zero = client.post("/scrape", json={"max_records": 0}, headers=headers)
    assert res_zero.status_code == 422

    # Over 100,000 records
    res_overflow = client.post("/scrape", json={"max_records": 250_000}, headers=headers)
    assert res_overflow.status_code == 422


def test_rate_limiter_enforcement():
    """Ensure rate limiter blocks rapid bursts with 429 Too Many Requests."""
    headers = {"X-API-Key": DEFAULT_DEV_KEY}
    payload = {"state": "ACT", "max_records": 10, "dry_run": True}

    # Exhaust configured rate limit (15 requests)
    for _ in range(15):
        res = client.post("/scrape", json=payload, headers=headers)
        assert res.status_code == 202

    # 16th request must trigger 429
    res_limited = client.post("/scrape", json=payload, headers=headers)
    assert res_limited.status_code == 429
    assert "Rate limit exceeded" in res_limited.json()["detail"]
    assert "Retry-After" in res_limited.headers


def test_job_status_and_dashboard():
    """Test retrieving job status and dashboard summary."""
    headers = {"X-API-Key": DEFAULT_DEV_KEY}

    # Unknown job -> 404
    res_404 = client.get("/scrape/status/job_nonexistent", headers=headers)
    assert res_404.status_code == 404

    # Trigger job
    res_trigger = client.post("/scrape", json={"state": "QLD", "max_records": 20}, headers=headers)
    job_id = res_trigger.json()["job_id"]

    # Known job -> 200
    res_status = client.get(f"/scrape/status/{job_id}", headers=headers)
    assert res_status.status_code == 200
    assert res_status.json()["job_id"] == job_id

    # Dashboard endpoint
    res_dash = client.get("/dashboard", headers=headers)
    assert res_dash.status_code == 200
    dash_data = res_dash.json()
    assert "target_volume" in dash_data
    assert "throughput_records_sec" in dash_data
    assert "estimated_cost_usd" in dash_data
