#!/usr/bin/env python3
import asyncio
import json
import os
import time
import shutil
import argparse
import logging
import glob
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from operations.crawl4ai import(
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    LLMExtractionStrategy,
    JsonCssExtractionStrategy,
    CrawlResult,
    DefaultMarkdownGenerator,
    PruningContentFilter,
    CacheMode,
    LLMConfig
)
from operations.scraping_by_suburb import scraping_by_suburb
from qdrant_client import AsyncQdrantClient as Qdrantclient
# ─── CONSTANTS ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
INPUT_JSON   = PROJECT_ROOT / "data" / "suburb_city_mapping_2.json"
#INPUT_JSON = PROJECT_ROOT / "data" / "brisbane_city.json"
SLICE_SIZE   = 500
MAX_RETRIES  = 3
# ──────────────────────────────────────────────────────────────────────────────

# Allow override via env-var; defaults to 2 if not set
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "5"))

def parse_args():
    p = argparse.ArgumentParser(description="Scrape property listings by suburb.")
    p.add_argument("-b", "--batch-order", type=int, default=0,
                   help="Batch index (0-based). Each batch processes 1000 suburbs.")
    p.add_argument("-c", "--max-concurrency", type=int, default=2,
                   help="Number of suburbs to process in parallel.")
    p.add_argument("-s", "--status", type=str, default='sale',
                   help="Scrape data (rent or sale)")
    return p.parse_args()


def setup_logger(log_file: Path) -> logging.Logger:
    """Thread-safe file logger (INFO level) with timestamps."""
    logger = logging.getLogger(f"scrape-{log_file}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    os.makedirs(log_file.parent, exist_ok=True)
    fh = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    fh.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(fh)
    return logger


def slugify(suburb: str, state: str, pc: str, suburb_dir: Path) -> str:
    """Match original suburb_csv naming: 'Suburb, State, Postcode' -> 'Suburb_State_Postcode.csv'"""
    raw = f"{suburb}, {state}, {pc}"
    fn  = raw.replace(', ', '_')
    return os.path.join(suburb_dir, fn + ".csv")


def get_tasks(input_json: Path, suburb_dir: Path, from_: int, to_: int) -> list[dict]:
    """Read JSON and produce list of {query, out_csv} (sliced)."""
    with open(input_json) as f:
        dd = json.load(f)

    dd = dd[from_ : min(to_, len(dd))]
    os.makedirs(suburb_dir, exist_ok=True)

    tasks = []
    for e in dd:
        suburb = e["suburb"]
        state  = e["state"]
        pc     = e["postcode"]
        query  = f"property for sale in {suburb}, {state}, {pc}"
        out_csv = slugify(suburb, state, pc, suburb_dir)
        tasks.append({"query": query, "out_csv": out_csv})
    return tasks


def _cleanup_chrome_tmp():
    """Best-effort cleanup of Chrome temp dirs (no glob in rmtree, so expand first)."""
    for pattern in ("/tmp/.org.chromium.Chromium*", "/tmp/.com.google.Chrome*"):
        for p in glob.glob(pattern):
            shutil.rmtree(p, ignore_errors=True)


async def scrape_one(sem: asyncio.Semaphore,
                     executor: ThreadPoolExecutor,
                     logger: logging.Logger,
                     task: dict, idx: int, total: int,
                     status: str):
    """Bound by semaphore; runs scraping_by_suburb in executor with retries."""
    async with sem:
        q  = task["query"]
        fn = task["out_csv"]
        print(task)
        label = f"[{idx}/{total}]"
        start = time.time()
        browser_config =  BrowserConfig(
            use_persistent_context=False,  
            user_data_dir=None,  

            extra_args=[
                "--disable-cache",  
                "--disable-application-cache", 
                "--disable-offline-load-stale-cache",  
                "--disk-cache-size=0",  
                "--media-cache-size=0",  
                "--disable-background-networking", 
                "--disable-background-sync",  
                "--disable-extensions",  
                "--disable-plugins", 
                "--disable-dev-shm-usage", 
                "--memory-pressure-off",  
                "--max_old_space_size=512",  
                "--incognito",  
                "--no-first-run",  
                "--no-default-browser-check",  
                "--disable-default-apps",  
                "--disable-sync", 
                "--disable-translate",  
                "--disable-features=TranslateUI", 
                "--disable-ipc-flooding-protection",
            ],
            accept_downloads=False,
            downloads_path=None,
            storage_state=None,  
            cookies=[], 

            headless=True, 
            
            light_mode=True,  
        )
        
        logger.info(f"{label} START query={q} out={os.path.basename(fn)}")
        print(f"{label} START {q}")
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                    loop = asyncio.get_running_loop()
                    # Run the coroutine in the thread executor
                    
                    await loop.run_in_executor(
                        executor,
                        lambda: asyncio.run(scraping_by_suburb(q, fn, link="",status=status))
                    )

                    status = "OK" if os.path.exists(fn) else "EMPTY"
                    dur = time.time() - start
                    logger.info(
                        f"{label} DONE status={status} file={os.path.basename(fn)} "
                        f"duration={dur:.1f}s attempts={attempt}"
                    )
                    print(f"{label} {'✔' if status=='OK' else '⚠️ EMPTY'} {os.path.basename(fn)} ({dur:.1f}s)")
                    break

            except Exception as e:
                logger.warning(f"{label} ERROR attempt={attempt} query={q} err={e!r}")
                print(f"{label} ❌ ERROR attempt {attempt} for {q}: {e}")
                _cleanup_chrome_tmp()

                if attempt < MAX_RETRIES + 1:
                    await asyncio.sleep(1)
                else:
                    logger.error(f"{label} GIVEUP query={q}")
                    print(f"{label} ✖ GIVING UP on {q}")


async def main():
    args = parse_args()
    batch_order   = args.batch_order
    status = args.status
    suburb_dir    = PROJECT_ROOT / "data" / f"batch_{batch_order}"
    log_file      = PROJECT_ROOT / "data" / f"log_progress_step1_batch_{batch_order}.txt"

    from_ = batch_order * SLICE_SIZE
    to_   = from_ + SLICE_SIZE

    logger  = setup_logger(log_file)
    tasks   = get_tasks(INPUT_JSON, suburb_dir, from_, to_)
    total   = len(tasks)
    print(status)
    print(type(status))
    logger.info(f"BEGIN batch={batch_order} total={total} concurrency={MAX_CONCURRENCY} range=[{from_}:{to_}]")
    print(f"Scraping {total} suburbs (concurrent={MAX_CONCURRENCY})…")

    sem      = asyncio.Semaphore(MAX_CONCURRENCY)
    executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)
    
    try:
        runners = [
            asyncio.create_task(scrape_one(sem, executor, logger, task, i+1, total, status))
            for i, task in enumerate(tasks)
        ]
        await asyncio.gather(*runners)
    finally:
        executor.shutdown(wait=True)
        logger.info("END")


if __name__ == "__main__":
    asyncio.run(main())
