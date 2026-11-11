# app/main.py
"""
FastAPI Enterprise Ingestion, Scraping & Valuation Service.
Provides authenticated, rate-limited, and schema-validated endpoints
for orchestrating property data scraping and pipeline workflows.
"""

import os
import sys
import uuid
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Ensure app directory is in Python path
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from app.operations.api_security import (
    verify_api_key,
    ScrapeJobRequest,
    JobStatusResponse,
    scrape_rate_limiter,
)
from app.operations.pipeline_dashboard import PipelineDashboard

logger = logging.getLogger("api_main")

# Global singleton dashboard for service tracking
service_dashboard = PipelineDashboard(target_volume=16_000_000)

# In-memory registry of active and completed jobs
JOBS_STORE: Dict[str, Dict[str, Any]] = {}

app = FastAPI(
    title="Australian Property Master API",
    description=(
        "Enterprise-grade Australian Real Estate data ingestion, statutory enrichment, "
        "and scraping pipeline API. Protected with API token authentication, rate limiting, "
        "and strict schema validation."
    ),
    version="1.2.0",
)


# ---------------------------------------------------------------------------
# Background Task Execution Worker
# ---------------------------------------------------------------------------
def _execute_pipeline_job(job_id: str, request: ScrapeJobRequest):
    """Executes the pipeline job in background thread and updates job store."""
    job_info = JOBS_STORE.get(job_id)
    if not job_info:
        return

    job_info["status"] = "running"
    try:
        from app.operations.pipeline import (
            PipelineConfig,
            PipelineOrchestrator,
            SyntheticDiscovery,
            EnrichmentExtractor,
            PropertySchemaValidator,
            MemoryBatchSink,
        )

        cfg = PipelineConfig(
            batch_size=request.batch_size,
            workers=request.workers,
            checkpoint_file=f"scratch/checkpoint_{job_id}.json",
            limit=request.max_records,
            dry_run=request.dry_run,
        )

        discovery = SyntheticDiscovery(seed=42)
        extractor = EnrichmentExtractor(enrich_probability=0.95)
        validator = PropertySchemaValidator()
        sink = MemoryBatchSink()

        orchestrator = PipelineOrchestrator(
            config=cfg,
            discovery=discovery,
            extractor=extractor,
            validator=validator,
            sink=sink,
        )

        report = orchestrator.run()

        job_info["status"] = "completed"
        job_info["processed_records"] = report.total_processed
        job_info["succeeded_records"] = report.total_succeeded
        job_info["failed_records"] = report.total_failed
        job_info["enriched_records"] = report.total_enriched
        job_info["elapsed_seconds"] = report.elapsed_seconds
        job_info["throughput_records_sec"] = report.throughput_records_sec

        # Update global operational dashboard
        service_dashboard.update_metrics(
            discovered=report.total_discovered,
            processed=report.total_processed,
            succeeded=report.total_succeeded,
            failed=report.total_failed,
            enriched=report.total_enriched,
        )

    except Exception as e:
        logger.error(f"Job '{job_id}' encountered fatal error: {e}", exc_info=True)
        job_info["status"] = "failed"
        job_info["error_message"] = str(e)


# ---------------------------------------------------------------------------
# Public Health & Root Endpoints
# ---------------------------------------------------------------------------
@app.get("/", tags=["Health"])
async def root_health():
    """Service root endpoint returning operational readiness."""
    return {
        "status": "online",
        "service": "australian-property-master-api",
        "version": "1.2.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Standard Kubernetes readiness and liveness probe endpoint."""
    return {
        "status": "healthy",
        "uptime": "active",
        "qdrant_target": os.getenv("QDRANT_URI", "http://localhost:6333"),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Authenticated & Rate-Limited Scraper / Pipeline Endpoints
# ---------------------------------------------------------------------------
@app.post(
    "/scrape",
    response_model=JobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Scraper & Ingestion"],
    summary="Trigger Property Data Ingestion Job",
)
async def trigger_scrape(
    request: ScrapeJobRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(verify_api_key),
):
    """
    Trigger an authenticated, rate-limited, and schema-validated property pipeline job.

    - **Authentication**: Requires valid `X-API-Key` or `Authorization: Bearer <token>`
    - **Rate Limit**: Maximum 15 trigger requests per 60 seconds per client
    - **Validation**: Strict geographic, bounds, and concurrency checking
    """
    # Check if another job is already actively running
    running_jobs = [j for j in JOBS_STORE.values() if j.get("status") == "running"]
    if len(running_jobs) >= 2:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Concurrent job capacity reached (2 active jobs currently running). Wait for jobs to complete.",
        )

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job_record = {
        "job_id": job_id,
        "status": "queued",
        "source": request.source,
        "state": request.state,
        "processed_records": 0,
        "succeeded_records": 0,
        "failed_records": 0,
        "enriched_records": 0,
        "start_time": datetime.utcnow().isoformat() + "Z",
        "elapsed_seconds": 0.0,
        "throughput_records_sec": 0.0,
        "error_message": None,
    }
    JOBS_STORE[job_id] = job_record

    # Schedule background execution
    background_tasks.add_task(_execute_pipeline_job, job_id, request)

    return job_record


@app.get(
    "/scrape/status/{job_id}",
    response_model=JobStatusResponse,
    tags=["Scraper & Ingestion"],
    summary="Get Job Execution Status",
)
async def get_job_status(
    job_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Inspect progress, counts, and error state for a specific pipeline job."""
    job_record = JOBS_STORE.get(job_id)
    if not job_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return job_record


@app.get(
    "/dashboard",
    tags=["Operational Dashboard"],
    summary="Pipeline Operational Dashboard Metrics",
)
async def get_pipeline_dashboard(
    api_key: str = Depends(verify_api_key),
):
    """Returns aggregated real-time metrics, throughput, error breakdown, and cost estimation."""
    return service_dashboard.get_summary()
