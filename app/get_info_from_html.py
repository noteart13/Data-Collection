from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from get_html import (
    QDRANT_COLLECTION,
    first_scrape_result,
    map_property_html,
    scrape,
    upload_propertyforsale_to_qdrant,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_CSV = PROJECT_ROOT / "urls.csv"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
PROPERTY_BASE_URL = "https://www.property.com.au/"

# Reuse one healthy Chrome process across sequential URLs. The finally block in
# main closes it even when the batch is interrupted.
os.environ.setdefault("NODRIVER_KEEP_BROWSER", "true")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape property URLs, map PropertyMeta, and upload to Qdrant."
    )
    parser.add_argument("--input-csv", type=Path, default=DEFAULT_INPUT_CSV)
    parser.add_argument("--url-column", default="url")
    parser.add_argument("--datatype", default="salesold")
    parser.add_argument("--collection", default=QDRANT_COLLECTION)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument(
        "--checkpoint-db",
        type=Path,
        default=None,
        help="Shared SQLite checkpoint (default: <log-dir>/get_info_from_html.sqlite3).",
    )
    parser.add_argument("--worker-id", type=int, default=0)
    parser.add_argument("--worker-count", type=int, default=1)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most this many URLs assigned to the worker.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Number of URLs passed to one scrape() call (default: 5).",
    )
    parser.add_argument(
        "--mapping-concurrency",
        type=int,
        default=5,
        help="Maximum concurrent mapping/upload tasks (default: 5).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and map data but do not write to Qdrant.",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Ignore completed-URL checkpoints and process assigned URLs again.",
    )
    parser.add_argument(
        "--claim-timeout-minutes",
        type=int,
        default=360,
        help="Retry a URL left 'processing' by a dead worker after this many minutes.",
    )
    args = parser.parse_args()

    if args.worker_count < 1:
        parser.error("--worker-count must be at least 1")
    if not 0 <= args.worker_id < args.worker_count:
        parser.error("--worker-id must be between 0 and worker-count - 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if args.mapping_concurrency < 1:
        parser.error("--mapping-concurrency must be at least 1")
    if args.claim_timeout_minutes < 1:
        parser.error("--claim-timeout-minutes must be at least 1")
    if args.checkpoint_db is None:
        args.checkpoint_db = args.log_dir / "get_info_from_html.sqlite3"
    return args


def normalize_property_url(value: str) -> str:
    """Convert CSV paths and scheme-less values to an absolute HTTPS URL."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Property URL is empty")

    if value.startswith("//"):
        value = f"https:{value}"
    elif not re.match(r"^https?://", value, re.IGNORECASE):
        value = urljoin(PROPERTY_BASE_URL, value.lstrip("/"))

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid property URL: {value!r}")
    if not parsed.netloc.lower().endswith("property.com.au"):
        raise ValueError(f"Only property.com.au URLs are supported: {value!r}")
    return value


def load_urls(input_csv: Path, url_column: str) -> list[str]:
    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    urls: list[str] = []
    seen: set[str] = set()
    with input_csv.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or url_column not in reader.fieldnames:
            available = ", ".join(reader.fieldnames or []) or "none"
            raise ValueError(
                f"Column {url_column!r} not found in {input_csv}; "
                f"available columns: {available}"
            )

        for row_number, row in enumerate(reader, start=2):
            value = row.get(url_column)
            if not value or not value.strip():
                continue
            try:
                url = normalize_property_url(value)
            except ValueError as error:
                print(f"Skip CSV row {row_number}: {error}", file=sys.stderr)
                continue
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def worker_log_path(log_dir: Path, worker_id: int, worker_count: int) -> Path:
    suffix = f"_worker_{worker_id}" if worker_count > 1 else ""
    return log_dir / f"get_info_from_html{suffix}.jsonl"


def append_log(log_path: Path, status: str, url: str, **details) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "url": url,
        **details,
    }
    with log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        file.flush()
        os.fsync(file.fileno())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointStore:
    """Process-safe URL claims and durable completion state backed by SQLite."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS url_checkpoints (
                    url TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    run_id TEXT,
                    worker_id INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    details TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoint_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30, isolation_level=None)

    def import_jsonl_logs(self, log_dir: Path) -> int:
        """Import successful records from old/per-worker JSONL logs once safely."""
        migration_key = f"jsonl_import:{log_dir.resolve()}"
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            already_imported = connection.execute(
                "SELECT 1 FROM checkpoint_metadata WHERE key = ?",
                (migration_key,),
            ).fetchone()
            if already_imported:
                connection.commit()
                return 0

            records: dict[str, dict] = {}
            for log_path in log_dir.glob("get_info_from_html*.jsonl"):
                with log_path.open("r", encoding="utf-8") as file:
                    for line in file:
                        try:
                            item = json.loads(line)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if (
                            item.get("status") == "done"
                            and item.get("url")
                            and not item.get("dry_run", False)
                        ):
                            records[item["url"]] = item

            now = utc_now()
            imported = 0
            for url, details in records.items():
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO url_checkpoints
                        (url, status, attempts, updated_at, details)
                    VALUES (?, 'done', 1, ?, ?)
                    """,
                    (url, now, json.dumps(details, ensure_ascii=False, default=str)),
                )
                imported += cursor.rowcount
            connection.execute(
                "INSERT INTO checkpoint_metadata (key, value) VALUES (?, ?)",
                (migration_key, now),
            )
            connection.commit()
        return imported

    def completed_urls(self) -> set[str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT url FROM url_checkpoints WHERE status = 'done'"
            )
            return {row[0] for row in rows}

    def claim_urls(
        self,
        urls: list[str],
        *,
        run_id: str,
        worker_id: int,
        claim_timeout_minutes: int,
        restart: bool,
    ) -> list[str]:
        """Atomically claim URLs, excluding done or actively processed records."""
        now = datetime.now(timezone.utc)
        stale_before = (now - timedelta(minutes=claim_timeout_minutes)).isoformat()
        claimed: list[str] = []
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for url in urls:
                row = connection.execute(
                    "SELECT status, updated_at FROM url_checkpoints WHERE url = ?",
                    (url,),
                ).fetchone()
                if row:
                    status, updated_at = row
                    if status == "done" and not restart:
                        continue
                    if status == "processing" and updated_at >= stale_before:
                        continue

                connection.execute(
                    """
                    INSERT INTO url_checkpoints
                        (url, status, run_id, worker_id, attempts, updated_at, details)
                    VALUES (?, 'processing', ?, ?, 1, ?, NULL)
                    ON CONFLICT(url) DO UPDATE SET
                        status = 'processing',
                        run_id = excluded.run_id,
                        worker_id = excluded.worker_id,
                        attempts = url_checkpoints.attempts + 1,
                        updated_at = excluded.updated_at,
                        details = NULL
                    """,
                    (url, run_id, worker_id, now.isoformat()),
                )
                claimed.append(url)
            connection.commit()
        return claimed

    def finish(self, url: str, status: str, run_id: str, details: dict) -> None:
        """Finish only a claim owned by this run, preventing stale-worker writes."""
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE url_checkpoints
                SET status = ?, updated_at = ?, details = ?
                WHERE url = ? AND run_id = ? AND status = 'processing'
                """,
                (
                    status,
                    utc_now(),
                    json.dumps(details, ensure_ascii=False, default=str),
                    url,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Lost checkpoint claim for URL: {url}")


async def process_scrape_result(
    url: str,
    result: dict,
    *,
    datatype: str,
    collection: str,
    dry_run: bool,
) -> dict:
    """Map one already-scraped result and optionally upload it to Qdrant."""
    html = result.get("html") or ""
    if len(html) < 500:
        raise RuntimeError(
            f"Scraper returned blank/incomplete HTML ({len(html)} bytes)"
        )

    mapped_payload = await map_property_html(
        html=html,
        url=url,
        datatype=datatype,
    )

    property_payload = (mapped_payload.get("mapped") or {}).get("propertyforsale")
    if not property_payload:
        raise RuntimeError("Mapping returned no mapped.propertyforsale payload")
    if not mapped_payload.get("debug", {}).get("metadata_found"):
        raise RuntimeError("Property metadata was not found in the scraped HTML")
    if not property_payload.get("slug"):
        raise RuntimeError("Mapped property payload has no slug")

    point_id = None
    if not dry_run:
        point_id = await upload_propertyforsale_to_qdrant(
            mapped_payload,
            collection_name=collection,
        )

    return {
        "slug": property_payload.get("slug"),
        "street": property_payload.get("street"),
        "listing_option": property_payload.get("listingOption"),
        "html_length": len(html),
        "qdrant_collection": None if dry_run else collection,
        "qdrant_point_id": point_id,
        "dry_run": dry_run,
    }


async def process_url(
    url: str,
    *,
    datatype: str,
    collection: str,
    dry_run: bool,
) -> dict:
    """Compatibility helper for processing a single URL."""
    results = await scrape(url, max_workers=1)
    result = first_scrape_result(results)
    return await process_scrape_result(
        url,
        result,
        datatype=datatype,
        collection=collection,
        dry_run=dry_run,
    )


def iter_batches(values: list[str], batch_size: int):
    for start in range(0, len(values), batch_size):
        yield values[start : start + batch_size]


async def main() -> None:
    args = parse_args()
    args.log_dir.mkdir(parents=True, exist_ok=True)
    runtime_log_path = worker_log_path(
        args.log_dir,
        args.worker_id,
        args.worker_count,
    ).with_suffix(".runtime.log")
    logging.basicConfig(
        filename=runtime_log_path,
        filemode="a",
        format="%(asctime)s - %(process)d - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    from operations.researcher.scraper.browser.nodriver_scraper import (
        NoDriverScraper,
    )
    all_urls = load_urls(args.input_csv, args.url_column)
    assigned_urls = all_urls[args.worker_id :: args.worker_count]
    log_path = worker_log_path(args.log_dir, args.worker_id, args.worker_count)
    checkpoint_store = CheckpointStore(args.checkpoint_db)
    imported = checkpoint_store.import_jsonl_logs(args.log_dir)
    completed_urls = set() if args.restart else checkpoint_store.completed_urls()
    urls = [url for url in assigned_urls if url not in completed_urls]
    if args.limit is not None:
        urls = urls[: args.limit]
    run_id = str(uuid.uuid4())

    succeeded = 0
    failed = 0
    print(
        f"Worker {args.worker_id}/{args.worker_count}: processing {len(urls)} "
        f"pending of {len(assigned_urls)} assigned URLs; "
        f"skipped_done={len(set(assigned_urls) & completed_urls)}, "
        f"imported_logs={imported}, dry_run={args.dry_run}",
        flush=True,
    )

    try:
        processed = 0
        for batch_number, batch_urls in enumerate(
            iter_batches(urls, args.batch_size),
            start=1,
        ):
            batch_urls = checkpoint_store.claim_urls(
                batch_urls,
                run_id=run_id,
                worker_id=args.worker_id,
                claim_timeout_minutes=args.claim_timeout_minutes,
                restart=args.restart,
            )
            if not batch_urls:
                continue
            print(
                f"Batch {batch_number}: scraping {len(batch_urls)} URLs "
                f"in one call",
                flush=True,
            )
            try:
                batch_results = await scrape(
                    batch_urls,
                    max_workers=len(batch_urls),
                )
            except Exception as error:
                processed += len(batch_urls)
                for url in batch_urls:
                    failed += 1
                    error_details = {
                        "error": f"Batch scrape failed: {type(error).__name__}: {error}"
                    }
                    checkpoint_store.finish(url, "failed", run_id, error_details)
                    append_log(
                        log_path,
                        "failed",
                        url,
                        run_id=run_id,
                        worker_id=args.worker_id,
                        **error_details,
                    )
                print(f"Skip failed batch: {error}", file=sys.stderr, flush=True)
                continue

            results_by_url = {
                result.get("url"): result
                for result in batch_results
                if result.get("url")
            }
            mapping_semaphore = asyncio.Semaphore(args.mapping_concurrency)

            async def map_and_upload(url, item_number):
                result = results_by_url.get(url)
                if result is None:
                    error = RuntimeError("No usable scrape result returned for URL")
                    error_details = {"error": f"{type(error).__name__}: {error}"}
                    checkpoint_store.finish(url, "failed", run_id, error_details)
                    append_log(
                        log_path,
                        "failed",
                        url,
                        run_id=run_id,
                        worker_id=args.worker_id,
                        **error_details,
                    )
                    print(f"Skip failed: {url} - {error}", file=sys.stderr, flush=True)
                    return False

                async with mapping_semaphore:
                    print(f"[{item_number}/{len(urls)}] Mapping {url}", flush=True)
                    try:
                        summary = await process_scrape_result(
                            url,
                            result,
                            datatype=args.datatype,
                            collection=args.collection,
                            dry_run=args.dry_run,
                        )
                    except Exception as error:
                        error_details = {
                            "error": f"{type(error).__name__}: {error}"
                        }
                        checkpoint_store.finish(url, "failed", run_id, error_details)
                        append_log(
                            log_path,
                            "failed",
                            url,
                            run_id=run_id,
                            worker_id=args.worker_id,
                            **error_details,
                        )
                        print(
                            f"Skip failed: {url} - "
                            f"{type(error).__name__}: {error}",
                            file=sys.stderr,
                            flush=True,
                        )
                        return False

                    checkpoint_status = "dry_run" if args.dry_run else "done"
                    checkpoint_store.finish(
                        url,
                        checkpoint_status,
                        run_id,
                        summary,
                    )
                    append_log(
                        log_path,
                        "done",
                        url,
                        run_id=run_id,
                        worker_id=args.worker_id,
                        **summary,
                    )
                    print(
                        f"Done: {summary['slug']} -> "
                        f"{summary['qdrant_point_id'] or 'dry-run'}",
                        flush=True,
                    )
                    return True

            item_start = processed + 1
            outcomes = await asyncio.gather(
                *(
                    map_and_upload(url, item_start + offset)
                    for offset, url in enumerate(batch_urls)
                )
            )
            processed += len(batch_urls)
            succeeded += sum(outcomes)
            failed += len(outcomes) - sum(outcomes)
    finally:
        await NoDriverScraper.close_all_browsers()

    print(
        f"Completed: succeeded={succeeded}, failed={failed}, log={log_path}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
