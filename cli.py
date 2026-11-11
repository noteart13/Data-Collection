#!/usr/bin/env python3
"""
Australian Property Master Unified Pipeline CLI.
Coordinates data ingestion, validation, deduplication, enrichment, and quality auditing.
"""

import argparse
import os
import re
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PYTHON_EXE = sys.executable or "python"


def print_banner():
    banner = """
========================================================================
    AUSTRALIAN PROPERTY MASTER - PIPELINE MANAGEMENT CLI (v0.2.0)
========================================================================
"""
    print(banner)


# ---------------------------------------------------------------------------
# 1. STATUS COMMAND: Health check & Collection stats
# ---------------------------------------------------------------------------
def cmd_status(args):
    """Inspect Qdrant server health, collection points, and configurations."""
    print("Connecting to Qdrant...")
    qdrant_url = os.getenv("QDRANT_URI", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API", "").strip('"').strip("'") or None

    print(f"Target URL : {qdrant_url}")
    print(f"Timezone   : {os.getenv('DATABASE_TIMEZONE', 'Australia/Sydney')}")
    print(f"Scratch Dir: {os.getenv('SCRATCH_DIR', 'scratch')}")
    print("-" * 70)

    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=qdrant_url, api_key=qdrant_key, timeout=10)
        collections = client.get_collections().collections
        col_names = [c.name for c in collections]

        print(f"Qdrant Status: ONLINE (Total Collections: {len(col_names)})")
        print("\nCollection Breakdown:")
        print(f"{'Collection Name':<30} | {'Points Count':<15} | {'Status':<10}")
        print("-" * 65)

        target_collections = ["PropertyMaster", "PropertySoldV2", "PropertyForSale", "PropertyForRent"]
        for target in target_collections:
            if target in col_names:
                try:
                    c_info = client.get_collection(target)
                    pts = c_info.points_count or 0
                    status = str(c_info.status)
                    print(f"{target:<30} | {pts:>15,} | {status:<10}")
                except Exception as e:
                    print(f"{target:<30} | {'Error':>15} | {e}")
            else:
                print(f"{target:<30} | {'Not Found':>15} | MISSING")

        print("-" * 65)
    except Exception as e:
        print(f"Error connecting to Qdrant: {e}")


# ---------------------------------------------------------------------------
# 2. AUDIT QUALITY COMMAND: Samples points and prints Data Quality breakdown
# ---------------------------------------------------------------------------
def cmd_audit_quality(args):
    """Sample records from PropertyMaster and evaluate data completeness & synthetic attribution."""
    print(f"Sampling up to {args.sample_size} records from PropertyMaster for Data Quality Audit...")
    qdrant_url = os.getenv("QDRANT_URI", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API", "").strip('"').strip("'") or None

    try:
        from qdrant_client import QdrantClient, models
        from app.schemas.property_schema import calculate_data_quality_score, evaluate_data_quality

        client = QdrantClient(url=qdrant_url, api_key=qdrant_key, timeout=30)
        scroll_filter = None
        if args.state:
            scroll_filter = models.Filter(
                must=[models.FieldCondition(key="state", match=models.MatchValue(value=args.state.upper()))]
            )

        records, _ = client.scroll(
            collection_name="PropertyMaster",
            limit=args.sample_size,
            scroll_filter=scroll_filter,
            with_payload=True,
            with_vectors=False,
        )

        if not records:
            print("No records retrieved. Collection may be empty or filtered out.")
            return

        total = len(records)
        scores = []
        synthetic_count = 0
        attr_counts = {}
        sources = {}

        for pt in records:
            pl = pt.payload or {}
            enriched = evaluate_data_quality(pl)
            dq = enriched.get("data_quality", {})

            score = dq.get("data_quality_score", 0.0)
            scores.append(score)

            if dq.get("is_synthetic"):
                synthetic_count += 1
                for attr in dq.get("synthetic_attributes", []):
                    attr_counts[attr] = attr_counts.get(attr, 0) + 1

            src = dq.get("data_source", "UNKNOWN")
            sources[src] = sources.get(src, 0) + 1

        avg_score = sum(scores) / max(total, 1)

        print("\n" + "=" * 60)
        print("          DATA QUALITY AUDIT REPORT")
        print("=" * 60)
        print(f"Sampled Records        : {total:,}")
        print(f"Average Quality Score  : {avg_score:.1f} / 100.0")
        print(f"Synthetic Inferred Pts : {synthetic_count:,} ({synthetic_count/total*100:.1f}%)")
        print(f"Verified / Pure Points : {total - synthetic_count:,} ({(total - synthetic_count)/total*100:.1f}%)")
        print("\nData Sources Provenance:")
        for src, cnt in sources.items():
            print(f"  • {src:<30}: {cnt:>5} ({cnt/total*100:.1f}%)")

        if attr_counts:
            print("\nSynthesized Attributes Breakdown:")
            for attr, cnt in sorted(attr_counts.items(), key=lambda x: -x[1]):
                print(f"  • {attr:<25}: {cnt:>5} ({cnt/total*100:.1f}%)")
        print("=" * 60)

    except Exception as e:
        print(f"Quality audit failed: {e}")


def cmd_view_payload(args):
    """Inspect and display property payloads in Qdrant (sold, sale, rent)."""
    try:
        from scripts.tools.view_payload import view_payloads
    except ImportError:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from scripts.tools.view_payload import view_payloads

    view_payloads(
        status=args.status,
        state=args.state,
        suburb=args.suburb,
        collection=args.collection,
        limit=args.limit,
        raw_json=args.raw_json
    )
    return 0


# ---------------------------------------------------------------------------
# 3. PIPELINE WRAPPERS: Execution helpers
# ---------------------------------------------------------------------------
def run_script(script_name: str, extra_args: List[str] = None):
    """Executes a pipeline script with unified output streaming."""
    script_path = BASE_DIR / script_name
    if not script_path.exists():
        print(f"Error: Script not found at {script_path}")
        return 1

    cmd = [PYTHON_EXE, str(script_path)] + (extra_args or [])
    print(f"\n[CLI] Executing: {' '.join(cmd)}")
    print("=" * 70)
    start_time = time.time()
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), env=env)
        elapsed = time.time() - start_time
        print("=" * 70)
        print(f"[CLI] Script {script_name} finished in {elapsed:.1f}s (Exit code: {proc.returncode})")
        return proc.returncode
    except KeyboardInterrupt:
        print("\n[CLI] Execution cancelled by user.")
        return 130
    except Exception as e:
        print(f"\n[CLI] Execution error: {e}")
        return 1


def cmd_ingest_gnaf(args):
    params = ["--batch-size", str(args.batch_size)]
    if args.states:
        params.extend(["--states"] + args.states)
    return run_script("stream_gnaf_to_qdrant.py", params)


def cmd_ingest_act(args):
    return run_script("ingest_act_gov_master.py")


def cmd_merge_sales(args):
    params = ["--concurrency", str(args.concurrency)]
    if args.suburb:
        params.extend(["--suburb", args.suburb])
    if args.limit_suburbs:
        params.extend(["--limit-suburbs", str(args.limit_suburbs)])
    if args.zip_path:
        params.extend(["--zip-path", args.zip_path])
    if args.dry_run:
        params.append("--dry-run")
    return run_script("merge_nsw_sales_to_master.py", params)


def cmd_enrich_sold(args):
    state = (args.state or "NSW").upper()
    script = "enrich_nsw_master_with_sold.py" if state == "NSW" else "enrich_act_master_with_sold.py"
    return run_script(script)


def cmd_dedup(args):
    params = ["--concurrency", str(args.concurrency)]
    if args.state:
        params.extend(["--state", args.state])
    if args.suburb:
        params.extend(["--suburb", args.suburb])
    if args.limit_suburbs:
        params.extend(["--limit-suburbs", str(args.limit_suburbs)])
    if args.dry_run:
        params.append("--dry-run")
    return run_script("dedup_propertymaster.py", params)


def cmd_bulk_enrich(args):
    params = [
        "--workers", str(args.workers),
        "--batch-size", str(args.batch_size),
    ]
    if args.state:
        params.extend(["--state", args.state])
    if args.suburb:
        params.extend(["--suburb", args.suburb])
    if args.limit:
        params.extend(["--limit", str(args.limit)])
    if args.dry_run:
        params.append("--dry-run")
    if args.force:
        params.append("--force")
    if getattr(args, "skip_portal", False):
        params.append("--skip-portal")
    if getattr(args, "portal_fields", None):
        params.extend(["--portal-fields", *args.portal_fields])
    if getattr(args, "no_images", False):
        params.append("--no-images")
    if getattr(args, "property_browser_fallback", False):
        params.append("--property-browser-fallback")
    if getattr(args, "property_browser_headless", False):
        params.append("--property-browser-headless")
    if getattr(args, "use_property_source", False):
        params.append("--use-property-source")
    else:
        params.append("--skip-property-source")
    if getattr(args, "rollback_journal", None):
        params.extend(["--rollback-journal", args.rollback_journal])
    if getattr(args, "reset_checkpoint", False):
        params.append("--reset-checkpoint")
    return run_script("bulk_enrich_master.py", params)


SELF_ENRICH_FIELDS = (
    "bed",
    "bath",
    "garage",
    "totalArea",
    "constructionYear",
    "expectedPrice",
    "valuation_low",
    "valuation_high",
    "weeklyRent",
    "estimatedYield",
    "features_list",
    "realestate_url",
    "propertyType",
    "title",
    "description",
    "historysale",
    "status",
    "listingOption",
    "soldDateInfo",
)
SELF_ENRICH_STATES = ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA")


def _self_enrich_scope(state: str, suburb: Optional[str]) -> str:
    scope = state.upper()
    if suburb:
        suburb_slug = re.sub(r"[^a-z0-9]+", "-", suburb.lower()).strip("-")
        scope = f"{scope}_{suburb_slug}"
    return scope


def _is_local_http_qdrant(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "http" and (parsed.hostname or "").lower() in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def build_self_enrich_params(args) -> List[str]:
    """Expand the concise self-enrich command into safe bulk-enrich flags."""
    state = str(args.state or "").upper()
    if state == "ALL" or state not in SELF_ENRICH_STATES:
        raise ValueError("self-enrich requires exactly one state")

    apply_changes = bool(args.apply)
    fast_mode = bool(getattr(args, "fast", False))
    if apply_changes and fast_mode:
        raise ValueError("--fast is a dry-run probe and cannot be combined with --apply")
    requested_fields = tuple(
        getattr(args, "fields", None)
        or (("bed", "bath") if fast_mode else SELF_ENRICH_FIELDS)
    )
    unknown_fields = set(requested_fields) - set(SELF_ENRICH_FIELDS)
    if unknown_fields:
        raise ValueError(
            "unsupported self-enrich field(s): " + ", ".join(sorted(unknown_fields))
        )
    workers = args.workers if args.workers is not None else (2 if apply_changes else 1)
    batch_size = args.batch_size if args.batch_size is not None else (25 if apply_changes else 5)
    limit = args.limit if args.limit is not None else (None if apply_changes else (1 if fast_mode else 5))
    if workers < 1 or batch_size < 1 or (limit is not None and limit < 0):
        raise ValueError("workers and batch-size must be positive; limit cannot be negative")

    scope = _self_enrich_scope(state, args.suburb)
    checkpoint = str(Path("scratch") / f"self_enrich_{scope}_checkpoint.json")
    rollback = str(Path("scratch") / f"self_enrich_{scope}_rollback.jsonl.gz")
    official_dataset = str(Path("scratch") / "archive.zip")

    params = [
        "--state", state,
        "--workers", str(workers),
        "--batch-size", str(batch_size),
        "--portal-fields", *requested_fields,
        "--no-images",
        "--preserve-payload-shape",
        "--skip-property-source",
        "--property-browser-fallback",
        "--checkpoint-path", checkpoint,
        "--nsw-psi-zip", official_dataset,
    ]
    if args.suburb:
        params.extend(["--suburb", args.suburb])
    if limit is not None:
        params.extend(["--limit", str(limit)])
    if fast_mode:
        params.extend([
            "--portal-http-max-candidates", "1",
            "--portal-browser-max-candidates", "1",
            "--portal-browser-timeout", "30",
        ])
    if args.refresh:
        params.extend(["--force", "--reset-checkpoint"])

    if apply_changes:
        params.extend(["--rollback-journal", rollback])
    else:
        params.extend(["--dry-run", "--no-retry-outages"])

    use_local = bool(getattr(args, "local", False))
    if use_local:
        qdrant_url = "http://127.0.0.1:6333"
        qdrant_key = (
            getattr(args, "local_api_key", None)
            or os.getenv("LOCAL_QDRANT_API_KEY")
            or "local-qdrant-key"
        )
        params.extend(["--url", qdrant_url, "--api-key", qdrant_key])
    else:
        qdrant_url = os.getenv("QDRANT_URI", "http://localhost:6333")
        qdrant_key = os.getenv("QDRANT_API", "").strip('"').strip("'")
    parsed_qdrant = urlparse(qdrant_url)
    allow_remote_http = bool(getattr(args, "allow_http", False))
    if (
        parsed_qdrant.scheme == "http"
        and not _is_local_http_qdrant(qdrant_url)
        and qdrant_key
        and not allow_remote_http
    ):
        raise ValueError(
            "Remote Qdrant uses plaintext HTTP with an API key. "
            "Use HTTPS or rerun self-enrich with --allow-http."
        )
    if _is_local_http_qdrant(qdrant_url) or allow_remote_http:
        params.append("--allow-insecure-api-key")
    return params


def cmd_self_enrich(args):
    mode = "APPLY" if args.apply else "DRY-RUN"
    scope = _self_enrich_scope(args.state, args.suburb)
    print(f"[self-enrich] mode={mode} scope={scope} source=official+portal")
    try:
        params = build_self_enrich_params(args)
    except ValueError as exc:
        print(f"[self-enrich] Error: {exc}")
        return 2
    return run_script("bulk_enrich_master.py", params)


def cmd_crawl_cluster(args):
    """Launches the distributed stealth Playwright crawler cluster."""
    params = [
        "--status", args.status,
        "--state", args.state,
        "--workers", str(args.workers),
        "--max-pages", str(args.max_pages),
    ]
    if args.suburbs:
        params.extend(["--suburbs"] + args.suburbs)
    if args.proxy_file:
        params.extend(["--proxy-file", args.proxy_file])
    if args.dry_run:
        params.append("--dry-run")
    if getattr(args, "headful", False):
        params.append("--headful")
    return run_script(str(Path("app") / "crawler_runner.py"), params)


def cmd_test_proxies(args):
    """Tests the smart proxy pool, displays Australian fingerprints, and checks latency."""
    print("Testing Smart Proxy Pool & Anti-Bot Fingerprint Generator...")
    from app.operations.proxy_manager import SmartProxyManager

    pm = SmartProxyManager()
    print(f"\nTotal Proxies In Pool : {pm.total_proxies}")
    print(f"Available (No Cooldown): {len(pm.available_proxies)}")
    print(f"Rotation Strategy     : {pm.strategy}")

    if pm.proxies:
        print("\nProxy Pool Inspection:")
        for idx, p in enumerate(pm.proxies[:10], 1):
            status = "COOLDOWN" if p.is_in_cooldown else "READY"
            print(f"  {idx}. {p.protocol}://{p.host}:{p.port} [{status}] (Success: {p.success_count}, Fails: {p.failure_count})")
        if len(pm.proxies) > 10:
            print(f"  ... and {len(pm.proxies) - 10} more.")
    else:
        print("Note: No proxies configured in PROXY_LIST or PROXY_FILE. Falling back to direct connection.")

    print("\nSample Australian Desktop Fingerprint:")
    fp = pm.get_australian_fingerprint()
    print(f"  • User-Agent : {fp['headers']['User-Agent']}")
    print(f"  • Viewport   : {fp['viewport']['width']}x{fp['viewport']['height']}")
    print(f"  • Timezone   : {fp['timezone_id']}")
    print(f"  • Geolocation: {fp['geolocation']}")


# ---------------------------------------------------------------------------
# PHASE 2: ML AVM, MULTIMODAL SEARCH & ROOM CLASSIFIER COMMANDS
# ---------------------------------------------------------------------------
def cmd_train_avm(args):
    """Train Gradient Boosting AVM model on sales records and benchmark MdAPE."""
    cmd = [
        PYTHON_EXE, "app/train_avm.py",
        "--samples", str(args.samples),
        "--state", args.state,
        "--out", args.out
    ]
    if args.bootstrap:
        cmd.append("--bootstrap")

    print(f"[CLI] Launching ML AVM Training Pipeline...")
    return subprocess.run(cmd, cwd=str(BASE_DIR)).returncode


def cmd_predict_avm(args):
    """Predict real estate valuation range using ML AVM."""
    try:
        from app.operations.ml_avm import MLAutomatedValuationModel
    except ImportError:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from app.operations.ml_avm import MLAutomatedValuationModel

    avm = MLAutomatedValuationModel()
    pred = avm.predict(
        lat=args.lat,
        lon=args.lon,
        bed=args.bed,
        bath=args.bath,
        parking=args.parking,
        area_sqm=args.area,
        prop_cat=args.prop_cat,
        state=args.state,
        suburb=args.suburb
    )

    print("\n" + "=" * 65)
    print(f" ML AVM PROPERTY VALUATION ESTIMATE")
    print("=" * 65)
    print(f"Property:        {args.suburb}, {args.state} ({args.prop_cat.title()})")
    print(f"Configuration:   {args.bed} Bed | {args.bath} Bath | {args.parking} Car | {args.area} m²")
    print(f"Distance to CBD: {pred.features_used.get('distance_to_cbd_km', 'N/A')} km")
    print("-" * 65)
    print(f"Valuation Mid:   ${pred.valuation_mid:,} AUD")
    print(f"Valuation Low:   ${pred.valuation_low:,} AUD (10th percentile)")
    print(f"Valuation High:  ${pred.valuation_high:,} AUD (90th percentile)")
    print(f"Confidence:      {pred.confidence_score * 100:.1f}%")
    print(f"Valuation Model: {pred.valuation_method}")
    print(f"Valuation Date:  {pred.valuation_date}")
    print("=" * 65 + "\n")
    return 0


def cmd_visual_search(args):
    """Execute natural language multimodal visual query against Qdrant."""
    try:
        from app.operations.multimodal_search import MultimodalSearchEngine, MultimodalEmbedder
    except ImportError:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from app.operations.multimodal_search import MultimodalSearchEngine, MultimodalEmbedder

    qdrant_url = os.getenv("QDRANT_URI", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API", "").strip('"').strip("'") or None

    client = None
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=qdrant_url, api_key=qdrant_key, timeout=15)
        client.get_collections()
    except Exception as e:
        print(f"[CLI] Qdrant connection offline ({e}). Running visual embedding simulation.")
        client = None

    engine = MultimodalSearchEngine(qdrant_client=client)
    print(f"\n[CLI] Multimodal Query: \"{args.query}\"")
    print(f"Target Collection:     {args.collection}")
    print(f"Filters:               State={args.state}, Suburb={args.suburb}, MinBed={args.min_bed}")
    print("-" * 65)

    results = engine.search_by_prompt(
        prompt=args.query,
        collection_name=args.collection,
        limit=args.limit,
        state=args.state,
        suburb=args.suburb,
        min_bed=args.min_bed,
        max_price=args.max_price
    )

    if not results or (results and results[0].get("status") == "simulated"):
        emb = engine.embedder.embed_text(args.query)
        print(f"✓ Generated 768-D Multimodal Unit Vector (norm = 1.0)")
        print(f"  Vector sample: [{emb[0]:.4f}, {emb[1]:.4f}, {emb[2]:.4f}, ..., {emb[-1]:.4f}]")
        print(f"  Search engine is ready to query live Qdrant cluster points.")
    else:
        print(f"Found {len(results)} visual concept matches:")
        for idx, hit in enumerate(results, 1):
            p = hit.get("payload") or {}
            print(f"  {idx}. [Score: {hit.get('score')}] {p.get('street', 'N/A')}, {p.get('suburb', 'N/A')} ({p.get('propertyType', 'N/A')}) - ${p.get('expectedPrice', 0):,}")
    print()
    return 0


def cmd_classify_rooms(args):
    """Classify listing photo URLs into structured room categories."""
    try:
        from app.operations.room_classifier import RoomClassifier
    except ImportError:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from app.operations.room_classifier import RoomClassifier

    classifier = RoomClassifier()
    urls = args.urls or [
        "https://images.realestate.com.au/hero_facade_front.jpg",
        "https://images.domain.com.au/property/123/kitchen-island-marble.jpg",
        "https://images.domain.com.au/property/123/master-bedroom-balcony.jpg",
        "https://images.domain.com.au/property/123/luxury-bathroom-ensuite.jpg",
        "https://images.domain.com.au/property/123/swimming-pool-backyard-garden.jpg",
        "https://images.domain.com.au/property/123/architectural-floorplan-2d.jpg"
    ]

    print("\n" + "=" * 65)
    print(f" ROOM SCENE CLASSIFIER & AUTO-TAGGING ENGINE")
    print("=" * 65)
    print(f"Classifying {len(urls)} listing photos...")

    res = classifier.classify_gallery(urls)
    print(f"\nPrimary Facade Photo: {res['primary_photo']}")
    print(f"Has Floorplan:        {res['has_floorplan']}")
    print(f"\nDetected Room Breakdown:")
    for cat, count in res["room_counts_detected"].items():
        print(f"  • {cat.replace('_', ' ').title():<15}: {count} photo(s)")

    print(f"\nTagged Photo Groups:")
    for cat, items in res["tagged_photos"].items():
        if items:
            print(f"  [{cat}]:")
            for u in items:
                print(f"    - {u}")
    print("=" * 65 + "\n")
    return 0


# ---------------------------------------------------------------------------
# PHASE 3: SPATIAL GIS, LOCATION SCORES & GEOFENCING COMMANDS
# ---------------------------------------------------------------------------
def cmd_score_location(args):
    """Calculate multi-dimensional location scores (Schools, Transit, Amenities, Overall)."""
    try:
        from app.operations.location_score_engine import LocationScoreEngine
    except ImportError:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from app.operations.location_score_engine import LocationScoreEngine

    engine = LocationScoreEngine()
    scores = engine.calculate_scores(
        lat=args.lat,
        lon=args.lon,
        state=args.state,
        suburb=args.suburb
    )

    print("\n" + "=" * 65)
    print(" LOCATION INTELLIGENCE & INFRASTRUCTURE SCORECARD")
    print("=" * 65)
    print(f"Target Point:      ({args.lat:.4f}, {args.lon:.4f}) | {args.suburb or 'N/A'}, {args.state.upper()}")
    print(f"Distance to CBD:   {scores['distance_to_cbd_km']} km")
    print("-" * 65)
    print(f"OVERALL LOCATION SCORE:  {scores['overall']} / 10.0")
    print("-" * 65)
    print(f"  • Schools & Catchment: {scores['schools']} / 10.0")
    stn = scores["nearest_station"]
    sch = scores["nearest_school"]
    hosp = scores["nearest_hospital"]
    print(f"    Nearest School:      {sch['name']} ({sch['distance_km']} km, {sch.get('type')})")
    print(f"  • Transit & WalkScore: {scores['transit']} / 10.0")
    print(f"    Nearest Transit:     {stn['name']} ({stn['distance_km']} km, {stn.get('type')})")
    print(f"  • Amenities & Health:  {scores['amenities']} / 10.0")
    print(f"    Nearest Hospital:    {hosp['name']} ({hosp['distance_km']} km)")
    print("=" * 65 + "\n")
    return 0


def cmd_geofence_monitor(args):
    """Run geospatial geofencing monitor to detect matching properties within radius or boundary."""
    try:
        from app.operations.geo_alerting import GeospatialAlertMonitor, GeofenceRule
        from app.operations.location_score_engine import LocationScoreEngine
    except ImportError:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from app.operations.geo_alerting import GeospatialAlertMonitor, GeofenceRule
        from app.operations.location_score_engine import LocationScoreEngine

    monitor = GeospatialAlertMonitor()
    rule = GeofenceRule(
        name=args.name,
        center_lat=args.center_lat,
        center_lon=args.center_lon,
        radius_km=args.radius_km,
        min_bed=args.min_bed,
        max_price=args.max_price,
        property_category=args.prop_cat,
        min_location_score=args.min_score
    )
    monitor.add_rule(rule)

    print("\n" + "=" * 65)
    print(" GEOSPATIAL GEOFENCING & ALERTING MONITOR")
    print("=" * 65)
    print(f"Active Rule:       '{rule.name}'")
    print(f"Center & Radius:   ({args.center_lat:.4f}, {args.center_lon:.4f}) within {args.radius_km} km")
    if args.max_price:
        print(f"Max Price Budget:  ${args.max_price:,} AUD")
    if args.min_bed:
        print(f"Min Bedrooms:      {args.min_bed}+")
    if args.min_score:
        print(f"Min Location Score:{args.min_score} / 10.0")
    print("-" * 65)

    # Test surveillance on sample properties
    loc_engine = LocationScoreEngine()
    test_properties = [
        {
            "id": "prop_syd_001",
            "street": "12 Castlereagh Street",
            "suburb": "Sydney",
            "state": args.state,
            "lat": args.center_lat + 0.005,
            "lon": args.center_lon + 0.005,
            "bed": 3,
            "price": 1850000,
            "property_category": "unit"
        },
        {
            "id": "prop_syd_002",
            "street": "45 Outside Radius Road",
            "suburb": "Regional Outskirts",
            "state": args.state,
            "lat": args.center_lat + 0.35,  # ~38 km away
            "lon": args.center_lon + 0.35,
            "bed": 4,
            "price": 950000,
            "property_category": "house"
        }
    ]

    total_alerts = 0
    for p in test_properties:
        loc_engine.enrich_property_payload(p)
        alerts = monitor.evaluate_property(p, event_type="NEW_LISTING")
        for a in alerts:
            total_alerts += 1
            print(f"🔔 ALERT TRIGGERED [{a.event_type}]:")
            print(f"   Address:        {a.address}")
            print(f"   Price:          ${a.price:,} AUD")
            print(f"   Distance:       {a.distance_km} km to center")
            print(f"   Location Score: {a.location_score} / 10.0")
            print(f"   Alert ID:       {a.alert_id}")

    print("-" * 65)
    print(f"Geofence monitoring complete. {total_alerts} alert(s) dispatched.\n")
    return 0


def cmd_parcel_inspect(args):
    """Inspect cadastral parcel polygon, calculate geodesic land area, and verify zoning."""
    try:
        from app.operations.cadastral_spatial import CadastralSpatialEngine
    except ImportError:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from app.operations.cadastral_spatial import CadastralSpatialEngine

    engine = CadastralSpatialEngine()
    parcel = engine.synthesize_parcel_for_address(
        street=args.street,
        suburb=args.suburb,
        state=args.state,
        lat=args.lat,
        lon=args.lon,
        area_sqm=args.area,
        zoning_code=args.zoning
    )

    print("\n" + "=" * 65)
    print(" CADASTRAL PARCEL GEOMETRY & ZONING INSPECTOR")
    print("=" * 65)
    print(f"Cadastral Key:      {parcel.block_key} ({parcel.state})")
    print(f"Lot / Plan:         Lot {parcel.lot} on {parcel.plan}")
    print(f"Zoning Code:        {parcel.zoning_code}")
    print(f"Zoning Description: {parcel.zoning_description}")
    print(f"Calculated Area:    {parcel.land_area_sqm:,.1f} m² (Shoelace geodesic)")
    print(f"Centroid:           ({parcel.centroid_lat:.6f}, {parcel.centroid_lon:.6f})")
    print(f"Bounding Box:       {parcel.bbox}")
    print("-" * 65)
    geojson_feat = parcel.to_geojson_feature()
    print(f"GeoJSON Geometry:   {geojson_feat['geometry']['type']} with {len(geojson_feat['geometry']['coordinates'][0])} vertices")
    print("=" * 65 + "\n")
    return 0


def cmd_serve_api(args):
    """Starts the FastAPI web service via uvicorn."""
    host = args.host or os.getenv("APP_HOST", "0.0.0.0")
    port = str(args.port or os.getenv("APP_PORT", 8000))
    reload_flag = ["--reload"] if args.reload else []
    cmd = [PYTHON_EXE, "-m", "uvicorn", "app.main:app", "--host", host, "--port", port] + reload_flag
    print(f"[CLI] Starting Suburb Scraper API on http://{host}:{port}")
    try:
        subprocess.run(cmd, cwd=str(BASE_DIR))
    except KeyboardInterrupt:
        print("\n[CLI] Server stopped.")


def cmd_benchmark(args):
    """Run data pipeline benchmark suite at 1K, 10K, or 100K records."""
    from scripts.benchmark_pipeline import run_benchmark_scale, generate_markdown_report, REPO_ROOT
    scales = [args.scale] if args.scale else [1_000, 10_000, 100_000]
    results = []
    for s in scales:
        res = run_benchmark_scale(
            scale=s,
            workers=args.workers,
            batch_size=args.batch_size,
            simulate_crash=not args.no_crash
        )
        results.append(res)
    output_file = str(REPO_ROOT / "docs" / "data_pipeline_benchmark_report.md")
    generate_markdown_report(results, output_file)
    return 0


def cmd_dashboard(args):
    """Render operational dashboard with live system metrics."""
    from app.operations.pipeline_dashboard import PipelineDashboard
    dash = PipelineDashboard(target_volume=args.target_volume)
    dash.update_metrics(
        discovered=args.discovered,
        processed=args.processed,
        succeeded=args.succeeded,
        failed=args.failed,
        enriched=args.enriched
    )
    print(dash.render_cli())
    return 0


# ---------------------------------------------------------------------------
# CLI PARSER ENTRYPOINT
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Australian Property Master Unified Pipeline CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available Commands")

    # 1. status
    subparsers.add_parser("status", help="Inspect Qdrant collections, count points, and verify env")

    # 2. audit-quality
    p_audit = subparsers.add_parser("audit-quality", help="Sample Qdrant points and audit data quality")
    p_audit.add_argument("--sample-size", type=int, default=500, help="Number of records to sample (default: 500)")
    p_audit.add_argument("--state", default=None, help="Filter by Australian state (NSW, ACT, VIC, etc.)")

    # 3. ingest-gnaf
    p_gnaf = subparsers.add_parser("ingest-gnaf", help="Stream G-NAF national addresses into Qdrant")
    p_gnaf.add_argument("--batch-size", type=int, default=2000, help="Batch point upload size (default: 2000)")
    p_gnaf.add_argument("--states", nargs="+", default=None, help="Specific states to ingest (e.g. NSW ACT)")

    # 4. ingest-act
    subparsers.add_parser("ingest-act", help="Ingest ACT Government official addresses into Qdrant")

    # 5. merge-sales
    p_sales = subparsers.add_parser("merge-sales", help="Merge Valuer General property sales records")
    p_sales.add_argument("--suburb", default=None, help="Process a single suburb")
    p_sales.add_argument("--concurrency", type=int, default=2, help="Worker concurrency (default: 2)")
    p_sales.add_argument("--limit-suburbs", type=int, default=None, help="Limit number of suburbs")
    p_sales.add_argument("--zip-path", default=None, help="Custom path to sales zip file")
    p_sales.add_argument("--dry-run", action="store_true", help="Match records without writing to Qdrant")

    # 6. enrich-sold
    p_sold = subparsers.add_parser("enrich-sold", help="Enrich master points from PropertySoldV2")
    p_sold.add_argument("--state", choices=["NSW", "ACT"], default="NSW", help="State to enrich (NSW or ACT)")

    # 7. dedup
    p_dedup = subparsers.add_parser("dedup", help="Deduplicate property records within suburbs")
    p_dedup.add_argument("--state", default="NSW", help="State code (default: NSW)")
    p_dedup.add_argument("--suburb", default=None, help="Specific suburb to deduplicate")
    p_dedup.add_argument("--concurrency", type=int, default=2, help="Worker threads")
    p_dedup.add_argument("--limit-suburbs", type=int, default=None, help="Limit number of suburbs")
    p_dedup.add_argument("--dry-run", action="store_true", help="Find duplicates without deleting")

    # 8. bulk-enrich
    p_enrich = subparsers.add_parser("bulk-enrich", help="Run high-throughput AVM & attribute enrichment")
    p_enrich.add_argument("--state", default=None, help="Target state (NSW, ACT, VIC, etc.)")
    p_enrich.add_argument("--suburb", default=None, help="Target specific suburb")
    p_enrich.add_argument("--workers", type=int, default=8, help="Worker threads (default: 8)")
    p_enrich.add_argument("--batch-size", type=int, default=1500, help="Batch scroll limit")
    p_enrich.add_argument("--limit", type=int, default=None, help="Total points limit")
    p_enrich.add_argument("--dry-run", action="store_true", help="Simulate without writing updates")
    p_enrich.add_argument("--force", action="store_true", help="Force re-enrichment of existing fields")
    p_enrich.add_argument("--skip-portal", action="store_true", help="Skip live portal scraping / profile hydration")
    p_enrich.add_argument("--portal-fields", nargs="+", default=None, help="Only persist these verified portal fields")
    p_enrich.add_argument("--no-images", action="store_true", help="Do not persist image URLs")
    p_enrich.add_argument("--property-browser-fallback", action="store_true", help="Self-scrape exact public property profiles in reusable Chrome")
    p_enrich.add_argument("--property-browser-headless", action="store_true", help="Run browser fallback headless (less reliable on protected portals)")
    p_enrich.add_argument("--use-property-source", action="store_true", help="Opt in to reading another scraped Qdrant collection")
    p_enrich.add_argument("--rollback-journal", default=None, help="Gzip JSONL before-image journal for writes")

    # 9. self-enrich (safe, concise self-scrape preset)
    p_self = subparsers.add_parser(
        "self-enrich",
        help="Self-scrape verified property fields with safe defaults",
    )
    p_self.add_argument("state", choices=SELF_ENRICH_STATES, help="One Australian state")
    p_self.add_argument("--suburb", default=None, help="Optional suburb scope")
    p_self.add_argument("--apply", action="store_true", help="Write verified sparse patches; default is a 5-record dry-run")
    p_self.add_argument("--refresh", action="store_true", help="Refresh sources and restart this scope from the beginning")
    p_self.add_argument("--workers", type=int, default=None, help="Override workers (dry-run: 1, apply: 2)")
    p_self.add_argument("--batch-size", type=int, default=None, help="Override batch size (dry-run: 5, apply: 25)")
    p_self.add_argument("--limit", type=int, default=None, help="Override record limit (dry-run: 5, apply: unlimited)")
    p_self.add_argument("--fast", action="store_true", help="Run a bounded one-record bed/bath probe with short browser timeout")
    p_self.add_argument("--fields", nargs="+", choices=SELF_ENRICH_FIELDS, default=None, help="Verified fields to scrape (fast default: bed bath)")
    p_self.add_argument("--allow-http", action="store_true", help="Explicitly allow an API key to a remote Qdrant over plaintext HTTP")
    p_self.add_argument("--local", action="store_true", help="Use local Qdrant at 127.0.0.1:6333 instead of QDRANT_URI")
    p_self.add_argument("--local-api-key", default=None, help="Override LOCAL_QDRANT_API_KEY (default: local-qdrant-key)")

    # 10. crawl-cluster (Phase 1)
    p_crawl = subparsers.add_parser("crawl-cluster", help="Run distributed stealth Playwright crawler with proxy rotation")
    p_crawl.add_argument("--status", default="sale", choices=["sale", "rent", "sold"], help="Listing status")
    p_crawl.add_argument("--suburbs", nargs="+", default=["Sydney", "Parramatta"], help="Suburbs to crawl")
    p_crawl.add_argument("--state", default="NSW", help="Australian state (default: NSW)")
    p_crawl.add_argument("--workers", type=int, default=2, help="Concurrency workers")
    p_crawl.add_argument("--max-pages", type=int, default=3, help="Max search pages per suburb")
    p_crawl.add_argument("--proxy-file", default=None, help="Path to proxy list text file")
    p_crawl.add_argument("--dry-run", action="store_true", help="Scrape without writing to Qdrant")
    p_crawl.add_argument("--headful", action="store_true", help="Launch visible browser window (helps bypass anti-bot challenges)")

    # 10. test-proxies (Phase 1)
    subparsers.add_parser("test-proxies", help="Test smart proxy pool, inspect Australian fingerprints & availability")

    # 11. serve-api
    p_api = subparsers.add_parser("serve-api", help="Start the FastAPI Scraper & Health Service")
    p_api.add_argument("--host", default=None, help="API host (default: 0.0.0.0)")
    p_api.add_argument("--port", type=int, default=None, help="API port (default: 8000)")
    p_api.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    # 12. train-avm (Phase 2)
    p_train = subparsers.add_parser("train-avm", help="Train Gradient Boosting ML AVM model on historical sales comps")
    p_train.add_argument("--samples", type=int, default=1000, help="Training dataset samples (default: 1000)")
    p_train.add_argument("--state", default="NSW", help="Target Australian state (default: NSW)")
    p_train.add_argument("--out", default="models/avm_property_model.joblib", help="Model artifact output path")
    p_train.add_argument("--bootstrap", action="store_true", help="Force synthetic bootstrap sales comps")

    # 13. predict-avm (Phase 2)
    p_pred = subparsers.add_parser("predict-avm", help="Predict real estate valuation range & confidence using ML AVM")
    p_pred.add_argument("--state", default="NSW", help="State code (default: NSW)")
    p_pred.add_argument("--suburb", default="Sydney", help="Suburb name (default: Sydney)")
    p_pred.add_argument("--bed", type=int, default=3, help="Bedroom count (default: 3)")
    p_pred.add_argument("--bath", type=int, default=2, help="Bathroom count (default: 2)")
    p_pred.add_argument("--parking", type=int, default=1, help="Parking / garage count (default: 1)")
    p_pred.add_argument("--area", type=float, default=550.0, help="Land/floor area in m2 (default: 550)")
    p_pred.add_argument("--prop-cat", default="house", choices=["house", "unit", "townhouse"], help="Property category")
    p_pred.add_argument("--lat", type=float, default=None, help="Latitude")
    p_pred.add_argument("--lon", type=float, default=None, help="Longitude")

    # 14. visual-search (Phase 2)
    p_vsearch = subparsers.add_parser("visual-search", help="Execute natural language multimodal visual query against Qdrant")
    p_vsearch.add_argument("--query", required=True, help="Visual concept description (e.g. 'Modern kitchen with marble island')")
    p_vsearch.add_argument("--collection", default="PropertyMaster", help="Target Qdrant collection")
    p_vsearch.add_argument("--limit", type=int, default=5, help="Number of matching results (default: 5)")
    p_vsearch.add_argument("--state", default=None, help="Filter by state (NSW, VIC, QLD, ACT)")
    p_vsearch.add_argument("--suburb", default=None, help="Filter by suburb")
    p_vsearch.add_argument("--min-bed", type=int, default=None, help="Minimum bedrooms")
    p_vsearch.add_argument("--max-price", type=int, default=None, help="Maximum price limit")

    # 15. classify-rooms (Phase 2)
    p_rooms = subparsers.add_parser("classify-rooms", help="Classify property photo URLs into structured room categories")
    p_rooms.add_argument("--urls", nargs="+", default=None, help="List of property image URLs to classify")

    # 16. score-location (Phase 3)
    p_loc = subparsers.add_parser("score-location", help="Calculate location intelligence scorecard (Schools, Transit, Amenities, CBD)")
    p_loc.add_argument("--lat", type=float, required=True, help="Latitude")
    p_loc.add_argument("--lon", type=float, required=True, help="Longitude")
    p_loc.add_argument("--state", default="NSW", help="State code (default: NSW)")
    p_loc.add_argument("--suburb", default=None, help="Suburb name")

    # 17. geofence-monitor (Phase 3)
    p_geo = subparsers.add_parser("geofence-monitor", help="Run geospatial geofencing monitor for radius or boundary surveillance")
    p_geo.add_argument("--name", default="Metropolitan Radius Monitor", help="Geofence rule name")
    p_geo.add_argument("--center-lat", type=float, default=-33.8688, help="Center latitude (default: Sydney CBD)")
    p_geo.add_argument("--center-lon", type=float, default=151.2093, help="Center longitude (default: Sydney CBD)")
    p_geo.add_argument("--radius-km", type=float, default=5.0, help="Search radius in kilometers (default: 5.0)")
    p_geo.add_argument("--state", default="NSW", help="State filter (default: NSW)")
    p_geo.add_argument("--max-price", type=float, default=None, help="Maximum budget threshold")
    p_geo.add_argument("--min-bed", type=int, default=None, help="Minimum bedrooms")
    p_geo.add_argument("--prop-cat", default=None, help="Property category (house, unit, townhouse)")
    p_geo.add_argument("--min-score", type=float, default=None, help="Minimum overall location score")

    # 18. parcel-inspect (Phase 3)
    p_parcel = subparsers.add_parser("parcel-inspect", help="Inspect cadastral parcel geometry, geodesic land area, and zoning")
    p_parcel.add_argument("--lat", type=float, required=True, help="Latitude coordinate")
    p_parcel.add_argument("--lon", type=float, required=True, help="Longitude coordinate")
    p_parcel.add_argument("--area", type=float, default=550.0, help="Land area in m2 (default: 550)")
    p_parcel.add_argument("--zoning", default="R2", help="Zoning code (default: R2)")
    p_parcel.add_argument("--street", default="George Street", help="Street name")
    p_parcel.add_argument("--suburb", default="Sydney", help="Suburb name")
    p_parcel.add_argument("--state", default="NSW", help="State code (default: NSW)")

    # 19. view-payload
    p_view = subparsers.add_parser("view-payload", help="Inspect and display payloads of sold, sale, or rent properties")
    p_view.add_argument("--status", choices=["sold", "sale", "rent", "off_market", "all"], default="sold", help="Listing status (default: sold)")
    p_view.add_argument("--state", default=None, help="Filter by State (NSW, VIC, QLD, ACT, etc.)")
    p_view.add_argument("--suburb", default=None, help="Filter by Suburb name")
    p_view.add_argument("--limit", type=int, default=1, help="Number of records to display (default: 1)")
    p_view.add_argument("--collection", default="PropertyMaster", help="Target collection (default: PropertyMaster)")
    p_view.add_argument("--raw-json", action="store_true", help="Print only raw JSON")

    # 20. benchmark
    p_bench = subparsers.add_parser("benchmark", help="Run empirical pipeline benchmark suite across 1K, 10K, or 100K records")
    p_bench.add_argument("--scale", type=int, choices=[1000, 10000, 100000], default=None, help="Specific benchmark scale (default: all)")
    p_bench.add_argument("--workers", type=int, default=8, help="Worker threads (default: 8)")
    p_bench.add_argument("--batch-size", type=int, default=250, help="Batch size (default: 250)")
    p_bench.add_argument("--no-crash", action="store_true", help="Skip simulated crash and recovery testing")

    # 21. dashboard
    p_dash = subparsers.add_parser("dashboard", help="Render operational pipeline dashboard with metrics and costs")
    p_dash.add_argument("--target-volume", type=int, default=16000000, help="Target volume (default: 16M)")
    p_dash.add_argument("--discovered", type=int, default=16000000, help="Discovered records count")
    p_dash.add_argument("--processed", type=int, default=1550000, help="Processed records count")
    p_dash.add_argument("--succeeded", type=int, default=1542000, help="Succeeded records count")
    p_dash.add_argument("--failed", type=int, default=8000, help="Failed records count")
    p_dash.add_argument("--enriched", type=int, default=1450000, help="Enriched records count")

    args = parser.parse_args()

    if not args.command:
        print_banner()
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "status": cmd_status,
        "audit-quality": cmd_audit_quality,
        "ingest-gnaf": cmd_ingest_gnaf,
        "ingest-act": cmd_ingest_act,
        "merge-sales": cmd_merge_sales,
        "enrich-sold": cmd_enrich_sold,
        "dedup": cmd_dedup,
        "bulk-enrich": cmd_bulk_enrich,
        "self-enrich": cmd_self_enrich,
        "crawl-cluster": cmd_crawl_cluster,
        "test-proxies": cmd_test_proxies,
        "train-avm": cmd_train_avm,
        "predict-avm": cmd_predict_avm,
        "visual-search": cmd_visual_search,
        "classify-rooms": cmd_classify_rooms,
        "score-location": cmd_score_location,
        "geofence-monitor": cmd_geofence_monitor,
        "parcel-inspect": cmd_parcel_inspect,
        "serve-api": cmd_serve_api,
        "view-payload": cmd_view_payload,
        "benchmark": cmd_benchmark,
        "dashboard": cmd_dashboard,
    }

    cmd_fn = dispatch.get(args.command)
    if cmd_fn:
        sys.exit(cmd_fn(args) or 0)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
