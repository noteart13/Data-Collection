"""
Stealth Anti-Bot Browser Engine.
Utilizes Playwright with browser fingerprint cloaking, human-like interaction jitter,
and integrated SmartProxyManager rotation to scrape Australian real estate listings.
"""

import asyncio
import logging
import os
import random
import re
import time
from typing import Optional, List, Dict, Any, Tuple
from bs4 import BeautifulSoup

from app.operations.proxy_manager import SmartProxyManager, ProxyNode

logger = logging.getLogger("stealth_crawler")

try:
    from playwright_stealth import stealth_async
    HAS_PLAYWRIGHT_STEALTH = True
except ImportError:
    HAS_PLAYWRIGHT_STEALTH = False

# Stealth injection script to remove bot automation artifacts
STEALTH_INJECTION_JS = """
// 1. Overwrite navigator.webdriver
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});

// 2. Overwrite chrome runtime object
if (!window.chrome) {
    window.chrome = {};
}
window.chrome.runtime = window.chrome.runtime || {};
window.chrome.loadTimes = window.chrome.loadTimes || function() {};
window.chrome.csi = window.chrome.csi || function() {};
window.chrome.app = window.chrome.app || {};

// 3. Spoof Australian English languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-AU', 'en-GB', 'en-US', 'en']
});

// 4. Spoof Notification permission
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);
"""


class StealthCrawler:
    """
    High-resilience stealth web crawler equipped with proxy rotation,
    anti-bot fingerprint evasion, and human interaction simulation.
    """

    def __init__(
        self,
        proxy_manager: Optional[SmartProxyManager] = None,
        headless: bool = True,
        timeout_ms: int = 45000
    ):
        self.proxy_manager = proxy_manager or SmartProxyManager()
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.last_error = ""
        self._playwright = None
        self._browser = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        """Initializes the Playwright browser instance."""
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            launch_options = {
                "headless": self.headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            }
            browser_channel = os.environ.get("STEALTH_BROWSER_CHANNEL", "chrome").strip()
            if browser_channel:
                launch_options["channel"] = browser_channel
            self._browser = await self._playwright.chromium.launch(**launch_options)
            logger.info("Stealth Playwright Chromium engine started.")
        except Exception as e:
            logger.error(f"Failed to start Playwright browser: {e}")
            raise

    async def close(self):
        """Cleanly releases browser resources."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Stealth Playwright browser terminated.")

    async def fetch_page(self, url: str, wait_for_selector: Optional[str] = None) -> Tuple[Optional[str], Optional[ProxyNode]]:
        """
        Navigates to the specified URL using a rotated proxy and realistic Australian fingerprint.
        Simulates natural scrolling and interaction before returning the rendered HTML.
        """
        if not self._browser:
            await self.start()

        proxy = self.proxy_manager.get_proxy()
        proxy_config = proxy.to_playwright_proxy() if proxy else None
        fingerprint = self.proxy_manager.get_australian_fingerprint()

        self.last_error = ""
        context = None
        page = None
        t0 = time.time()

        try:
            # Create isolated stealth browser context
            context = await self._browser.new_context(
                proxy=proxy_config,
                user_agent=fingerprint["headers"]["User-Agent"],
                viewport=fingerprint["viewport"],
                locale=fingerprint["locale"],
                timezone_id=fingerprint["timezone_id"],
                geolocation=fingerprint["geolocation"],
                permissions=["geolocation"],
                extra_http_headers=fingerprint["headers"],
            )

            # Inject evasion script into early document lifecycle
            await context.add_init_script(STEALTH_INJECTION_JS)

            page = await context.new_page()
            page.set_default_timeout(self.timeout_ms)

            # Apply advanced playwright-stealth if available
            if HAS_PLAYWRIGHT_STEALTH:
                try:
                    await stealth_async(page)
                except Exception as stealth_err:
                    logger.debug(f"playwright_stealth hook warning: {stealth_err}")

            # Navigate
            response = await page.goto(url, wait_until="domcontentloaded")
            status_code = response.status if response else 0

            # Check for blocking status
            if status_code in (403, 429):
                err_msg = f"HTTP {status_code} Access Blocked by Portal/WAF (Akamai/Kasada anti-bot active on {url} using proxy {proxy.host if proxy else 'DIRECT'})"
                logger.warning(err_msg)
                self.last_error = err_msg
                self.proxy_manager.report_failure(proxy, status_code=status_code)
                return None, proxy

            if wait_for_selector:
                try:
                    await page.wait_for_selector(wait_for_selector, timeout=8000)
                except Exception:
                    pass

            # Simulate human behavior: subtle randomized scroll
            await self._simulate_human_interaction(page)

            html = await page.content()

            # Anti-bot text check (Cloudflare, Akamai, PerimeterX, Kasada)
            challenge_markers = ["checking your browser", "cloudflare ray id", "access denied", "please verify you are a human", "x-kpsdk"]
            if any(term in html.lower() for term in challenge_markers):
                err_msg = f"Bot challenge screen encountered on {url} (proxy: {proxy.host if proxy else 'DIRECT'})"
                logger.warning(err_msg)
                self.last_error = err_msg
                self.proxy_manager.report_failure(proxy, status_code=403)
                return None, proxy

            latency = (time.time() - t0) * 1000
            self.proxy_manager.report_success(proxy, latency_ms=latency)
            return html, proxy

        except Exception as e:
            err_msg = f"Error fetching {url}: {e}"
            logger.error(err_msg)
            self.last_error = err_msg
            self.proxy_manager.report_failure(proxy, status_code=500)
            return None, proxy
        finally:
            if page:
                await page.close()
            if context:
                await context.close()

    async def _simulate_human_interaction(self, page):
        """Simulate realistic human behavior (reading delay, micro-scroll)."""
        try:
            # Random short pause
            await asyncio.sleep(random.uniform(0.8, 2.2))
            # Gentle mouse move & scroll
            scroll_delta = random.randint(250, 650)
            await page.mouse.wheel(0, scroll_delta)
            await asyncio.sleep(random.uniform(0.4, 1.2))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# AUSTRALIAN REAL ESTATE PORTAL DATA EXTRACTORS
# ---------------------------------------------------------------------------
def parse_domain_search_page(html: str) -> List[Dict[str, Any]]:
    """
    Parses listing cards from a Domain.com.au search result page.
    Extracts canonical listing URLs, advertised price text, address, bed, bath, and car.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    # Domain uses data-testid or regular card classes
    cards = soup.find_all("li", attrs={"data-testid": lambda v: v and "listing__item" in v})
    if not cards:
        cards = soup.find_all("div", class_=lambda c: c and "search-result" in c)

    for card in cards:
        link_elem = card.find("a", href=lambda h: h and "/property-profile/" in h or re.search(r"-\d{6,10}$", h or ""))
        if not link_elem or not link_elem.get("href"):
            continue

        raw_href = link_elem["href"]
        full_url = raw_href if raw_href.startswith("http") else f"https://www.domain.com.au{raw_href}"

        # Clean tracking query parameters
        clean_url = full_url.split("?")[0]

        # Extract price string
        price_elem = card.find(attrs={"data-testid": "listing-card-price"})
        price_str = price_elem.get_text(strip=True) if price_elem else ""

        # Extract address
        addr_elem = card.find("span", attrs={"data-testid": "address-line1"}) or card.find("h2")
        address = addr_elem.get_text(strip=True) if addr_elem else ""

        # Features (bed, bath, parking)
        features = {}
        for feature_tag in card.find_all("span", attrs={"data-testid": lambda t: t and "property-features__feature" in t}):
            text = feature_tag.get_text(strip=True).lower()
            if "bed" in text:
                digits = re.findall(r"\d+", text)
                if digits:
                    features["bed"] = int(digits[0])
            elif "bath" in text:
                digits = re.findall(r"\d+", text)
                if digits:
                    features["bath"] = int(digits[0])
            elif "parking" in text or "car" in text:
                digits = re.findall(r"\d+", text)
                if digits:
                    features["garage"] = int(digits[0])

        listings.append({
            "url": clean_url,
            "advertised_price": price_str,
            "address": address,
            "features": features
        })

    return listings
