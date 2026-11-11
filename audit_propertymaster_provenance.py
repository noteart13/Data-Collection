#!/usr/bin/env python3
"""
READ-ONLY PROVENANCE & LEGACY AUDIT TOOL FOR PROPERTYMASTER
===========================================================
Inspects Qdrant points to classify records into:
  1. VERIFIED: Records enriched with verified portal provenance and authentic URLs.
  2. LEGACY UNVERIFIED: Records carrying pre-existing unverified values from older heuristic runs.
  3. RAW / UNENRICHED: Records lacking core attributes (bed/bath/price).

Zero database writes. 100% safe read-only inspection.
"""

import argparse
import os
import re
import sys
from typing import Dict, Any, List
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

try:
    from app.operations.property_evidence import CORE_LEGACY_PATHS, evidence_matches_value, get_path
except ImportError:
    from operations.property_evidence import CORE_LEGACY_PATHS, evidence_matches_value, get_path

# UTF-8 stdout on Windows
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

DEFAULT_QDRANT_URL = os.getenv("QDRANT_URI", "http://localhost:6333")
DEFAULT_QDRANT_API_KEY = os.getenv("QDRANT_API", "").strip('"').strip("'") or None
COLLECTION_NAME = "PropertyMaster"


def audit_collection(
    url: str,
    api_key: str,
    collection: str,
    state: str,
    suburb: str = None,
    limit: int = 0,
    batch_size: int = 500,
    allow_insecure_api_key: bool = False,
):
    print("=" * 76)
    print("PROPERTYMASTER DATA PROVENANCE & AUDIT REPORT (READ-ONLY)")
    print("=" * 76)
    print(f"Target Database : {url} | Collection: {collection}")
    print(f"Target Filter   : State={state.upper()} | Suburb={suburb or 'ALL'}")
    print("=" * 76)

    if api_key and url.lower().startswith("http://") and not allow_insecure_api_key:
        raise RuntimeError(
            "Refusing to send Qdrant API key over plaintext HTTP. Use HTTPS or explicitly pass "
            "--allow-insecure-api-key."
        )
    effective_api_key = api_key if url.lower().startswith("https://") or allow_insecure_api_key else None
    client = QdrantClient(url=url, api_key=effective_api_key, timeout=60, check_compatibility=False)

    filter_must = []
    if state.upper() != "ALL":
        filter_must.append(models.FieldCondition(key="state", match=models.MatchValue(value=state.upper())))
    if suburb:
        sub_clean = suburb.strip()
        suburb_variants = list(dict.fromkeys([
            sub_clean.title(),
            sub_clean.upper(),
            sub_clean.lower(),
            sub_clean
        ]))
        filter_must.append(models.FieldCondition(key="suburb", match=models.MatchAny(any=suburb_variants)))

    query_filter = models.Filter(must=filter_must) if filter_must else None

    total_scanned = 0
    verified_count = 0
    legacy_count = 0
    raw_count = 0
    address_only_count = 0
    verified_bed_count = 0
    verified_bath_count = 0
    records_with_images = 0
    audit_error = None

    legacy_with_price = 0
    legacy_with_beds = 0
    legacy_with_year = 0

    verified_samples = []
    legacy_samples = []

    offset = None

    try:
        while True:
            scroll_limit = batch_size
            if limit > 0:
                rem = limit - total_scanned
                if rem <= 0:
                    break
                scroll_limit = min(batch_size, rem)

            points, next_offset = client.scroll(
                collection_name=collection,
                scroll_filter=query_filter,
                limit=scroll_limit,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )

            if not points:
                break

            for pt in points:
                total_scanned += 1
                pl = pt.payload or {}
                field_provenance = pl.get("fieldProvenance") if isinstance(pl.get("fieldProvenance"), dict) else {}
                accepted_evidence = {
                    path: evidence
                    for path, evidence in field_provenance.items()
                    if evidence_matches_value(get_path(pl, path), evidence)
                }
                has_beds = pl.get("bed") is not None
                has_price = pl.get("expectedPrice") is not None
                has_year = pl.get("constructionYear") not in ["N/A", "None", "", None]

                accepted_core = {path: evidence for path, evidence in accepted_evidence.items() if path in CORE_LEGACY_PATHS}
                is_verified = bool(accepted_core)
                if "bed" in accepted_evidence:
                    verified_bed_count += 1
                if "bath" in accepted_evidence:
                    verified_bath_count += 1
                if isinstance(pl.get("images"), list) and pl.get("images"):
                    records_with_images += 1

                if is_verified:
                    verified_count += 1
                    if len(verified_samples) < 5:
                        verified_samples.append({
                            "id": pt.id,
                            "street": pl.get("street"),
                            "suburb": pl.get("suburb"),
                            "bed": pl.get("bed"),
                            "bath": pl.get("bath"),
                            "expectedPrice": pl.get("expectedPrice"),
                            "dataSource": ", ".join(sorted({
                                str(evidence.get("sourceName"))
                                for evidence in accepted_core.values()
                                if evidence.get("sourceName")
                            })),
                            "url": pl.get("realestate_url")
                        })
                elif has_beds or has_price or has_year or bool(pl.get("legacyUnverified")):
                    legacy_count += 1
                    if has_price:
                        legacy_with_price += 1
                    if has_beds:
                        legacy_with_beds += 1
                    if has_year:
                        legacy_with_year += 1
                    if len(legacy_samples) < 5:
                        legacy_samples.append({
                            "id": pt.id,
                            "street": pl.get("street"),
                            "suburb": pl.get("suburb"),
                            "bed": pl.get("bed"),
                            "bath": pl.get("bath"),
                            "expectedPrice": pl.get("expectedPrice"),
                            "constructionYear": pl.get("constructionYear"),
                            "url": pl.get("realestate_url")
                        })
                else:
                    raw_count += 1
                    if accepted_evidence:
                        address_only_count += 1

            offset = next_offset
            if next_offset is None or (limit > 0 and total_scanned >= limit):
                break

    except Exception as e:
        audit_error = str(e)
        print(f"\n[Error during audit]: {e}")

    print("\n" + "=" * 76)
    print("AUDIT RESULTS SUMMARY")
    print("=" * 76)
    print(f"Total Properties Scanned          : {total_scanned:,}")
    print(f"  ├── 1. Field-evidenced Records  : {verified_count:,} ({verified_count/max(total_scanned, 1)*100:.1f}%)")
    print(f"  ├── 2. Legacy Unverified Records: {legacy_count:,} ({legacy_count/max(total_scanned, 1)*100:.1f}%)")
    print(f"  │    ├── Carrying Legacy Price  : {legacy_with_price:,}")
    print(f"  │    ├── Carrying Legacy Beds   : {legacy_with_beds:,}")
    print(f"  │    └── Carrying Legacy Year   : {legacy_with_year:,}")
    print(f"  └── 3. Raw / Unenriched Records : {raw_count:,} ({raw_count/max(total_scanned, 1)*100:.1f}%)")
    print(f"       └── Address evidence only   : {address_only_count:,}")
    print(f"  Verified bedroom values          : {verified_bed_count:,}")
    print(f"  Verified bathroom values         : {verified_bath_count:,}")
    print(f"  Records carrying image URLs      : {records_with_images:,}")
    print("=" * 76)

    if verified_samples:
        print("\n[Sample Verified Records]:")
        for s in verified_samples:
            print(f"  - [{s['id']}] {s['street']}, {s['suburb']} | Bed/Bath: {s['bed']}/{s['bath']} | Price: {s['expectedPrice']} | Source: {s['dataSource']} | URL: {s['url']}")

    if legacy_samples:
        print("\n[Sample Legacy Unverified Records]:")
        for s in legacy_samples:
            print(f"  - [{s['id']}] {s['street']}, {s['suburb']} | Bed/Bath: {s['bed']}/{s['bath']} | Price: {s['expectedPrice']} | Year: {s['constructionYear']} | URL: {s['url'] or 'NO_URL'}")

    print("\n" + "=" * 76 + "\n")
    return {
        "status": "failed" if audit_error else "completed",
        "error": audit_error,
        "state": state.upper(),
        "suburb": suburb,
        "total_scanned": total_scanned,
        "field_evidenced_records": verified_count,
        "legacy_unverified_records": legacy_count,
        "raw_records": raw_count,
        "address_only_records": address_only_count,
        "verified_bed_records": verified_bed_count,
        "verified_bath_records": verified_bath_count,
        "records_with_images": records_with_images,
    }


def main():
    parser = argparse.ArgumentParser(description="Audit provenance and legacy unverified records in PropertyMaster")
    parser.add_argument("--url", default=DEFAULT_QDRANT_URL, help="Qdrant server URL")
    parser.add_argument("--api-key", default=DEFAULT_QDRANT_API_KEY, help="Qdrant API Key")
    parser.add_argument("--collection", default=COLLECTION_NAME, help="Target collection")
    parser.add_argument("--state", default="NSW", help="Target state (e.g. NSW, VIC, WA, ALL)")
    parser.add_argument("--suburb", default=None, help="Specific suburb to audit (optional)")
    parser.add_argument("--limit", type=int, default=0, help="Max records to audit (0 = unlimited)")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size per scroll")
    parser.add_argument("--allow-insecure-api-key", action="store_true", help="Allow API key over plaintext HTTP")

    args = parser.parse_args()
    audit_collection(
        url=args.url,
        api_key=args.api_key,
        collection=args.collection,
        state=args.state,
        suburb=args.suburb,
        limit=args.limit,
        batch_size=args.batch_size,
        allow_insecure_api_key=args.allow_insecure_api_key,
    )


if __name__ == "__main__":
    main()
