"""Fail-closed Property.com.au browser enrichment.

This adapter never reads another property dataset.  It discovers a public
Property.com.au profile URL, loads the page in the existing nodriver browser,
and returns only fields whose scraped address exactly matches the requested
address.
"""

from __future__ import annotations

import asyncio
import atexit
from collections import Counter
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional
from urllib.parse import quote_plus, urlparse

from app.operations.portal_profile_hydrator import PortalSqliteCache
from app.operations.property_evidence import (
    address_slug_matches,
    annotate_portal_result,
    has_meaningful_value,
    normalize_address_slug,
)
from app.operations.shared_domain_limiter import (
    DomainCooldownActive,
    SharedDomainRateLimiter,
)


logger = logging.getLogger(__name__)
PROPERTY_DOMAIN = "property.com.au"
DEFAULT_BROWSER_CACHE = Path("scratch") / "property_com_browser_cache.db"
DEFAULT_RATE_LIMIT_ROOT = Path("scratch") / "portal_rate_limits"
BROWSER_SUPPORTED_FIELDS = frozenset(
    {
        "bed",
        "bath",
        "garage",
        "totalArea",
        "expectedPrice",
        "constructionYear",
        "description",
        "propertyType",
        "title",
        "historysale",
        "status",
        "listingOption",
        "soldDateInfo",
    }
)
BLOCK_PAGE_MARKERS = (
    "too many requests",
    "access denied",
    "verify you are human",
    "checking your browser",
    "x-kpsdk",
    "captcha",
)


class PortalBlockedError(RuntimeError):
    pass


def _looks_like_block_page(html: str) -> bool:
    sample = str(html or "")[:200_000].lower()
    return any(marker in sample for marker in BLOCK_PAGE_MARKERS)


def _is_property_profile_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc.lower().removeprefix("www.")
    return parsed.scheme in {"http", "https"} and host == PROPERTY_DOMAIN and "-pid-" in parsed.path


def _is_supported_profile_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc.lower().removeprefix("www.")
    if _is_property_profile_url(url):
        return True
    return parsed.scheme in {"http", "https"} and (
        (host == "view.com.au" and parsed.path.startswith("/property/"))
        or (host == "domain.com.au" and parsed.path.startswith("/property-profile/"))
        or (host == "realestate.com.au" and parsed.path.startswith("/property/"))
    )


def _source_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "domain.com.au" in host:
        return "DOMAIN_COM_AU_BROWSER"
    if "realestate.com.au" in host:
        return "REALESTATE_COM_AU_BROWSER"
    if "view.com.au" in host:
        return "VIEW_COM_AU_BROWSER"
    return "PROPERTY_COM_AU_BROWSER"


class _BrowserLoop:
    """Own one persistent event loop so zendriver objects never cross loops."""

    def __init__(self) -> None:
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.ready = threading.Event()
        self._lock = threading.Lock()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        self.ready.set()
        loop.run_forever()
        loop.close()

    def start(self) -> None:
        with self._lock:
            if self.thread and self.thread.is_alive():
                return
            self.ready.clear()
            self.thread = threading.Thread(
                target=self._run,
                name="property-com-browser-loop",
                daemon=True,
            )
            self.thread.start()
        if not self.ready.wait(timeout=10):
            raise RuntimeError("Property.com.au browser event loop did not start")

    def run(self, coroutine, timeout: float):
        self.start()
        if self.loop is None:
            raise RuntimeError("Property.com.au browser event loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        return future.result(timeout=timeout)

    def close(self) -> None:
        loop = self.loop
        thread = self.thread
        if not loop or not thread or not thread.is_alive():
            return
        try:
            async def close_browsers() -> None:
                try:
                    from operations.researcher.scraper.browser.nodriver_scraper import NoDriverScraper

                    await NoDriverScraper.close_all_browsers()
                except Exception as exc:  # pragma: no cover - best-effort process cleanup
                    logger.debug("Browser cleanup failed: %s", exc)

            asyncio.run_coroutine_threadsafe(close_browsers(), loop).result(timeout=20)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)


class PropertyComBrowserAdapter:
    """Synchronous exact-address interface for Property.com.au browser scraping."""

    def __init__(
        self,
        *,
        profile_loader: Optional[Callable[[str], Dict[str, Any]]] = None,
        stealth_profile_loader: Optional[Callable[[str], Dict[str, Any]]] = None,
        url_finder: Optional[Callable[..., Iterable[str]]] = None,
        cache_db_path: Optional[Path] = None,
        headless: bool = False,
        request_timeout: float = 180.0,
        scrape_do_token: Optional[str] = None,
        rate_limit_root: Optional[Path] = None,
        wait_on_cooldown: bool = True,
        max_candidates: Optional[int] = None,
    ) -> None:
        self.profile_loader = profile_loader
        self.stealth_profile_loader = stealth_profile_loader
        self.url_finder = url_finder or self._find_urls_with_search
        self.cache = PortalSqliteCache(cache_db_path or DEFAULT_BROWSER_CACHE)
        self.headless = bool(headless)
        self.request_timeout = max(30.0, float(request_timeout))
        self.max_candidates = (
            max(1, int(max_candidates)) if max_candidates is not None else None
        )
        self.scrape_do_token = (
            scrape_do_token
            if scrape_do_token is not None
            else os.getenv("SCRAPE_DO_TOKEN", "")
        ).strip()
        self.rate_limiter = SharedDomainRateLimiter(
            rate_limit_root or DEFAULT_RATE_LIMIT_ROOT,
            min_delay_seconds=float(os.getenv("PORTAL_MIN_DELAY_SECONDS", "6")),
            max_delay_seconds=float(os.getenv("PORTAL_MAX_DELAY_SECONDS", "10")),
            cooldown_seconds=float(os.getenv("PORTAL_COOLDOWN_SECONDS", "300")),
            wait_on_cooldown=wait_on_cooldown,
            scope="browser-direct",
        )
        self.stealth_rate_limiter = SharedDomainRateLimiter(
            rate_limit_root or DEFAULT_RATE_LIMIT_ROOT,
            min_delay_seconds=float(os.getenv("PORTAL_MIN_DELAY_SECONDS", "6")),
            max_delay_seconds=float(os.getenv("PORTAL_MAX_DELAY_SECONDS", "10")),
            cooldown_seconds=float(os.getenv("PORTAL_COOLDOWN_SECONDS", "300")),
            wait_on_cooldown=wait_on_cooldown,
            scope="browser-stealth",
        )
        self._browser_loop: Optional[_BrowserLoop] = None
        self._stealth_crawler = None
        self._lookup_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._metrics: Counter[str] = Counter()
        self._timings: Counter[str] = Counter()
        atexit.register(self.close)

    def _record(self, outcome: str) -> None:
        with self._metrics_lock:
            self._metrics[outcome] += 1

    def _record_timing(self, stage: str, seconds: float) -> None:
        with self._metrics_lock:
            self._timings[stage] += max(0.0, seconds)

    def get_metrics_snapshot(self) -> Dict[str, Any]:
        with self._metrics_lock:
            snapshot = dict(self._metrics)
            timings = dict(self._timings)
        snapshot["total"] = sum(snapshot.values())
        snapshot.update(
            {
                f"{stage}_seconds": round(seconds, 3)
                for stage, seconds in timings.items()
            }
        )
        lookup_count = sum(
            count
            for name, count in snapshot.items()
            if name
            in {
                "cache_hit",
                "negative_cache_hit",
                "invalid_address",
                "success",
                "partial_success",
                "unresolved",
                "unsupported_fields_only",
            }
        )
        if lookup_count and timings.get("lookup") is not None:
            snapshot["avg_lookup_seconds"] = round(
                timings["lookup"] / lookup_count,
                3,
            )
        return snapshot

    @staticmethod
    def _find_urls_with_search(**address: str) -> Iterable[str]:
        """Find profile URLs from the public search engine, never from Qdrant."""
        from app.get_html import build_property_title, find_property_result_by_title
        from app.operations.researcher.retrievers import Duckduckgo

        full_address = (
            f"{address.get('street', '')}, {address.get('suburb', '')}, "
            f"{address.get('state', '')} {address.get('postcode', '')}"
        ).strip()
        expected_title = build_property_title(full_address)
        slug = normalize_address_slug(
            str(address.get('street', '')),
            str(address.get('suburb', '')),
            str(address.get('state', '')),
            str(address.get('postcode', '')),
        )
        ordered_urls = []
        if slug:
            ordered_urls.append(f"https://view.com.au/property/{slug}/")
            ordered_urls.append(f"https://www.domain.com.au/property-profile/{slug}")
        try:
            retriever = Duckduckgo(query=f'"{expected_title}"', query_domains=[PROPERTY_DOMAIN])
            results = retriever.search(max_results=20)
            ordered = []
            exact = find_property_result_by_title(results, full_address)
            if exact:
                ordered.append(exact)
            ordered.extend(result for result in results if result is not exact)
            for result in ordered:
                url_str = str(result.get("href") or result.get("url") or "")
                if _is_property_profile_url(url_str) and url_str not in ordered_urls:
                    ordered_urls.append(url_str)
        except Exception:
            pass
        return ordered_urls

    def _load_profile(self, url: str) -> Dict[str, Any]:
        if self.profile_loader is not None:
            return self.profile_loader(url) or {}
        try:
            self.rate_limiter.wait(url)
        except DomainCooldownActive as exc:
            raise PortalBlockedError(str(exc)) from exc
        if self._browser_loop is None:
            self._browser_loop = _BrowserLoop()
        return self._browser_loop.run(
            self._load_profile_async(url),
            timeout=self.request_timeout,
        ) or {}

    async def _load_profile_async(self, url: str) -> Dict[str, Any]:
        app_dir = str(Path(__file__).resolve().parents[1])
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)

        os.environ["NODRIVER_HEADLESS"] = "true" if self.headless else "false"
        os.environ["NODRIVER_KEEP_BROWSER"] = "true"

        # The enrichment path only maps metadata; it must not upload media or
        # require the legacy GCS bucket merely because the parser imports it.
        from app.get_html import install_debug_gcs_stub

        install_debug_gcs_stub()
        from operations.researcher.scraper.scaper import Scraper
        from operations.utils.workers import WorkerPool

        worker_pool = WorkerPool(max_workers=1)
        scraper = Scraper(
            urls=[url],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/137.0.0.0 Safari/537.36"
            ),
            scraper="nodriver",
            worker_pool=worker_pool,
            max_retries=1,
            retry_delay_seconds=2.0,
            min_content_length=100,
            scrape_timeout_seconds=min(120.0, self.request_timeout),
        )
        results = await scraper.run()
        result = results[0] if results else {}
        html = result.get("html") or ""
        if _looks_like_block_page(html):
            self.rate_limiter.record_challenge(url)
            raise PortalBlockedError("portal returned an anti-bot challenge")
        return await self._parse_profile_html(html, url)

    def _load_stealth_profile(self, url: str) -> Dict[str, Any]:
        if self.stealth_profile_loader is not None:
            return self.stealth_profile_loader(url) or {}
        try:
            self.stealth_rate_limiter.wait(url)
        except DomainCooldownActive as exc:
            raise PortalBlockedError(str(exc)) from exc
        if self._browser_loop is None:
            self._browser_loop = _BrowserLoop()
        return self._browser_loop.run(
            self._load_stealth_profile_async(url),
            timeout=self.request_timeout,
        ) or {}

    async def _load_stealth_profile_async(self, url: str) -> Dict[str, Any]:
        app_dir = str(Path(__file__).resolve().parents[1])
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        from app.get_html import install_debug_gcs_stub
        from app.operations.stealth_crawler import StealthCrawler

        install_debug_gcs_stub()
        if self._stealth_crawler is None:
            self._stealth_crawler = StealthCrawler(
                headless=self.headless,
                timeout_ms=int(min(120.0, self.request_timeout) * 1000),
            )
        html, _proxy = await self._stealth_crawler.fetch_page(url)
        if _looks_like_block_page(html):
            self.stealth_rate_limiter.record_challenge(url)
            raise PortalBlockedError("portal returned an anti-bot challenge")
        return await self._parse_profile_html(html or "", url)

    def _load_scrape_do_profile(self, url: str) -> Dict[str, Any]:
        """Fetch through Scrape.do only when the operator supplied a token."""
        if not self.scrape_do_token:
            return {}
        import requests

        endpoint = (
            "https://api.scrape.do/?token="
            f"{quote_plus(self.scrape_do_token)}&url={quote_plus(url)}"
            "&super=true&geoCode=au"
        )
        response = requests.get(
            endpoint,
            timeout=min(90.0, self.request_timeout),
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        if response.status_code in {403, 429}:
            raise PortalBlockedError(
                f"Scrape.do returned HTTP {response.status_code}"
            )
        response.raise_for_status()
        html = response.text or ""
        if _looks_like_block_page(html):
            raise PortalBlockedError("Scrape.do returned a portal challenge page")
        if self._browser_loop is None:
            self._browser_loop = _BrowserLoop()
        return self._browser_loop.run(
            self._parse_profile_html(html, url),
            timeout=min(60.0, self.request_timeout),
        ) or {}

    async def _parse_profile_html(self, html: str, url: str) -> Dict[str, Any]:
        if len(html) < 500:
            return {}

        host = urlparse(url).netloc.lower()
        if "property.com.au" in host:
            from operations.property_address_property import PropertyMeta

            property_meta = PropertyMeta(html, url, "salesold")
            if not property_meta.meta:
                return {}
            profile = property_meta.propertysale()
            historysale, sale_info = await property_meta.getPriceInfo("salesold")
            profile["historysale"] = historysale
            profile["status"] = sale_info.get("status")
            profile["listingOption"] = sale_info.get("listingOption")
            profile["soldDateInfo"] = sale_info.get("soldDateInfo")
            profile["_sourceStatusText"] = property_meta.safe_get(
                property_meta.meta,
                ["header", "status", "badgeV2", "text"],
                "",
            )
            return profile

        from app.operations.portal_profile_hydrator import PortalProfileHydrator

        parser = PortalProfileHydrator.__new__(PortalProfileHydrator)
        next_data = parser.extract_next_data(html)
        props = (next_data or {}).get("props", {}).get("pageProps", {})
        if not props:
            return {}
        if "domain.com.au" in host:
            return parser.parse_domain_profile_json(props)
        if "realestate.com.au" in host:
            return parser.parse_realestate_profile_json(props)
        if "view.com.au" in host:
            return parser.parse_view_profile_json(props)
        return {}

    @staticmethod
    def _profile_failure_outcome(profile: Dict[str, Any], expected_slug: str) -> str:
        if not profile:
            return "parser_missing"
        observed_slug = str(profile.get("_addressSlug") or "") or normalize_address_slug(
            str(profile.get("street") or ""),
            str(profile.get("suburb") or ""),
            str(profile.get("state") or ""),
            str(profile.get("postcode") or ""),
        )
        if observed_slug and not address_slug_matches(observed_slug, {expected_slug}):
            return "address_mismatch"
        return "evidence_missing"

    @staticmethod
    def _verified_result(
        profile: Dict[str, Any],
        *,
        expected_slug: str,
        source_url: str,
    ) -> Dict[str, Any]:
        observed_slug = str(profile.get("_addressSlug") or "") or normalize_address_slug(
            str(profile.get("street") or ""),
            str(profile.get("suburb") or ""),
            str(profile.get("state") or ""),
            str(profile.get("postcode") or ""),
        )
        if not address_slug_matches(observed_slug, {expected_slug}):
            return {}

        result: Dict[str, Any] = {"_addressSlug": observed_slug}
        positive_int_fields = {
            "bed": profile.get("bed"),
            "bath": profile.get("bath"),
            "garage": profile.get("garage")
            if profile.get("garage") is not None
            else (profile.get("features") or {}).get("garage"),
        }
        for key, value in positive_int_fields.items():
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                result[key] = parsed

        area = profile.get("area") if isinstance(profile.get("area"), dict) else {}
        try:
            total_area = float(
                profile.get("totalArea")
                if profile.get("totalArea") is not None
                else area.get("totalArea")
            )
            if total_area > 0:
                result["totalArea"] = total_area
                result["_areaType"] = "landArea"
        except (TypeError, ValueError):
            pass

        expected_price = profile.get("expectedPrice")
        try:
            if expected_price is not None and float(expected_price) > 0:
                result["expectedPrice"] = expected_price
        except (TypeError, ValueError):
            pass

        val_low = profile.get("valuation_low")
        try:
            if val_low is not None and float(val_low) > 0:
                result["valuation_low"] = float(val_low)
        except (TypeError, ValueError):
            pass

        val_high = profile.get("valuation_high")
        try:
            if val_high is not None and float(val_high) > 0:
                result["valuation_high"] = float(val_high)
        except (TypeError, ValueError):
            pass

        weekly_rent = profile.get("weeklyRent") or (profile.get("pricing") or {}).get("weeklyRent")
        try:
            if weekly_rent is not None and float(weekly_rent) > 0:
                result["weeklyRent"] = int(float(weekly_rent))
        except (TypeError, ValueError):
            pass

        est_yield = profile.get("estimatedYield") or (profile.get("pricing") or {}).get("estimatedYield")
        if est_yield:
            result["estimatedYield"] = str(est_yield)

        feats_list = profile.get("features_list") or (profile.get("features") or {}).get("indoorFeatures")
        if isinstance(feats_list, list) and feats_list:
            result["features_list"] = [str(f).strip() for f in feats_list if str(f).strip()]

        construction_year = str(profile.get("constructionYear") or "").strip()
        if construction_year.isdigit() and 1700 <= int(construction_year) <= 2100:
            result["constructionYear"] = construction_year

        passthrough = (
            "description",
            "propertyType",
            "title",
        )
        for key in passthrough:
            value = profile.get(key)
            if has_meaningful_value(value):
                result[key] = value

        # The legacy Property.com mapper maps "off market" to "sold".  Only
        # admit sale-state fields when the source badge explicitly supports
        # them; absence or off-market is not evidence of a sale.
        source_status = str(profile.get("_sourceStatusText") or "").strip().lower()
        status_is_explicit = bool(source_status) and source_status not in {
            "off market",
            "off-market",
        }
        if status_is_explicit or not _is_property_profile_url(source_url):
            for key in ("historysale", "status", "listingOption", "soldDateInfo"):
                value = profile.get(key)
                if has_meaningful_value(value):
                    result[key] = value

        result["realestate_url"] = source_url
        result["sourceConfidence"] = 0.99
        return annotate_portal_result(
            result,
            source_name=_source_name(source_url),
            source_url=source_url,
        )

    @staticmethod
    def _portal_candidate_urls(
        street: str,
        suburb: str,
        state: str,
        postcode: str,
    ) -> Iterable[str]:
        from app.operations.portal_profile_hydrator import get_candidate_slugs

        for slug in get_candidate_slugs(street, suburb, state, postcode):
            yield f"https://view.com.au/property/{slug}/"
            yield f"https://www.domain.com.au/property-profile/{slug}"
            yield f"https://www.realestate.com.au/property/{slug}"

    @staticmethod
    def _merge_verified_result(
        merged: Dict[str, Any],
        result: Dict[str, Any],
        *,
        source_url: str,
        desired: set[str],
    ) -> bool:
        evidence = result.get("fieldEvidence") or {}
        merged.setdefault("fieldEvidence", {}).update(evidence)
        for key, value in result.items():
            if key == "fieldEvidence":
                continue
            if key.startswith("_") or not has_meaningful_value(merged.get(key)):
                merged[key] = value
        if any(has_meaningful_value(result.get(key)) for key in desired):
            merged["realestate_url"] = source_url
        return all(has_meaningful_value(merged.get(key)) for key in desired)

    def lookup(
        self,
        *,
        street: str,
        suburb: str,
        state: str,
        postcode: str,
        known_url: Optional[str] = None,
        force_refresh: bool = False,
        requested_fields: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            return self._lookup_impl(
                street=street,
                suburb=suburb,
                state=state,
                postcode=postcode,
                known_url=known_url,
                force_refresh=force_refresh,
                requested_fields=requested_fields,
            )
        finally:
            self._record_timing("lookup", time.perf_counter() - started)

    def _lookup_impl(
        self,
        *,
        street: str,
        suburb: str,
        state: str,
        postcode: str,
        known_url: Optional[str] = None,
        force_refresh: bool = False,
        requested_fields: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        expected_slug = normalize_address_slug(street, suburb, state, postcode)
        requested = set(requested_fields or ("bed", "bath"))
        desired = requested & BROWSER_SUPPORTED_FIELDS
        if not desired:
            self._record("unsupported_fields_only")
            return {}
        if not expected_slug:
            self._record("invalid_address")
            return {}
        # Versioned because older cache entries could contain mapper defaults
        # or the legacy off-market -> sold conversion.
        cache_key = f"property-com-browser:v2:{expected_slug}"
        cached_partial: Dict[str, Any] = {}
        if not force_refresh:
            cached, negative = self.cache.get(cache_key)
            if cached and address_slug_matches(cached.get("_addressSlug"), {expected_slug}):
                if all(has_meaningful_value(cached.get(key)) for key in desired):
                    self._record("cache_hit")
                    return cached
                cached_partial = cached
            if negative:
                self._record("negative_cache_hit")
                return {}

        with self._lookup_lock:
            candidates = []
            if _is_property_profile_url(str(known_url or "")):
                candidates.append(str(known_url))
            if not candidates:
                search_started = time.perf_counter()
                try:
                    candidates.extend(
                        self.url_finder(
                            street=street,
                            suburb=suburb,
                            state=state,
                            postcode=postcode,
                        )
                        or []
                    )
                except Exception as exc:
                    logger.warning("Property.com.au URL discovery failed for %s: %s", expected_slug, exc)
                    self._record("search_error")
                finally:
                    self._record_timing(
                        "search",
                        time.perf_counter() - search_started,
                    )

            candidates.extend(self._portal_candidate_urls(street, suburb, state, postcode))
            max_candidates = self.max_candidates or max(
                1, int(os.getenv("PORTAL_BROWSER_MAX_CANDIDATES", "4"))
            )
            candidates = candidates[:max_candidates]

            seen = set()
            merged: Dict[str, Any] = dict(cached_partial)
            if cached_partial.get("fieldEvidence"):
                merged["fieldEvidence"] = dict(cached_partial["fieldEvidence"])
            for candidate in candidates:
                url = str(candidate or "").strip()
                if url in seen or not _is_supported_profile_url(url):
                    continue
                seen.add(url)
                if self.profile_loader is None and hasattr(self.rate_limiter, "is_cooldown_active"):
                    is_blocked, remaining = self.rate_limiter.is_cooldown_active(url)
                    if is_blocked:
                        logger.debug("Skipping %s due to active cooldown (%.1fs remaining)", url, remaining)
                        self._record("primary_blocked")
                        continue
                loaders = [("primary", self._load_profile)]
                # A second local browser rarely changes a valid parsed profile.
                # Keep it for Property.com (and explicit test/injected loaders),
                # but do not multiply it across every guessed portal URL.
                stealth_enabled = self.stealth_profile_loader is not None or os.getenv(
                    "PORTAL_ENABLE_STEALTH_FALLBACK", ""
                ).strip().lower() in {"1", "true", "yes", "on"}
                if _is_supported_profile_url(url) and stealth_enabled:
                    loaders.append(("stealth", self._load_stealth_profile))
                if self.scrape_do_token:
                    loaders.append(("scrape_do", self._load_scrape_do_profile))
                for loader_name, loader in loaders:
                    loader_started = time.perf_counter()
                    try:
                        profile = loader(url)
                    except PortalBlockedError as exc:
                        logger.warning("%s blocked for %s: %s", loader_name, url, exc)
                        self._record(f"{loader_name}_blocked")
                        continue
                    except Exception as exc:
                        logger.warning(
                            "%s browser scrape failed for %s: %s",
                            loader_name,
                            url,
                            exc,
                        )
                        self._record(f"{loader_name}_scrape_error")
                        continue
                    finally:
                        self._record_timing(
                            loader_name,
                            time.perf_counter() - loader_started,
                        )
                    result = self._verified_result(
                        profile,
                        expected_slug=expected_slug,
                        source_url=url,
                    )
                    if not result:
                        self._record(
                            f"{loader_name}_{self._profile_failure_outcome(profile, expected_slug)}"
                        )
                        continue
                    if self._merge_verified_result(
                        merged,
                        result,
                        source_url=url,
                        desired=desired,
                    ):
                        self.cache.set(cache_key, merged, source="MULTI_PORTAL_BROWSER")
                        self._record("success")
                        return merged

            if merged.get("fieldEvidence"):
                self.cache.set(cache_key, merged, source="MULTI_PORTAL_BROWSER")
                self._record("partial_success")
                return merged

            self.cache.set(cache_key, None, is_not_found=True, source="MULTI_PORTAL_BROWSER")
            self._record("unresolved")
            return {}

    def close(self) -> None:
        if self._browser_loop is not None:
            if self._stealth_crawler is not None:
                try:
                    self._browser_loop.run(
                        self._stealth_crawler.close(),
                        timeout=30.0,
                    )
                except Exception as exc:
                    logger.debug("Stealth browser cleanup failed: %s", exc)
                self._stealth_crawler = None
            self._browser_loop.close()
            self._browser_loop = None
