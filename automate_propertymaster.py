"""State-by-state PropertyMaster collection, enrichment, audit, and resume orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

from qdrant_client import QdrantClient

from audit_propertymaster_provenance import audit_collection
from bulk_enrich_master import PORTAL_FIELD_PATHS, run_bulk_enrichment
from stream_gnaf_to_qdrant import (
    DirectGnafExtractor,
    GNAF_ZIP_URL,
    ensure_collection,
    process_state,
)


STATE_ORDER = ("ACT", "NT", "TAS", "SA", "WA", "VIC", "QLD", "NSW")


def target_run_directory(url: str, collection: str, root: Path = Path("scratch") / "automation") -> Path:
    identity = f"{url.rstrip('/')}|{collection}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return root / f"{collection}_{digest}"


def checkpoint_path(run_directory: Path, state: str, phase: str) -> Path:
    return run_directory / state.upper() / f"{phase}_checkpoint.json"


def write_manifest(path: Path, manifest: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def run_with_retries(
    operation: Callable[[], Dict],
    *,
    attempts: int,
    base_delay_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict:
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            if isinstance(result, dict) and result.get("status") == "failed":
                raise RuntimeError(str(result.get("error") or "operation reported failure"))
            return result or {"status": "completed"}
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = min(base_delay_seconds * (2 ** (attempt - 1)), 900.0)
            print(f"[Retry {attempt}/{attempts}] {exc}; retrying in {delay:.0f}s", flush=True)
            sleep(delay)
    raise RuntimeError(f"operation failed after {attempts} attempt(s): {last_error}") from last_error


def build_gnaf_context(args: argparse.Namespace):
    effective_key = args.api_key if args.url.lower().startswith("https://") or args.allow_insecure_api_key else None
    client = QdrantClient(url=args.url, api_key=effective_key, timeout=300, check_compatibility=False)
    ensure_collection(client, args.collection)
    extractor = DirectGnafExtractor(GNAF_ZIP_URL)
    return client, extractor


def run_gnaf_import(args: argparse.Namespace, state: str, context) -> Dict:
    client, extractor = context
    uploaded = process_state(
        client,
        extractor,
        state,
        args.collection,
        limit=args.limit or None,
        batch_size=args.batch_size,
        force=False,
    )
    return {"status": "completed", "uploaded": uploaded}


def run_enrichment_phase(
    args: argparse.Namespace,
    state: str,
    run_directory: Path,
    phase: str,
    *,
    skip_portal: bool,
) -> Dict:
    state_directory = run_directory / state.upper()
    return run_bulk_enrichment(
        url=args.url,
        api_key=args.api_key,
        collection=args.collection,
        target_state=state,
        batch_size=args.batch_size,
        max_limit=args.limit,
        force_update=args.force,
        dry_run=args.dry_run,
        workers=args.workers,
        skip_portal=skip_portal,
        reset_checkpoint=not args.resume,
        nsw_psi_zip=args.nsw_psi_zip,
        allow_insecure_api_key=args.allow_insecure_api_key,
        portal_fields=[] if skip_portal else args.portal_fields,
        no_images=args.no_images,
        checkpoint_path=str(checkpoint_path(run_directory, state, phase)),
        rollback_journal=str(state_directory / f"{phase}_rollback.jsonl.gz"),
    )


def normalize_states(values: Iterable[str]) -> list[str]:
    states = [value.upper() for value in values]
    if "ALL" in states:
        return list(STATE_ORDER)
    unknown = [state for state in states if state not in STATE_ORDER]
    if unknown:
        raise ValueError(f"Unsupported state(s): {', '.join(unknown)}")
    return list(dict.fromkeys(states))


def run_automation(args: argparse.Namespace) -> Dict:
    if args.api_key and args.url.lower().startswith("http://") and not args.allow_insecure_api_key:
        raise RuntimeError("Refusing API key over HTTP without --allow-insecure-api-key")

    states = normalize_states(args.states)
    run_directory = target_run_directory(args.url, args.collection, Path(args.run_root))
    manifest_path = run_directory / "manifest.json"
    manifest = {
        "target": {"url": args.url, "collection": args.collection},
        "states": states,
        "portalFields": list(args.portal_fields),
        "noImages": args.no_images,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "results": {},
    }
    write_manifest(manifest_path, manifest)
    gnaf_context = None
    if not args.skip_gnaf and not args.dry_run:
        gnaf_context = build_gnaf_context(args)

    for state in states:
        state_result = {"status": "running", "phases": {}}
        manifest["results"][state] = state_result
        write_manifest(manifest_path, manifest)
        print(f"\n{'=' * 76}\nAUTOMATION STATE: {state}\n{'=' * 76}")

        phases = []
        if gnaf_context is not None:
            phases.append(("gnaf", lambda state=state: run_gnaf_import(args, state, gnaf_context)))
        if state == "NSW":
            phases.append((
                "official",
                lambda state=state: run_enrichment_phase(args, state, run_directory, "official", skip_portal=True),
            ))
        phases.append((
            "portal",
            lambda state=state: run_enrichment_phase(args, state, run_directory, "portal", skip_portal=False),
        ))
        if not args.dry_run:
            phases.append((
                "audit",
                lambda state=state: audit_collection(
                    url=args.url,
                    api_key=args.api_key,
                    collection=args.collection,
                    state=state,
                    batch_size=max(args.batch_size, 500),
                    allow_insecure_api_key=args.allow_insecure_api_key,
                ),
            ))

        for phase_name, operation in phases:
            print(f"\n[{state}] Phase: {phase_name}")
            try:
                phase_result = run_with_retries(
                    operation,
                    attempts=args.max_state_retries,
                    base_delay_seconds=args.retry_base_seconds,
                )
                state_result["phases"][phase_name] = phase_result
                write_manifest(manifest_path, manifest)
            except Exception as exc:
                state_result["status"] = "failed"
                state_result["failedPhase"] = phase_name
                state_result["error"] = str(exc)
                write_manifest(manifest_path, manifest)
                print(f"[{state}] Failed in {phase_name}; checkpoint retained. Moving to next state: {exc}")
                break
        else:
            state_result["status"] = "completed"
            write_manifest(manifest_path, manifest)

    failures = [state for state, result in manifest["results"].items() if result.get("status") != "completed"]
    manifest["finishedAt"] = datetime.now(timezone.utc).isoformat()
    manifest["status"] = "completed_with_failures" if failures else "completed"
    manifest["failedStates"] = failures
    write_manifest(manifest_path, manifest)
    print(f"\nAutomation manifest: {manifest_path}")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect and evidence-gate PropertyMaster data state by state")
    parser.add_argument("--url", required=True, help="Qdrant URL")
    parser.add_argument("--api-key", default=None, help="Qdrant API key")
    parser.add_argument("--allow-insecure-api-key", action="store_true", help="Allow API key over plaintext HTTP")
    parser.add_argument("--collection", default="PropertyMaster")
    parser.add_argument("--states", nargs="+", default=["NSW"], help="State list or ALL")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--portal-fields", nargs="+", choices=sorted(PORTAL_FIELD_PATHS), default=["bed", "bath"])
    parser.add_argument("--no-images", action="store_true", help="Do not retain image URLs")
    parser.add_argument("--nsw-psi-zip", default=str(Path("scratch") / "archive.zip"))
    parser.add_argument("--max-state-retries", type=int, default=5)
    parser.add_argument("--retry-base-seconds", type=float, default=60.0)
    parser.add_argument("--resume", action="store_true", help="Resume endpoint-scoped checkpoints")
    parser.add_argument("--force", action="store_true", help="Refresh accepted source fields")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-gnaf", action="store_true", help="Assume G-NAF addresses already exist")
    parser.add_argument("--run-root", default=str(Path("scratch") / "automation"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = run_automation(args)
    return 1 if manifest.get("failedStates") else 0


if __name__ == "__main__":
    raise SystemExit(main())
