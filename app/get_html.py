import asyncio
import argparse
import json
import os
import re
import sys
import types
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

OUTPUT_CSV = "urls.csv"
PROPERTY_DOMAIN = "property.com.au"
DEBUG_OUTPUT_JSON = "property_meta_debug_output.json"
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "PropertySoldV2")
QDRANT_URL = os.getenv("QDRANT_URI", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API", None)
DUMMY_VECTOR = [0.0] * 768
DATABASE_TIMEZONE = ZoneInfo("Australia/Brisbane")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_HTML_PATH = PROJECT_ROOT / "test.html"
ADDRESS_WITH_STATE_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<suburb>.+?),\s*(?P<state>NSW|VIC|QLD|SA|WA|TAS|ACT|NT)\s+(?P<postcode>\d{4})$",
    re.IGNORECASE,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Search and scrape property pages from an address CSV."
    )
    parser.add_argument("--input-csv",default=OUTPUT_CSV,help=f"Input CSV containing an address column (default: {OUTPUT_CSV}).",)
    parser.add_argument("--worker-id",type=int,default=0,help="Zero-based worker number (default: 0).",)
    parser.add_argument("--worker-count",
        type=int,
        default=1,
        help="Total number of parallel worker processes (default: 1).",
    )
    args = parser.parse_args()

    if args.worker_count < 1:
        parser.error("--worker-count must be at least 1")
    if not 0 <= args.worker_id < args.worker_count:
        parser.error("--worker-id must be between 0 and worker-count - 1")

    return args


def worker_debug_output_path(worker_id, worker_count):
    output_path = Path(DEBUG_OUTPUT_JSON)
    if worker_count == 1:
        return output_path
    return output_path.with_name(
        f"{output_path.stem}_worker_{worker_id}{output_path.suffix}"
    )


def build_property_title(address):
    match = ADDRESS_WITH_STATE_RE.match(address.strip())
    if not match:
        return f"{address.strip()} - {PROPERTY_DOMAIN}"

    street = match.group("street").strip()
    suburb = match.group("suburb").strip()
    state = match.group("state").upper()
    postcode = match.group("postcode")
    return f"{street}, {suburb} {state} {postcode} - {PROPERTY_DOMAIN}"


def normalize_text(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def find_property_result_by_title(search_results, address):
    expected_title = build_property_title(address)
    normalized_expected_title = normalize_text(expected_title)

    for result in search_results:
        title = result.get("title", "")
        if normalize_text(title) == normalized_expected_title:
            return result

    return None


class PassthroughGCS:
    async def upload_images_to_gcs(self, image_url, *_args, **_kwargs):
        return image_url


def install_debug_gcs_stub():
    try:
        import json5  # noqa: F401
    except Exception:
        json5_stub = types.ModuleType("json5")
        json5_stub.loads = json.loads
        json5_stub.load = json.load
        json5_stub.dump = json.dump
        sys.modules["json5"] = json5_stub

    try:
        from bs4 import BeautifulSoup  # noqa: F401
    except Exception:
        bs4_stub = types.ModuleType("bs4")

        class BeautifulSoupStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def find(self, *_args, **_kwargs):
                return None

        bs4_stub.BeautifulSoup = BeautifulSoupStub
        sys.modules["bs4"] = bs4_stub

    storage_stub = types.ModuleType("operations.storage")
    storage_stub.gcs = PassthroughGCS()
    sys.modules.setdefault("operations.storage", storage_stub)


def first_scrape_result(results):
    if isinstance(results, list):
        return results[0] if results else {}
    if isinstance(results, dict):
        return results
    return {}


async def map_property_html(html, url, datatype="salesold", include_raw_meta=False):
    """Map scraped property HTML without writing diagnostic files."""
    install_debug_gcs_stub()
    from operations.property_address_property import PropertyMeta

    property_meta = PropertyMeta(html, url, datatype)
    mapped = await property_meta.Mapping()

    payload = {
        "input": {
            "url": url,
            "datatype": datatype,
        },
        "debug": {
            "slug": property_meta.create_slug(),
            "metadata_found": bool(property_meta.meta),
            "metadata_top_level_keys": sorted(property_meta.meta.keys()) if isinstance(property_meta.meta, dict) else [],
        },
        "mapped": mapped,
    }
    if include_raw_meta:
        payload["raw_meta"] = property_meta.meta
    return payload


async def save_property_meta_debug(html, url, output_path=DEBUG_OUTPUT_JSON, datatype="salesold"):
    payload = await map_property_html(
        html,
        url,
        datatype=datatype,
        include_raw_meta=True,
    )

    output_file = Path(output_path)
    output_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"Saved mapped property JSON: {output_file.resolve()}")
    return payload


def point_id_from_property(property_payload):
    raw_id = (
        property_payload.get("slug")
        or property_payload.get("realestate_url")
        or property_payload.get("url")
        or property_payload.get("street")
        or json.dumps(property_payload, sort_keys=True, default=str)
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(raw_id)))


async def upload_propertyforsale_to_qdrant(payload, collection_name=QDRANT_COLLECTION):
    property_payload = (payload.get("mapped") or {}).get("propertyforsale")

    if not property_payload:
        print("No mapped.propertyforsale payload found; skip Qdrant upload")
        return None

    point_id = point_id_from_property(property_payload)

    await upload_propertyforsale_to_qdrant_rest(
        point_id=point_id,
        property_payload=property_payload,
        collection_name=collection_name,
    )

    print(f"Uploaded mapped.propertyforsale to Qdrant: {collection_name}/{point_id}")
    return point_id


def apply_database_timestamps(property_payload, existing_payload=None, now=None):
    """Apply creation/update timestamps before a database upsert."""
    write_time = now or datetime.now(DATABASE_TIMEZONE).replace(
        microsecond=0
    ).isoformat()

    # createdAt is immutable after the first insert. Older records that predate
    # these fields cannot expose their original creation time, so initialize
    # both timestamps to the current write time when createdAt is unavailable.
    property_payload["createdAt"] = (
        existing_payload.get("createdAt") if existing_payload else write_time
    ) or write_time
    property_payload["updatedAt"] = write_time
    return property_payload


async def upload_propertyforsale_to_qdrant_rest(point_id, property_payload, collection_name):
    existing_payload = await get_qdrant_point_payload(collection_name, point_id)
    apply_database_timestamps(property_payload, existing_payload)

    url = f"{QDRANT_URL.rstrip('/')}/collections/{collection_name}/points?wait=true"
    point = {
        "id": point_id,
        "payload": property_payload,
    }
    vector_payload = await get_qdrant_vector_payload(collection_name)

    if vector_payload is not None:
        point["vector"] = vector_payload

    body = {
        "points": [point]
    }
    data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=data,
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "api-key": QDRANT_API_KEY,
        },
    )

    def send_request():
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Qdrant upsert failed: HTTP {exc.code} {body}") from exc

    return await asyncio.to_thread(send_request)


async def get_qdrant_point_payload(collection_name, point_id):
    """Return the current point payload, or None when the point is new."""
    url = (
        f"{QDRANT_URL.rstrip('/')}/collections/{collection_name}/points/{point_id}"
    )
    request = urllib.request.Request(
        url=url,
        method="GET",
        headers={"api-key": QDRANT_API_KEY},
    )

    def send_request():
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Qdrant point lookup failed: HTTP {exc.code} {error_body}"
            ) from exc

        result = body.get("result")
        if not result:
            return None
        return result.get("payload") or {}

    return await asyncio.to_thread(send_request)


async def get_qdrant_vector_payload(collection_name):
    url = f"{QDRANT_URL.rstrip('/')}/collections/{collection_name}"
    request = urllib.request.Request(
        url=url,
        method="GET",
        headers={"api-key": QDRANT_API_KEY},
    )

    def send_request():
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8")

    response = await asyncio.to_thread(send_request)
    collection = json.loads(response)
    vectors = (
        collection.get("result", {})
        .get("config", {})
        .get("params", {})
        .get("vectors")
    )

    if vectors == {}:
        return {}

    if isinstance(vectors, dict) and "emb" in vectors:
        return {"emb": DUMMY_VECTOR}

    if isinstance(vectors, dict):
        return {
            name: DUMMY_VECTOR
            for name, config in vectors.items()
            if isinstance(config, dict)
        }

    return DUMMY_VECTOR


def canonical_url_from_html(html):
    match = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', html)
    return match.group(1) if match else None

async def scrape(url, max_workers=2):
    from operations.researcher.scraper.scaper import Scraper
    from operations.utils.workers import WorkerPool

    if isinstance(url, str):
        urls = [url]
    elif isinstance(url, list):
        urls = url
    else:
        raise TypeError("Url must be a string or a list of strings")

    worker_pool = WorkerPool(max_workers=max(1, int(max_workers)))

    scraper = Scraper(
        urls=urls,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        scraper="nodriver",
        worker_pool=worker_pool,
    )

    results = await scraper.run()
    return results


async def process_address(street, debug_output_path, retriever_class):
    import time

    start = time.time()
    expected_title = build_property_title(street)
    query = f'"{expected_title}"'
    print(query, flush=True)

    try:
        retriever = retriever_class(
            query=query,
            query_domains=[PROPERTY_DOMAIN],
        )
        search_results = await asyncio.to_thread(
            retriever.search,
            max_results=100,
        )
        matched_result = find_property_result_by_title(search_results, street)
        print(f"Elapsed time search {time.time() - start:.2f}s", flush=True)

        if not matched_result:
            print(f"No matching title found: {expected_title}", flush=True)
            return

        url = matched_result.get("href")
        print(url, flush=True)
        results = await scrape(url)
        result = first_scrape_result(results)
        print(f"Elapsed time scrape {time.time() - start:.2f}s", flush=True)
        html = result.get("html")

        if not html:
            print(f"No html found in scrape result for: {url}", flush=True)
            return

        payload = await save_property_meta_debug(
            html=html,
            url=url,
            output_path=debug_output_path,
            datatype="salesold",
        )
        await upload_propertyforsale_to_qdrant(payload)
    except Exception as exc:
        print(
            f"Failed address {street!r}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )


async def main():
    args = parse_args()
    # if TEST_HTML_PATH.exists():
    #     html = TEST_HTML_PATH.read_text(encoding="utf-8")
    #     url = canonical_url_from_html(html) or "https://www.property.com.au/"
    #     payload = await save_property_meta_debug(
    #         html=html,
    #         url=url,
    #         output_path=DEBUG_OUTPUT_JSON,
    #         datatype="salesold",
    #     )
    #     await upload_propertyforsale_to_qdrant(payload)
    #     return

    import pandas as pd
    from operations.researcher.retrievers import Duckduckgo

    # from qdrant_client import AsyncQdrantClient as QdrantClient
    # client = QdrantClient(url=os.getenv("QDRANT_URI", "http://localhost:6333"), api_key=os.getenv("QDRANT_API"))

    # collection_name = "PropertyForSale"
    # streets = []
    # rows = []
    # offset = None

    # while True: 
    #     points, offset = await client.scroll(
    #         collection_name=collection_name,
    #         limit=1000,
    #         offset=offset,
    #         with_payload=["street"],
    #         with_vectors=False,
    #     )

    #     for point in points:
    #         rows.append({
    #             "id": point.id,
    #             "street": point.payload.get("street"),
    #         })

    #     if offset is None:
    #         break

    # df = pd.DataFrame(rows)
    # df.to_csv("PropertySaleAddr.csv", index=False, encoding="utf-8-sig")
    all_addrs = pd.read_csv(args.input_csv)["address"].dropna().tolist()
    addrs = all_addrs[args.worker_id::args.worker_count]
    debug_output_path = worker_debug_output_path(
        args.worker_id,
        args.worker_count,
    )
    print(
        f"Worker {args.worker_id}/{args.worker_count}: "
        f"processing {len(addrs)} of {len(all_addrs)} addresses"
    )
    for addr in addrs:
        await process_address(addr, debug_output_path, Duckduckgo)

    print(f"Worker {args.worker_id}/{args.worker_count}: completed", flush=True)
    
if __name__ == '__main__':
    asyncio.run(main())
