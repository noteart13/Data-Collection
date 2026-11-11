"""
API Security, Authentication, Rate Limiting & Input Validation Module.
Provides token authentication, IP-based sliding window rate limiting,
and request schema validation for data scraping and ingestion endpoints.
"""

import os
import time
import logging
from collections import defaultdict, deque
from typing import Optional, List, Dict, Any, Literal
from fastapi import Request, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("api_security")

# Environment API Key lookup
DEFAULT_DEV_KEY = "dev-property-master-key-2026"
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)
bearer_security = HTTPBearer(auto_error=False)

VALID_AU_STATES = {"NSW", "VIC", "QLD", "WA", "SA", "ACT", "TAS", "NT", "OT"}


class APIRateLimiter:
    """
    In-memory Sliding Window Rate Limiter.
    Tracks requests per client identifier within a given rolling time window.
    """

    def __init__(self, requests_limit: int = 10, window_seconds: int = 60):
        self.requests_limit = requests_limit
        self.window_seconds = window_seconds
        self.client_records: Dict[str, deque] = defaultdict(deque)

    def is_allowed(self, client_id: str) -> tuple[bool, int, float]:
        """
        Check if request is allowed.
        Returns: (allowed: bool, remaining_requests: int, retry_after_sec: float)
        """
        now = time.time()
        window_start = now - self.window_seconds
        records = self.client_records[client_id]

        # Purge timestamps outside current rolling window
        while records and records[0] < window_start:
            records.popleft()

        if len(records) >= self.requests_limit:
            oldest = records[0]
            retry_after = round(max(0.0, oldest + self.window_seconds - now), 2)
            return False, 0, retry_after

        records.append(now)
        remaining = self.requests_limit - len(records)
        return True, remaining, 0.0

    def reset(self):
        """Clear all rate limit records (useful for testing)."""
        self.client_records.clear()


# Default limiter: 15 trigger requests per 60 seconds per client
scrape_rate_limiter = APIRateLimiter(requests_limit=15, window_seconds=60)


def get_configured_api_key() -> str:
    """Get expected API key from environment, defaulting to dev fallback with warning."""
    configured_key = os.getenv("SCRAPER_API_KEY") or os.getenv("API_KEY")
    if not configured_key:
        configured_key = DEFAULT_DEV_KEY
    return configured_key


async def verify_api_key(
    request: Request,
    header_key: Optional[str] = Security(api_key_header),
    bearer_creds: Optional[HTTPAuthorizationCredentials] = Security(bearer_security),
) -> str:
    """
    Authenticate incoming request using either X-API-Key header or Bearer token.
    Raises HTTP 401 Unauthorized if invalid or missing.
    """
    expected_key = get_configured_api_key()

    # Extract token from header or bearer
    provided_key = None
    if header_key:
        provided_key = header_key.strip()
    elif bearer_creds and bearer_creds.credentials:
        provided_key = bearer_creds.credentials.strip()

    if not provided_key or provided_key != expected_key:
        logger.warning(f"Unauthorized API access attempt from {request.client.host if request.client else 'unknown'}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API authentication credentials. Provide valid 'X-API-Key' or 'Authorization: Bearer <key>' header.",
            headers={"WWW-Authenticate": "ApiKey, Bearer"},
        )

    # Enforce rate limiting per client IP or key
    client_id = provided_key[:8] + (request.client.host if request.client else "unknown")
    allowed, remaining, retry_after = scrape_rate_limiter.is_allowed(client_id)
    if not allowed:
        logger.warning(f"Rate limit exceeded for client {client_id}. Retry after {retry_after}s")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {scrape_rate_limiter.requests_limit} requests per {scrape_rate_limiter.window_seconds}s.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    return provided_key


# ---------------------------------------------------------------------------
# Input Validation & Job Request Schemas
# ---------------------------------------------------------------------------
class ScrapeJobRequest(BaseModel):
    """
    Strict input validation model for triggering property scraping and ingestion jobs.
    """
    state: Optional[str] = Field(
        None,
        description="Australian State or Territory (e.g. NSW, VIC, QLD, WA, SA, ACT, TAS, NT)"
    )
    suburbs: Optional[List[str]] = Field(
        None,
        description="Optional list of specific suburb names to filter scraping",
        max_length=100
    )
    max_records: int = Field(
        default=1000,
        ge=1,
        le=100000,
        description="Maximum records to process in this job (1 to 100,000)"
    )
    batch_size: int = Field(
        default=100,
        ge=10,
        le=2000,
        description="Batch size for vector upsert and storage sinks"
    )
    workers: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Number of concurrent worker threads (1 to 16)"
    )
    dry_run: bool = Field(
        default=False,
        description="When True, executes discovery and validation without persisting to Qdrant/Storage"
    )
    source: Literal["statutory_open_data", "gnaf", "nsw_valuer_general", "act_cadastre", "web_portal", "synthetic_test"] = Field(
        default="statutory_open_data",
        description="Selected data pipeline source"
    )
    priority: Literal["low", "normal", "high"] = Field(
        default="normal",
        description="Job scheduling priority"
    )

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            norm = v.strip().upper()
            if norm not in VALID_AU_STATES:
                raise ValueError(f"Invalid state '{v}'. Must be one of: {sorted(VALID_AU_STATES)}")
            return norm
        return v

    @field_validator("suburbs")
    @classmethod
    def validate_suburbs(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v:
            cleaned = [s.strip() for s in v if s and s.strip()]
            if not cleaned:
                return None
            return cleaned[:100]
        return v


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    source: str
    state: Optional[str] = None
    processed_records: int = 0
    succeeded_records: int = 0
    failed_records: int = 0
    enriched_records: int = 0
    start_time: Optional[str] = None
    elapsed_seconds: float = 0.0
    throughput_records_sec: float = 0.0
    error_message: Optional[str] = None
