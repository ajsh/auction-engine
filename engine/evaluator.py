"""
Deal Evaluation & Due Diligence Engine
=======================================
Runs after scoring. For every BUY/WATCH deal, this module:

  1. P&L Calculator       — computes full cost stack + profit + margin
  2. Legal Checker        — scores 5 legal flags from listing data
  3. Possession Checker   — classifies possession risk (BEST/OK/RISKY/AVOID)
  4. Decision Gate        — final BID / INVESTIGATE / PASS verdict
  5. Call Script          — bank officer questions tailored to the deal

Based on the framework from the screenshots:
  - Legal check (non-negotiable): nationalized bank, title chain 15–20yr, no court stay, society ready
  - Possession check (most critical): Vacant=BEST, Owner cooperative=OK, Tenant/locked=RISKY, Family dispute=AVOID
  - Final decision rule: bid only if ALL 4 gates are YES (legal clear + possession clear + 25%+ margin + liquidity strong)
  - P&L: auction + stamp duty + legal + renovation + holding cost → sell price → profit
"""

from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from engine.models import AuctionListing

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

STAMP_DUTY_RATE   = 0.06    # 6% of auction price (Mumbai/Gujarat standard)
LEGAL_COST_FIXED  = 100_000 # ₹1L flat for title search + lawyer
HOLDING_COST_RATE = 0.01    # 1% of auction price per month (taxes, society, EMI)
HOLDING_MONTHS    = 6       # Assumed months to resell

# Renovation cost estimates per sqft by property type
RENOVATION_PER_SQFT = {
    "flat":        600,   # ₹600/sqft basic renovation
    "shop":        400,
    "office":      500,
    "house":       700,
    "villa":       800,
    "plot":          0,   # No renovation needed
    "land":          0,
    "industrial":  300,
    "default":     500,
}

# Nationalized / reputed banks (legal check A)
SAFE_BANKS = [
    "state bank", "sbi", "punjab national", "pnb", "bank of india", "boi",
    "bank of baroda", "bob", "canara bank", "union bank", "central bank",
    "indian bank", "uco bank", "bank of maharashtra", "indian overseas",
    "hdfc", "icici", "axis", "kotak", "yes bank", "idfc", "federal bank",
    "south indian bank", "karnataka bank", "dcb bank",
]

# Risky bank/nbfc signals
RISKY_LENDERS = [
    "cooperative", "co-op", "urban bank", "sahakari", "patsanstha",
    "credit society", "chit fund",
]

# Legal red flags in text
LEGAL_RED_FLAGS = [
    "court stay", "stay order", "writ petition", "high court", "supreme court",
    "litigation", "disputed", "encroachment", "illegal", "unauthorized",
    "demolition", "notice", "injunction", "attachment",
]

# Possession red flags
POSSESSION_RED_FLAGS = [
    "occupied", "tenant", "locked", "family dispute", "dispute",
    "encroachment", "illegal occupant", "not vacant", "possession not given",
]

# Liquidity signals
HIGH_LIQUIDITY_KEYWORDS = [
    "station", "metro", "highway", "main road", "link road", "junction",
    "nh", "express", "bandra", "andheri", "borivali", "lower parel",
    "worli", "sg highway", "prahlad nagar", "satellite", "bodakdev",
    "vastrapur", "alkapuri", "race course",
]
LOW_LIQUIDITY_KEYWORDS = [
    "village", "gaon", "wadi", "pada", "remote", "isolated",
    "outskirts", "highway junction", "no development",
]


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass
class DealEvaluation:
    listing_id:       str = ""

    # ── P&L ──────────────────────────────────────────────────────────────────
    auction_price:    Optional[float] = None
    stamp_duty:       float = 0.0
    legal_cost:       float = LEGAL_COST_FIXED
    renovation_cost:  float = 0.0
    holding_cost:     float = 0.0
    total_cost:       Optional[float] = None
    estimated_sell:   Optional[float] = None
    gross_profit:     Optional[float] = None
    net_margin_pct:   Optional[float] = None
    margin_gate:      str = "UNKNOWN"   # PASS / FAIL / UNKNOWN

    # ── Legal Check ───────────────────────────────────────────────────────────
    bank_type:        str = "UNKNOWN"   # NATIONALIZED / NBFC / COOPERATIVE / UNKNOWN
    bank_gate:        str = "UNKNOWN"   # PASS / FAIL / UNKNOWN
    title_chain_gate: str = "UNKNOWN"   # PASS (inferred) / FLAG
    court_stay_gate:  str = "UNKNOWN"   # PASS / FAIL
    society_gate:     str = "UNKNOWN"   # PASS / UNKNOWN
    legal_flags:      List[str] = field(default_factory=list)
    legal_gate:       str = "UNKNOWN"   # PASS / FAIL / UNKNOWN

    # ── Possession Check ─────────────────────────────────────────────────────
    possession_class: str = "UNKNOWN"   # BEST / OK / RISKY / AVOID
    possession_gate:  str = "UNKNOWN"   # PASS / FAIL / UNKNOWN

    # ── Liquidity Check ───────────────────────────────────────────────────────
    liquidity_class:  str = "UNKNOWN"   # HIGH / MEDIUM / LOW
    liquidity_gate:   str = "UNKNOWN"   # PASS / FAIL / UNKNOWN

    # ── Final Verdict ─────────────────────────────────────────────────────────
    verdict:          str = "INVESTIGATE"  # BID / INVESTIGATE / PASS
    verdict_reason:   str = ""

    # ── Call Script ──────────────────────────────────────────────────────────
    call_script:      str = ""

    def to_sheet_row(self) -> list:
        """Extra columns for the DEAL EVALUATIONS sheet tab."""
        def fmt_inr(v):
            if v is None: return ""
            if v >= 10_000_000: return f"₹{v/10_000_000:.2f}Cr"
            return f"₹{v/100_000:.1f}L"

        return [
            self.listing_id,
            fmt_inr(self.auction_price),
            fmt_inr(self.stamp_duty),
            fmt_inr(self.legal_cost),
            fmt_inr(self.renovation_cost),
            fmt_inr(self.holding_cost),
            fmt_inr(self.total_cost),
            fmt_inr(self.estimated_sell),
            fmt_inr(self.gross_profit),
            f"{self.net_margin_pct:.1f}%" if self.net_margin_pct is not None else "",
            self.margin_gate,
            self.bank_type,
            self.bank_gate,
            self.court_stay_gate,
            self.legal_gate,
            "; ".join(self.legal_flags) if self.legal_flags else "None",
            self.possession_class,
            self.possession_gate,
            self.liquidity_class,
            self.liquidity_gate,
            self.verdict,
            self.verdict_reason,
        ]

    def summary_text(self) -> str:
        """Short one-line summary for email / sheet notes."""
        margin_str = f"{self.net_margin_pct:.0f}%" if self.net_margin_pct else "N/A"
        return (
            f"[{self.verdict}] "
            f"Margin:{margin_str} | "
            f"Legal:{self.legal_gate} | "
            f"Possession:{self.possession_class} | "
            f"Liquidity:{self.liquidity_class}"
        )


EVAL_SHEET_HEADERS = [
    "Listing ID", "Auction Price", "Stamp Duty (6%)", "Legal Cost",
    "Renovation Cost", "Holding Cost (6mo)", "Total Cost", "Est. Sell Price",
    "Gross Profit", "Net Margin %", "Margin Gate",
    "Bank Type", "Bank Gate", "Court Stay Gate", "Legal Gate", "Legal Flags",
    "Possession Class", "Possession Gate",
    "Liquidity Class", "Liquidity Gate",
    "VERDICT", "Reason",
]


# ─── P&L Calculator ──────────────────────────────────────────────────────────

def _calc_pnl(listing: AuctionListing, eval: DealEvaluation):
    """Compute full cost stack and profit."""
    price = listing.reserve_price
    if not price:
        return

    eval.auction_price   = price
    eval.stamp_duty      = price * STAMP_DUTY_RATE
    eval.legal_cost      = LEGAL_COST_FIXED
    eval.holding_cost    = price * HOLDING_COST_RATE * HOLDING_MONTHS

    # Renovation estimate
    ptype = (listing.property_type or "default").lower()
    reno_key = next((k for k in RENOVATION_PER_SQFT if k in ptype), "default")
    sqft = listing.area_sqft or 500  # default 500 sqft if unknown
    eval.renovation_cost = sqft * RENOVATION_PER_SQFT[reno_key]

    eval.total_cost = (
        price +
        eval.stamp_duty +
        eval.legal_cost +
        eval.renovation_cost +
        eval.holding_cost
    )

    # Estimated sell price: use market price if known, else infer from total cost + 25% target margin
    if listing.market_price:
        eval.estimated_sell = listing.market_price
    else:
        # Conservative: 15% above total cost (reflects real resale friction)
        eval.estimated_sell = eval.total_cost * 1.15

    eval.gross_profit  = eval.estimated_sell - eval.total_cost
    eval.net_margin_pct = (eval.gross_profit / eval.total_cost * 100) if eval.total_cost else None

    if eval.net_margin_pct is None:
        eval.margin_gate = "UNKNOWN"
    elif eval.net_margin_pct >= 25:
        eval.margin_gate = "PASS"
    else:
        eval.margin_gate = "FAIL"


# ─── Legal Checker ────────────────────────────────────────────────────────────

def _check_legal(listing: AuctionListing, eval: DealEvaluation):
    """Score legal flags from all available text."""
    text = " ".join([
        listing.bank_name,
        listing.title,
        listing.location,
        listing.legal_status,
        listing.notes,
    ]).lower()

    # A. Bank type
    bank_lower = listing.bank_name.lower()
    if any(b in bank_lower for b in SAFE_BANKS):
        eval.bank_type = "NATIONALIZED/REPUTED"
        eval.bank_gate = "PASS"
    elif any(b in bank_lower for b in RISKY_LENDERS):
        eval.bank_type = "COOPERATIVE/RISKY"
        eval.bank_gate = "FAIL"
        eval.legal_flags.append(f"Risky lender: {listing.bank_name}")
    else:
        eval.bank_type = "UNKNOWN"
        eval.bank_gate = "UNKNOWN"

    # B. Court stay / litigation flags
    found_flags = [f for f in LEGAL_RED_FLAGS if f in text]
    if found_flags:
        eval.court_stay_gate = "FAIL"
        eval.legal_flags.extend([f"Legal flag: {f}" for f in found_flags])
    else:
        eval.court_stay_gate = "PASS"

    # C. Legal status field
    ls = listing.legal_status.lower()
    if "clear" in ls:
        eval.title_chain_gate = "PASS"
        eval.society_gate     = "PASS"
    elif "dispute" in ls or "unknown" in ls or not ls:
        eval.title_chain_gate = "UNKNOWN"
        eval.society_gate     = "UNKNOWN"
    else:
        eval.title_chain_gate = "FLAG"
        eval.legal_flags.append(f"Legal status: {listing.legal_status}")

    # Final legal gate: PASS only if bank OK + no court flags
    if eval.bank_gate == "PASS" and eval.court_stay_gate == "PASS":
        eval.legal_gate = "PASS"
    elif eval.bank_gate == "FAIL" or eval.court_stay_gate == "FAIL":
        eval.legal_gate = "FAIL"
    else:
        eval.legal_gate = "UNKNOWN"


# ─── Possession Checker ───────────────────────────────────────────────────────

def _check_possession(listing: AuctionListing, eval: DealEvaluation):
    """Classify possession status."""
    text = " ".join([
        listing.possession,
        listing.title,
        listing.notes,
        listing.location,
    ]).lower()

    if "vacant" in text:
        eval.possession_class = "BEST"
        eval.possession_gate  = "PASS"
    elif "owner" in text and ("cooperative" in text or "willing" in text):
        eval.possession_class = "OK"
        eval.possession_gate  = "PASS"
    elif any(f in text for f in ["family dispute", "dispute", "encroachment", "illegal"]):
        eval.possession_class = "AVOID"
        eval.possession_gate  = "FAIL"
    elif any(f in text for f in POSSESSION_RED_FLAGS):
        eval.possession_class = "RISKY"
        eval.possession_gate  = "FAIL"
    else:
        # Unknown — default to caution
        eval.possession_class = "UNKNOWN"
        eval.possession_gate  = "UNKNOWN"


# ─── Liquidity Checker ────────────────────────────────────────────────────────

def _check_liquidity(listing: AuctionListing, eval: DealEvaluation):
    """Assess how easy the property is to resell."""
    text = (listing.location + " " + listing.city + " " + listing.title).lower()

    if any(k in text for k in HIGH_LIQUIDITY_KEYWORDS):
        eval.liquidity_class = "HIGH"
        eval.liquidity_gate  = "PASS"
    elif any(k in text for k in LOW_LIQUIDITY_KEYWORDS):
        eval.liquidity_class = "LOW"
        eval.liquidity_gate  = "FAIL"
    else:
        # Use liquidity_score from scorer as fallback
        if listing.liquidity_score >= 7.5:
            eval.liquidity_class = "HIGH"
            eval.liquidity_gate  = "PASS"
        elif listing.liquidity_score >= 5.0:
            eval.liquidity_class = "MEDIUM"
            eval.liquidity_gate  = "PASS"
        else:
            eval.liquidity_class = "LOW"
            eval.liquidity_gate  = "FAIL"


# ─── Final Decision Gate ──────────────────────────────────────────────────────

def _make_verdict(eval: DealEvaluation):
    """
    BID   = all 4 gates PASS
    PASS  = any gate hard FAIL
    INVESTIGATE = any gate UNKNOWN (need more info before deciding)
    """
    gates = {
        "Legal":      eval.legal_gate,
        "Possession": eval.possession_gate,
        "Margin":     eval.margin_gate,
        "Liquidity":  eval.liquidity_gate,
    }

    fail_gates    = [k for k, v in gates.items() if v == "FAIL"]
    unknown_gates = [k for k, v in gates.items() if v == "UNKNOWN"]
    pass_gates    = [k for k, v in gates.items() if v == "PASS"]

    if fail_gates:
        eval.verdict = "PASS"
        eval.verdict_reason = f"Hard fail on: {', '.join(fail_gates)}"
    elif unknown_gates:
        eval.verdict = "INVESTIGATE"
        eval.verdict_reason = f"Needs verification: {', '.join(unknown_gates)}"
    elif len(pass_gates) == 4:
        eval.verdict = "BID"
        eval.verdict_reason = "All 4 gates clear — legal, possession, margin, liquidity"
    else:
        eval.verdict = "INVESTIGATE"
        eval.verdict_reason = "Partial data — manual review needed"


# ─── Call Script Generator ────────────────────────────────────────────────────

def _generate_call_script(listing: AuctionListing, eval: DealEvaluation):
    """Generate tailored bank officer call script based on deal flags."""
    location = listing.location or listing.city or "the property"
    bank     = listing.bank_name or "your bank"
    price_str = f"₹{listing.reserve_price/100_000:.1f}L" if listing.reserve_price else "the listed reserve price"

    # Core questions always asked
    questions = [
        f'1. "Is possession of {location} currently vacant, or is there an occupant?"',
        f'2. "Is there any ongoing court case, stay order, or writ petition against this property?"',
        f'3. "Is the title chain clear for the last 15–20 years?"',
        f'4. "Is the housing society ready to issue NOC and process the transfer?"',
        f'5. "Has the reserve price of {price_str} been approved by SARFAESI / DRT?"',
        f'6. "Can I arrange a physical inspection before the auction date?"',
        f'7. "Is there any encumbrance or dues — society maintenance, property tax, water charges?"',
        f'8. "What is the last date to submit the EMD (Earnest Money Deposit)?"',
    ]

    # Conditional questions based on flags
    if eval.possession_class in ("RISKY", "AVOID", "UNKNOWN"):
        questions.append(
            '9. "If the property is occupied — has the bank initiated eviction proceedings under Section 14 SARFAESI?"'
        )
        questions.append(
            '10. "What is the estimated timeline to get vacant possession from the bank side?"'
        )

    if eval.legal_gate in ("FAIL", "UNKNOWN"):
        questions.append(
            '11. "Has the bank obtained a legal opinion / title report from an empanelled lawyer?"'
        )
        questions.append(
            '12. "Can you share the property documents — sale deed, encumbrance certificate, approved plan?"'
        )

    if eval.margin_gate == "UNKNOWN":
        questions.append(
            '13. "What is the current market rate per sqft for similar properties in this area?"'
        )

    # Opening + script
    script = f"""📞 BANK OFFICER CALL SCRIPT
Property: {location} ({listing.city})
Bank: {bank}
Reserve Price: {price_str}
Your Verdict: {eval.verdict} — {eval.verdict_reason}
{'─'*60}

OPENING:
"Hello, I am calling regarding your auction notice for property at {location}. 
I am a serious buyer and would like to clarify a few points before submitting my EMD."

QUESTIONS TO ASK:
{chr(10).join(questions)}

CLOSING:
"Thank you. Based on your answers, I will confirm my participation within 24 hours. 
Can I have your direct email to send a formal inspection request?"

{'─'*60}
WHAT TO LISTEN FOR:
✅ BID if: Vacant + No court case + Society cooperative + Docs available
⚠️  INVESTIGATE if: Any hesitation on possession or legal docs
❌ WALK AWAY if: Court stay mentioned / Occupied with dispute / Docs unavailable"""

    eval.call_script = script


# ─── Main Evaluator ───────────────────────────────────────────────────────────

def evaluate(listing: AuctionListing) -> DealEvaluation:
    """Run all evaluation checks on a single listing."""
    eval = DealEvaluation(listing_id=listing.listing_id)

    _calc_pnl(listing, eval)
    _check_legal(listing, eval)
    _check_possession(listing, eval)
    _check_liquidity(listing, eval)
    _make_verdict(eval)
    _generate_call_script(listing, eval)

    logger.debug(
        f"[Evaluator] {listing.city} | {listing.source} | "
        f"Verdict:{eval.verdict} | Margin:{eval.net_margin_pct:.0f}%" if eval.net_margin_pct
        else f"[Evaluator] {listing.city} | {listing.source} | Verdict:{eval.verdict}"
    )
    return eval


def evaluate_all(listings: List[AuctionListing]) -> List[tuple[AuctionListing, DealEvaluation]]:
    """Evaluate all BUY/WATCH listings. Returns (listing, evaluation) pairs."""
    results = []
    for l in listings:
        if l.action in ("BUY", "WATCH"):
            ev = evaluate(l)
            # Attach summary back to listing notes
            l.notes = (l.notes + " | " if l.notes else "") + ev.summary_text()
            results.append((l, ev))
    logger.info(f"[Evaluator] Evaluated {len(results)} BUY/WATCH listings")
    return results
