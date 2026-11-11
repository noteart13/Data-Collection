"""
Database Payload Scrubber for PropertyMaster.
Sanitizes existing points in Qdrant to match PropertySoldV2 1:1 schema:
- Drops all unaligned keys (planning, zoning, strataDetails, lotPlan, location_scores, cadastral_parcel, data_quality, etc.)
- Strips weeklyRent and estimatedYield from pricing
- Cleans fake/mock URLs and images
- Preserves 100% of vectors (Zero Vector Loss via OverwritePayloadOperation)
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Tuple, Optional

# UTF-8 stdout on Windows
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

load_dotenv()

PROPERTYSOLD_V2_KEYS = {
    "architecturalStyle",
    "area",
    "bed",
    "bath",
    "coordinates",
    "constructionYear",
    "expectedPrice",
    "street",
    "state",
    "suburb",
    "city",
    "postcode",
    "propertyType",
    "title",
    "description",
    "features",
    "structuralRemodelYear",
    "recommended",
    "published",
    "realestate_url",
    "contactInfo",
    "images",
    "soldDateInfo",
    "listingOption",
    "pricing",
    "status",
    "historysale",
    "slug",
    "createdAt",
    "updatedAt",
}

CANONICAL_FEATURES_DEFAULT = {
    "garage": 0,
    "floorNo": 1,
    "indoorFeatures": [],
    "outdoorAmenities": [],
    "basement": "None",
    "floorCovering": [],
    "roof": [],
    "view": [],
    "appliances": [],
    "buildingAmenities": [],
    "displayAddress": "fullAddress"
}

def clean_point_payload(pl: dict) -> Tuple[dict, bool]:
    has_unaligned = any(k not in PROPERTYSOLD_V2_KEYS for k in pl.keys())
    has_missing = any(k not in pl for k in PROPERTYSOLD_V2_KEYS)
    
    # Check pricing
    p_raw = pl.get("pricing") if isinstance(pl.get("pricing"), dict) else {}
    has_unaligned_pricing = (
        "weeklyRent" in p_raw 
        or "estimatedYield" in p_raw 
        or str(p_raw.get("councilBill", "")).strip() != ""
        or str(p_raw.get("authority", "")).strip() in ("N/A", "None", "")
    )
    
    # Check dirty url or images
    r_url = str(pl.get("realestate_url") or "").strip()
    is_dirty_url = ("view.com.au/property/" in r_url) or ("sample" in r_url.lower()) or (r_url and not r_url.startswith("http"))
    final_url = "" if is_dirty_url else r_url
    
    # Check dirty / synthetic description
    d_raw = str(pl.get("description") or "").strip()
    is_dirty_desc = (" located at " in d_raw and "|" in d_raw) or any(w in d_raw for w in ["effortless modern living", "bathed in natural light", "Positioned in a highly desirable"])

    # Check unverified / synthetic inferred data on unlisted properties (Zero Fabrication / Pure Scraping Rule)
    has_unverified_unlisted_data = False
    if not final_url:
        if pl.get("bed") is not None or pl.get("bath") is not None or pl.get("expectedPrice") is not None:
            has_unverified_unlisted_data = True
        if str(pl.get("constructionYear") or "N/A") != "N/A":
            has_unverified_unlisted_data = True
        if str(p_raw.get("waterBillPeriod") or "").strip() != "":
            has_unverified_unlisted_data = True
        if str(p_raw.get("pricingOptions") or "N/A") not in ("N/A", ""):
            has_unverified_unlisted_data = True

    # Check slug format (G-NAF PID vs address slug)
    street = str(pl.get("street") or "")
    suburb = str(pl.get("suburb") or "")
    state = str(pl.get("state") or "").upper()
    postcode = str(pl.get("postcode") or "")
    raw_slug = f"{street} {suburb} {state} {postcode}".lower().replace("/", "-")
    canonical_slug = re.sub(r"[^a-z0-9]+", "-", raw_slug).strip("-")
    has_pid_slug = str(pl.get("slug") or "") != canonical_slug

    # Check historysale extra keys
    raw_hist = pl.get("historysale") or []
    has_extra_hist_keys = any(
        isinstance(h, dict) and ("datePrecision" in h or "isEstimatedDate" in h)
        for h in raw_hist
    )

    imgs = pl.get("images") or []
    has_dirty_img = any("sample" in str(img.get("url", "")).lower() for img in imgs if isinstance(img, dict))
    
    # Check features completeness
    feat_raw = pl.get("features") if isinstance(pl.get("features"), dict) else {}
    has_missing_features = any(k not in feat_raw for k in CANONICAL_FEATURES_DEFAULT.keys())

    if not (has_unaligned or has_missing or has_unaligned_pricing or is_dirty_url or has_dirty_img or is_dirty_desc or has_pid_slug or has_extra_hist_keys or has_missing_features or has_unverified_unlisted_data):
        return pl, False

    # Sanitize pricing and rooms (1:1 with PropertySoldV2 & Pure Scraping Rule)
    status_val = str(pl.get("status") or "off_market")
    
    # Pure Scraping Rule: If no verified portal listing URL exists, zero fabrication/estimation
    if not final_url:
        price_val = None
        bed_val = None
        bath_val = None
        year_val = "N/A"
        pricing_options_str = "N/A"
        water_bill_str = ""
    else:
        price_val = pl.get("expectedPrice")
        bed_val = int(pl["bed"]) if pl.get("bed") is not None else None
        bath_val = int(pl["bath"]) if pl.get("bath") is not None else None
        year_val = str(pl.get("constructionYear") or "N/A")
        pricing_options_str = f"${int(price_val):,}" if price_val else str(p_raw.get("pricingOptions") or "N/A")
        water_bill_str = str(p_raw.get("waterBillPeriod") or "")
    
    cleaned_features = dict(CANONICAL_FEATURES_DEFAULT)
    for fk in ["garage", "floorNo", "indoorFeatures", "outdoorAmenities", "displayAddress"]:
        if fk in feat_raw and feat_raw[fk] is not None:
            cleaned_features[fk] = feat_raw[fk]
    if not final_url:
        cleaned_features["garage"] = 0
        cleaned_features["indoorFeatures"] = []
        cleaned_features["outdoorAmenities"] = []

    prop_type_str = str(pl.get("propertyType") or "House")
    is_unit_prop = any(u in prop_type_str.lower() for u in ["unit", "apartment", "flat"])

    area_raw = pl.get("area") if isinstance(pl.get("area"), dict) else {}
    raw_total_area = area_raw.get("totalArea") if "totalArea" in area_raw else pl.get("totalArea")
    # For unlisted units, do not preserve synthetic floor area without portal listing
    if not final_url and is_unit_prop:
        final_area = None
    else:
        final_area = raw_total_area

    cleaned_area = {
        "totalArea": final_area,
        "unit": "sqM"
    }

    coords_raw = pl.get("coordinates") if isinstance(pl.get("coordinates"), dict) else None
    if not coords_raw and pl.get("lat") and pl.get("lon"):
        try:
            coords_raw = {"lat": float(pl["lat"]), "lon": float(pl["lon"])}
        except (ValueError, TypeError):
            coords_raw = None

    # Sanitize historysale (strictly 3 keys: soldDate, soldPrice, agency)
    cleaned_hist = []
    for h in raw_hist:
        if isinstance(h, dict) and (h.get("soldPrice") or h.get("price")):
            cleaned_hist.append({
                "soldDate": str(h.get("soldDate") or h.get("date") or h.get("contractDate") or ""),
                "soldPrice": int(h.get("soldPrice") or h.get("price") or 0),
                "agency": str(h.get("agency") or "Commercial Listing Record")
            })

    # Exact 30 Canonical Keys construction matching PropertySoldV2
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+11:00")
    cleaned = {
        "architecturalStyle": str(pl.get("architecturalStyle") or "None"),
        "area": cleaned_area,
        "bed": bed_val,
        "bath": bath_val,
        "coordinates": coords_raw,
        "constructionYear": year_val,
        "expectedPrice": float(price_val) if price_val is not None else None,
        "street": street,
        "state": state,
        "suburb": suburb,
        "city": str(pl.get("city") or suburb),
        "postcode": postcode,
        "propertyType": prop_type_str,
        "title": str(pl.get("title") or f"{street}, {suburb} {state} {postcode}"),
        "description": "" if is_dirty_desc or not final_url else d_raw,
        "features": cleaned_features,
        "structuralRemodelYear": str(pl.get("structuralRemodelYear") or "N/A"),
        "recommended": bool(pl.get("recommended", False)),
        "published": bool(pl.get("published", False)),
        "realestate_url": final_url,
        "contactInfo": pl.get("contactInfo") if isinstance(pl.get("contactInfo"), list) else [],
        "images": [
            img for img in imgs 
            if isinstance(img, dict) and str(img.get("url", "")).startswith("http") and "sample" not in str(img.get("url", "")).lower()
        ] if final_url else [],
        "soldDateInfo": str(pl.get("soldDateInfo") or ""),
        "listingOption": status_val,
        "pricing": {
            "waterBillPeriod": water_bill_str,
            "councilBill": "",
            "pricingOptions": pricing_options_str,
            "authority": status_val,
            "priceIncludes": p_raw.get("priceIncludes") if isinstance(p_raw.get("priceIncludes"), list) else []
        },
        "status": status_val,
        "historysale": cleaned_hist,
        "slug": canonical_slug,
        "createdAt": str(pl.get("createdAt") or now_iso),
        "updatedAt": str(pl.get("updatedAt") or now_iso)
    }

    return cleaned, True

def robust_scroll(client: QdrantClient, collection: str, limit: int, offset, retries: int = 5):
    """Robust scroll with exponential backoff against remote network resets."""
    for attempt in range(retries):
        try:
            return client.scroll(
                collection_name=collection,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def robust_batch_update(client: QdrantClient, collection: str, updates: list, chunk_size: int = 300, retries: int = 5):
    """Robust batch payload updates chunked to prevent socket buffer exhaustion."""
    if not updates:
        return
    for i in range(0, len(updates), chunk_size):
        chunk = updates[i:i + chunk_size]
        for attempt in range(retries):
            try:
                client.batch_update_points(
                    collection_name=collection,
                    update_operations=chunk,
                    wait=False
                )
                break
            except Exception as e:
                if attempt == retries - 1:
                    raise
                time.sleep(2 * (attempt + 1))


CHECKPOINT_FILE = Path("scrub_checkpoint.json")

def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_checkpoint(offset, scanned, scrubbed):
    try:
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "offset": offset,
                "total_scanned": scanned,
                "total_scrubbed": scrubbed,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")
            }, f)
    except Exception:
        pass


def scrub_collection(url: str, api_key: str, collection: str, batch_size: int = 1000, dry_run: bool = False, reset_checkpoint: bool = False):
    print("=" * 70)
    print("PROPERTYMASTER SCHEMA SANITIZER & SCRUBBER (1:1 PROPERTYSOLDV2)")
    print("=" * 70)
    print(f"Target Database : {url} | Collection: {collection}")
    print(f"Batch Size      : {batch_size:,} | Dry Run: {dry_run}")
    print("=" * 70)

    effective_api_key = api_key or os.getenv("QDRANT_API", "").strip("'\"") or ("local-qdrant-key" if ("localhost" in url or "127.0.0.1" in url) else None)
    client = QdrantClient(url=url, api_key=effective_api_key, timeout=300, check_compatibility=False)

    if reset_checkpoint and CHECKPOINT_FILE.exists():
        try:
            CHECKPOINT_FILE.unlink()
            print("[Reset] Checkpoint file deleted. Starting from record 0.")
        except Exception:
            pass

    ckpt = None if reset_checkpoint else load_checkpoint()
    offset = None
    total_scanned = 0
    total_scrubbed = 0
    if ckpt and ckpt.get("offset"):
        offset = ckpt["offset"]
        total_scanned = ckpt.get("total_scanned", 0)
        total_scrubbed = ckpt.get("total_scrubbed", 0)
        print(f"[Resume] Found checkpoint! Resuming at offset: {offset} (Scanned: {total_scanned:,})")

    t_start = time.time()

    while True:
        res, next_offset = robust_scroll(
            client=client,
            collection=collection,
            limit=batch_size,
            offset=offset
        )
        if not res:
            break

        updates = []
        for pt in res:
            cleaned_pl, modified = clean_point_payload(pt.payload or {})
            if modified:
                updates.append(models.OverwritePayloadOperation(
                    overwrite_payload=models.SetPayload(
                        payload=cleaned_pl,
                        points=[pt.id]
                    )
                ))

        if updates and not dry_run:
            robust_batch_update(
                client=client,
                collection=collection,
                updates=updates,
                chunk_size=300
            )
            time.sleep(0.04)  # Small backpressure sleep for Qdrant WAL

        total_scanned += len(res)
        total_scrubbed += len(updates)
        sec = max(time.time() - t_start, 0.001)
        speed = total_scanned / sec

        print(
            f"Scanned: {total_scanned:,} | Scrubbed: {total_scrubbed:,} | Speed: {speed:,.0f} pts/sec",
            end="\r",
            flush=True
        )

        if next_offset is None:
            if CHECKPOINT_FILE.exists():
                try:
                    CHECKPOINT_FILE.unlink()
                except Exception:
                    pass
            break

        offset = next_offset
        if total_scanned % (batch_size * 2) == 0:
            save_checkpoint(offset, total_scanned, total_scrubbed)

    print(f"\n[Done] Completed! Scanned: {total_scanned:,} | Sanitized: {total_scrubbed:,}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrub PropertyMaster Qdrant payload to 1:1 PropertySoldV2.")
    parser.add_argument("--url", default=os.getenv("QDRANT_URI", "http://localhost:6333"))
    parser.add_argument("--api-key", default=os.getenv("QDRANT_API", ""))
    parser.add_argument("--collection", default="PropertyMaster")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Scrub from the very beginning (record 0)")
    args = parser.parse_args()

    scrub_collection(
        url=args.url,
        api_key=args.api_key,
        collection=args.collection,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        reset_checkpoint=args.reset_checkpoint
    )
