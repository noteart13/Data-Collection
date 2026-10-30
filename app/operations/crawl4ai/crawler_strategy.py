import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable
import time
import random
import base64
import logging
import requests
from io import BytesIO
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import InvalidArgumentException, WebDriverException
from .utils import sanitize_input_encode
from webdriver_manager.chrome import ChromeDriverManager
# Logging setup
for log_name in [
    'selenium.webdriver.remote.remote_connection',
    'selenium.webdriver.common.service',
    'urllib3.connectionpool',
    'http.client',
    'selenium.webdriver.common.driver_finder'
]:
    logging.getLogger(log_name).setLevel(logging.WARNING)


def create_unique_temp_dir():
    session_id = f"session_{os.getpid()}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    temp_dir = os.path.join(tempfile.gettempdir(), session_id)
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def configure_chrome_options(temp_dir: str, user_agent: str = None, headless: bool = True) -> Options:
    options = Options()
    options.add_argument(f"--user-data-dir={temp_dir}")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument("--no-sandbox")
    options.add_argument("--incognito")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-sync")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--disable-default-apps")
    options.add_argument("--no-first-run")
    options.add_argument("--disable-cache")
    options.add_argument("--disable-application-cache")
    options.add_argument("--disk-cache-size=0")
    options.add_argument("--disable-logging")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--log-level=3")

    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")

    if headless:
        options.add_argument("--headless=new")

    return options


class CrawlerStrategy(ABC):
    @abstractmethod
    def crawl(self, url: str, **kwargs) -> str:
        pass

    @abstractmethod
    def take_screenshot(self) -> str:
        pass

    @abstractmethod
    def update_user_agent(self, user_agent: str):
        pass

    @abstractmethod
    def set_hook(self, hook_type: str, hook: Callable):
        pass


class CloudCrawlerStrategy(CrawlerStrategy):
    def crawl(self, url: str) -> str:
        data = {"urls": [url], "include_raw_html": True, "forced": True, "extract_blocks": False}
        response = requests.post("http://crawl4ai.uccode.io/crawl", json=data).json()
        return sanitize_input_encode(response["results"][0]["html"])

    def take_screenshot(self):
        raise NotImplementedError("Cloud crawler doesn't support screenshots")

    def update_user_agent(self, user_agent: str):
        pass

    def set_hook(self, hook_type: str, hook: Callable):
        pass


class LocalSeleniumCrawlerStrategy(CrawlerStrategy):
    def __init__(self, js_code=None, **kwargs):
        time.sleep(random.uniform(1, 3))
        self.temp_dir = create_unique_temp_dir()
        self.options = configure_chrome_options(
            temp_dir=self.temp_dir,
            user_agent=kwargs.get("user_agent"),
            headless=kwargs.get("headless", True)
        )
        # chromedriver_path = ChromeDriverManager().install()
        # chromedriver_path = ''
        self.service = Service(ChromeDriverManager().install())
        self.service.log_path = "NUL"

        try:
            self.driver = webdriver.Chrome(service=self.service, options=self.options)
        except Exception as e:
            print(f"[FALLBACK] Failed to start Chrome: {e}. Trying again with a new temp directory.")
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = create_unique_temp_dir()
            self.options = configure_chrome_options(
                temp_dir=self.temp_dir,
                user_agent=kwargs.get("user_agent"),
                headless=kwargs.get("headless", True)
            )
            self.driver = webdriver.Chrome(service=self.service, options=self.options)

        self.js_code = js_code
        self.verbose = kwargs.get("verbose", False)
        self.hooks = {
            'on_driver_created': None,
            'on_user_agent_updated': None,
            'before_get_url': None,
            'after_get_url': None,
            'before_return_html': None
        }

    def set_hook(self, hook_type: str, hook: Callable):
        if hook_type in self.hooks:
            self.hooks[hook_type] = hook

    def execute_hook(self, hook_type: str, *args):
        hook = self.hooks.get(hook_type)
        if hook:
            result = hook(*args)
            if isinstance(result, webdriver.Chrome):
                return result
        return args[0]

    def crawl(self, url: str, **kwargs) -> str:
        try:
            return self._do_crawl(url, **kwargs)
        except WebDriverException as e:
            print(f"[RETRY] Crawl failed: {e}. Restarting browser and retrying once...")
            self.quit()
            self.temp_dir = create_unique_temp_dir()
            self.options = configure_chrome_options(self.temp_dir)
            self.driver = webdriver.Chrome(service=self.service, options=self.options)
            return self._do_crawl(url, **kwargs)

    def _do_crawl(self, url: str, **kwargs) -> str:
        self.driver = self.execute_hook('before_get_url', self.driver)
        self.driver.get(url)
        WebDriverWait(self.driver, 20).until(lambda d: d.execute_script('return document.readyState') == 'complete')
        WebDriverWait(self.driver, 10).until(EC.presence_of_all_elements_located((By.TAG_NAME, "body")))
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        self.driver = self.execute_hook('after_get_url', self.driver)
        html = sanitize_input_encode(self.driver.page_source)
        self.driver = self.execute_hook('before_return_html', self.driver, html)
        return html

    def take_screenshot(self) -> str:
        try:
            self.driver.set_window_size(
                self.driver.execute_script("return document.body.scrollWidth"),
                self.driver.execute_script("return document.body.scrollHeight")
            )
            screenshot = self.driver.get_screenshot_as_png()
            return base64.b64encode(screenshot).decode('utf-8')
        except Exception as e:
            if self.verbose:
                print(f"[ERROR] Failed to take screenshot: {str(e)}")
            return ""

    def update_user_agent(self, user_agent: str):
        self.driver.quit()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir = create_unique_temp_dir()
        self.options = configure_chrome_options(self.temp_dir, user_agent=user_agent)
        self.driver = webdriver.Chrome(service=self.service, options=self.options)
        self.driver = self.execute_hook('on_user_agent_updated', self.driver)

    def quit(self):
        self.driver.quit()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
