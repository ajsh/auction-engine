"""
BankEAuctions Scraper — bankeauctions.com
250K+ properties aggregated from Indian banks.

Tech: AJAX-heavy — tbody loaded dynamically.
Strategy: Use Selenium to fully render the page, then paginate through
          Bootstrap pagination controls.

Filters used:
  - City/State dropdown → Mumbai / Ahmedabad / Vadodara
  - Budget range dropdown
"""

import re
import time
import logging
from datetime import datetime, date
from typing import List, Optional
from bs4 import BeautifulSoup

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scrapers.base import BaseScraper
from engine.models import AuctionListing
import config.settings as cfg

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bankeauctions.com"

# Map our city names to BankEAuctions filter values
CITY_FILTER_MAP = {
    "Mumbai":      "Mumbai",
    "Thane":       "Thane",
    "Navi Mumbai": "Navi Mumbai",
    "Ahmedabad":   "Ahmedabad",
    "Vadodara":    "Vadodara",
    "Baroda":      "Vadodara",
}


def _parse_date(text: str) -> Optional[date]:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y",
                "%d.%m.%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    # Try partial — just extract digits
    m = re.search(r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


class BankEAuctionsScraper(BaseScraper):
    source_name = "BankEAuctions"

    def _wait_for_table(self, driver, timeout: int = 15):
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.auction-table tbody tr"))
        )
        time.sleep(2)  # Extra settle time for AJAX

    def _set_filter(self, driver, filter_name: str, value: str) -> bool:
        """Try to set a dropdown/text filter. Returns True if successful."""
        try:
            # Try <select> dropdowns first
            selects = driver.find_elements(By.CSS_SELECTOR, "select")
            for sel in selects:
                placeholder = sel.get_attribute("id") or sel.get_attribute("name") or ""
                options = [o.text.strip() for o in sel.find_elements(By.TAG_NAME, "option")]
                if filter_name.lower() in placeholder.lower():
                    for opt in options:
                        if value.lower() in opt.lower():
                            Select(sel).select_by_visible_text(opt)
                            time.sleep(1.5)
                            return True
                # Also try matching by options content
                if any(value.lower() in opt.lower() for opt in options):
                    for opt in options:
                        if value.lower() in opt.lower():
                            Select(sel).select_by_visible_text(opt)
                            time.sleep(1.5)
                            return True
        except Exception as e:
            logger.debug(f"[BankEAuctions] Filter set failed for {filter_name}={value}: {e}")
        return False

    def _parse_table_page(self, driver) -> List[AuctionListing]:
        """Parse all rows from the current rendered table page."""
        soup = BeautifulSoup(driver.page_source, "lxml")
        table = soup.find("table", class_="auction-table")
        if not table:
            return []

        listings = []
        rows = table.find("tbody").find_all("tr") if table.find("tbody") else []

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 7:
                continue

            listing = AuctionListing(source=self.source_name)

            # Col 2: Auction ID
            auction_id = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            if auction_id:
                detail_link = row.find("a", href=re.compile(r"intpop"))
                if detail_link:
                    href = detail_link.get("href", "")
                    listing.source_url = href if href.startswith("http") else BASE_URL + href
                else:
                    listing.source_url = f"{BASE_URL}/home/intpop/{auction_id}"

            # Col 3: Bank/Org Name
            listing.bank_name = cells[2].get_text(strip=True) if len(cells) > 2 else ""

            # Col 4: Property Description
            prop_desc = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            listing.title = prop_desc[:200]

            # Try to extract property type from description
            for ptype in ["Flat", "Shop", "Plot", "Industrial", "Commercial",
                          "Residential", "Office", "House", "Villa", "Land"]:
                if ptype.lower() in prop_desc.lower():
                    listing.property_type = ptype
                    break

            # Col 5: City/District
            city_text = cells[4].get_text(strip=True) if len(cells) > 4 else ""
            listing.location = city_text
            # Try to match known city
            for city in cfg.TARGET_CITIES:
                if city.lower() in city_text.lower():
                    listing.city = city
                    break
            if not listing.city:
                listing.city = city_text.split(",")[0].strip()

            # Col 6: Auction/Bid Submission Date
            date_text = cells[5].get_text(strip=True) if len(cells) > 5 else ""
            listing.auction_date = _parse_date(date_text)

            # Col 7: Reserve Price
            price_text = cells[6].get_text(strip=True) if len(cells) > 6 else ""
            listing.reserve_price = self.parse_price_inr(price_text)

            # Col 9: Event Type (SARFAESI / DRT / OTHERS)
            if len(cells) > 8:
                listing.notes = cells[8].get_text(strip=True)

            # Default possession unknown for BankEAuctions
            listing.possession = "Unknown"
            listing.legal_status = "Unknown"

            if listing.reserve_price or listing.title:
                listings.append(listing)

        return listings

    def _get_total_pages(self, driver) -> int:
        """Read pagination to get total page count."""
        try:
            soup = BeautifulSoup(driver.page_source, "lxml")
            pagination = soup.find("ul", class_="pagination")
            if not pagination:
                return 1
            page_items = pagination.find_all("li", class_="page-item")
            page_nums = []
            for item in page_items:
                txt = item.get_text(strip=True)
                if txt.isdigit():
                    page_nums.append(int(txt))
            return max(page_nums) if page_nums else 1
        except Exception:
            return 1

    def _go_to_next_page(self, driver) -> bool:
        """Click the 'next page' control. Returns False if no more pages."""
        try:
            next_btn = driver.find_element(
                By.CSS_SELECTOR,
                "ul.pagination li:not(.disabled) a[aria-label='Next'], "
                "ul.pagination li:not(.disabled) a[aria-label='next'], "
                "ul.pagination .page-item:not(.disabled) a.page-link[aria-label*='ext']"
            )
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(2.5)
            return True
        except NoSuchElementException:
            return False
        except Exception as e:
            logger.debug(f"[BankEAuctions] Next page click failed: {e}")
            return False

    def _scrape_city(self, driver, city: str) -> List[AuctionListing]:
        """Scrape all pages for a given city filter."""
        driver.get(BASE_URL)
        time.sleep(3)

        # Apply city filter
        filter_value = CITY_FILTER_MAP.get(city, city)
        self._set_filter(driver, "city", filter_value)
        time.sleep(2)

        # Wait for table to load
        try:
            self._wait_for_table(driver)
        except TimeoutException:
            logger.warning(f"[BankEAuctions] Table did not load for city={city}")
            return []

        all_listings = []
        page = 1
        max_pages = self._get_total_pages(driver)
        logger.info(f"[BankEAuctions] City={city}, Pages={max_pages}")

        while page <= min(max_pages, 30):  # Safety cap at 30 pages
            listings = self._parse_table_page(driver)
            all_listings.extend(listings)
            logger.info(f"[BankEAuctions] Page {page}/{max_pages}: {len(listings)} listings")

            if page >= max_pages:
                break
            if not self._go_to_next_page(driver):
                break
            page += 1

        return all_listings

    def scrape(self) -> List[AuctionListing]:
        driver = self.get_driver()
        all_listings: List[AuctionListing] = []
        seen_ids = set()

        # Scrape unique cities (skip duplicates like Baroda/Vadodara)
        cities_done = set()
        for city in cfg.TARGET_CITIES:
            filter_val = CITY_FILTER_MAP.get(city, city)
            if filter_val in cities_done:
                continue
            cities_done.add(filter_val)

            logger.info(f"[BankEAuctions] Scraping city: {city}")
            try:
                listings = self._scrape_city(driver, city)
                for l in listings:
                    if l.listing_id not in seen_ids:
                        seen_ids.add(l.listing_id)
                        all_listings.append(l)
            except Exception as e:
                logger.error(f"[BankEAuctions] Failed for {city}: {e}")

        logger.info(f"[BankEAuctions] Total: {len(all_listings)} listings")
        return all_listings
