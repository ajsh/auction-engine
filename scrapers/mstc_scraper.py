"""
MSTC Scraper — mstcecommerce.com/auctionhome/ibapi/
⚠️  NOTE: MSTC IBAPI is currently suspended (NPA property auctions ceased).
    This scraper is kept for when service resumes.
    Falls back gracefully with zero listings when suspended.

Tech: JSP, Bootstrap table, no pagination, all data in one scrollable table.
Strategy: JS render via Selenium → parse tbody rows.
"""

import re
import time
import logging
from datetime import datetime, date
from typing import List, Optional
from bs4 import BeautifulSoup

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scrapers.base import BaseScraper
from engine.models import AuctionListing
import config.settings as cfg

logger = logging.getLogger(__name__)

MSTC_URL = "https://www.mstcecommerce.com/auctionhome/ibapi/upcoming_auctions.jsp"

CITY_KEYWORDS = {
    "Mumbai":    ["mumbai", "thane", "navi mumbai", "bandra", "andheri", "borivali"],
    "Ahmedabad": ["ahmedabad", "gandhinagar"],
    "Vadodara":  ["vadodara", "baroda"],
    "Gujarat":   ["gujarat", "surat", "rajkot"],
}


def _parse_date(text: str) -> Optional[date]:
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                "%d-%m-%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _city_from_text(text: str) -> str:
    t = text.lower()
    for city, kws in CITY_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return city
    return ""


class MSTCScraper(BaseScraper):
    source_name = "MSTC"

    def scrape(self) -> List[AuctionListing]:
        driver = self.get_driver()
        try:
            driver.get(MSTC_URL)
            time.sleep(4)  # JSP + Bootstrap rendering
        except Exception as e:
            logger.error(f"[MSTC] Page load failed: {e}")
            return []

        soup = BeautifulSoup(driver.page_source, "lxml")

        # Detect suspended notice
        body_text = soup.get_text().lower()
        if "suspended" in body_text or "no properties found" in body_text:
            logger.warning("[MSTC] Service appears suspended — no listings available")
            return []

        # Find the main table
        table = soup.find("table", class_=re.compile(r"table-bordered", re.IGNORECASE))
        if not table:
            logger.warning("[MSTC] No table found")
            return []

        tbody = table.find("tbody")
        if not tbody:
            logger.warning("[MSTC] No tbody found")
            return []

        rows = tbody.find_all("tr")
        listings = []
        seen_ids = set()

        # Table columns: S.No | Property ID | Bank Name | Start Price | Pre-bid | Bid Start | Bid End
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            prop_id    = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            bank_name  = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            price_text = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            bid_start  = cells[5].get_text(strip=True) if len(cells) > 5 else ""
            bid_end    = cells[6].get_text(strip=True) if len(cells) > 6 else ""

            if not prop_id or prop_id in seen_ids:
                continue
            seen_ids.add(prop_id)

            listing = AuctionListing(source=self.source_name)
            listing.title         = prop_id
            listing.bank_name     = bank_name
            listing.reserve_price = self.parse_price_inr(price_text)
            listing.auction_date  = _parse_date(bid_end) or _parse_date(bid_start)
            listing.source_url    = MSTC_URL
            listing.possession    = "Unknown"
            listing.legal_status  = "Unknown"

            # MSTC doesn't have location in the table — property ID starts with IFSC code
            # which encodes the bank branch → we can infer state from IFSC prefix
            # IFSC format: 4 letters (bank) + 0 + 6 chars (branch)
            # We skip city filtering here since location is unavailable at list level
            # and keep all listings (filtered later by scorer if no city match)
            listing.city = ""  # Will remain empty; scorer will handle

            # Try IBAPI detail lookup using prop_id (same endpoint as IBAPI scraper)
            # prop_id format matches IBAPI's prop_id
            try:
                detail_url = (
                    f"https://ibapi.in/Sale_Info_Home.aspx/bind_modal_detail"
                )
                import requests as _req
                resp = self.session.post(
                    detail_url,
                    json={"prop_id": prop_id},
                    headers={
                        "Content-Type": "application/json; charset=UTF-8",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://ibapi.in/sale_info_home.aspx",
                    },
                    timeout=10,
                )
                data = resp.json()
                inner = data.get("d", "")
                if inner:
                    from scrapers.ibapi_scraper import IBAPIScraper
                    detail = IBAPIScraper()._parse_detail_html(inner)
                    if detail:
                        listing.city           = detail.get("city", "")
                        listing.location       = detail.get("address", "")
                        listing.contact_person = detail.get("ao_name", "")
                        listing.contact_number = detail.get("ao_contact", "")
                        listing.possession     = detail.get("possession", "Unknown")
                        if detail.get("property_type"):
                            listing.property_type = detail["property_type"]
                        if detail.get("area"):
                            try:
                                listing.area_sqft = float(
                                    re.sub(r"[^\d.]", "", detail["area"]) or 0
                                ) or None
                            except ValueError:
                                pass
                        if detail.get("auction_close"):
                            from scrapers.ibapi_scraper import _parse_date as _pd
                            listing.auction_date = _pd(detail["auction_close"]) or listing.auction_date
            except Exception as e:
                logger.debug(f"[MSTC] IBAPI detail lookup failed for {prop_id}: {e}")

            listings.append(listing)

        # Filter to target cities only if we have city data
        city_filtered = [l for l in listings if any(
            c.lower() in (l.city + l.location).lower()
            for c in cfg.TARGET_CITIES
        )]

        # If none matched, return all (city will be blank, scorer will handle)
        result = city_filtered if city_filtered else listings
        logger.info(f"[MSTC] {len(result)} listings (city-filtered: {len(city_filtered)})")
        return result
