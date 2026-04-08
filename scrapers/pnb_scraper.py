"""
PNB Auction Scraper — pnb.bank.in
Scrapes: EAuction-Property-List.aspx (searchable, structured) +
         EAuction.aspx (notices, batch PDFs)

Tech: ASP.NET WebForms — ViewState POST pagination (10 rows/page).
Strategy: POST search form with state=MH (Maharashtra) and GJ (Gujarat),
          parse results, iterate pages via __doPostBack.
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
from selenium.common.exceptions import TimeoutException, NoSuchElementException

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scrapers.base import BaseScraper
from engine.models import AuctionListing
import config.settings as cfg

logger = logging.getLogger(__name__)

PNB_BASE            = "https://pnb.bank.in"
PNB_PROPERTY_URL    = f"{PNB_BASE}/EAuction-Property-List.aspx"
PNB_NOTICES_URL     = f"{PNB_BASE}/EAuction.aspx"

STATE_FILTERS = {
    "MH": ["Maharashtra"],    # Mumbai, Thane, Navi Mumbai
    "GJ": ["Gujarat"],        # Ahmedabad, Vadodara, Baroda
}

CITY_KEYWORDS_PNB = {
    "Mumbai":    ["mumbai", "bandra", "andheri", "borivali", "dadar", "thane",
                  "kurla", "malad", "kandivali", "vasai"],
    "Thane":     ["thane", "navi mumbai", "kalyan", "dombivli"],
    "Ahmedabad": ["ahmedabad", "gandhinagar"],
    "Vadodara":  ["vadodara", "baroda"],
    "Gujarat":   ["surat", "rajkot", "gujarat"],
}


def _parse_date_pnb(text: str) -> Optional[date]:
    text = text.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d-%b-%Y",
                "%d %B %Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except (ValueError, AttributeError):
            continue
    m = re.search(r"(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def _city_from_text(text: str) -> str:
    t = text.lower()
    for city, kws in CITY_KEYWORDS_PNB.items():
        if any(kw in t for kw in kws):
            return city
    return ""


class PNBScraper(BaseScraper):
    source_name = "PNB"

    # ─── Property List (structured search) ────────────────────────────────────

    def _search_property_list(self, driver, state_value: str) -> List[AuctionListing]:
        """Search PNB property list for a given state."""
        listings = []

        driver.get(PNB_PROPERTY_URL)
        time.sleep(3)

        try:
            # Select state
            state_sel = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.ID, "ContentPlaceHolder1_ddl_State")
                )
            )
            Select(state_sel).select_by_value(state_value)
            time.sleep(1)

            # Click Search
            search_btn = driver.find_element(By.ID, "ContentPlaceHolder1_btnsearch")
            driver.execute_script("arguments[0].click();", search_btn)
            time.sleep(3)
        except (TimeoutException, NoSuchElementException) as e:
            logger.warning(f"[PNB] Search form interaction failed for {state_value}: {e}")
            return []

        # Paginate
        page = 1
        while page <= 50:  # safety cap
            soup = BeautifulSoup(driver.page_source, "lxml")
            page_listings = self._parse_property_table(soup)
            listings.extend(page_listings)
            logger.info(f"[PNB] State={state_value} Page {page}: {len(page_listings)} rows")

            # Try next page
            if not self._click_next_page_pnb(driver, soup):
                break
            page += 1

        return listings

    def _parse_property_table(self, soup: BeautifulSoup) -> List[AuctionListing]:
        """Parse the property results table on EAuction-Property-List.aspx."""
        listings = []
        result_div = soup.find("div", id="alltab") or soup.find("div", class_="tabcontent")
        if not result_div:
            result_div = soup  # fallback

        table = result_div.find("table", class_=re.compile(r"inner|table|grid", re.IGNORECASE))
        if not table:
            tables = result_div.find_all("table")
            table = next((t for t in tables if len(t.find_all("tr")) > 2), None)
        if not table:
            return []

        rows = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[2:]

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            texts = [c.get_text(strip=True) for c in cells]

            listing = AuctionListing(source=self.source_name)
            listing.bank_name = "Punjab National Bank"

            # Extract from columns — PNB structure varies, so we scan all cells
            for i, t in enumerate(texts):
                if not t:
                    continue
                # Price pattern
                if re.search(r"[₹\d,]+\s*(L|Lakh|Cr|Crore)", t, re.IGNORECASE):
                    if not listing.reserve_price:
                        listing.reserve_price = self.parse_price_inr(t)
                # Date pattern
                elif re.search(r"\d{1,2}[-/]\d{1,2}[-/]\d{4}", t):
                    if not listing.auction_date:
                        listing.auction_date = _parse_date_pnb(t)
                # City match
                elif _city_from_text(t):
                    listing.city     = _city_from_text(t)
                    listing.location = t
                # Property type
                elif any(pt.lower() in t.lower() for pt in
                         ["Flat", "Shop", "Plot", "Industrial", "Residential",
                          "Commercial", "Office", "House", "Land"]):
                    listing.property_type = t

            # Row click target for detail link
            detail_link = row.find("a", href=re.compile(r"detail|view|prop", re.IGNORECASE))
            if detail_link:
                href = detail_link.get("href", "")
                listing.source_url = href if href.startswith("http") else PNB_BASE + href
            else:
                listing.source_url = PNB_PROPERTY_URL

            # Description from the longest cell text
            if texts:
                listing.title = max(texts, key=len)[:250]

            listing.possession  = "Unknown"
            listing.legal_status = "Unknown"

            # Only keep if city matches target
            if listing.city or _city_from_text(" ".join(texts)):
                if not listing.city:
                    listing.city = _city_from_text(" ".join(texts))
                listings.append(listing)

        return listings

    def _click_next_page_pnb(self, driver, soup: BeautifulSoup) -> bool:
        """Click ASP.NET page postback for next page."""
        try:
            # Look for page navigation links with __doPostBack
            nav_links = driver.find_elements(
                By.XPATH,
                "//a[contains(@href, '__doPostBack') and (text()='>' or text()='Next' or text()='»')]"
            )
            if nav_links:
                driver.execute_script("arguments[0].click();", nav_links[0])
                time.sleep(2.5)
                return True

            # Look for numbered page links (current page + 1)
            pager_table = soup.find("table", class_=re.compile(r"pager|page", re.IGNORECASE))
            if pager_table:
                # Find current active page number
                active = pager_table.find("span")  # current page is a <span> not <a>
                if active:
                    try:
                        curr_page = int(active.get_text(strip=True))
                        next_link = driver.find_element(
                            By.XPATH,
                            f"//table[contains(@class,'pager')]//a[text()='{curr_page + 1}']"
                        )
                        driver.execute_script("arguments[0].click();", next_link)
                        time.sleep(2.5)
                        return True
                    except (ValueError, NoSuchElementException):
                        pass
        except Exception as e:
            logger.debug(f"[PNB] Pagination failed: {e}")
        return False

    # ─── Notices page (batch PDFs) ─────────────────────────────────────────────

    def _scrape_notices(self) -> List[AuctionListing]:
        """Scrape EAuction.aspx notice list — extracts title/date/PDF URL."""
        listings = []
        try:
            soup = self.soup(PNB_NOTICES_URL)
        except Exception as e:
            logger.error(f"[PNB] Notices fetch failed: {e}")
            return []

        table = soup.find("table", class_="inner-page-table")
        if not table:
            return []

        rows = table.find_all("tr")
        for row in rows[3:]:  # Skip header rows
            office_span = row.find("span", id=re.compile(r"Label3"))
            title_link  = row.find("a",    id=re.compile(r"lbtnTenderTitle"))
            end_span    = row.find("span", id=re.compile(r"Label4"))

            if not title_link:
                continue

            title   = title_link.get_text(strip=True)
            office  = office_span.get_text(strip=True) if office_span else ""
            end_txt = end_span.get_text(strip=True) if end_span else ""

            # Only keep notices mentioning our target cities
            combined = f"{title} {office}"
            city = _city_from_text(combined)
            if not city:
                continue

            # Extract end date
            end_date = None
            date_m = re.search(r"End Date[:\s]+(.+)", end_txt)
            if date_m:
                end_date = _parse_date_pnb(date_m.group(1).strip())

            listing = AuctionListing(source=self.source_name)
            listing.title        = title[:250]
            listing.city         = city
            listing.location     = office
            listing.bank_name    = "Punjab National Bank"
            listing.auction_date = end_date
            listing.source_url   = PNB_NOTICES_URL
            listing.notes        = "Notice (PDF batch)"
            listing.possession   = "Unknown"
            listing.legal_status = "Unknown"

            # Try to extract price from title
            price_m = re.search(
                r"(?:rs\.?|₹)\s*([\d,]+(?:\.\d+)?\s*(?:lakh|lac|crore|cr|l)?)",
                title, re.IGNORECASE
            )
            if price_m:
                listing.reserve_price = self.parse_price_inr(price_m.group(1))

            listings.append(listing)

        logger.info(f"[PNB] Notices: {len(listings)} city-matched")
        return listings

    # ─── Main scrape ──────────────────────────────────────────────────────────

    def scrape(self) -> List[AuctionListing]:
        all_listings = []
        seen_ids = set()
        driver = self.get_driver()

        # Structured property search by state
        for state_code, state_names in STATE_FILTERS.items():
            for state_name in state_names:
                logger.info(f"[PNB] Searching property list: {state_name}")
                try:
                    listings = self._search_property_list(driver, state_code)
                    for l in listings:
                        if l.listing_id not in seen_ids:
                            seen_ids.add(l.listing_id)
                            all_listings.append(l)
                except Exception as e:
                    logger.error(f"[PNB] Property list failed for {state_name}: {e}")

        # Notice board (batch PDFs)
        logger.info("[PNB] Scraping notice board")
        for l in self._scrape_notices():
            if l.listing_id not in seen_ids:
                seen_ids.add(l.listing_id)
                all_listings.append(l)

        logger.info(f"[PNB] Total: {len(all_listings)}")
        return all_listings
