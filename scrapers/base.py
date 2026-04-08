"""
Base scraper class — all source scrapers inherit from this.
Handles retries, logging, polite delays, and Selenium setup.
"""
import time
import logging
import random
import requests
from abc import ABC, abstractmethod
from typing import List
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.models import AuctionListing
import config.settings as cfg

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class BaseScraper(ABC):
    source_name: str = "Unknown"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._driver = None

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def get(self, url: str, **kwargs) -> requests.Response:
        for attempt in range(cfg.MAX_RETRIES):
            try:
                time.sleep(cfg.REQUEST_DELAY_SECONDS + random.uniform(0, 1))
                resp = self.session.get(url, timeout=cfg.REQUEST_TIMEOUT, **kwargs)
                resp.raise_for_status()
                return resp
            except Exception as e:
                logger.warning(f"[{self.source_name}] Attempt {attempt+1} failed for {url}: {e}")
                if attempt == cfg.MAX_RETRIES - 1:
                    raise
        raise RuntimeError(f"All retries failed for {url}")

    def soup(self, url: str) -> BeautifulSoup:
        resp = self.get(url)
        return BeautifulSoup(resp.text, "lxml")

    # ── Selenium helpers ──────────────────────────────────────────────────────

    def get_driver(self) -> webdriver.Chrome:
        if self._driver:
            return self._driver
        opts = Options()
        if cfg.USE_HEADLESS_BROWSER:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_argument(f"user-agent={HEADERS['User-Agent']}")
        opts.add_argument("--window-size=1920,1080")
        try:
            self._driver = webdriver.Chrome(options=opts)
        except Exception:
            # fallback: try with explicit chromedriver path
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            self._driver = webdriver.Chrome(service=service, options=opts)
        return self._driver

    def js_get(self, url: str, wait_selector: str = None, wait_seconds: int = 10) -> BeautifulSoup:
        driver = self.get_driver()
        driver.get(url)
        if wait_selector:
            try:
                WebDriverWait(driver, wait_seconds).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
                )
            except Exception:
                logger.warning(f"[{self.source_name}] Wait timeout for {wait_selector}")
        time.sleep(2)
        return BeautifulSoup(driver.page_source, "lxml")

    def quit_driver(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    # ── Price parsing ─────────────────────────────────────────────────────────

    @staticmethod
    def parse_price_inr(text: str) -> float | None:
        """
        Parses Indian price strings like:
          '₹42,50,000', 'Rs.42.5 Lakh', '1.2 Cr', '₹42L'
        Returns float in INR.
        """
        if not text:
            return None
        t = text.replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip().lower()
        try:
            if "crore" in t or " cr" in t or t.endswith("cr"):
                num = float(t.replace("crore", "").replace("cr", "").strip())
                return num * 10_000_000
            elif "lakh" in t or " l" in t or t.endswith("l"):
                num = float(t.replace("lakh", "").replace(" l", "").replace("l", "").strip())
                return num * 100_000
            else:
                return float(t)
        except (ValueError, AttributeError):
            return None

    # ── Interface ─────────────────────────────────────────────────────────────

    @abstractmethod
    def scrape(self) -> List[AuctionListing]:
        """Main method — returns list of AuctionListing objects."""
        ...

    def safe_scrape(self) -> List[AuctionListing]:
        """Wraps scrape() with error handling — never crashes the pipeline."""
        try:
            listings = self.scrape()
            logger.info(f"[{self.source_name}] Fetched {len(listings)} listings")
            return listings
        except Exception as e:
            logger.error(f"[{self.source_name}] Scrape failed: {e}", exc_info=True)
            return []
        finally:
            self.quit_driver()
