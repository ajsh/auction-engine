"""
Data models for auction listings.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
import hashlib


@dataclass
class AuctionListing:
    # ── Source ──────────────────────────────────────────────
    source:          str = ""        # e.g. "IBAPI", "MSTC", "SBI", "PNB"
    source_url:      str = ""        # Direct URL to listing

    # ── Property ─────────────────────────────────────────────
    title:           str = ""
    property_type:   str = ""        # Flat / Shop / Plot / Industrial
    city:            str = ""
    location:        str = ""        # Full address / area
    area_sqft:       Optional[float] = None

    # ── Financials ───────────────────────────────────────────
    reserve_price:   Optional[float] = None   # in INR
    market_price:    Optional[float] = None   # in INR (from external source)
    discount_pct:    Optional[float] = None   # computed

    # ── Auction Details ──────────────────────────────────────
    auction_date:    Optional[date]  = None
    bank_name:       str = ""
    contact_person:  str = ""
    contact_number:  str = ""

    # ── Risk Flags ───────────────────────────────────────────
    possession:      str = ""        # Vacant / Occupied / Unknown
    legal_status:    str = ""        # Clear / Dispute / Unknown
    society_dues:    str = ""        # Yes / No / Unknown

    # ── Scoring (computed) ───────────────────────────────────
    liquidity_score: float = 5.0     # 1–10
    risk_score:      float = 5.0     # 1–10 (higher = less risky)
    location_score:  float = 5.0     # 1–10
    final_score:     float = 0.0
    action:          str = ""        # BUY / WATCH / IGNORE

    # ── Meta ────────────────────────────────────────────────
    date_added:      datetime = field(default_factory=datetime.now)
    notes:           str = ""
    competition:     str = ""        # High / Medium / Low

    @property
    def listing_id(self) -> str:
        """Stable unique ID based on source + URL or title + price."""
        key = f"{self.source}|{self.source_url or self.title}|{self.reserve_price}"
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def compute_discount(self):
        if self.reserve_price and self.market_price and self.market_price > 0:
            self.discount_pct = (self.market_price - self.reserve_price) / self.market_price

    def compute_score(self, weights: dict):
        """
        Weighted scoring:
          discount  × 0.30  (normalized: discount_pct / 0.50, capped at 1)
          liquidity × 0.20  (liquidity_score / 10)
          risk      × 0.25  (risk_score / 10)
          location  × 0.25  (location_score / 10)
        """
        if self.discount_pct is None:
            d_norm = 0
        else:
            d_norm = min(self.discount_pct / 0.50, 1.0) * 10  # 50% discount → 10

        score = (
            d_norm               * weights.get("discount",  0.30) +
            self.liquidity_score * weights.get("liquidity", 0.20) +
            self.risk_score      * weights.get("risk",      0.25) +
            self.location_score  * weights.get("location",  0.25)
        )
        self.final_score = round(score, 2)

        if self.final_score >= 8:
            self.action = "BUY"
        elif self.final_score >= 6:
            self.action = "WATCH"
        else:
            self.action = "IGNORE"

    def to_sheet_row(self) -> list:
        """Returns a flat list matching the Google Sheet column order."""
        return [
            self.date_added.strftime("%Y-%m-%d %H:%M"),
            self.source,
            self.city,
            self.location,
            self.property_type,
            self.area_sqft or "",
            self.reserve_price or "",
            self.market_price or "",
            f"{round(self.discount_pct * 100, 1)}%" if self.discount_pct else "",
            str(self.auction_date) if self.auction_date else "",
            self.bank_name,
            self.contact_person,
            self.contact_number,
            self.possession,
            self.legal_status,
            round(self.liquidity_score, 1),
            round(self.risk_score, 1),
            round(self.final_score, 2),
            self.action,
            self.notes,
            self.source_url,
        ]

    def to_alert_text(self) -> str:
        discount_str = f"{round(self.discount_pct*100)}%" if self.discount_pct else "N/A"
        res_str = f"₹{self.reserve_price/100000:.1f}L" if self.reserve_price else "N/A"
        mkt_str = f"₹{self.market_price/100000:.1f}L" if self.market_price else "N/A"
        return (
            f"📍 {self.location} ({self.city})\n"
            f"🏠 {self.property_type} | {self.area_sqft or '?'} sqft\n"
            f"💰 Auction: {res_str}  |  Market: {mkt_str}\n"
            f"📉 Discount: {discount_str}\n"
            f"⭐ Score: {self.final_score}/10\n"
            f"🏦 {self.bank_name}\n"
            f"📅 Auction: {self.auction_date}\n"
            f"⚠️  Risk: {self.possession} possession | Legal: {self.legal_status}\n"
            f"🎯 Action: {self.action}\n"
            f"🔗 {self.source_url}"
        )
