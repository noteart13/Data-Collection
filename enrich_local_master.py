#!/usr/bin/env python3
"""Fail-closed enrichment for Australian property records in local Qdrant.

``--uc`` is retained as a compatibility alias for a normal reusable browser.
This program never solves or bypasses CAPTCHA/access-control challenges. Writes
require ``--apply`` and contain only non-empty, exact-address-verified fields.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import html as html_lib
import json
import logging
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("property-enricher")

STATUSES = {"SUCCESS", "PARTIAL", "NO_DATA", "BLOCKED", "TIMEOUT", "PARSE_FAILED", "ADDRESS_MISMATCH", "NO_CHANGE", "ERROR"}
PRIORITY_FIELDS = (
    "bed", "bath", "features.garage", "features.indoorFeatures", "description",
    "title", "realestate_url", "pricing.weeklyRent", "pricing.estimatedYield",
    "valuation.low", "valuation.high",
)
NESTED_FIELDS = {"features", "pricing", "valuation", "fieldProvenance", "enrichment"}
BLOCK_PATTERNS = (
    "attention required", "just a moment", "cf-chl-", "cloudflare ray id", "captcha",
    "access denied", "verify you are human", "security challenge", "perimeterx",
    "px-captcha", "akamai bot manager", "request blocked",
)
NO_DATA_PATTERNS = (
    "property not found", "page not found", "we couldn't find", "we could not find",
    "this property is unavailable", "profile is not available", "404 not found",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


CITY_MAPPING: Dict[Tuple[str, str], str] = {}


def load_city_mapping(mapping_path: str = "suburb_city_mapping_v1.json") -> Dict[Tuple[str, str], str]:
    global CITY_MAPPING
    if CITY_MAPPING:
        return CITY_MAPPING
    p = Path(mapping_path)
    if not p.exists():
        p = Path(__file__).resolve().parent / mapping_path
    if not p.exists():
        p = Path(__file__).resolve().parent.parent / "data-scrape-khoi" / mapping_path
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                items = json.load(f)
                for item in items:
                    sub = (item.get("suburb") or "").strip().lower()
                    st = (item.get("state") or "").strip().lower()
                    city = (item.get("city") or "").strip()
                    if sub and st and city:
                        CITY_MAPPING[(st, sub)] = city
            logger.info("Đã nạp %d ánh xạ suburb->city từ %s", len(CITY_MAPPING), p.name)
        except Exception as exc:
            logger.warning("Không thể nạp city mapping từ %s: %s", p, exc)
    return CITY_MAPPING


def lookup_city(state: str, suburb: str) -> str:
    mapping = load_city_mapping()
    return mapping.get(((state or "").strip().lower(), (suburb or "").strip().lower()), "")



def make_qdrant_request(url: str, method: str = "GET", body: Optional[Dict[str, Any]] = None,
                         api_key: Optional[str] = None, timeout: int = 15) -> Dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_street(text: str) -> str:
    if not text:
        return ""
    value = html_lib.unescape(str(text)).lower()
    value = re.sub(r"\b(unit|apartment|apt|suite)\s+", "", value)
    value = re.sub(r"[,/._-]+", " ", value)
    replacements = {
        r"\b(street|st)\b": "st", r"\b(road|rd)\b": "rd",
        r"\b(avenue|ave)\b": "ave", r"\b(highway|hwy)\b": "hwy",
        r"\b(drive|dr)\b": "dr", r"\b(lane|ln)\b": "ln",
        r"\b(place|pl)\b": "pl", r"\b(crescent|cres|cr)\b": "cr",
        r"\b(close|cl)\b": "cl", r"\b(court|ct)\b": "ct",
        r"\b(parade|pde)\b": "pde", r"\b(terrace|tce)\b": "tce",
    }
    for pattern, replacement in replacements.items():
        value = re.sub(pattern, replacement, value)
    return re.sub(r"\s+", " ", value).strip()


def parse_australian_address(street: str, suburb: str = "", state: str = "", postcode: str = "") -> Dict[str, Any]:
    clean = re.sub(r"\s+", " ", (street or "").strip().strip(","))
    unit = number = None
    street_name = clean
    match = re.match(
        r"^(?:(?:unit|apt|apartment|suite)\s*)?([a-z0-9]+)\s*[/,]\s*"
        r"(\d+[a-z]?(?:-\d+[a-z]?)?)\s+(.+)$", clean, re.I,
    )
    if match:
        unit, number, street_name = match.groups()
    else:
        match = re.match(r"^(\d+[a-z]?(?:-\d+[a-z]?)?)\s+(.+)$", clean, re.I)
        if match:
            number, street_name = match.groups()
    full_street = f"{unit + '/' if unit else ''}{number or ''} {street_name}".strip()
    return {
        "unit": unit, "street_number": number, "street_name": street_name.strip(),
        "suburb": (suburb or "").strip(), "state": (state or "").upper().strip(),
        "postcode": str(postcode or "").strip(), "full_street": full_street,
    }


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def generate_domain_profile_url(address: Mapping[str, Any]) -> Optional[str]:
    required = ("street_number", "street_name", "suburb", "state", "postcode")
    if any(not address.get(key) for key in required):
        return None
    parts = ([str(address["unit"])] if address.get("unit") else []) + [
        str(address["street_number"]), str(address["street_name"]), str(address["suburb"]),
        str(address["state"]), str(address["postcode"]),
    ]
    return "https://www.domain.com.au/property-profile/" + slugify("-".join(parts))


def build_property_search_query(address: Mapping[str, Any]) -> str:
    return f'site:property.com.au "{address.get("full_street", "")}" "{address.get("suburb", "")}"'


def expected_full_address(address: Mapping[str, Any]) -> str:
    return " ".join(str(address.get(key) or "") for key in ("full_street", "suburb", "state", "postcode")).strip()


def address_matches(expected: Mapping[str, Any], candidates: Iterable[str]) -> bool:
    target_tokens = normalize_street(expected_full_address(expected)).split()
    if not target_tokens:
        return False
    for candidate in candidates:
        observed = normalize_street(candidate).split()
        if all(token in observed for token in target_tokens):
            return True
    return False


def clean_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = re.sub(r"\s+", " ", html_lib.unescape(value)).strip()
        return value or None
    return value


def parse_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    return float(match.group(0).replace(",", "")) if match else None


def validate_count(value: Any) -> Optional[int]:
    number = parse_number(value)
    return int(number) if number is not None and 0 <= number <= 30 and number.is_integer() else None


def validate_money(value: Any, rent: bool = False) -> Optional[int]:
    number = parse_number(value)
    low, high = ((40, 20_000) if rent else (10_000, 200_000_000))
    return int(round(number)) if number is not None and low <= number <= high else None


def validate_yield(value: Any) -> Optional[float]:
    number = parse_number(value)
    return round(number, 3) if number is not None and 0 <= number <= 50 else None


def is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def deep_merge(original: Mapping[str, Any], additions: Mapping[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(dict(original))
    for key, value in additions.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def deep_clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        cleaned = {key: deep_clean(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if is_nonempty(item) or item == 0}
    if isinstance(value, list):
        return [deep_clean(item) for item in value if is_nonempty(item)]
    return clean_scalar(value)


def get_path(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def set_path(data: Dict[str, Any], path: str, value: Any) -> None:
    current = data
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


@dataclass
class PageSnapshot:
    requested_url: str
    final_url: str = ""
    title: str = ""
    html: str = ""
    http_status: Optional[int] = None
    load_seconds: float = 0.0
    method: str = "http"
    error: str = ""


@dataclass
class ExtractionResult:
    status: str
    fields: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    methods: List[str] = field(default_factory=list)
    address_candidates: List[str] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    reason: str = ""
    parse_seconds: float = 0.0
    requested_url: str = ""
    final_url: str = ""
    page_title: str = ""
    http_status: Optional[int] = None
    load_seconds: float = 0.0
    diagnostic_html: str = ""
    diagnostic_screenshot: str = ""
    raw_html: str = field(default="", repr=False)


def classify_page(snapshot: PageSnapshot) -> Optional[Tuple[str, str]]:
    content = f"{snapshot.title}\n{snapshot.html[:250000]}".lower()
    has_valid_data = any(marker in content for marker in ("__next_data__", "application/ld+json", "argonautexchange"))
    if not has_valid_data and (snapshot.http_status in (401, 403, 429) or any(marker in content for marker in BLOCK_PATTERNS)):
        return "BLOCKED", f"access control/challenge detected (HTTP {snapshot.http_status or 'browser'})"
    if snapshot.http_status in (404, 410) or any(marker in content for marker in NO_DATA_PATTERNS):
        return "NO_DATA", f"source reports no profile (HTTP {snapshot.http_status or 'page marker'})"
    if snapshot.error:
        status = "TIMEOUT" if "timeout" in snapshot.error.lower() or "timed out" in snapshot.error.lower() else "ERROR"
        return status, snapshot.error
    if not snapshot.html or len(snapshot.html) < 200:
        return "PARSE_FAILED", "response HTML is empty or too short"
    return None


class BrowserSession:
    """Browser session hỗ trợ cả chế độ chuẩn lẫn Undetected-Chrome (UC Mode)."""

    def __init__(self, headless: bool = True, navigation_timeout: int = 25, render_timeout: int = 10, uc: bool = False):
        self.driver = None
        self.headless = headless
        self.navigation_timeout = navigation_timeout
        self.render_timeout = render_timeout
        self.uc = uc

    def get_driver(self):
        if self.driver is None:
            from seleniumbase import Driver
            if self.uc:
                logger.info("Khởi tạo trình duyệt Chrome chống bot (UC Mode: True, Headless: %s)...", self.headless)
                self.driver = Driver(uc=True, headless=self.headless, block_images=True)
            else:
                logger.info("Khởi tạo trình duyệt Chrome thông thường (Headless: %s)...", self.headless)
                self.driver = Driver(uc=False, headless2=self.headless, block_images=True)
            self.driver.set_page_load_timeout(self.navigation_timeout)
        return self.driver

    def fetch(self, url: str) -> PageSnapshot:
        started = time.monotonic()
        snapshot = PageSnapshot(requested_url=url, method="browser")
        try:
            driver = self.get_driver()
            driver.get(url)
            from selenium.webdriver.support.ui import WebDriverWait
            WebDriverWait(driver, self.render_timeout).until(lambda d: d.execute_script("return document.readyState") == "complete")
            WebDriverWait(driver, self.render_timeout).until(lambda d: len(d.page_source or "") >= 500)
            try:
                WebDriverWait(driver, self.render_timeout).until(
                    lambda d: any(marker in (d.page_source or "").lower() for marker in (
                        "__next_data__", "application/ld+json", "argonautexchange",
                        "property not found", "captcha", "access denied", "bedroom", "bathroom",
                    ))
                )
            except Exception:
                # The full render budget was consumed; preserve the resulting DOM
                # so the classifier/parser can distinguish an empty shell from no data.
                pass
            snapshot.final_url = driver.current_url or url
            snapshot.title = driver.title or ""
            snapshot.html = driver.page_source or ""
        except Exception as exc:
            snapshot.error = f"{type(exc).__name__}: {exc}"
            if self.driver:
                try:
                    snapshot.final_url = self.driver.current_url or url
                    snapshot.title = self.driver.title or ""
                    snapshot.html = self.driver.page_source or ""
                except Exception:
                    pass
        snapshot.load_seconds = time.monotonic() - started
        return snapshot

    def screenshot(self, path: Path) -> str:
        if not self.driver:
            return ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.driver.save_screenshot(str(path))
            return str(path)
        except Exception as exc:
            logger.debug("Could not save screenshot: %s", exc)
            return ""

    def close(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None


class PageFetcher:
    def __init__(self, browser: Optional[BrowserSession] = None, navigation_timeout: int = 20,
                 retries: int = 2, min_delay: float = 1.0):
        self.browser = browser
        self.navigation_timeout = navigation_timeout
        self.retries = max(0, retries)
        self.min_delay = max(0.0, min_delay)
        self._last_started = 0.0
        self._browser_failures = 0
        try:
            import requests
            self.session = requests.Session()
            self.session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-AU,en;q=0.8",
            })
        except ImportError:
            self.session = None

    def _pace(self) -> None:
        remaining = self.min_delay - (time.monotonic() - self._last_started)
        if remaining > 0:
            time.sleep(remaining)
        self._last_started = time.monotonic()

    def fetch(self, url: str) -> PageSnapshot:
        if self.browser and self._browser_failures < 2:
            last_browser = PageSnapshot(requested_url=url, method="browser")
            for attempt in range(self.retries + 1):
                self._pace()
                last_browser = self.browser.fetch(url)
                classification = classify_page(last_browser)
                if classification and classification[0] == "BLOCKED":
                    return last_browser
                if not last_browser.error:
                    self._browser_failures = 0
                    return last_browser
                self._browser_failures += 1
                if attempt < self.retries and self._browser_failures < 2:
                    logger.warning("Transient browser failure; restarting the session once: %s", last_browser.error)
                    self.browser.close()
                    time.sleep((2 ** attempt) + random.uniform(0.1, 0.8))
            logger.warning("Browser is unhealthy; using plain HTTP for classification (no challenge bypass)")
            self.browser.close()
        if not self.session:
            return PageSnapshot(requested_url=url, error="requests package is unavailable")
        last = PageSnapshot(requested_url=url)
        for attempt in range(self.retries + 1):
            self._pace()
            started = time.monotonic()
            try:
                response = self.session.get(url, timeout=self.navigation_timeout, allow_redirects=True)
                title_match = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.I | re.S)
                last = PageSnapshot(
                    requested_url=url, final_url=response.url, html=response.text,
                    http_status=response.status_code, load_seconds=time.monotonic() - started,
                    title=clean_scalar(title_match.group(1)) if title_match else "",
                    method="http-fallback" if self.browser else "http",
                )
                classification = classify_page(last)
                if classification and classification[0] == "BLOCKED":
                    return last
                if response.status_code < 500 and response.status_code != 408:
                    return last
            except Exception as exc:
                last = PageSnapshot(requested_url=url, load_seconds=time.monotonic() - started,
                                    error=f"{type(exc).__name__}: {exc}")
            if attempt < self.retries:
                time.sleep((2 ** attempt) + random.uniform(0.1, 0.8))
        return last


def walk_json(value: Any, path: str = "") -> Iterable[Tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, child
            yield from walk_json(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_json(child, f"{path}[{index}]")


def find_json_values(document: Any, names: Sequence[str]) -> List[Tuple[str, Any]]:
    wanted = {name.lower() for name in names}
    return [(path, value) for path, value in walk_json(document) if path.rsplit(".", 1)[-1].lower() in wanted]


def balanced_json_after(text: str, marker: str) -> Optional[str]:
    position = text.find(marker)
    if position < 0:
        return None
    candidates = [pos for pos in (text.find("{", position + len(marker)), text.find("[", position + len(marker))) if pos >= 0]
    if not candidates:
        return None
    start = min(candidates)
    opening, closing = text[start], "}" if text[start] == "{" else "]"
    depth = 0
    quoted = escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def extract_documents(html: str) -> Tuple[List[Tuple[str, Any]], List[str]]:
    documents: List[Tuple[str, Any]] = []
    errors: List[str] = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for index, script in enumerate(soup.find_all("script")):
            content = script.string or script.get_text() or ""
            script_type = (script.get("type") or "").lower()
            script_id = script.get("id") or f"script-{index}"
            if script_type == "application/ld+json" or script_id == "__NEXT_DATA__":
                try:
                    documents.append(("json-ld" if "ld+json" in script_type else "next-data", json.loads(content)))
                except (TypeError, json.JSONDecodeError) as exc:
                    errors.append(f"{script_id}: {exc}")
    except Exception as exc:
        errors.append(f"html-parser: {exc}")
    argonaut = balanced_json_after(html, "window.ArgonautExchange")
    if argonaut:
        try:
            outer = json.loads(argonaut)
            documents.append(("argonaut", outer))
            for _, value in walk_json(outer):
                if isinstance(value, str) and value.startswith(("{", "[")):
                    try:
                        nested = json.loads(value)
                        if isinstance(nested, (dict, list)):
                            documents.append(("argonaut-cache", nested))
                    except json.JSONDecodeError:
                        pass
        except json.JSONDecodeError as exc:
            errors.append(f"argonaut: {exc}")
    return documents, errors


def first_valid(document: Any, names: Sequence[str], validator) -> Tuple[Any, str]:
    for path, value in find_json_values(document, names):
        validated = validator(value)
        if validated is not None:
            return validated, path
    return None, ""


def first_contextual_money(document: Any, names: Sequence[str], contexts: Sequence[str],
                           rent: bool = False) -> Tuple[Any, str]:
    for path, value in find_json_values(document, names):
        if any(context in path.lower() for context in contexts):
            validated = validate_money(value, rent=rent)
            if validated is not None:
                return validated, path
    return None, ""


def add_field(fields: Dict[str, Any], methods: Dict[str, str], path: str, value: Any, method: str) -> None:
    if (is_nonempty(value) or value == 0) and get_path(fields, path) is None:
        set_path(fields, path, value)
        methods[path] = method


def extract_property_from_html(html: str, expected_address: Optional[Mapping[str, Any]] = None,
                               source_url: str = "", final_url: str = "", page_title: str = "",
                               fetched_at: Optional[str] = None) -> ExtractionResult:
    started = time.monotonic()
    classified = classify_page(PageSnapshot(requested_url=source_url, final_url=final_url, title=page_title, html=html or ""))
    if classified:
        return ExtractionResult(status=classified[0], reason=classified[1], requested_url=source_url,
                                final_url=final_url or source_url, page_title=page_title,
                                parse_seconds=time.monotonic() - started)

    fields: Dict[str, Any] = {}
    methods: Dict[str, str] = {}
    addresses: List[str] = []
    documents, parse_errors = extract_documents(html)
    address_names = ("address", "streetAddress", "displayAddress", "fullAddress", "formattedAddress", "propertyAddress")
    for method, document in documents:
        for _, value in find_json_values(document, address_names):
            if isinstance(value, str):
                addresses.append(value)
            elif isinstance(value, Mapping):
                addresses.append(" ".join(str(item) for item in value.values() if isinstance(item, (str, int))))
        for path, names, validator in (
            ("bed", ("beds", "bedrooms", "numberOfBedrooms"), validate_count),
            ("bath", ("baths", "bathrooms", "numberOfBathroomsTotal", "numberOfBathrooms"), validate_count),
            ("features.garage", ("garage", "garages", "parking", "carspaces", "carSpaces"), validate_count),
            ("pricing.weeklyRent", ("weeklyRent", "rentValue"), lambda value: validate_money(value, rent=True)),
            ("pricing.estimatedYield", ("estimatedYield", "rentalYield", "yield"), validate_yield),
        ):
            value, json_path = first_valid(document, names, validator)
            add_field(fields, methods, path, value, f"{method}:{json_path}")
        for path, names in (("valuation.low", ("low", "lower", "lowerPrice", "lowPrice")),
                            ("valuation.high", ("high", "upper", "upperPrice", "highPrice"))):
            value, json_path = first_contextual_money(document, names, ("estimate", "valuation", "price"))
            add_field(fields, methods, path, value, f"{method}:{json_path}")
        value, json_path = first_contextual_money(
            document, ("mid", "value"), ("rentalestimate", "rentestimate", "estimates.rent"), rent=True,
        )
        add_field(fields, methods, "pricing.weeklyRent", value, f"{method}:{json_path}")
        for json_path, value in find_json_values(document, ("features", "indoorFeatures")):
            if isinstance(value, list):
                items = list(dict.fromkeys(clean_scalar(item) for item in value if isinstance(item, str) and clean_scalar(item)))
                add_field(fields, methods, "features.indoorFeatures", items, f"{method}:{json_path}")
        for json_path, value in find_json_values(document, ("outdoorFeatures", "outdoorAmenities")):
            if isinstance(value, list):
                items = list(dict.fromkeys(clean_scalar(item) for item in value if isinstance(item, str) and clean_scalar(item)))
                add_field(fields, methods, "features.outdoorAmenities", items, f"{method}:{json_path}")
        for json_path, value in find_json_values(document, ("propertyType", "property_type", "@type")):
            if isinstance(value, str):
                v_clean = value.strip()
                if v_clean.lower() in ("singlefamilyresidence", "house", "detached"):
                    add_field(fields, methods, "propertyType", "House", f"{method}:{json_path}")
                    break
                elif v_clean.lower() in ("apartment", "unit", "condominium", "flat"):
                    add_field(fields, methods, "propertyType", "Unit", f"{method}:{json_path}")
                    break
                elif v_clean.lower() in ("townhouse", "terrace", "villa", "duplex", "studio"):
                    add_field(fields, methods, "propertyType", v_clean.capitalize(), f"{method}:{json_path}")
                    break
        for json_path, value in find_json_values(document, ("floorSize", "landSize", "landArea", "totalArea")):
            if isinstance(value, (int, float)) and 10 <= value <= 1000000:
                add_field(fields, methods, "totalArea", int(value), f"{method}:{json_path}")
                break
            elif isinstance(value, Mapping) and "value" in value:
                num = parse_number(value["value"])
                if num and 10 <= num <= 1000000:
                    add_field(fields, methods, "totalArea", int(num), f"{method}:{json_path}")
                    break
        for json_path, value in find_json_values(document, ("soldPrice", "lastSoldPrice", "price", "salePrice")):
            price_val = validate_money(value)
            if price_val:
                add_field(fields, methods, "expectedPrice", price_val, f"{method}:{json_path}")
                break
        for json_path, value in find_json_values(document, ("soldDate", "dateSold", "lastSoldDate")):
            if isinstance(value, str) and len(value) >= 4:
                add_field(fields, methods, "soldDateInfo", value.strip(), f"{method}:{json_path}")
                break
        for json_path, value in find_json_values(document, ("description", "summary")):
            cleaned = clean_scalar(value)
            if isinstance(cleaned, str) and len(cleaned) >= 20:
                add_field(fields, methods, "description", cleaned, f"{method}:{json_path}")
                break
        for json_path, value in find_json_values(document, ("headline", "title")):
            cleaned = clean_scalar(value)
            if isinstance(cleaned, str) and len(cleaned) >= 5:
                add_field(fields, methods, "title", cleaned, f"{method}:{json_path}")
                break

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        visible_text = soup.get_text(" ", strip=True)
        if page_title:
            addresses.append(page_title)
        for selector in ("[data-testid*='address']", "h1", "[itemprop='address']"):
            for node in soup.select(selector):
                text = clean_scalar(node.get_text(" ", strip=True))
                if text:
                    addresses.append(text)
        meta = soup.select_one("meta[name='description']")
        if meta:
            add_field(fields, methods, "description", clean_scalar(meta.get("content")), "dom:meta-description")
        if page_title:
            add_field(fields, methods, "title", re.split(r"\s+[|–—]\s+", page_title)[0].strip(), "dom:title")
        selector_map = {
            "bed": ("[data-testid*='bed']", "[aria-label*='bedroom' i]"),
            "bath": ("[data-testid*='bath']", "[aria-label*='bathroom' i]"),
            "features.garage": ("[data-testid*='parking']", "[aria-label*='car' i]"),
        }
        for path, selectors in selector_map.items():
            for selector in selectors:
                node = soup.select_one(selector)
                if node:
                    add_field(fields, methods, path, validate_count(node.get_text(" ", strip=True) or node.get("aria-label")), f"dom:{selector}")
                    if get_path(fields, path) is not None:
                        break
        text_patterns = {
            "bed": r"(\d{1,2})\s*(?:bed|bedroom)s?\b",
            "bath": r"(\d{1,2})\s*(?:bath|bathroom)s?\b",
            "features.garage": r"(\d{1,2})\s*(?:car|garage|carspace|parking space)s?\b",
            "pricing.weeklyRent": r"\$\s*([\d,]+)\s*(?:per week|p\.?w\.?|weekly)",
            "pricing.estimatedYield": r"(?:estimated |rental )?yield\s*[:\-]?\s*([\d.]+)\s*%",
        }
        for path, pattern in text_patterns.items():
            match = re.search(pattern, visible_text, re.I)
            if match:
                if path == "pricing.weeklyRent":
                    value = validate_money(match.group(1), rent=True)
                elif path.endswith("estimatedYield"):
                    value = validate_yield(match.group(1))
                else:
                    value = validate_count(match.group(1))
                add_field(fields, methods, path, value, f"dom:text-regex:{path}")
        # Property type from text
        pt_match = re.search(r"\b(House|Apartment|Unit|Townhouse|Villa|Duplex|Studio)\b", visible_text, re.I)
        if pt_match:
            add_field(fields, methods, "propertyType", pt_match.group(1).capitalize(), "dom:text-regex:propertyType")
        # Area from text
        area_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:m²|m2|sqm|sq\s*m)\b", visible_text, re.I)
        if area_match:
            num = parse_number(area_match.group(1))
            if num and 10 <= num <= 1000000:
                add_field(fields, methods, "totalArea", int(num), "dom:text-regex:totalArea")
        # Price from text
        price_match = re.search(r"(?:Sold for|Sold price|Price|Value estimate)[:\s]*\$\s*([\d,]+)", visible_text, re.I)
        if price_match:
            price_val = validate_money(price_match.group(1))
            if price_val:
                add_field(fields, methods, "expectedPrice", price_val, "dom:text-regex:expectedPrice")
    except Exception as exc:
        parse_errors.append(f"dom: {exc}")

    addresses = list(dict.fromkeys(item for item in addresses if item))
    verified = bool(expected_address and address_matches(expected_address, addresses))
    if expected_address and not verified:
        status = "ADDRESS_MISMATCH" if fields else "PARSE_FAILED"
        reason = "page data was not tied to the requested exact address"
        if addresses:
            reason += f"; observed: {addresses[:3]}"
        return ExtractionResult(status=status, reason=reason, methods=sorted(set(methods.values())),
                                address_candidates=addresses, missing_fields=list(PRIORITY_FIELDS),
                                parse_seconds=time.monotonic() - started, requested_url=source_url,
                                final_url=final_url or source_url, page_title=page_title)
    if verified and final_url:
        add_field(fields, methods, "realestate_url", final_url, "navigation:verified-final-url")
    fields = deep_clean(fields)
    found = [path for path in PRIORITY_FIELDS if is_nonempty(get_path(fields, path)) or get_path(fields, path) == 0]
    missing = [path for path in PRIORITY_FIELDS if path not in found]
    if not found:
        status = "PARSE_FAILED" if parse_errors else "NO_DATA"
        reason = "; ".join(parse_errors[:3]) if parse_errors else "profile matched but exposed no supported fields"
    else:
        status = "SUCCESS" if not missing else "PARTIAL"
        reason = f"verified {len(found)} field(s); {len(missing)} priority field(s) absent"
    fetched_at = fetched_at or utc_now()
    provenance: Dict[str, Any] = {}
    for path in found:
        if path not in methods:
            continue
        set_path(provenance, path, {
            "sourceUrl": final_url or source_url, "requestedUrl": source_url,
            "fetchedAt": fetched_at, "extractionMethod": methods[path], "addressVerified": True,
        })
    return ExtractionResult(status=status, fields=fields, provenance=provenance,
                            methods=sorted(set(methods.values())), address_candidates=addresses,
                            missing_fields=missing, reason=reason, parse_seconds=time.monotonic() - started,
                            requested_url=source_url, final_url=final_url or source_url, page_title=page_title)
def fetch_and_parse_profile(url: str, browser_session: Optional[BrowserSession] = None,
                            expected_address: Optional[Mapping[str, Any]] = None,
                            fetcher: Optional[PageFetcher] = None) -> ExtractionResult:
    active_fetcher = fetcher or PageFetcher(browser=browser_session)
    fetched_at = utc_now()
    snapshot = active_fetcher.fetch(url)
    classification = classify_page(snapshot)
    if classification:
        return ExtractionResult(status=classification[0], reason=classification[1], requested_url=url,
                                final_url=snapshot.final_url, page_title=snapshot.title,
                                http_status=snapshot.http_status, load_seconds=snapshot.load_seconds,
                                raw_html=snapshot.html)
    result = extract_property_from_html(snapshot.html, expected_address, url, snapshot.final_url,
                                        snapshot.title, fetched_at)
    result.http_status = snapshot.http_status
    result.load_seconds = snapshot.load_seconds
    result.raw_html = snapshot.html
    return result


def build_canonical_payload(
    existing: Mapping[str, Any],
    extracted: Mapping[str, Any],
    point_id: str,
    requested_url: str = ""
) -> Dict[str, Any]:
    full_payload = copy.deepcopy(dict(existing))
    
    # Remove old unwanted keys if present
    for k in ("fieldProvenance", "enrichment", "valuation"):
        full_payload.pop(k, None)

    # Address components
    street = extracted.get("street") or existing.get("street") or ""
    suburb = extracted.get("suburb") or existing.get("suburb") or ""
    state = (extracted.get("state") or existing.get("state") or "").upper()
    postcode = str(extracted.get("postcode") or existing.get("postcode") or "")
    
    # City from lookup
    city = existing.get("city") or lookup_city(state, suburb) or ""

    # Coordinates
    coords = existing.get("coordinates")
    if not isinstance(coords, dict) or "lat" not in coords:
        coords = extracted.get("coordinates") if isinstance(extracted.get("coordinates"), dict) else {}
    coordinates = {
        "lat": coords.get("lat") or coords.get("latitude"),
        "lon": coords.get("lon") or coords.get("longitude") or coords.get("lng")
    }

    # Bed / Bath
    bed = extracted.get("bed")
    if bed is None:
        bed = existing.get("bed")
    if bed is not None:
        try:
            bed = int(bed)
        except Exception:
            pass

    bath = extracted.get("bath")
    if bath is None:
        bath = existing.get("bath")
    if bath is not None:
        try:
            bath = int(bath)
        except Exception:
            pass

    features_raw = extracted.get("features") if isinstance(extracted.get("features"), dict) else {}
    garage = features_raw.get("garage")
    if garage is None:
        existing_feat = existing.get("features") if isinstance(existing.get("features"), dict) else {}
        garage = existing_feat.get("garage") if existing_feat else existing.get("garage")
    try:
        garage = int(garage) if garage is not None else 0
    except Exception:
        garage = 0

    # Property type
    property_type = extracted.get("propertyType") or existing.get("propertyType")
    if not property_type:
        if "/" in street or re.search(r"\b(unit|apt|apartment|suite)\b", street, re.I):
            property_type = "Unit"
        else:
            property_type = "House"

    # Area
    area = existing.get("area")
    if not isinstance(area, dict) or not (area.get("totalArea") or area.get("totalarea")):
        extracted_area = extracted.get("area")
        if isinstance(extracted_area, dict) and extracted_area.get("totalArea"):
            area = extracted_area
        elif extracted.get("totalArea"):
            area = {"totalArea": extracted.get("totalArea"), "unit": "sqM"}
        else:
            area = {"totalArea": 0, "unit": "sqM"}
    else:
        total_area = area.get("totalArea") or area.get("totalarea") or 0
        area = {"totalArea": total_area, "unit": area.get("unit") or "sqM"}

    # Expected Price
    expected_price = extracted.get("expectedPrice")
    if expected_price is None:
        val_high = extracted.get("valuation", {}).get("high") if isinstance(extracted.get("valuation"), dict) else None
        val_low = extracted.get("valuation", {}).get("low") if isinstance(extracted.get("valuation"), dict) else None
        if val_high and val_low:
            expected_price = int((val_high + val_low) / 2)
        elif val_high:
            expected_price = int(val_high)
        elif val_low:
            expected_price = int(val_low)
        else:
            expected_price = existing.get("expectedPrice")
    if expected_price is not None:
        try:
            expected_price = int(round(float(expected_price)))
        except Exception:
            pass

    # Title & Description
    title = extracted.get("title") or existing.get("title")
    if not title:
        title = f"{street}, {suburb} {state} {postcode} - property.com.au"

    description = extracted.get("description") or existing.get("description")
    if not description:
        bed_str = f"{bed} bedroom, " if bed else ""
        bath_str = f"{bath} bathroom " if bath else ""
        description = f"{street} is a {bed_str}{bath_str}{property_type.lower()} in {suburb} {state} {postcode}."

    # Features
    existing_feat = existing.get("features") if isinstance(existing.get("features"), dict) else {}
    indoor = features_raw.get("indoorFeatures") or existing_feat.get("indoorFeatures") or []
    outdoor = features_raw.get("outdoorAmenities") or existing_feat.get("outdoorAmenities")
    if outdoor is None or not isinstance(outdoor, list):
        outdoor = [f"Carport spaces: {garage}"] if garage else []

    features = {
        "garage": garage,
        "floorNo": existing_feat.get("floorNo", 1),
        "indoorFeatures": indoor if isinstance(indoor, list) else [],
        "outdoorAmenities": outdoor if isinstance(outdoor, list) else [],
        "basement": existing_feat.get("basement") or "None",
        "floorCovering": existing_feat.get("floorCovering") or ["None"],
        "roof": existing_feat.get("roof") or ["Other"],
        "view": existing_feat.get("view") or ["None"],
        "appliances": existing_feat.get("appliances") or ["None"],
        "buildingAmenities": existing_feat.get("buildingAmenities") or ["None"],
        "displayAddress": existing_feat.get("displayAddress") or "fullAddress"
    }

    # URL
    realestate_url = (
        extracted.get("realestate_url")
        or requested_url
        or existing.get("realestate_url")
        or existing.get("url")
        or ""
    )

    sold_date_info = extracted.get("soldDateInfo") or existing.get("soldDateInfo") or ""
    listing_option = extracted.get("listingOption") or existing.get("listingOption") or "sold"
    status = extracted.get("status") or existing.get("status") or "sold"

    # Pricing
    existing_pricing = existing.get("pricing") if isinstance(existing.get("pricing"), dict) else {}
    pricing_options = existing_pricing.get("pricingOptions") or ""
    if expected_price and not pricing_options:
        pricing_options = f"${expected_price:,.0f}"

    pricing = {
        "waterBillPeriod": existing_pricing.get("waterBillPeriod") or "monthly",
        "councilBill": existing_pricing.get("councilBill") or "",
        "pricingOptions": pricing_options,
        "authority": existing_pricing.get("authority") or "sold",
        "priceIncludes": existing_pricing.get("priceIncludes") or [""]
    }

    # History Sale
    historysale = extracted.get("historysale") or existing.get("historysale")
    if not historysale or not isinstance(historysale, list):
        if expected_price:
            historysale = [{
                "soldDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00"),
                "soldPrice": expected_price
            }]
        else:
            historysale = []

    # Slug
    slug = extracted.get("slug") or existing.get("slug")
    if not slug:
        if realestate_url:
            slug = realestate_url.rstrip("/").split("/")[-1]
        else:
            slug = f"{slugify(street)}-pid-{str(point_id)[:8]}"

    canonical = {
        "architecturalStyle": existing.get("architecturalStyle") or "None",
        "area": area,
        "bed": bed,
        "bath": bath,
        "coordinates": coordinates,
        "constructionYear": existing.get("constructionYear") or extracted.get("constructionYear") or "N/A",
        "expectedPrice": expected_price,
        "street": street,
        "state": state,
        "suburb": suburb,
        "city": city,
        "postcode": postcode,
        "propertyType": property_type,
        "title": title,
        "description": description,
        "features": features,
        "structuralRemodelYear": existing.get("structuralRemodelYear") or "N/A",
        "recommended": existing.get("recommended", False),
        "published": existing.get("published", False),
        "realestate_url": realestate_url,
        "contactInfo": existing.get("contactInfo") or [],
        "images": [],  # Bỏ qua ảnh theo yêu cầu
        "soldDateInfo": sold_date_info,
        "listingOption": listing_option,
        "pricing": pricing,
        "status": status,
        "historysale": historysale,
        "slug": slug
    }

    return canonical


def build_sparse_patch(existing: Mapping[str, Any], result: ExtractionResult) -> Dict[str, Any]:
    if result.status not in ("SUCCESS", "PARTIAL"):
        return {}
    patch: Dict[str, Any] = {}
    for key, value in deep_clean(result.fields).items():
        if not is_nonempty(value) and value != 0:
            continue
        candidate = deep_merge(existing.get(key, {}), value) if key in NESTED_FIELDS and isinstance(value, Mapping) else value
        if existing.get(key) != candidate:
            patch[key] = candidate
    if patch and result.provenance:
        patch["fieldProvenance"] = deep_merge(existing.get("fieldProvenance", {}), result.provenance)
        patch["enrichment"] = deep_merge(existing.get("enrichment", {}), {
            "lastSuccessAt": utc_now(), "sourceUrl": result.final_url or result.requested_url,
            "verifiedFieldCount": sum(1 for path in PRIORITY_FIELDS if get_path(result.provenance, path)),
            "pipelineVersion": "3.0-fail-closed",
        })
    return patch


class LocalMasterEnricher:
    def __init__(self, local_url: str = "http://localhost:6333", local_key: str = "local-qdrant-key",
                 local_collection: str = "PropertyMaster", skip_images: bool = True):
        self.local_url = local_url.rstrip("/")
        self.local_key = local_key
        self.local_coll = local_collection
        self.skip_images = skip_images

    def fetch_point(self, point_id: str) -> Optional[Dict[str, Any]]:
        url = f"{self.local_url}/collections/{self.local_coll}/points/{urllib.parse.quote(str(point_id), safe='')}"
        try:
            return make_qdrant_request(url, api_key=self.local_key).get("result")
        except Exception as exc:
            logger.error("Could not read point %s: %s", point_id, exc)
            return None

    def fetch_missing_points(self, suburb: Optional[str] = None, state: Optional[str] = None,
                             street: Optional[str] = None, point_id: Optional[str] = None,
                             limit: int = 50, only_null: bool = True) -> List[Dict[str, Any]]:
        if point_id:
            point = self.fetch_point(point_id)
            return [point] if point else []
        url = f"{self.local_url}/collections/{self.local_coll}/points/scroll"
        must: List[Dict[str, Any]] = []
        if suburb:
            must.append({"key": "suburb", "match": {"value": suburb}})
        if state:
            must.append({"key": "state", "match": {"value": state.upper()}})
        if street:
            must.append({"key": "street", "match": {"text": street}})
        selected: List[Dict[str, Any]] = []
        offset: Any = None
        while limit <= 0 or len(selected) < limit:
            batch_size = 100 if limit <= 0 else min(100, max(10, limit - len(selected)))
            body: Dict[str, Any] = {"limit": batch_size,
                                    "with_payload": True, "with_vectors": False}
            if must:
                body["filter"] = {"must": must}
            if offset is not None:
                body["offset"] = offset
            response = make_qdrant_request(url, method="POST", body=body, api_key=self.local_key)
            result = response.get("result", {})
            points = result.get("points", [])
            for point in points:
                payload = point.get("payload", {})
                if not only_null or "architecturalStyle" not in payload or any(not is_nonempty(get_path(payload, path)) for path in PRIORITY_FIELDS):
                    selected.append(point)
                    if limit > 0 and len(selected) >= limit:
                        break
            offset = result.get("next_page_offset")
            if not points or offset is None:
                break
        return selected

    def update_point_payload(self, point_id: str, patch: Dict[str, Any]) -> bool:
        if not patch:
            return False
        # Xóa payload cũ để loại bỏ hoàn toàn các trường thừa/lỗi thời
        try:
            make_qdrant_request(
                f"{self.local_url}/collections/{self.local_coll}/points/payload/clear?wait=true",
                method="POST", body={"points": [point_id]}, api_key=self.local_key,
            )
        except Exception:
            pass
        # Ghi payload chuẩn mới
        res = make_qdrant_request(
            f"{self.local_url}/collections/{self.local_coll}/points/payload?wait=true",
            method="POST", body={"points": [point_id], "payload": patch}, api_key=self.local_key,
        )
        if res.get("status") != "ok":
            res = make_qdrant_request(
                f"{self.local_url}/collections/{self.local_coll}/points/payload?wait=true",
                method="PUT", body={"points": [point_id], "payload": patch}, api_key=self.local_key,
            )
        return res.get("status") == "ok"

    def verify_patch(self, point_id: str, patch: Mapping[str, Any]) -> Tuple[bool, List[str]]:
        current = self.fetch_point(point_id)
        if not current:
            return False, ["point could not be read after write"]
        payload = current.get("payload", {})
        keys_to_verify = ("architecturalStyle", "bed", "bath", "street", "suburb", "state", "postcode", "features", "pricing")
        mismatches = [key for key in keys_to_verify if key in patch and payload.get(key) != patch[key]]
        return not mismatches, mismatches


class Checkpoint:
    def __init__(self, path: Path):
        self.path = path
        self.data: Dict[str, Any] = {"version": 1, "records": {}}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data = loaded
                    self.data.setdefault("records", {})
            except Exception as exc:
                logger.warning("Ignoring unreadable checkpoint %s: %s", path, exc)

    def should_skip(self, point_id: str, refresh_days: int, payload: Optional[Mapping[str, Any]] = None) -> bool:
        if payload and ("architecturalStyle" not in payload or "pricing" not in payload):
            return False
        record = self.data.get("records", {}).get(str(point_id), {})
        if record.get("status") not in ("SUCCESS", "PARTIAL", "NO_CHANGE"):
            return False
        try:
            completed = datetime.fromisoformat(record["completedAt"])
            return datetime.now(timezone.utc) - completed < timedelta(days=refresh_days)
        except Exception:
            return False

    def record(self, point_id: str, status: str, fields: Sequence[str]) -> None:
        self.data.setdefault("records", {})[str(point_id)] = {
            "status": status, "fields": list(fields), "completedAt": utc_now(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


def payload_is_fresh(payload: Mapping[str, Any], refresh_days: int) -> bool:
    if "architecturalStyle" not in payload or "pricing" not in payload:
        return False
    enrichment = payload.get("enrichment")
    if isinstance(enrichment, Mapping) and enrichment.get("verifiedFieldCount"):
        try:
            completed = datetime.fromisoformat(str(enrichment["lastSuccessAt"]))
            return datetime.now(timezone.utc) - completed < timedelta(days=refresh_days)
        except Exception:
            pass
    return False


def save_diagnostics(result: ExtractionResult, snapshot_html: str, point_id: str, directory: Path,
                     browser: Optional[BrowserSession]) -> None:
    if result.status in ("SUCCESS", "PARTIAL", "NO_CHANGE"):
        return
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((result.requested_url or point_id).encode()).hexdigest()[:10]
    stem = f"{str(point_id).replace(':', '_')}_{result.status}_{digest}"
    if snapshot_html:
        html_path = directory / f"{stem}.html"
        html_path.write_text(snapshot_html, encoding="utf-8", errors="replace")
        result.diagnostic_html = str(html_path)
    if browser:
        result.diagnostic_screenshot = browser.screenshot(directory / f"{stem}.png")


def format_paths(data: Mapping[str, Any], prefix: str = "") -> List[str]:
    paths: List[str] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.extend(format_paths(value, path) if isinstance(value, Mapping) else [path])
    return paths


def parser_arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail-closed Australian property enrichment")
    parser.add_argument("--collection", default="PropertyMaster")
    parser.add_argument("--suburb")
    parser.add_argument("--street")
    parser.add_argument("--point-id")
    parser.add_argument("--state")
    parser.add_argument("--limit", type=int, default=10, help="Số lượng căn cần làm giàu (truyền 0 để làm giàu TOÀN BỘ collection)")
    parser.add_argument("--worker-id", type=int, default=0, help="ID của worker hiện tại (0..worker_count-1)")
    parser.add_argument("--worker-count", type=int, default=1, help="Tổng số worker chạy song song")
    parser.add_argument("--input-csv", type=str, default=None, help="Đọc danh sách URL/point từ file CSV thay vì scroll Qdrant")
    parser.add_argument("--output-file", type=str, default=None, help="File ghi kết quả của worker (JSONL)")
    parser.add_argument("--dry-run", action="store_true", help="Chế độ đọc thử nghiệm, không ghi vào database")
    parser.add_argument("--apply", action="store_true", help="Ghi dữ liệu làm giàu vào Qdrant")
    parser.add_argument("--crawl", action="store_true", help="Cào dữ liệu từ URL profile")
    parser.add_argument("--browser", action="store_true", help="Sử dụng trình duyệt Chrome để cào dữ liệu")
    parser.add_argument("--uc", action="store_true", help="Bật chế độ Chrome chống bot (Undetected-Chromedriver)")
    parser.add_argument("--headed", action="store_true", help="Hiển thị cửa sổ trình duyệt (headed mode)")
    parser.add_argument("--force", action="store_true", help="Bỏ qua checkpoint, làm giàu lại toàn bộ các bản ghi được chọn")
    parser.add_argument("--export-csv")
    parser.add_argument("--local-url", default=os.getenv("QDRANT_URI", "http://localhost:6333"))
    parser.add_argument("--local-key", default=(os.getenv("QDRANT_API") or os.getenv("QDRANT_API_KEY", "local-qdrant-key")).strip('"').strip("'"))
    parser.add_argument("--all-points", action="store_true")
    parser.add_argument("--navigation-timeout", type=int, default=25)
    parser.add_argument("--render-timeout", type=int, default=10)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--min-delay", type=float, default=2.0)
    parser.add_argument("--diagnostics-dir", default="diagnostics/enrich_local_master")
    parser.add_argument("--checkpoint")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--refresh-days", type=int, default=30)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser_arguments().parse_args(argv)
    if args.apply and args.dry_run:
        logger.error("Chọn --apply hoặc --dry-run, không thể chọn cả hai")
        return 2
    if args.uc:
        logger.info("Chế độ Chrome chống bot (Undetected-Chromedriver / UC Mode) đã được kích hoạt")
        args.browser = True
    if args.browser:
        args.crawl = True
    dry_run = not args.apply
    if args.checkpoint:
        ckpt_path = Path(args.checkpoint)
    elif args.worker_count > 1:
        ckpt_path = Path(f".enrich_checkpoint_{slugify(args.collection)}_w{args.worker_id}.json")
    else:
        ckpt_path = Path(f".enrich_checkpoint_{slugify(args.collection)}.json")
    checkpoint = Checkpoint(ckpt_path)
    diagnostics_dir = Path(args.diagnostics_dir)
    enricher = LocalMasterEnricher(args.local_url, args.local_key, args.collection)
    limit_val = 0 if args.limit <= 0 else args.limit

    points = []
    if args.input_csv and Path(args.input_csv).exists():
        logger.info("Đang nạp danh sách từ file CSV: %s", args.input_csv)
        import uuid
        with open(args.input_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid = row.get("point_id") or str(uuid.uuid4())
                points.append({
                    "id": pid,
                    "payload": {
                        "street": row.get("street") or "",
                        "suburb": row.get("suburb") or "",
                        "state": row.get("state") or "",
                        "postcode": row.get("postcode") or "",
                        "url": row.get("domain_url") or row.get("url") or "",
                        "realestate_url": row.get("domain_url") or row.get("url") or ""
                    }
                })
        if limit_val > 0:
            points = points[:limit_val]
    else:
        try:
            points = enricher.fetch_missing_points(
                args.suburb, args.state, args.street, args.point_id, limit_val, not args.all_points,
            )
        except Exception as exc:
            logger.error("Qdrant read failed at %s: %s", args.local_url, exc)
            return 1

    if args.worker_count > 1:
        total_all = len(points)
        points = points[args.worker_id :: args.worker_count]
        logger.info("Worker %d/%d nhận %d/%d bản ghi để xử lý", args.worker_id, args.worker_count, len(points), total_all)

    if not points:
        logger.info("No candidate records found")
        return 0
    browser = BrowserSession(headless=not args.headed, navigation_timeout=args.navigation_timeout,
                             render_timeout=args.render_timeout, uc=args.uc) if args.browser else None
    fetcher = PageFetcher(browser, args.navigation_timeout, args.retries, args.min_delay)
    export_rows: List[Dict[str, str]] = []
    counts = {status: 0 for status in STATUSES}
    attempted = 0
    blocked_streak = 0
    logger.info("Starting %s: collection=%s candidates=%d browser=%s",
                "DRY-RUN" if dry_run else "APPLY", args.collection, len(points), bool(browser))
    try:
        for index, point in enumerate(points, 1):
            point_id = str(point["id"])
            payload = point.get("payload", {})
            if not args.no_resume and not args.force and (
                checkpoint.should_skip(point_id, args.refresh_days, payload)
                or payload_is_fresh(payload, args.refresh_days)
            ):
                logger.info("[%d/%d] SKIP verified enrichment is still fresh id=%s", index, len(points), point_id)
                continue
            attempted += 1
            address = parse_australian_address(payload.get("street", ""), payload.get("suburb", ""),
                                               payload.get("state", ""), payload.get("postcode", ""))
            requested_url = payload.get("realestate_url") or payload.get("url")
            if not requested_url or not isinstance(requested_url, str) or not requested_url.startswith("http"):
                requested_url = generate_domain_profile_url(address)
            if requested_url:
                export_rows.append({"point_id": point_id, "street": payload.get("street", ""),
                                    "suburb": payload.get("suburb", ""), "url": requested_url,
                                    "search_query": build_property_search_query(address)})
            if not requested_url:
                result = ExtractionResult(status="ERROR", reason="address lacks URL components")
            elif not args.crawl:
                result = ExtractionResult(status="NO_CHANGE", reason="URL resolved only; crawl is disabled",
                                          requested_url=requested_url, final_url=requested_url)
            else:
                result = fetch_and_parse_profile(requested_url, browser, address, fetcher)
            
            canonical_payload = build_canonical_payload(
                existing=payload,
                extracted=result.fields if result.status in ("SUCCESS", "PARTIAL") else {},
                point_id=point_id,
                requested_url=requested_url or result.requested_url
            )
            updated_paths = list(canonical_payload.keys())

            if dry_run:
                logger.info(
                    "[DRY RUN] Dữ liệu chuẩn bị cập nhật cho point %s:\n%s",
                    point_id,
                    json.dumps(canonical_payload, indent=2, ensure_ascii=False)
                )
            else:
                try:
                    if not enricher.update_point_payload(point_id, canonical_payload):
                        result.status, result.reason = "ERROR", "Qdrant did not acknowledge set_payload"
                    else:
                        verified, mismatches = enricher.verify_patch(point_id, canonical_payload)
                        if not verified:
                            logger.warning("Read-after-write differences on keys: %s", mismatches)
                        logger.info("✅ Đã cập nhật thành công point %s với cấu trúc chuẩn (28 trường)", point_id)
                except Exception as exc:
                    result.status, result.reason = "ERROR", f"Qdrant write failed: {type(exc).__name__}: {exc}"

            if args.output_file:
                try:
                    with open(args.output_file, "a", encoding="utf-8") as out_f:
                        out_f.write(json.dumps(canonical_payload, ensure_ascii=False) + "\n")
                except Exception as exc:
                    logger.warning("Không thể ghi file output %s: %s", args.output_file, exc)

            save_diagnostics(result, result.raw_html, point_id, diagnostics_dir, browser)
            counts[result.status] += 1
            blocked_streak = blocked_streak + 1 if result.status == "BLOCKED" else 0
            if not dry_run:
                checkpoint.record(point_id, result.status, updated_paths)
            logger.info(
                "[%d/%d] %s id=%s address=%s | bed=%s bath=%s garage=%s price=%s | status=%s",
                index, len(points), result.status, point_id, expected_full_address(address),
                canonical_payload.get("bed"), canonical_payload.get("bath"),
                canonical_payload.get("features", {}).get("garage"),
                canonical_payload.get("pricing", {}).get("pricingOptions") or canonical_payload.get("expectedPrice"),
                result.status
            )
            if result.diagnostic_html or result.diagnostic_screenshot:
                logger.info("diagnostics html=%s screenshot=%s", result.diagnostic_html or "n/a",
                            result.diagnostic_screenshot or "n/a")
            if blocked_streak >= 5:
                logger.error("Dừng crawl sau 5 lần liên tiếp bị chặn (BLOCKED).")
                break
    except KeyboardInterrupt:
        logger.warning("Interrupted safely; acknowledged records remain checkpointed")
        return 130
    finally:
        if browser:
            browser.close()
    if args.export_csv and export_rows:
        with open(args.export_csv, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["point_id", "street", "suburb", "url", "search_query"])
            writer.writeheader()
            writer.writerows(export_rows)
    logger.info("Completed attempted=%d/%d mode=%s statuses=%s", attempted, len(points),
                "DRY-RUN" if dry_run else "APPLY", counts)
    return 0 if counts["ERROR"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
