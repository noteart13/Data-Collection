import asyncio
import csv
import json
import math
import os
import random
import re
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from property_com_address_export import extract_property_paths

UTILS_DIR = APP_DIR / "utils"
OUTPUT_CSV = "urls.csv"
LOG_DIR = PROJECT_ROOT / "logs"

STATE_MAPPING_FILES = {
    "NSW": "suburb_city_mapping_v1_NSW_ACT.json",
    "ACT": "suburb_city_mapping_v1_NSW_ACT.json",
    "VIC": "suburb_city_mapping_v1_VIC_SA_TAS.json",
    "SA": "suburb_city_mapping_v1_VIC_SA_TAS.json",
    "TAS": "suburb_city_mapping_v1_VIC_SA_TAS.json",
    "QLD": "suburb_city_mapping_v1_QLD_NT_WA.json",
    "NT": "suburb_city_mapping_v1_QLD_NT_WA.json",
    "WA": "suburb_city_mapping_v1_QLD_NT_WA.json",
}

os.environ["NODRIVER_HEADLESS"] = "false"
os.environ["NODRIVER_KEEP_BROWSER"] = "true"
LAST_REQUEST_STARTED_AT = 0.0
REQUEST_START_LOCK = asyncio.Lock()



def detect_installed_chrome_version():
    chrome_root = Path(r"C:\Program Files\Google\Chrome\Application")
    versions = []
    if chrome_root.exists():
        for child in chrome_root.iterdir():
            if child.is_dir() and re.fullmatch(r"\d+(?:\.\d+){3}", child.name):
                versions.append(child.name)
    if not versions:
        return "150.0.0.0"
    return max(versions, key=lambda value: tuple(map(int, value.split("."))))


CHROME_VERSION = detect_installed_chrome_version()
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{CHROME_VERSION} Safari/537.36"
)


class PropertyPageLoadError(RuntimeError):
    pass


async def wait_before_request():
    global LAST_REQUEST_STARTED_AT
    async with REQUEST_START_LOCK:
        now = asyncio.get_running_loop().time()
        minimum_interval = random.uniform(2.0, 3.5)
        remaining = minimum_interval - (now - LAST_REQUEST_STARTED_AT)
        if LAST_REQUEST_STARTED_AT and remaining > 0:
            await asyncio.sleep(remaining)
        LAST_REQUEST_STARTED_AT = asyncio.get_running_loop().time()


async def scrape(url):
    from operations.researcher.scraper.scaper import Scraper
    from operations.utils.workers import WorkerPool

    if isinstance(url, str):
        urls = [url]
    elif isinstance(url, list):
        urls = url
    else:
        raise TypeError("Url must be a string or a list of strings")

    await wait_before_request()
    worker_pool = WorkerPool(max_workers=1)

    scraper = Scraper(
        urls=urls,
        user_agent=USER_AGENT,
        scraper="nodriver",
        worker_pool=worker_pool,
        max_retries=1,
        retry_delay_seconds=5.0,
        scrape_timeout_seconds=60.0,
    )

    results = await scraper.run()
    return results


def validate_loaded_page(result, url, require_property_url=False):
    html = result.get("html") or ""
    raw_content = result.get("raw_content") or ""
    title = result.get("title") or ""
    page_text = f"{title}\n{raw_content}\n{html[:10000]}".lower()
    block_markers = (
        "too many requests",
        "access denied",
        "rate limit",
        "verify you are human",
        "checking your browser",
        "x-kpsdk",
    )
    if any(marker in page_text for marker in block_markers):
        raise PropertyPageLoadError(
            f"property.com.au returned a block/challenge page: {url}"
        )
    if len(html) < 500 or len(raw_content) < 100:
        raise PropertyPageLoadError(
            f"property.com.au returned a blank/incomplete page: {url}"
        )
    return result

def load_existing_urls(output_csv=OUTPUT_CSV):
    urls = set()

    if not Path(output_csv).exists():
        return urls

    with open(output_csv, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            property_url = row.get("url") or row.get("urls") or row.get("address")
            if property_url:
                urls.add(property_url.strip())

    return urls

def save_urls(urls, output_csv=OUTPUT_CSV):
    output_path = Path(output_csv)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")

    with open(temporary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["url"])

        for property_url in sorted(urls):
            writer.writerow([property_url])

    temporary_path.replace(output_path)


def load_progress(status, state="", worker_id=None, worker_count=1):
    """Load completed suburbs and the last successful resume checkpoint."""
    done = set()
    last_done_key = None

    # Load from unified log first if it exists
    unified_log = get_log_path(status, state)
    if unified_log.exists():
        with open(unified_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("status") == "done" and item.get("suburb_key"):
                    done.add(item["suburb_key"].lower())

    # Then load from worker log if running as multi-worker
    worker_log = get_log_path(status, state, worker_id, worker_count)
    if worker_log.exists() and worker_log != unified_log:
        with open(worker_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("status") == "done" and item.get("suburb_key"):
                    last_done_key = item["suburb_key"].lower()
                    done.add(last_done_key)

    return done, last_done_key


def load_done_suburbs(status, state="", worker_id=None, worker_count=1):
    done, _ = load_progress(status, state, worker_id, worker_count)
    return done


def resume_after_checkpoint(addrs, checkpoint_key):
    """Return input rows after the last completed suburb from the log."""
    if not checkpoint_key:
        return addrs, 0

    for index, address in enumerate(addrs):
        if suburb_key(address) == checkpoint_key:
            return addrs[index + 1 :], index + 1

    print(
        f"Warning: checkpoint {checkpoint_key!r} was not found in the input; "
        "falling back to done-suburb filtering.",
        flush=True,
    )
    return addrs, 0


def suburb_key(ad):
    return f"{ad['suburb']}|{ad['state']}|{ad['postcode']}".lower()


def get_log_path(status, state="", worker_id=None, worker_count=1):
    safe_status = status.replace(" ", "_").lower()
    prefix = f"{state.lower()}_" if state else ""
    worker_suffix = f"_worker_{worker_id}" if worker_count > 1 and worker_id is not None else ""
    return LOG_DIR / f"get_url_{prefix}{safe_status}{worker_suffix}.jsonl"


def append_done_log(status, ad, found_count, state="", worker_id=None, worker_count=1):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = get_log_path(status, state, worker_id, worker_count)
    payload = {
        "status": "done",
        "suburb_key": suburb_key(ad),
        "suburb": ad["suburb"],
        "state": ad["state"],
        "postcode": ad["postcode"],
        "found_count": found_count,
    }

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def append_failed_log(status, ad, error, state="", worker_id=None, worker_count=1):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = get_log_path(status, state, worker_id, worker_count)
    payload = {
        "status": "failed",
        "suburb_key": suburb_key(ad),
        "suburb": ad["suburb"],
        "state": ad["state"],
        "postcode": ad["postcode"],
        "reason": f"{type(error).__name__}: {error}",
    }

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def extract_total_results(raw_content: str, html: str = ""):
    content = f"{raw_content}\n{html}"
    patterns = [
        r'Viewing\s+\d+\s*-\s*\d+\s+of\s+(\d+)\s+results',
        r'\b\d+\s*-\s*\d+\s+of\s+(\d+)\s+results',
        r'Viewing\s+(\d+)\s+results',
        r'\b(\d+)\s+results\b'
    ]

    for pattern in patterns:
        m = re.search(pattern, content, re.IGNORECASE)
        if m:
            return int(m.group(1))

    return None


def search_url(ad, status):
    suburb = ad["suburb"].replace(" ", "+")
    base = (
        f"https://www.property.com.au/search/"
        f"?locations={suburb}%2C+{ad['state']}+{ad['postcode']}"
    )
    status_parameters = {
        "sale": "",
        "sold": "&propertyStatus=SOLD",
        "off market": "&propertyStatus=OFF_MARKET",
    }
    if status not in status_parameters:
        raise ValueError(f"Unsupported status: {status}")
    return base + status_parameters[status]


async def process_suburb(ad, status, property_urls, output_csv=OUTPUT_CSV):
    """Scrape one suburb; callers decide whether an error stops the batch."""
    base_url = search_url(ad, status)
    first_page_results = await scrape(base_url)
    if not first_page_results:
        raise PropertyPageLoadError(
            f"property.com.au returned no usable data: {base_url}"
        )

    first_page = validate_loaded_page(first_page_results[0], base_url)
    total_results = extract_total_results(
        first_page.get("raw_content") or "",
        first_page.get("html") or "",
    )
    if total_results is None:
        raise PropertyPageLoadError(
            f"page loaded but result count was not found: {base_url}"
        )

    first_page_paths = extract_property_paths(first_page.get("html") or "")
    if total_results > 0 and not first_page_paths:
        raise PropertyPageLoadError(
            f"search page loaded without any property URLs: {base_url}"
        )

    before_count = len(property_urls)
    property_urls.update(first_page_paths)
    save_urls(property_urls, output_csv)

    total_pages = max(1, math.ceil(total_results / 20))
    for page_number in range(2, total_pages + 1):
        page_url = f"{base_url}&pageNumber={page_number}"
        page_results = await scrape(page_url)
        if not page_results:
            raise PropertyPageLoadError(
                f"property.com.au returned no usable data: {page_url}"
            )
        loaded_page = validate_loaded_page(
            page_results[0],
            page_url,
            require_property_url=True,
        )
        property_urls.update(extract_property_paths(loaded_page.get("html") or ""))
        save_urls(property_urls, output_csv)

    return len(property_urls) - before_count


def merge_worker_csvs(state, status, worker_count, target_csv=None):
    safe_status = status.replace(" ", "_").lower()
    state_lower = state.lower()
    if not target_csv:
        target_csv = PROJECT_ROOT / f"urls_{state_lower}_{safe_status}.csv"
    else:
        target_csv = Path(target_csv)

    all_urls = set()
    if target_csv.exists():
        with open(target_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                u = r.get("url") or r.get("urls") or r.get("address")
                if u:
                    all_urls.add(u.strip())

    for w in range(worker_count):
        w_file = PROJECT_ROOT / f"urls_{state_lower}_{safe_status}_worker_{w}.csv"
        if w_file.exists():
            with open(w_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    u = r.get("url") or r.get("urls") or r.get("address")
                    if u:
                        all_urls.add(u.strip())

    save_urls(all_urls, target_csv)
    print(f"Successfully merged {len(all_urls):,} unique URLs into {target_csv.name}")
    return len(all_urls)


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape property URLs by suburb from property.com.au"
    )
    parser.add_argument(
        "--state",
        default="WA",
        help="Target state code (e.g. NSW, VIC, QLD, WA). Default: WA",
    )
    parser.add_argument(
        "--status",
        default="sold",
        choices=["sale", "sold", "off market"],
        help="Property status (sale, sold, off market). Default: sold",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: urls_<state>_<status>.csv)",
    )
    parser.add_argument(
        "--mapping",
        default=None,
        help="Path to suburb mapping JSON file (auto-detected if not specified)",
    )
    parser.add_argument(
        "--worker-id",
        type=int,
        default=0,
        help="Zero-based worker number (default: 0).",
    )
    parser.add_argument(
        "--worker-count",
        type=int,
        default=1,
        help="Total number of parallel worker processes (default: 1).",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Merge worker output CSVs into the main output CSV and exit.",
    )
    return parser.parse_args()


async def main(state="WA", status="sold", output_csv=None, mapping_path=None, worker_id=0, worker_count=1):
    state = state.upper()
    status = status.lower()

    if not output_csv:
        if worker_count > 1:
            output_csv = PROJECT_ROOT / f"urls_{state.lower()}_{status.replace(' ', '_')}_worker_{worker_id}.csv"
        else:
            output_csv = PROJECT_ROOT / f"urls_{state.lower()}_{status.replace(' ', '_')}.csv"
    else:
        output_csv = Path(output_csv)
        if not output_csv.is_absolute():
            output_csv = PROJECT_ROOT / output_csv

    if not mapping_path:
        filename = STATE_MAPPING_FILES.get(state)
        if not filename:
            raise ValueError(
                f"No default mapping file found for state: {state}. Please specify --mapping <file.json>"
            )
        mapping_path = UTILS_DIR / filename
    else:
        mapping_path = Path(mapping_path)
        if not mapping_path.is_absolute():
            mapping_path = PROJECT_ROOT / mapping_path

    if not mapping_path.exists():
        raise FileNotFoundError(f"Mapping file not found: {mapping_path}")

    print(f"Reading suburbs from: {mapping_path}")
    with open(mapping_path, "r", encoding="utf-8") as f:
        all_addrs = json.load(f)

    all_state_addrs = [ad for ad in all_addrs if ad.get("state", "").upper() == state]
    if worker_count > 1:
        addrs = [ad for idx, ad in enumerate(all_state_addrs) if idx % worker_count == worker_id]
        print(f"Worker {worker_id}/{worker_count} assigned {len(addrs)} of {len(all_state_addrs)} suburbs.")
    else:
        addrs = all_state_addrs

    print(f"State: {state} (Total suburbs assigned: {len(addrs)})")
    print(f"Status: {status}")
    print(f"Output CSV: {output_csv}")

    done_suburbs, last_done_key = load_progress(status, state, worker_id, worker_count)
    property_urls = load_existing_urls(output_csv)
    print(f"Existing URLs loaded from {output_csv.name}: {len(property_urls)}")

    addrs, resume_index = resume_after_checkpoint(addrs, last_done_key)
    if last_done_key:
        if addrs:
            next_address = addrs[0]
            print(
                f"Resume after checkpoint: {last_done_key} (input index "
                f"{resume_index - 1}); next suburb: "
                f"{next_address['suburb']}, {next_address['state']} "
                f"{next_address['postcode']}",
                flush=True,
            )
        else:
            print(
                f"All input suburbs are complete through {last_done_key}.",
                flush=True,
            )

    for ad in addrs:
        key = suburb_key(ad)
        if key in done_suburbs:
            print(f"Skip done: {ad['suburb']}, {ad['state']} {ad['postcode']}")
            continue

        try:
            found_count = await process_suburb(ad, status, property_urls, output_csv)
        except Exception as error:
            append_failed_log(status, ad, error, state, worker_id, worker_count)
            print(
                f"Skip failed: {ad['suburb']}, {ad['state']} "
                f"{ad['postcode']} - {type(error).__name__}: {error}",
                flush=True,
            )
            continue

        append_done_log(status, ad, found_count, state, worker_id, worker_count)
        done_suburbs.add(key)
        print(
            f"Done: {ad['suburb']}, {ad['state']} {ad['postcode']} - "
            f"new URLs: {found_count}, total URLs: {len(property_urls)}"
        )


async def run(args):
    from operations.researcher.scraper.browser.nodriver_scraper import NoDriverScraper

    if args.merge:
        merge_worker_csvs(args.state, args.status, args.worker_count, args.output)
        return

    print(f"Browser: installed Google Chrome {CHROME_VERSION} (visible, reused)")
    try:
        await main(
            state=args.state,
            status=args.status,
            output_csv=args.output,
            mapping_path=args.mapping,
            worker_id=args.worker_id,
            worker_count=args.worker_count,
        )
    finally:
        await NoDriverScraper.close_all_browsers()


if __name__ == "__main__":
    cli_args = parse_args()
    asyncio.run(run(cli_args))

