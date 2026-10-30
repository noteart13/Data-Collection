# app/main.py

import os
import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

# Ensure app directory is in Python path for local or container execution
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

try:
    from scraping_all_suburbs_fastapi import get_env_paths, scrape_all_suburbs
except ImportError:
    from app.scraping_all_suburbs_fastapi import get_env_paths, scrape_all_suburbs


class ScrapeResult(BaseModel):
    status: str
    invalid_url_file: str
    progress_csv: str
    name_fail_csv: str


app = FastAPI(
    title="Suburb Scraper API",
    description="Trigger and monitor suburb scraping and property data ingestion jobs",
    version="1.1.0",
)


@app.get("/", tags=["health"])
async def root_health():
    return {"status": "ok", "service": "suburb-scraper"}


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "service": "suburb-scraper"}


@app.post("/scrape", response_model=ScrapeResult, tags=["scraper"])
async def trigger_scrape(background_tasks: BackgroundTasks):
    """
    Trigger a background suburb scraping task.
    """
    try:
        cfg = get_env_paths()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Configuration error: {e}")

    background_tasks.add_task(scrape_all_suburbs, cfg)
    return {
        "status": "queued",
        "invalid_url_file": cfg["invalid_url_file"],
        "progress_csv": cfg["progress_csv"],
        "name_fail_csv": cfg["name_fail_csv"]
    }
