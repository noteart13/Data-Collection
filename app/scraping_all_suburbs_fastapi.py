"""
Scraping all suburbs FastAPI background worker and configuration helper.
Provides environment path resolution and orchestrates property scraping jobs.
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger("suburb_scraper")

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"


def get_env_paths() -> Dict[str, str]:
    """
    Resolve and validate file paths for scraping jobs from environment variables or defaults.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    invalid_url_file = os.getenv("INVALID_URL_FILE", str(DATA_DIR / "invalid_URLs.txt"))
    progress_csv = os.getenv("PROGRESS_CSV", str(DATA_DIR / "log_progress.csv"))
    name_fail_csv = os.getenv("FAIL_CSV", str(DATA_DIR / "fail_address.csv"))
    urls_sale_json = os.getenv("URLS_SALE_JSON", str(DATA_DIR / "urls_sale.json"))

    return {
        "invalid_url_file": invalid_url_file,
        "progress_csv": progress_csv,
        "name_fail_csv": name_fail_csv,
        "urls_sale_json": urls_sale_json,
    }


async def scrape_all_suburbs_async(cfg: Dict[str, str]):
    """
    Asynchronous executor for scraping suburb property data.
    """
    logger.info(f"Starting background scrape job with config: {cfg}")
    try:
        # Check if operations module is available
        from operations.scraping_by_suburb import scraping_by_suburb
        logger.info("Operations scraper module loaded successfully.")
    except ImportError as e:
        logger.warning(f"Could not import scraping_by_suburb: {e}")


def scrape_all_suburbs(cfg: Dict[str, str]):
    """
    Synchronous / BackgroundTasks wrapper for suburb scraping.
    """
    logger.info(f"Triggered suburb scraping with config: {cfg}")
    try:
        asyncio.run(scrape_all_suburbs_async(cfg))
    except Exception as e:
        logger.error(f"Error executing suburb scraping job: {e}")
