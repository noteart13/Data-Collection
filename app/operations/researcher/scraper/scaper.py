"""Web scraper module for GPT Researcher.

This module provides the Scraper class that extracts content from URLs
using various scraping backends (BeautifulSoup, PyMuPDF, Browser, etc.).
"""

import asyncio
import importlib
import logging
import subprocess
import sys
from urllib.parse import urlparse

import requests
from colorama import Fore, init

from operations.utils.workers import WorkerPool

from . import (
    ArxivScraper,
    BeautifulSoupScraper,
    BrowserScraper,
    FireCrawl,
    NoDriverScraper,
    PyMuPDFScraper,
    TavilyExtract,
    WebBaseLoaderScraper,
)


class Scraper:
    """
    Scraper class to extract the content from the links
    """

    def __init__(
        self,
        urls,
        user_agent,
        scraper,
        worker_pool: WorkerPool,
        max_retries: int = 3,
        retry_delay_seconds: float = 2.0,
        min_content_length: int = 100,
        scrape_timeout_seconds: float = 90.0,
    ):
        """
        Initialize the Scraper class.
        Args:
            urls: List of URLs to scrape (duplicates will be removed)
        """
        # Optimization: Remove duplicate URLs to avoid redundant scraping
        unique_urls = list(dict.fromkeys(urls))  # Preserves order while removing duplicates
        duplicates_removed = len(urls) - len(unique_urls)

        self.urls = unique_urls
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.scraper = scraper
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.min_content_length = min_content_length
        self.scrape_timeout_seconds = scrape_timeout_seconds
        if self.scraper == "tavily_extract":
            self._check_pkg(self.scraper)
        if self.scraper == "firecrawl":
            self._check_pkg(self.scraper)
        self.logger = logging.getLogger(__name__)
        self.worker_pool = worker_pool

        # Log deduplication results if duplicates were found
        if duplicates_removed > 0:
            self.logger.info(
                f"Removed {duplicates_removed} duplicate URL(s). "
                f"Scraping {len(unique_urls)} unique URLs instead of {len(urls)}."
            )

    async def run(self):
        """
        Extracts the content from the links
        """
        contents = await asyncio.gather(
            *(self.extract_data_from_url(url, self.session) for url in self.urls)
        )

        res = [content for content in contents if content["raw_content"] is not None]
        return res

    def _check_pkg(self, scrapper_name: str) -> None:
        """
        Checks and ensures required Python packages are available for scrapers that need
        dependencies beyond requirements.txt. When adding a new scraper to the repo, update `pkg_map`
        with its required information and call check_pkg() during initialization.
        """
        pkg_map = {
            "tavily_extract": {
                "package_installation_name": "tavily-python",
                "import_name": "tavily",
            },
            "firecrawl": {
                "package_installation_name": "firecrawl-py",
                "import_name": "firecrawl",
            },
        }
        pkg = pkg_map[scrapper_name]
        if not importlib.util.find_spec(pkg["import_name"]):
            pkg_inst_name = pkg["package_installation_name"]
            init(autoreset=True)
            print(Fore.YELLOW + f"{pkg_inst_name} not found. Attempting to install...")
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", pkg_inst_name]
                )
                importlib.invalidate_caches()
                print(Fore.GREEN + f"{pkg_inst_name} installed successfully.")
            except subprocess.CalledProcessError:
                raise ImportError(
                    Fore.RED
                    + f"Unable to install {pkg_inst_name}. Please install manually with "
                    f"`pip install -U {pkg_inst_name}`"
                )

    async def extract_data_from_url(self, link, session):
        """
        Extracts the data from the link with logging
        """
        async with self.worker_pool.throttle():
            last_result = {
                "url": link,
                "raw_content": None,
                "image_urls": [],
                "title": "",
                "html": "",
            }

            for attempt in range(1, self.max_retries + 1):
                if attempt > 1:
                    delay = self.retry_delay_seconds * attempt
                    self.logger.info(
                        f"Retrying {link} in {delay:.1f}s "
                        f"(attempt {attempt}/{self.max_retries})"
                    )
                    await asyncio.sleep(delay)

                try:
                    result = await asyncio.wait_for(
                        self._extract_data_once(link, session),
                        timeout=self.scrape_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    self.logger.error(
                        f"Timed out scraping {link} after "
                        f"{self.scrape_timeout_seconds:.0f}s"
                    )
                    result = {
                        "url": link,
                        "raw_content": None,
                        "image_urls": [],
                        "title": "",
                        "html": "",
                    }
                last_result = result

                raw_content = result.get("raw_content") or ""
                if len(raw_content) >= self.min_content_length:
                    return result

                self.logger.warning(
                    f"Attempt {attempt}/{self.max_retries} returned short content "
                    f"for {link}: {len(raw_content)} characters"
                )

            return last_result

    async def _extract_data_once(self, link, session):
        """
        Extracts data from a URL once. Retry policy lives in extract_data_from_url.
        """
        try:
            Scraper = self.get_scraper(link)
            scraper = Scraper(link, session)

            scraper_name = scraper.__class__.__name__
            self.logger.info(f"\n=== Using {scraper_name} ===")

            if hasattr(scraper, "scrape_async"):
                scrape_result = await scraper.scrape_async()
            else:
                scrape_result = await asyncio.get_running_loop().run_in_executor(
                    self.worker_pool.executor, scraper.scrape
                )

            content, image_urls, title = scrape_result[:3]
            html = scrape_result[3] if len(scrape_result) > 3 else ""

            if len(content) < self.min_content_length:
                self.logger.warning(f"Content too short or empty for {link}")
                return {
                    "url": link,
                    "raw_content": None,
                    "image_urls": [],
                    "title": title,
                    "html": html,
                }

            self.logger.info(f"\nTitle: {title}")
            self.logger.info(
                f"Content length: {len(content) if content else 0} characters"
            )
            self.logger.info(f"Number of images: {len(image_urls)}")
            self.logger.info(f"URL: {link}")
            self.logger.info("=" * 50)

            return {
                "url": link,
                "raw_content": content,
                "image_urls": image_urls,
                "title": title,
                "html": html,
            }

        except Exception as e:
            self.logger.error(f"Error processing {link}: {str(e)}")
            return {
                "url": link,
                "raw_content": None,
                "image_urls": [],
                "title": "",
                "html": "",
            }

    def get_scraper(self, link):
        """
        The function `get_scraper` determines the appropriate scraper class based on the provided link
        or a default scraper if none matches.

        Args:
          link: The `get_scraper` method takes a `link` parameter which is a URL link to a webpage or a
        PDF file. Based on the type of content the link points to, the method determines the appropriate
        scraper class to use for extracting data from that content.

        Returns:
          The `get_scraper` method returns the scraper class based on the provided link. The method
        checks the link to determine the appropriate scraper class to use based on predefined mappings
        in the `SCRAPER_CLASSES` dictionary. If the link ends with ".pdf", it selects the
        `PyMuPDFScraper` class. If the link contains "arxiv.org", it selects the `ArxivScraper
        """

        SCRAPER_CLASSES = {
            "pdf": PyMuPDFScraper,
            "arxiv": ArxivScraper,
            "bs": BeautifulSoupScraper,
            "web_base_loader": WebBaseLoaderScraper,
            "browser": BrowserScraper,
            "nodriver": NoDriverScraper,
            "tavily_extract": TavilyExtract,
            "firecrawl": FireCrawl,
        }

        scraper_key = None

        path = urlparse(link).path
        if path.lower().endswith(".pdf"):
            scraper_key = "pdf"
        elif "arxiv.org" in link:
            scraper_key = "arxiv"
        else:
            scraper_key = self.scraper

        scraper_class = SCRAPER_CLASSES.get(scraper_key)
        if scraper_class is None:
            raise Exception("Scraper not found.")

        return scraper_class
