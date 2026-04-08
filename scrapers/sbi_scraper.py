"""
SBI Auction Scraper — sbi.bank.in
Scrapes multiple auction notice sub-pages (Sarfaesi, Bank E-Auctions, Mega E-Auction).

Tech: Liferay DXP, server-side rendered, FooTable.js pagination (client-side).
Strategy: HTTP GET → parse all rows from the static HTML table.
          PDFs are NOT parsed (they require OCR); we extract title + date from the table row.

Limitation: SBI notices are batch PDFs, not individual property records.
            We extract notice-level data (description, date, PDF URL) as leads.
"""

import re
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

SBI_BASE = "https://sbi.bank.in"

# Sub-pages that contain tabular auction data
SBI_NOTICE_PAGES = {
    "Sarfaesi": f"{SBI_BASE}/web/sbi-in-the-news/auction-notices/sarfaesi-and-others",
    "BankEAuction": f"{SBI_BASE}/web/sbi-in-the-news/auction-notices/bank-e-auctions",
    "MegaEAuction": f"{SBI_BASE}/web/sbi-in-the-news/auction-notices/mega-e-auction",
    "ARC_DRT":  f"{SBI_BASE}/web/sbi-in-the-news/auction-notices/arc-drt",
}

# City keywords that map to our target cities
CITY_KEYWORDS = {
    "Mumbai":     ["mumbai", "bandra", "andheri", "borivali", "worli", "thane",
                   "kurla", "dadar", "malad", "kandivali", "vasai", "virar"],
    "Thane":      ["thane", "navi mumbai", "kalyan", "dombivli", "ulhasnagar"],
    "Ahmedabad":  ["ahmedabad", "amdavad", "gandhinagar"],
    "Vadodara":   ["vadodara", "baroda"],
    "Gujarat":    ["gujarat", "surat", "rajkot"],
}


def _parse_date_sbi(text: str) -> Optional[date]:
    text = re.sub(r"\.", "", text.strip())
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d %m %Y", "%d %b %Y",
                "%d%m%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except (ValueError, AttributeError):
            continue
    m = re.search(r"(\d{2})\.?(\d{2})\.?(\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def _city_from_text(text: str) -> str:
    text_lower = text.lower()
    for city, keywords in CITY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return city
    return ""


def _matches_target_city(text: str) -> bool:
    return bool(_city_from_text(text))


class SBIScraper(BaseScraper):
    source_name = "SBI"

    def _scrape_notice_page(self, label: str, url: str) -> List[AuctionListing]:
        """Scrape a single SBI auction sub-page."""
        listings = []
        try:
            soup = self.soup(url)
        except Exception as e:
            logger.error(f"[SBI] Failed to fetch {url}: {e}")
            return []

        # FooTable — all data in static HTML
        # Try both id patterns SBI uses
        table = (
            soup.find("table", id=re.compile(r"sarfesi|auction|foota", re.IGNORECASE)) or
            soup.find("table", class_=re.compile(r"footable|table", re.IGNORECASE))
        )
        if not table:
            # Fallback: any table with >2 rows
            all_tables = soup.find_all("table")
            table = next((t for t in all_tables if len(t.find_all("tr")) > 3), None)

        if not table:
            logger.warning(f"[SBI] No table found on {url}")
            return []

        rows = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            description = cells[0].get_text(strip=True) if cells else ""
            date_text   = cells[1].get_text(strip=True) if len(cells) > 1 else ""

            # PDF link
            pdf_link = ""
            link_tag = row.find("a", href=re.compile(r"\.pdf|/documents/", re.IGNORECASE))
            if link_tag:
                href = link_tag.get("href", "")
                pdf_link = href if href.startswith("http") else SBI_BASE + href

            # City match filter
            if not _matches_target_city(description):
                continue

            listing = AuctionListing(source=self.source_name)
            listing.title        = description[:250]
            listing.city         = _city_from_text(description)
            listing.location     = description[:250]
            listing.bank_name    = "State Bank of India"
            listing.auction_date = _parse_date_sbi(date_text)
            listing.source_url   = pdf_link or url
            listing.notes        = f"Type: {label}"
            listing.possession   = "Unknown"
            listing.legal_status = "Unknown"

            # Try to extract reserve price from description
            price_m = re.search(
                r"(?:reserve|upset|bid)\s*(?:price|amount)[\s:]*"
                r"(?:rs\.?|₹)\s*([\d,]+(?:\.\d+)?(?:\s*(?:lakh|lac|crore|cr|l))?)",
                description, re.IGNORECASE
            )
            if price_m:
                listing.reserve_price = self.parse_price_inr(price_m.group(1))

            # Property type from description
            for ptype in ["Flat", "Shop", "Plot", "Industrial", "Commercial",
                          "Residential", "Office", "House", "Villa", "Land", "Shed"]:
                if ptype.lower() in description.lower():
                    listing.property_type = ptype
                    break

            listings.append(listing)

        logger.info(f"[SBI] {label}: {len(listings)} city-matched listings from {url}")
        return listings

    def scrape(self) -> List[AuctionListing]:
        all_listings = []
        seen_ids = set()

        for label, url in SBI_NOTICE_PAGES.items():
            logger.info(f"[SBI] Fetching: {label}")
            listings = self._scrape_notice_page(label, url)
            for l in listings:
                if l.listing_id not in seen_ids:
                    seen_ids.add(l.listing_id)
                    all_listings.append(l)

        logger.info(f"[SBI] Total city-matched: {len(all_listings)}")
        return all_listings
