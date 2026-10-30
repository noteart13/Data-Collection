from contextlib import asynccontextmanager
import math
from pathlib import Path
import random
import traceback
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from typing import Dict, Literal, cast, Tuple, List
import requests
import asyncio
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time

try:
    import psutil
except ImportError:  # Cleanup remains best-effort in minimal environments.
    psutil = None

from ..utils import get_relevant_images, extract_title, get_text_from_soup, clean_soup


class NoDriverScraper:
    logger = logging.getLogger(__name__)
    max_browsers = 5
    browser_load_threshold = 8
    browser_start_timeout_seconds = 20
    navigation_timeout_seconds = 35
    page_command_timeout_seconds = 8
    close_timeout_seconds = 8
    browser_stop_timeout_seconds = 10
    min_text_length = 200
    browsers: set["NoDriverScraper.Browser"] = set()
    browsers_lock = asyncio.Lock()
    domain_semaphores: Dict[str, asyncio.Semaphore] = {}
    profile_prefix = "profile-"

    @classmethod
    def cache_root(cls) -> Path:
        """Return the scraper cache outside the Windows system drive by default."""
        configured = os.environ.get("NODRIVER_CACHE_DIR")
        if configured:
            cache_root = Path(configured).expanduser()
        else:
            project_root = Path(__file__).resolve().parents[5]
            cache_root = project_root / ".cache" / "nodriver"
        cache_root.mkdir(parents=True, exist_ok=True)
        return cache_root.resolve()

    @staticmethod
    def process_is_running(process_id: int) -> bool:
        if psutil is not None:
            return psutil.pid_exists(process_id)
        try:
            os.kill(process_id, 0)
        except (OSError, ValueError):
            return False
        return True

    @classmethod
    def cleanup_orphaned_profiles(cls) -> None:
        """Remove profiles left by dead workers without touching active workers."""
        cache_root = cls.cache_root()
        max_age_hours = max(
            1,
            int(os.environ.get("NODRIVER_ORPHAN_MAX_AGE_HOURS", "24")),
        )
        cutoff = time.time() - (max_age_hours * 60 * 60)

        for profile_dir in cache_root.glob(f"{cls.profile_prefix}*"):
            if not profile_dir.is_dir():
                continue
            try:
                # profile-<pid>-<random suffix>
                owner_pid = int(profile_dir.name.split("-", 2)[1])
                if cls.process_is_running(owner_pid):
                    continue
                if profile_dir.stat().st_mtime > cutoff:
                    continue
                shutil.rmtree(profile_dir)
                cls.logger.info(f"Removed orphaned browser profile: {profile_dir}")
            except (IndexError, ValueError, FileNotFoundError, PermissionError, OSError):
                # Cleanup is best-effort; a concurrent worker may own/remove it.
                continue

    @staticmethod
    def scraper_chrome_process_ids(user_data_dir: str | None = None) -> set[int]:
        """Return Chrome roots for one zendriver profile, never another worker's."""
        if psutil is None:
            return set()
        normalized_profile = (
            os.path.normcase(os.path.normpath(user_data_dir))
            if user_data_dir
            else None
        )
        process_ids = set()
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (process.info.get("name") or "").lower()
                arguments = process.info.get("cmdline") or []
                profile_arguments = [
                    argument.split("=", 1)[1]
                    for argument in arguments
                    if argument.startswith("--user-data-dir=")
                ]
                owns_profile = not normalized_profile or any(
                    os.path.normcase(os.path.normpath(profile))
                    == normalized_profile
                    for profile in profile_arguments
                )
                if (
                    name in {"chrome.exe", "chrome", "chromium", "chromium-browser"}
                    and any(
                        argument.startswith("--remote-debugging-port=")
                        for argument in arguments
                    )
                    and profile_arguments
                    and owns_profile
                ):
                    process_ids.add(process.pid)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        return process_ids

    @staticmethod
    def stop_process_ids(process_ids: set[int]):
        if psutil is None:
            return
        processes = []
        for process_id in process_ids:
            try:
                process = psutil.Process(process_id)
                processes.extend(process.children(recursive=True))
                processes.append(process)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue

        unique_processes = {process.pid: process for process in processes}.values()
        for process in unique_processes:
            try:
                process.terminate()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

        _, alive = psutil.wait_procs(list(unique_processes), timeout=3)
        for process in alive:
            try:
                process.kill()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

    @staticmethod
    def get_domain(url: str) -> str:
        domain = urlparse(url).netloc
        parts = domain.split(".")
        if len(parts) > 2:
            domain = ".".join(parts[-2:])
        return domain

    @staticmethod
    def should_run_headless() -> bool:
        configured = os.environ.get("NODRIVER_HEADLESS")
        if configured is not None:
            return configured.strip().lower() in {"1", "true", "yes", "y", "on"}

        return os.name != "nt" and not os.environ.get("DISPLAY")

    class Browser:
        def __init__(
            self,
            driver: "zendriver.Browser",
            process_ids: set[int] | None = None,
            user_data_dir: str | None = None,
        ):
            self.driver = driver
            self.process_ids = process_ids or set()
            self.user_data_dir = user_data_dir
            self.processing_count = 0
            self.has_blank_page = True
            self.allowed_requests_times = {}
            self.tab_mode = True
            self.max_scroll_percent = 500
            self.stopping = False
            self.unhealthy = False

        async def get(
            self,
            url: str,
            timeout: float | None = None,
        ) -> "zendriver.Tab":
            self.processing_count += 1
            try:
                async with self.rate_limit_for_domain(url):
                    self.has_blank_page = False
                    timeout = timeout or NoDriverScraper.navigation_timeout_seconds
                    NoDriverScraper.logger.info(f"Navigating browser to {url}")

                    # Navigating zendriver's startup about:blank tab can hang in
                    # connection.send(Page.navigate) while Chrome itself remains
                    # alive. Creating the target with its URL goes through the
                    # browser CDP connection and avoids that dead path.
                    page = await asyncio.wait_for(
                        self.driver.get(
                            url,
                            new_tab=True,
                            new_window=not self.tab_mode,
                        ),
                        timeout=timeout,
                    )
                    await asyncio.wait_for(
                        page.bring_to_front(),
                        timeout=NoDriverScraper.page_command_timeout_seconds,
                    )
                    NoDriverScraper.logger.info(f"Browser navigation started: {url}")
                    return page
            except BaseException:
                self.processing_count = max(0, self.processing_count - 1)
                raise

        async def scroll_page_to_bottom(
            self,
            page: "zendriver.Tab",
            timeout: float = 25,
        ):
            deadline = asyncio.get_running_loop().time() + timeout
            total_scroll_percent = 0
            while asyncio.get_running_loop().time() < deadline:
                # in tab mode, we need to bring the tab to front before scrolling to load the page content properly
                if self.tab_mode:
                    await asyncio.wait_for(
                        page.bring_to_front(),
                        timeout=NoDriverScraper.page_command_timeout_seconds,
                    )
                scroll_percent = random.randrange(46, 97)
                total_scroll_percent += scroll_percent
                await asyncio.wait_for(
                    page.scroll_down(scroll_percent),
                    timeout=NoDriverScraper.page_command_timeout_seconds,
                )
                await self.wait_or_timeout(page, "idle", 2)
                await page.sleep(random.uniform(0.23, 0.56))

                if total_scroll_percent >= self.max_scroll_percent:
                    break

                if cast(
                    bool,
                    await asyncio.wait_for(
                        page.evaluate(
                            "window.innerHeight + window.scrollY >= document.scrollingElement.scrollHeight"
                        ),
                        timeout=NoDriverScraper.page_command_timeout_seconds,
                    ),
                ):
                    break

        async def wait_or_timeout(
            self,
            page: "zendriver.Tab",
            until: Literal["complete", "idle"] = "idle",
            timeout: float = 3,
        ):
            try:
                if until == "idle":
                    await asyncio.wait_for(page.wait(), timeout)
                else:
                    timeout = math.ceil(timeout)
                    await asyncio.wait_for(
                        page.wait_for_ready_state(until, timeout=timeout),
                        timeout=timeout + 1,
                    )
            except asyncio.TimeoutError:
                NoDriverScraper.logger.debug(
                    f"timeout waiting for {until} after {timeout} seconds"
                )

        async def wait_for_min_text_length(
            self,
            page: "zendriver.Tab",
            min_length: int = None,
            timeout: float = 12,
        ) -> int:
            min_length = min_length or NoDriverScraper.min_text_length
            deadline = asyncio.get_running_loop().time() + timeout
            last_length = 0

            while asyncio.get_running_loop().time() < deadline:
                try:
                    last_length = cast(
                        int,
                        await asyncio.wait_for(
                            page.evaluate(
                                "document.body ? document.body.innerText.length : 0"
                            ),
                            timeout=NoDriverScraper.page_command_timeout_seconds,
                        ),
                    )
                    if last_length >= min_length:
                        return last_length
                except Exception as e:
                    NoDriverScraper.logger.debug(
                        f"Failed to inspect text length: {str(e)}"
                    )

                await page.sleep(random.uniform(0.7, 1.2))

            return last_length

        async def wait_for_property_content(
            self,
            page: "zendriver.Tab",
            timeout: float = 18,
        ) -> bool:
            deadline = asyncio.get_running_loop().time() + timeout
            property_markers = (
                "property.com.au",
                "viewing",
                "results",
                "for sale",
                "sold",
                "off market",
                "beds",
                "baths",
            )

            while asyncio.get_running_loop().time() < deadline:
                try:
                    page_state = cast(
                        dict,
                        await asyncio.wait_for(
                            page.evaluate(
                                r"""
                                (() => {
                                    const body = document.body ? document.body.innerText : "";
                                    const title = document.title || "";
                                    return {
                                        textLength: body.length,
                                        text: body.slice(0, 5000).toLowerCase(),
                                        title: title.toLowerCase(),
                                        links: document.querySelectorAll("a[href]").length,
                                    };
                                })()
                                """
                            ),
                            timeout=NoDriverScraper.page_command_timeout_seconds,
                        ),
                    )
                    text = f"{page_state.get('title', '')}\n{page_state.get('text', '')}"
                    has_marker = any(marker in text for marker in property_markers)
                    has_content = (
                        page_state.get("textLength", 0)
                        >= NoDriverScraper.min_text_length
                    )
                    has_links = page_state.get("links", 0) > 5
                    if has_marker and (has_content or has_links):
                        return True
                except Exception as e:
                    NoDriverScraper.logger.debug(
                        f"Failed to inspect property content: {str(e)}"
                    )

                await page.sleep(random.uniform(0.7, 1.2))

            return False

        async def close_page(self, page: "zendriver.Tab"):
            try:
                await asyncio.wait_for(
                    page.close(),
                    timeout=NoDriverScraper.close_timeout_seconds,
                )
            except Exception as e:
                NoDriverScraper.logger.error(f"Failed to close page: {e}")
            finally:
                self.processing_count = max(0, self.processing_count - 1)

        @asynccontextmanager
        async def rate_limit_for_domain(self, url: str):
            domain = NoDriverScraper.get_domain(url)
            semaphore = NoDriverScraper.domain_semaphores.setdefault(
                domain,
                asyncio.Semaphore(1),
            )

            was_locked = semaphore.locked()
            async with semaphore:
                if was_locked:
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                # Exceptions raised by the request body must propagate to the
                # scraper's retry and browser-health handling. Catching them
                # here used to turn navigation failures into a misleading None.
                yield

        async def stop(self):
            if self.stopping:
                return
            self.stopping = True
            try:
                await asyncio.wait_for(
                    self.driver.stop(),
                    timeout=NoDriverScraper.browser_stop_timeout_seconds,
                )
            except Exception as exc:
                self.unhealthy = True
                NoDriverScraper.logger.warning(
                    f"Browser shutdown failed; force stopping scraper browser: {exc}"
                )
            if self.unhealthy:
                await asyncio.to_thread(self.force_stop_process)
            await asyncio.to_thread(self.cleanup_profile)

        def cleanup_profile(self):
            """Delete this scraper's disposable profile after Chrome exits."""
            if not self.user_data_dir:
                return
            try:
                shutil.rmtree(self.user_data_dir)
            except FileNotFoundError:
                pass
            except (PermissionError, OSError) as exc:
                NoDriverScraper.logger.warning(
                    f"Unable to remove browser profile {self.user_data_dir}: {exc}"
                )

        def force_stop_process(self):
            """Kill only the browser process started by this driver, when available."""
            process = None
            for owner in (
                self.driver,
                getattr(self.driver, "connection", None),
                getattr(self.driver, "_connection", None),
            ):
                if owner is None:
                    continue
                for attribute in ("process", "_process", "browser_process"):
                    candidate = getattr(owner, attribute, None)
                    if getattr(candidate, "pid", None):
                        process = candidate
                        break
                if process:
                    break

            if process is None:
                if self.process_ids:
                    NoDriverScraper.stop_process_ids(self.process_ids)
                    return
                NoDriverScraper.logger.error("Unable to find scraper Chrome PID")
                return

            pid = process.pid
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        check=False,
                        capture_output=True,
                        timeout=NoDriverScraper.close_timeout_seconds,
                    )
                else:
                    os.kill(pid, signal.SIGTERM)
            except Exception as exc:
                NoDriverScraper.logger.error(
                    f"Failed to force stop scraper browser process {pid}: {exc}"
                )

    @classmethod
    async def get_browser(cls, headless: bool | None = None) -> "NoDriverScraper.Browser":
        async def create_browser():
            try:
                global zendriver
                import zendriver
            except ImportError:
                raise ImportError(
                    "The zendriver package is required to use NoDriverScraper. "
                    "Please install it with: pip install zendriver"
                )

            cls.cleanup_orphaned_profiles()
            cache_root = cls.cache_root()
            user_data_dir = tempfile.mkdtemp(
                prefix=f"{cls.profile_prefix}{os.getpid()}-",
                dir=cache_root,
            )
            disk_cache_mb = max(
                16,
                int(os.environ.get("NODRIVER_DISK_CACHE_MB", "256")),
            )
            disk_cache_bytes = disk_cache_mb * 1024 * 1024
            config = zendriver.Config(
                user_data_dir=user_data_dir,
                headless=cls.should_run_headless() if headless is None else headless,
                browser_args=[
                    f"--disk-cache-size={disk_cache_bytes}",
                    f"--media-cache-size={disk_cache_bytes}",
                ],
                # zendriver sleeps for this value before every connection
                # attempt. Ten seconds made startup appear frozen and left very
                # little time for the actual navigation.
                browser_connection_timeout=0.5,
                browser_connection_max_tries=40,
            )
            # Force creation of this browser's unique profile before startup. It
            # gives cleanup a stable identity even when several worker processes
            # start Chrome at the same time.
            try:
                driver = await asyncio.wait_for(
                    zendriver.start(config),
                    timeout=cls.browser_start_timeout_seconds,
                )
            except BaseException:
                failed_process_ids = cls.scraper_chrome_process_ids(user_data_dir)
                if failed_process_ids:
                    await asyncio.to_thread(
                        cls.stop_process_ids,
                        failed_process_ids,
                    )
                shutil.rmtree(user_data_dir, ignore_errors=True)
                raise

            driver_pid = getattr(driver, "_process_pid", None)
            process_ids = {driver_pid} if driver_pid else set()
            if not process_ids:
                process_ids = cls.scraper_chrome_process_ids(user_data_dir)
            browser = cls.Browser(driver, process_ids, user_data_dir)
            cls.browsers.add(browser)
            return browser

        async with cls.browsers_lock:
            if len(cls.browsers) == 0:
                # No browsers available, create new one
                return await create_browser()

            # Load balancing: Get browser with lowest number of tabs
            available_browsers = [
                browser for browser in cls.browsers
                if not browser.stopping and not browser.unhealthy
            ]
            if not available_browsers:
                if len(cls.browsers) < cls.max_browsers:
                    return await create_browser()
                available_browsers = [
                    browser for browser in cls.browsers
                    if not browser.stopping and not browser.unhealthy
                ]

            browser = min(available_browsers, key=lambda b: b.processing_count)

            # If all browsers are heavily loaded and we can create more
            if (
                browser.processing_count >= cls.browser_load_threshold
                and len(cls.browsers) < cls.max_browsers
            ):
                return await create_browser()

            return browser

    @classmethod
    async def release_browser(cls, browser: Browser):
        async with cls.browsers_lock:
            keep_browser = os.environ.get("NODRIVER_KEEP_BROWSER", "false").strip().lower() in {
                "1",
                "true",
                "yes",
                "y",
                "on",
            }
            should_stop = browser and (
                browser.unhealthy
                or (browser.processing_count <= 0 and not keep_browser)
            )
            if should_stop:
                try:
                    await browser.stop()
                except Exception as e:
                    NoDriverScraper.logger.error(f"Failed to release browser: {e}")
                finally:
                    cls.browsers.discard(browser)

    @classmethod
    async def close_all_browsers(cls):
        """Close browser sessions retained by long-running scraper jobs."""
        async with cls.browsers_lock:
            browsers = list(cls.browsers)
            cls.browsers.clear()

        for browser in browsers:
            try:
                await browser.stop()
            except Exception as e:
                cls.logger.error(f"Failed to close retained browser: {e}")

    def __init__(
        self,
        url: str,
        session: requests.Session | None = None,
    ):
        self.url = url
        self.session = session
        self.debug = False

    async def scrape_async(self) -> Tuple[str, list[dict], str, str]:
        """Returns tuple of (text, image_urls, title, html)"""
        if not self.url:
            return (
                "A URL was not specified, cancelling request to browse website.",
                [],
                "",
                "",
            )

        browser: NoDriverScraper.Browser | None = None
        page = None
        try:
            try:
                browser = await self.get_browser()
            except ImportError as e:
                self.logger.error(f"Failed to initialize browser: {str(e)}")
                return "", [], "", ""

            page = await browser.get(self.url)
            if page is None:
                # browser.get() increments processing_count before returning;
                # a None result means the connection timed out. Decrement to
                # avoid leaking the slot and deadlocking the browser pool.
                browser.processing_count = max(0, browser.processing_count - 1)
                return "Browser failed to open page (returned None)", [], "", ""
            await browser.wait_or_timeout(page, "complete", 10)
            # wait for potential redirection
            await page.sleep(random.uniform(1.0, 2.0))
            await browser.wait_or_timeout(page, "idle", 5)
            if "property.com.au" in urlparse(self.url).netloc:
                await browser.wait_for_property_content(page, 20)
            else:
                await browser.wait_for_min_text_length(page, 200, 12)

            await browser.scroll_page_to_bottom(page, timeout=25)
            await browser.wait_for_min_text_length(page, 200, 8)
            html = await asyncio.wait_for(page.get_content(), timeout=15)
            soup = BeautifulSoup(html, "lxml")
            clean_soup(soup)
            text = get_text_from_soup(soup)
            image_urls = get_relevant_images(soup, self.url)
            title = extract_title(soup)

            if len(text) < 200:
                await page.sleep(random.uniform(2.0, 4.0))
                if "property.com.au" in urlparse(self.url).netloc:
                    await browser.wait_for_property_content(page, 10)
                await browser.scroll_page_to_bottom(page, timeout=15)
                html = await asyncio.wait_for(
                    page.get_content(),
                    timeout=NoDriverScraper.page_command_timeout_seconds,
                )
                soup = BeautifulSoup(html, "lxml")
                clean_soup(soup)
                text = get_text_from_soup(soup)
                image_urls = get_relevant_images(soup, self.url)
                title = extract_title(soup)

            if len(text) < 200:
                self.logger.warning(
                    f"Content is too short from {self.url}. Title: {title}, Text length: {len(text)},\n"
                    f"excerpt: {text}."
                )
                if self.debug:
                    screenshot_dir = Path("logs/screenshots")
                    screenshot_dir.mkdir(exist_ok=True)
                    screenshot_path = (
                        screenshot_dir
                        / f"screenshot-error-{NoDriverScraper.get_domain(self.url)}.jpeg"
                    )
                    await page.save_screenshot(screenshot_path)
                    self.logger.warning(
                        f"check screenshot at [{screenshot_path}] for more details."
                    )

            return text, image_urls, title, html
        except asyncio.CancelledError:
            # The outer scraper timeout cancelled this task. Do not reuse the
            # browser: force-close its process before the next retry starts.
            if browser:
                browser.unhealthy = True
            raise
        except Exception as e:
            self.logger.error(
                f"An error occurred during scraping: {str(e)}\n"
                "Full stack trace:\n"
                f"{traceback.format_exc()}"
            )
            if browser:
                browser.unhealthy = True
            return "", [], "", ""
        finally:
            try:
                if page and browser:
                    await browser.close_page(page)
                if browser:
                    await self.release_browser(browser)
            except Exception as e:
                self.logger.error(e)
