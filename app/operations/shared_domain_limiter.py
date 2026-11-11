"""Small cross-process rate limiter for portal browser traffic.

The browser workers are separate processes, so an asyncio/thread semaphore is
not sufficient.  This module uses one OS-locked state file per hostname.  It
contains no credentials and can safely live in ``scratch/``.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import random
import re
import threading
import time
from typing import Callable, Iterator, Optional
from urllib.parse import urlparse


class DomainCooldownActive(RuntimeError):
    """Raised when fail-fast mode encounters an active portal cooldown."""

    def __init__(self, host: str, retry_after_seconds: float) -> None:
        self.host = host
        self.retry_after_seconds = max(0.0, float(retry_after_seconds))
        super().__init__(
            f"{host} is cooling down; retry after "
            f"{self.retry_after_seconds:.0f} seconds"
        )


_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _get_thread_lock(lock_path: Path) -> threading.Lock:
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        if key not in _THREAD_LOCKS:
            _THREAD_LOCKS[key] = threading.Lock()
        return _THREAD_LOCKS[key]


class SharedDomainRateLimiter:
    def __init__(
        self,
        root: Path,
        *,
        min_delay_seconds: float = 6.0,
        max_delay_seconds: float = 10.0,
        cooldown_seconds: float = 300.0,
        wait_on_cooldown: bool = True,
        scope: str = "default",
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.min_delay_seconds = max(0.0, float(min_delay_seconds))
        self.max_delay_seconds = max(
            self.min_delay_seconds, float(max_delay_seconds)
        )
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.wait_on_cooldown = bool(wait_on_cooldown)
        self.scope = re.sub(
            r"[^a-z0-9.-]+", "_", str(scope or "default").lower()
        )
        self.clock = clock
        self.sleeper = sleeper
        self.jitter = jitter

    @staticmethod
    def _host(url: str) -> str:
        host = (urlparse(str(url or "")).hostname or "unknown").lower()
        return re.sub(r"[^a-z0-9.-]+", "_", host)

    def _paths(self, url: str) -> tuple[Path, Path]:
        host = self._host(url)
        stem = f"{self.scope}__{host}"
        return self.root / f"{stem}.lock", self.root / f"{stem}.json"

    @contextmanager
    def _locked(self, lock_path: Path) -> Iterator[None]:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        thread_lock = _get_thread_lock(lock_path)
        with thread_lock:
            with open(lock_path, "a+b") as lock_file:
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    locked_file = False
                    for _ in range(30):
                        try:
                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                            locked_file = True
                            break
                        except (OSError, IOError):
                            time.sleep(0.05 + random.uniform(0.01, 0.05))

                    if not locked_file:
                        try:
                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                            locked_file = True
                        except (OSError, IOError):
                            pass

                    try:
                        yield
                    finally:
                        if locked_file:
                            try:
                                lock_file.seek(0)
                                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                            except (OSError, IOError):
                                pass
                else:  # pragma: no cover - Windows is the production environment
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read_state(state_path: Path) -> dict:
        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _write_state(state_path: Path, state: dict) -> None:
        temporary = state_path.with_suffix(state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(temporary, state_path)

    def is_cooldown_active(self, url: str) -> tuple[bool, float]:
        """Check if domain is currently on cooldown without acquiring a lock. Returns (is_blocked, remaining_seconds)."""
        _lock_path, state_path = self._paths(url)
        state = self._read_state(state_path)
        blocked_until = float(state.get("blocked_until") or 0.0)
        now = self.clock()
        if blocked_until > now:
            return True, blocked_until - now
        return False, 0.0

    def wait(self, url: str) -> float:
        """Reserve the next request slot for ``url`` and return seconds slept."""
        lock_path, state_path = self._paths(url)
        with self._locked(lock_path):
            state = self._read_state(state_path)
            now = self.clock()
            blocked_until = float(state.get("blocked_until") or 0.0)
            if blocked_until > now and not self.wait_on_cooldown:
                raise DomainCooldownActive(
                    self._host(url), blocked_until - now
                )
            delay = self.jitter(
                self.min_delay_seconds, self.max_delay_seconds
            )
            ready_at = max(
                blocked_until,
                float(state.get("last_started_at") or 0.0) + delay,
            )
            slept = max(0.0, ready_at - now)
            state["last_started_at"] = max(ready_at, self.clock())
            self._write_state(state_path, state)
        if slept:
            if slept >= 1.0:
                print(f"\n[RateLimit] Domain '{self._host(url)}' cooldown/delay active: waiting {slept:.1f}s...", flush=True)
            self.sleeper(slept)
        return slept

    def record_status(self, url: str, status_code: Optional[int]) -> None:
        """Apply a shared cooldown after an explicit anti-bot response."""
        if status_code not in {403, 429}:
            return
        lock_path, state_path = self._paths(url)
        with self._locked(lock_path):
            state = self._read_state(state_path)
            state["blocked_until"] = max(
                float(state.get("blocked_until") or 0.0),
                self.clock() + self.cooldown_seconds,
            )
            state["last_status"] = int(status_code)
            self._write_state(state_path, state)

    def record_challenge(self, url: str) -> None:
        self.record_status(url, 429)
