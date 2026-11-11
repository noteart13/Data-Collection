import json

import pytest

from app.operations.shared_domain_limiter import (
    DomainCooldownActive,
    SharedDomainRateLimiter,
)


def test_shared_limiter_spaces_requests_for_the_same_host(tmp_path):
    now = [100.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    limiter = SharedDomainRateLimiter(
        tmp_path,
        min_delay_seconds=7,
        max_delay_seconds=7,
        clock=lambda: now[0],
        sleeper=sleep,
    )

    assert limiter.wait("https://www.property.com.au/a") == 0
    assert limiter.wait("https://www.property.com.au/b") == 7
    assert sleeps == [7]


def test_shared_limiter_applies_anti_bot_cooldown(tmp_path):
    now = [100.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    limiter = SharedDomainRateLimiter(
        tmp_path,
        min_delay_seconds=0,
        max_delay_seconds=0,
        cooldown_seconds=300,
        clock=lambda: now[0],
        sleeper=sleep,
    )
    url = "https://www.domain.com.au/property-profile/example"

    limiter.record_status(url, 429)
    assert limiter.wait(url) == 300
    assert sleeps == [300]

    state_file = next(tmp_path.glob("*.json"))
    assert json.loads(state_file.read_text(encoding="utf-8"))["last_status"] == 429


def test_shared_limiter_can_fail_fast_during_anti_bot_cooldown(tmp_path):
    now = [100.0]
    sleeps = []
    limiter = SharedDomainRateLimiter(
        tmp_path,
        min_delay_seconds=0,
        max_delay_seconds=0,
        cooldown_seconds=300,
        wait_on_cooldown=False,
        clock=lambda: now[0],
        sleeper=sleeps.append,
    )
    url = "https://www.domain.com.au/property-profile/example"

    limiter.record_status(url, 429)

    with pytest.raises(DomainCooldownActive, match="300"):
        limiter.wait(url)
    assert sleeps == []


def test_cooldown_is_isolated_per_egress_scope(tmp_path):
    now = [100.0]
    direct = SharedDomainRateLimiter(
        tmp_path,
        min_delay_seconds=0,
        max_delay_seconds=0,
        cooldown_seconds=300,
        wait_on_cooldown=False,
        scope="http-direct",
        clock=lambda: now[0],
        sleeper=lambda _seconds: None,
    )
    browser = SharedDomainRateLimiter(
        tmp_path,
        min_delay_seconds=0,
        max_delay_seconds=0,
        cooldown_seconds=300,
        wait_on_cooldown=False,
        scope="browser-direct",
        clock=lambda: now[0],
        sleeper=lambda _seconds: None,
    )
    url = "https://www.domain.com.au/property-profile/example"

    direct.record_status(url, 429)

    with pytest.raises(DomainCooldownActive):
        direct.wait(url)
    assert browser.wait(url) == 0
