"""
Unit tests for SmartProxyManager & Stealth Anti-Bot Fingerprint Generator.
"""

import time
from app.operations.proxy_manager import SmartProxyManager, ProxyNode, AU_USER_AGENTS


def test_proxy_node_parsing():
    p1 = ProxyNode(raw_url="http://user:pass123@1.2.3.4:8080")
    assert p1.protocol == "http"
    assert p1.host == "1.2.3.4"
    assert p1.port == 8080
    assert p1.username == "user"
    assert p1.password == "pass123"

    playwright_cfg = p1.to_playwright_proxy()
    assert playwright_cfg["server"] == "http://1.2.3.4:8080"
    assert playwright_cfg["username"] == "user"
    assert playwright_cfg["password"] == "pass123"

    p2 = ProxyNode(raw_url="5.6.7.8:3128")
    assert p2.protocol == "http"
    assert p2.host == "5.6.7.8"
    assert p2.port == 3128
    assert p2.username is None
    print("test_proxy_node_parsing passed.")


def test_proxy_rotation_and_cooldown():
    proxies = ["http://proxy1:8080", "http://proxy2:8080", "http://proxy3:8080"]
    pm = SmartProxyManager(proxy_list=proxies, default_cooldown_sec=5, strategy="ROUND_ROBIN")

    assert pm.total_proxies == 3
    assert len(pm.available_proxies) == 3

    # Round robin gets different proxies
    first = pm.get_proxy()
    second = pm.get_proxy()
    assert first.host != second.host

    # Report failure on first proxy (HTTP 429 triggering anti-bot backoff)
    pm.report_failure(first, status_code=429)
    assert first.is_in_cooldown is True
    assert len(pm.available_proxies) == 2

    # Success on second proxy
    pm.report_success(second, latency_ms=120.5)
    assert second.success_count == 1
    assert second.latency_ms == 120.5
    print("test_proxy_rotation_and_cooldown passed.")


def test_australian_fingerprint():
    pm = SmartProxyManager()
    fp = pm.get_australian_fingerprint()

    assert "User-Agent" in fp["headers"]
    assert "en-AU" in fp["headers"]["Accept-Language"]
    assert fp["locale"] == "en-AU"
    assert fp["timezone_id"] == "Australia/Sydney"
    assert fp["viewport"]["width"] in [1440, 1536, 1920]
    print("test_australian_fingerprint passed.")


if __name__ == "__main__":
    test_proxy_node_parsing()
    test_proxy_rotation_and_cooldown()
    test_australian_fingerprint()
    print("All SmartProxyManager tests passed successfully!")
