"""
Smart Proxy & Stealth Anti-Bot Subsystem.
Provides intelligent residential/datacenter proxy pool rotation, failure backoff cooldowns,
and authentic Australian user-agent & browser fingerprint generation.
"""

import os
import time
import random
import asyncio
import logging
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger("proxy_manager")

# Authentic Australian Desktop User-Agents & Fingerprints
AU_USER_AGENTS = [
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "platform": "Windows",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec_ch_ua_platform": '"Windows"',
    },
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "platform": "Windows",
        "sec_ch_ua": '"Chromium";v="123", "Google Chrome";v="123", "Not-A.Brand";v="99"',
        "sec_ch_ua_platform": '"Windows"',
    },
    {
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "platform": "macOS",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec_ch_ua_platform": '"macOS"',
    },
    {
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        "platform": "macOS",
        "sec_ch_ua": "",
        "sec_ch_ua_platform": '"macOS"',
    },
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "platform": "Windows",
        "sec_ch_ua": "",
        "sec_ch_ua_platform": '"Windows"',
    },
]


@dataclass
class ProxyNode:
    raw_url: str
    protocol: str = "http"
    host: str = ""
    port: int = 80
    username: Optional[str] = None
    password: Optional[str] = None

    latency_ms: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_used: float = 0.0
    is_active: bool = True

    def __post_init__(self):
        self._parse()

    def _parse(self):
        url = self.raw_url.strip()
        if not url.startswith("http://") and not url.startswith("https://") and not url.startswith("socks5://"):
            url = f"http://{url}"

        parsed = urlparse(url)
        self.protocol = parsed.scheme or "http"
        self.host = parsed.hostname or ""
        self.port = parsed.port or (443 if self.protocol == "https" else 80)
        self.username = parsed.username
        self.password = parsed.password

    @property
    def is_in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until

    def mark_success(self, latency_ms: float = 0.0):
        self.success_count += 1
        self.consecutive_failures = 0
        self.latency_ms = latency_ms
        self.last_used = time.time()

    def mark_failure(self, cooldown_duration_sec: int = 300):
        self.failure_count += 1
        self.consecutive_failures += 1
        self.last_used = time.time()
        # Exponential backoff for repeated failures
        factor = min(self.consecutive_failures, 4)
        actual_cooldown = cooldown_duration_sec * factor
        self.cooldown_until = time.time() + actual_cooldown
        logger.warning(
            f"Proxy {self.host}:{self.port} hit error (fails={self.consecutive_failures}). "
            f"Entering cooldown for {actual_cooldown}s"
        )

    def to_playwright_proxy(self) -> Dict[str, str]:
        """Convert to Playwright browser context proxy dictionary."""
        server = f"{self.protocol}://{self.host}:{self.port}"
        cfg = {"server": server}
        if self.username:
            cfg["username"] = self.username
        if self.password:
            cfg["password"] = self.password
        return cfg


class SmartProxyManager:
    """
    Enterprise proxy pool manager supporting automatic rotation,
    anti-bot evasions, latency tracking, and cooldown resilience.
    """

    def __init__(
        self,
        proxy_list: Optional[List[str]] = None,
        default_cooldown_sec: int = 300,
        strategy: str = "ROUND_ROBIN"
    ):
        self.default_cooldown_sec = default_cooldown_sec
        self.strategy = strategy.upper()  # ROUND_ROBIN, LEAST_USED, or RANDOM
        self.proxies: List[ProxyNode] = []
        self._current_index = 0

        # Load from arguments or environment
        if proxy_list:
            for p in proxy_list:
                self.add_proxy(p)
        else:
            self._load_from_env()

    def _load_from_env(self):
        """Loads proxies from PROXY_LIST or PROXY_FILE environment variables."""
        env_proxies = os.getenv("PROXY_LIST", "").strip()
        if env_proxies:
            for p in env_proxies.split(","):
                p_clean = p.strip()
                if p_clean:
                    self.add_proxy(p_clean)

        proxy_file = os.getenv("PROXY_FILE", "")
        if proxy_file and os.path.exists(proxy_file):
            try:
                with open(proxy_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line_clean = line.strip()
                        if line_clean and not line_clean.startswith("#"):
                            self.add_proxy(line_clean)
            except Exception as e:
                logger.error(f"Failed to read proxy file {proxy_file}: {e}")

        logger.info(f"Loaded {len(self.proxies)} proxies into pool.")

    def add_proxy(self, raw_proxy_str: str):
        raw = raw_proxy_str.strip()
        if raw:
            node = ProxyNode(raw_url=raw)
            if node.host:
                self.proxies.append(node)

    @property
    def total_proxies(self) -> int:
        return len(self.proxies)

    @property
    def available_proxies(self) -> List[ProxyNode]:
        """Returns proxies not currently undergoing cooldown."""
        return [p for p in self.proxies if p.is_active and not p.is_in_cooldown]

    def get_proxy(self) -> Optional[ProxyNode]:
        """
        Retrieves next eligible proxy according to chosen strategy.
        Falls back to None if proxy pool is empty (triggers direct connection).
        """
        available = self.available_proxies
        if not available:
            if self.proxies:
                # All proxies are in cooldown; pick the one whose cooldown expires earliest
                earliest = min(self.proxies, key=lambda p: p.cooldown_until)
                logger.warning("All proxies in cooldown. Re-using earliest expiring proxy.")
                return earliest
            return None

        if self.strategy == "LEAST_USED":
            chosen = min(available, key=lambda p: (p.last_used, p.failure_count))
        elif self.strategy == "RANDOM":
            chosen = random.choice(available)
        else:  # ROUND_ROBIN
            self._current_index = (self._current_index + 1) % len(available)
            chosen = available[self._current_index]

        chosen.last_used = time.time()
        return chosen

    def report_success(self, proxy: Optional[ProxyNode], latency_ms: float = 0.0):
        if proxy:
            proxy.mark_success(latency_ms)

    def report_failure(self, proxy: Optional[ProxyNode], status_code: Optional[int] = None):
        """Record proxy failure. Automatically trigger cooldown on HTTP 403 or 429."""
        if not proxy:
            return

        cooldown = self.default_cooldown_sec
        if status_code in (403, 429):
            # Anti-bot trigger: long cooldown
            cooldown = max(cooldown, 600)

        proxy.mark_failure(cooldown_duration_sec=cooldown)

    @staticmethod
    def get_australian_fingerprint() -> Dict[str, Any]:
        """Returns realistic Australian desktop headers and browser fingerprints."""
        preset = random.choice(AU_USER_AGENTS)
        headers = {
            "User-Agent": preset["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        if preset.get("sec_ch_ua"):
            headers["Sec-Ch-Ua"] = preset["sec_ch_ua"]
            headers["Sec-Ch-Ua-Mobile"] = "?0"
            headers["Sec-Ch-Ua-Platform"] = preset["sec_ch_ua_platform"]

        return {
            "headers": headers,
            "viewport": {"width": random.choice([1440, 1536, 1920]), "height": random.choice([900, 864, 1080])},
            "locale": "en-AU",
            "timezone_id": "Australia/Sydney",
            "geolocation": {"latitude": -33.8688, "longitude": 151.2093},  # Sydney default
        }
