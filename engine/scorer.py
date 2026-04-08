"""
Deal Scoring & Filtering Engine
Applies hard filters + computes weighted scores for every listing.
"""
from datetime import date, timedelta
from typing import List
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.models import AuctionListing
import config.settings as cfg

logger = logging.getLogger(__name__)


# ─── Location quality lookup ─────────────────────────────────────────────────
LOCATION_SCORES = {
    # Mumbai premium zones
    "bandra": 9.5, "worli": 9.5, "lower parel": 9.0, "andheri": 8.5,
    "powai": 8.5, "borivali": 7.5, "kandivali": 7.5, "thane": 7.5,
    "navi mumbai": 7.0, "virar": 5.5, "nalasopara": 5.0,
    # Ahmedabad zones
    "sg highway": 9.0, "prahlad nagar": 9.0, "satellite": 8.5,
    "bodakdev": 8.5, "vastrapur": 8.0, "naranpura": 7.5,
    "chandkheda": 6.5, "naroda": 5.5,
    # Vadodara / Baroda
    "alkapuri": 8.5, "fatehgunj": 7.5, "race course": 8.0,
    "gotri": 7.0, "waghodia": 5.5,
}

# Liquidity signals in address
LIQUIDITY_HIGH  = ["station", "metro", "highway", "junction", "main road",
                   "link road", "express", "highway", "nh"]
LIQUIDITY_LOW   = ["isolated", "remote", "village", "gaon", "pada", "wadi"]

# Risk signals
RISK_RED_FLAGS  = ["occupied", "encroachment", "dispute", "litigation",
                   "court", "stay order", "demolition", "illegal"]


def _infer_liquidity(listing: AuctionListing) -> float:
    text = (listing.location + " " + listing.title).lower()
    for kw in LIQUIDITY_HIGH:
        if kw in text:
            return 8.5
    for kw in LIQUIDITY_LOW:
        if kw in text:
            return 3.0
    return 5.5   # default medium


def _infer_risk(listing: AuctionListing) -> float:
    """Higher = safer."""
    text = (
        listing.possession + " " +
        listing.legal_status + " " +
        listing.notes
    ).lower()

    score = 7.0  # start optimistic
    for flag in RISK_RED_FLAGS:
        if flag in text:
            score -= 2.0

    if listing.possession.lower() == "vacant":
        score += 1.5
    elif listing.possession.lower() == "occupied":
        score -= 2.0

    if listing.legal_status.lower() == "clear":
        score += 1.0
    elif "dispute" in listing.legal_status.lower():
        score -= 2.5

    return max(1.0, min(10.0, score))


def _infer_location_score(listing: AuctionListing) -> float:
    text = (listing.location + " " + listing.city).lower()
    for area, score in LOCATION_SCORES.items():
        if area in text:
            return score
    # Generic city default
    city = listing.city.lower()
    defaults = {"mumbai": 7.0, "thane": 6.5, "navi mumbai": 6.5,
                "ahmedabad": 6.5, "vadodara": 6.0, "baroda": 6.0}
    return defaults.get(city, 5.0)


def _infer_competition(listing: AuctionListing) -> str:
    loc = listing.location.lower()
    if any(kw in loc for kw in ["bandra", "juhu", "worli", "lower parel",
                                  "sg highway", "prahlad nagar"]):
        return "High"
    if listing.city.lower() in ["mumbai", "thane"]:
        return "Medium"
    return "Low"


def enrich_and_score(listings: List[AuctionListing]) -> List[AuctionListing]:
    """Infer scores, compute final score, set action."""
    weights = {
        "discount":  cfg.WEIGHT_DISCOUNT,
        "liquidity": cfg.WEIGHT_LIQUIDITY,
        "risk":      cfg.WEIGHT_RISK,
        "location":  cfg.WEIGHT_LOCATION,
    }
    for l in listings:
        l.compute_discount()
        l.liquidity_score = _infer_liquidity(l)
        l.risk_score      = _infer_risk(l)
        l.location_score  = _infer_location_score(l)
        l.competition     = _infer_competition(l)
        # For listings without price data (SBI/PNB batch notices),
        # use location+risk+liquidity only — skip discount weight
        if l.discount_pct is None and l.reserve_price is None:
            no_price_weights = {
                "discount":  0.0,
                "liquidity": cfg.WEIGHT_LIQUIDITY + 0.10,
                "risk":      cfg.WEIGHT_RISK + 0.10,
                "location":  cfg.WEIGHT_LOCATION + 0.10,
            }
            l.compute_score(no_price_weights)
        else:
            l.compute_score(weights)
    return listings


def apply_filters(listings: List[AuctionListing]) -> List[AuctionListing]:
    """Hard filters based on settings.py"""
    today     = date.today()
    cutoff    = today + timedelta(days=cfg.AUCTION_DAYS_AHEAD)
    price_min = cfg.BUDGET_MIN_LAC * 100_000
    price_max = cfg.BUDGET_MAX_CR  * 10_000_000

    filtered = []
    for l in listings:
        # City filter
        city_match = any(
            c.lower() in (l.city + " " + l.location).lower()
            for c in cfg.TARGET_CITIES
        )
        if not city_match:
            continue

        # Price filter (only apply if price is known)
        if l.reserve_price:
            if l.reserve_price < price_min or l.reserve_price > price_max:
                continue

        # Auction date filter (only apply if date is known)
        if l.auction_date:
            if l.auction_date < today or l.auction_date > cutoff:
                continue

        # Discount filter (only apply if both prices are known)
        if l.discount_pct is not None and l.discount_pct < cfg.MIN_DISCOUNT_PCT:
            continue

        filtered.append(l)

    logger.info(f"Filtered {len(listings)} → {len(filtered)} listings")
    return filtered


def get_top_deals(listings: List[AuctionListing], n: int = 5) -> List[AuctionListing]:
    """Return top N listings by final_score."""
    scored = [l for l in listings if l.action != "IGNORE"]
    return sorted(scored, key=lambda x: x.final_score, reverse=True)[:n]
