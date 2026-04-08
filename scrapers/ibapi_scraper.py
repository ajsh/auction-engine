"""
IBAPI Scraper — ibapi.in
India's central bank auction portal (aggregates SBI, HDFC, PNB, BOI, etc.)

Tech: ASP.NET WebForms — server-side rendered + ViewState pagination
Strategy:
  1. POST the search form with city/state filters
  2. Parse the resulting HTML table
  3. For each listing, POST bind_modal_detail to get full details
  4. Handle ViewState-based pagination to get all pages

⚠️  IBAPI blocks datacenter IPs — use a residential/Indian proxy.
    Set IBAPI_PROXY in environment or config if needed.
"""

import json
import re
import os
import logging
from datetime import datetime, date
from typing import List, Optional
from bs4 import BeautifulSoup
import requests

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scrapers.base import BaseScraper, HEADERS
from engine.models import AuctionListing
import config.settings as cfg

logger = logging.getLogger(__name__)

BASE_URL   = "https://ibapi.in"
SEARCH_URL = f"{BASE_URL}/sale_info_home.aspx"
DETAIL_API = f"{BASE_URL}/Sale_Info_Home.aspx/bind_modal_detail"

# Map our city names → IBAPI state codes (ISO)
STATE_MAP = {
    "Mumbai": "MH", "Thane": "MH", "Navi Mumbai": "MH",
    "Ahmedabad": "GJ", "Vadodara": "GJ", "Baroda": "GJ",
}

# IBAPI property type codes
PROP_TYPE_MAP = {
    "Flat":        "RESIDENTIAL",
    "Shop":        "COMMERCIAL",
    "Plot":        "AGRICULTURAL",
    "Industrial":  "INDUSTRIAL",
    "Commercial":  "COMMERCIAL",
    "Residential": "RESIDENTIAL",
}


def _parse_date(text: str) -> Optional[date]:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _extract_viewstate(soup: BeautifulSoup) -> dict:
    """Extract ASP.NET hidden fields needed for form postbacks."""
    fields = {}
    for name in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                 "__EVENTTARGET", "__EVENTARGUMENT"]:
        tag = soup.find("input", {"name": name})
        if tag:
            fields[name] = tag.get("value", "")
    return fields


class IBAPIScraper(BaseScraper):
    source_name = "IBAPI"

    def __init__(self, proxy: str = None):
        super().__init__()
        self.proxy = proxy or os.environ.get("IBAPI_PROXY")
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}
        self.session.headers.update({
            "Referer": SEARCH_URL,
            "Origin":  BASE_URL,
        })

    # ── Page fetch helpers ────────────────────────────────────────────────────

    def _get_search_page(self) -> BeautifulSoup:
        resp = self.get(SEARCH_URL)
        return BeautifulSoup(resp.text, "lxml")

    def _post_search(self, soup: BeautifulSoup, city: str, state_code: str,
                     page_target: str = None) -> BeautifulSoup:
        """POST the search form filtered by city + state."""
        vs = _extract_viewstate(soup)

        data = {
            **vs,
            "__EVENTTARGET":   page_target or "",
            "__EVENTARGUMENT": "",
            # Search filters
            "ctl00$ContentPlaceHolder1$ddl_state":     state_code,
            "ctl00$ContentPlaceHolder1$txt_city":       city,
            "ctl00$ContentPlaceHolder1$ddl_prop_type":  "ALL",
            "ctl00$ContentPlaceHolder1$ddl_bank":       "ALL",
            "ctl00$ContentPlaceHolder1$ddl_bidding_month": "ALL",
            "ctl00$ContentPlaceHolder1$ddl_notify":     "ALL",
            "ctl00$ContentPlaceHolder1$btn_search":     "Search",
        }

        resp = self.session.post(
            SEARCH_URL,
            data=data,
            timeout=cfg.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    def _get_all_pages(self, soup: BeautifulSoup, city: str,
                       state_code: str) -> List[BeautifulSoup]:
        """Return soup objects for all result pages."""
        pages = [soup]
        # Find pagination links — ASP.NET uses table with page numbers
        while True:
            pager = soup.find("tr", class_=re.compile(r"pager|Pager|GridPager"))
            if not pager:
                break
            # Look for a ">" or "Next" link that hasn't been visited
            next_link = pager.find("a", string=re.compile(r"^>$|Next|›"))
            if not next_link:
                break
            # __EVENTTARGET for GridView paging: ctl00$ContentPlaceHolder1$GridView1$ctl00$...
            event_target = next_link.get("href", "")
            # Extract __doPostBack target
            m = re.search(r"__doPostBack\('([^']+)'", event_target)
            if not m:
                break
            postback_target = m.group(1)
            soup = self._post_search(soup, city, state_code, page_target=postback_target)
            pages.append(soup)
            if len(pages) > 20:  # safety cap
                break
        return pages

    # ── Detail fetch ──────────────────────────────────────────────────────────

    def _fetch_detail(self, prop_id: str) -> dict:
        """Call the modal detail WebMethod."""
        try:
            resp = self.session.post(
                DETAIL_API,
                json={"prop_id": prop_id},
                headers={**HEADERS, "Content-Type": "application/json; charset=UTF-8",
                         "X-Requested-With": "XMLHttpRequest"},
                timeout=cfg.REQUEST_TIMEOUT,
            )
            data = resp.json()
            # Response is { "d": "<html>..." } — parse inner HTML
            inner_html = data.get("d", "")
            return self._parse_detail_html(inner_html)
        except Exception as e:
            logger.warning(f"[IBAPI] Detail fetch failed for {prop_id}: {e}")
            return {}

    def _parse_detail_html(self, html: str) -> dict:
        """Parse the modal detail HTML into a dict."""
        soup = BeautifulSoup(html, "lxml")
        detail = {}

        def get_field(label: str) -> str:
            tag = soup.find(string=re.compile(label, re.IGNORECASE))
            if tag:
                parent = tag.find_parent()
                if parent:
                    nxt = parent.find_next_sibling()
                    if nxt:
                        return nxt.get_text(strip=True)
            return ""

        detail["city"]           = get_field(r"City")
        detail["address"]        = get_field(r"Address")
        detail["area"]           = get_field(r"Floor Area|Area")
        detail["reserve_price"]  = get_field(r"Reserve Price")
        detail["auction_open"]   = get_field(r"Auction Open Date")
        detail["auction_close"]  = get_field(r"Auction Close Date")
        detail["bank"]           = get_field(r"Bank")
        detail["ao_name"]        = get_field(r"Authorised Officer")
        detail["ao_contact"]     = get_field(r"Contact|Phone|Mobile")
        detail["possession"]     = get_field(r"Possession")
        detail["bidding_url"]    = get_field(r"Bidding URL|For Further Details")
        detail["summary"]        = get_field(r"Description|Summary")
        detail["property_type"]  = get_field(r"Property Type|Sub Type")

        return detail

    # ── Row parsing ───────────────────────────────────────────────────────────

    def _parse_listing_row(self, row) -> Optional[AuctionListing]:
        """Parse a <tr> from the search results table."""
        cells = row.find_all("td")
        if len(cells) < 5:
            return None

        texts = [c.get_text(strip=True) for c in cells]

        # Extract property ID from the detail link or first cell
        prop_id = None
        link = row.find("a", href=re.compile(r"prop_id|bind_modal", re.IGNORECASE))
        if link:
            href = link.get("href", "")
            m = re.search(r"prop_id[=:]([A-Z0-9]+)", href, re.IGNORECASE)
            if m:
                prop_id = m.group(1)
            # Also try onclick
        onclick = row.get("onclick", "") + " ".join(
            c.get("onclick", "") for c in cells
        )
        if not prop_id:
            m = re.search(r"'([A-Z]{4}\d{10,})'", onclick)
            if m:
                prop_id = m.group(1)

        # Basic fields from the table columns
        # IBAPI table columns (typical): S.No | Property ID | Bank | City | Type | Reserve Price | Auction Date | View
        listing = AuctionListing(source=self.source_name)

        if prop_id:
            listing.source_url = f"{BASE_URL}/sale_info_home.aspx?prop_id={prop_id}"

        # Try to map columns by content
        for i, t in enumerate(texts):
            if re.match(r"[A-Z]{4}\d{10,}", t):
                # Looks like a property ID
                listing.source_url = f"{BASE_URL}/sale_info_home.aspx?prop_id={t}"
                prop_id = t
            elif re.search(r"[₹\d,]+\s*(L|Lakh|Cr|Crore)", t, re.IGNORECASE):
                listing.reserve_price = self.parse_price_inr(t)
            elif re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", t):
                listing.auction_date = _parse_date(t)
            elif any(c in t for c in cfg.TARGET_CITIES):
                listing.city = t

        # Enrich with detail API call
        if prop_id:
            detail = self._fetch_detail(prop_id)
            if detail:
                listing.city           = detail.get("city") or listing.city
                listing.location       = detail.get("address", "")
                listing.bank_name      = detail.get("bank", "")
                listing.contact_person = detail.get("ao_name", "")
                listing.contact_number = detail.get("ao_contact", "")
                listing.possession     = detail.get("possession", "Unknown")
                listing.title          = detail.get("property_type", "")
                listing.property_type  = detail.get("property_type", "")
                if detail.get("reserve_price"):
                    listing.reserve_price = self.parse_price_inr(detail["reserve_price"])
                if detail.get("auction_close"):
                    listing.auction_date = _parse_date(detail["auction_close"])
                if detail.get("area"):
                    try:
                        listing.area_sqft = float(
                            re.sub(r"[^\d.]", "", detail["area"]) or 0
                        ) or None
                    except ValueError:
                        pass
                if detail.get("bidding_url"):
                    listing.source_url = detail["bidding_url"]

        return listing if listing.reserve_price or listing.location else None

    # ── Main scrape ───────────────────────────────────────────────────────────

    def scrape(self) -> List[AuctionListing]:
        listings: List[AuctionListing] = []
        seen_ids = set()

        # Deduplicate cities by state
        state_city_pairs = {}
        for city in cfg.TARGET_CITIES:
            state = STATE_MAP.get(city)
            if state:
                state_city_pairs.setdefault(state, []).append(city)

        base_soup = self._get_search_page()

        for state_code, cities in state_city_pairs.items():
            for city in cities:
                logger.info(f"[IBAPI] Searching: {city}, {state_code}")
                try:
                    result_soup = self._post_search(base_soup, city, state_code)
                    pages = self._get_all_pages(result_soup, city, state_code)

                    for page_soup in pages:
                        table = page_soup.find(
                            "table",
                            id=re.compile(r"GridView|gvSearch|tbl", re.IGNORECASE)
                        )
                        if not table:
                            # Try any data table
                            tables = page_soup.find_all("table")
                            table = next(
                                (t for t in tables if len(t.find_all("tr")) > 2), None
                            )
                        if not table:
                            continue

                        rows = table.find_all("tr")[1:]  # skip header
                        for row in rows:
                            listing = self._parse_listing_row(row)
                            if listing and listing.listing_id not in seen_ids:
                                seen_ids.add(listing.listing_id)
                                listings.append(listing)

                except Exception as e:
                    logger.error(f"[IBAPI] Failed for {city}/{state_code}: {e}")
                    continue

        logger.info(f"[IBAPI] Total listings scraped: {len(listings)}")
        return listings
