"""
Main Pipeline Orchestrator
Runs all scrapers → enriches + scores → filters → syncs to Sheets → sends alerts.

Usage:
    python engine/pipeline.py                  # single run
    python engine/pipeline.py --dry-run        # scrape + score, no sheets/email
    python engine/pipeline.py --sources ibapi  # run only specific sources
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List

# Ensure repo root is on the path so engine.* imports work whether invoked as
# `python engine/pipeline.py` or `python -m engine.pipeline`
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from engine.models import AuctionListing
from engine.scorer import enrich_and_score, apply_filters, get_top_deals
from engine.evaluator import evaluate_all
from engine.sheets_eval import write_evaluations
import config.settings as cfg

# Lazy imports to avoid crashing if a scraper dependency is missing
def _load_scrapers(sources: List[str]):
    scrapers = []
    for src in sources:
        try:
            if src == "ibapi":
                from scrapers.ibapi_scraper import IBAPIScraper
                scrapers.append(IBAPIScraper())
            elif src == "banke":
                from scrapers.banke_scraper import BankEAuctionsScraper
                scrapers.append(BankEAuctionsScraper())
            elif src == "sbi":
                from scrapers.sbi_scraper import SBIScraper
                scrapers.append(SBIScraper())
            elif src == "pnb":
                from scrapers.pnb_scraper import PNBScraper
                scrapers.append(PNBScraper())
            elif src == "mstc":
                from scrapers.mstc_scraper import MSTCScraper
                scrapers.append(MSTCScraper())
        except ImportError as e:
            logger.warning(f"Scraper '{src}' could not be loaded: {e}")
    return scrapers


def setup_logging(log_dir: str = cfg.LOG_DIR):
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M')}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )
    return logging.getLogger(__name__)


logger = logging.getLogger(__name__)

ALL_SOURCES = ["banke", "sbi", "pnb", "ibapi", "mstc"]


def run_pipeline(
    sources: List[str] = None,
    dry_run: bool = False,
    top_n: int = 10,
) -> dict:
    """
    Execute the full auction intelligence pipeline.

    Returns a summary dict with:
      - total_scraped
      - total_filtered
      - top_deals (list of AuctionListing)
      - new_rows_written
      - email_sent
      - duration_seconds
    """
    start_time = time.time()
    sources = sources or ALL_SOURCES
    summary = {
        "run_at":          datetime.now().isoformat(),
        "sources":         sources,
        "total_scraped":   0,
        "total_filtered":  0,
        "top_deals":       [],
        "new_rows_written": 0,
        "email_sent":      False,
        "duration_seconds": 0,
        "errors":          [],
    }

    logger.info("=" * 60)
    logger.info(f"🚀 Auction Engine Pipeline — {summary['run_at']}")
    logger.info(f"   Sources: {sources}")
    logger.info(f"   Dry run: {dry_run}")
    logger.info("=" * 60)

    # ── Step 1: Scrape all sources ───────────────────────────────────────────
    all_raw: List[AuctionListing] = []
    scrapers = _load_scrapers(sources)

    for scraper in scrapers:
        logger.info(f"\n📡 Scraping: {scraper.source_name}")
        try:
            listings = scraper.safe_scrape()
            all_raw.extend(listings)
            logger.info(f"   ✅ {scraper.source_name}: {len(listings)} raw listings")
        except Exception as e:
            msg = f"{scraper.source_name}: {e}"
            logger.error(f"   ❌ {msg}")
            summary["errors"].append(msg)

    summary["total_scraped"] = len(all_raw)
    logger.info(f"\n📊 Total raw listings: {len(all_raw)}")

    if not all_raw:
        logger.warning("No listings scraped. Check scraper logs.")
        summary["duration_seconds"] = round(time.time() - start_time, 1)
        return summary

    # ── Step 2: Enrich + Score ───────────────────────────────────────────────
    logger.info("\n🧠 Scoring all listings...")
    scored = enrich_and_score(all_raw)

    # ── Step 3: Filter ───────────────────────────────────────────────────────
    logger.info("\n🔍 Applying filters...")
    filtered = apply_filters(scored)
    summary["total_filtered"] = len(filtered)
    logger.info(f"   {len(filtered)} listings passed filters")

    # ── Step 4: Top deals ────────────────────────────────────────────────────
    top = get_top_deals(filtered, n=top_n)
    summary["top_deals"] = top

    logger.info(f"\n🏆 Top {len(top)} deals:")
    for i, deal in enumerate(top, 1):
        discount = f"{deal.discount_pct*100:.0f}%" if deal.discount_pct else "N/A"
        price    = f"₹{deal.reserve_price/100000:.1f}L" if deal.reserve_price else "N/A"
        logger.info(
            f"   #{i} [{deal.action}] Score:{deal.final_score} | "
            f"{deal.city} | {price} | Discount:{discount} | {deal.source}"
        )

    # ── Step 5: Save locally ─────────────────────────────────────────────────
    os.makedirs(cfg.DATA_DIR, exist_ok=True)
    data_file = os.path.join(
        cfg.DATA_DIR,
        f"deals_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    )
    _save_json(filtered, data_file)
    logger.info(f"\n💾 Saved {len(filtered)} filtered deals → {data_file}")

    if dry_run:
        logger.info("\n🔧 Dry run — skipping Sheets sync and email")
        summary["duration_seconds"] = round(time.time() - start_time, 1)
        return summary

    # ── Step 6: Google Sheets sync ───────────────────────────────────────────
    logger.info("\n📋 Syncing to Google Sheets...")
    try:
        from engine.sheets import upsert_listings
        new_rows = upsert_listings(filtered)
        summary["new_rows_written"] = new_rows
        logger.info(f"   ✅ {new_rows} new rows written")
    except Exception as e:
        msg = f"Sheets sync failed: {e}"
        logger.error(f"   ❌ {msg}")
        summary["errors"].append(msg)

    # ── Step 7: Deal evaluation ────────────────────────────────────────────
    try:
        logger.info("\n🧮 Step 7: Running deal evaluations...")
        eval_results = evaluate_all(top)
        # Attach verdict to each listing so the email can display it
        for listing, ev in eval_results:
            listing.verdict = ev.verdict
        counts = write_evaluations(eval_results)
        summary["eval_rows"] = counts.get("eval_rows", 0)
        summary["script_rows"] = counts.get("script_rows", 0)
        logger.info(f"   Eval rows: {summary['eval_rows']} | Call scripts: {summary['script_rows']}")
    except Exception as e:
        logger.warning(f"Eval step failed (non-fatal): {e}")

    # ── Step 8: Email alert ──────────────────────────────────────────────────
    if top:
        logger.info("\n📧 Sending email digest...")
        try:
            from alerts.email_alert import send_daily_digest
            sent = send_daily_digest(top, total_scraped=len(all_raw))
            summary["email_sent"] = sent
            logger.info(f"   {'✅ Sent' if sent else '❌ Failed'}")
        except Exception as e:
            msg = f"Email failed: {e}"
            logger.error(f"   ❌ {msg}")
            summary["errors"].append(msg)

    summary["duration_seconds"] = round(time.time() - start_time, 1)

    logger.info("\n" + "=" * 60)
    logger.info(f"✅ Pipeline complete in {summary['duration_seconds']}s")
    logger.info(f"   Scraped: {summary['total_scraped']}")
    logger.info(f"   Filtered: {summary['total_filtered']}")
    logger.info(f"   Top deals: {len(top)}")
    logger.info(f"   Sheets: {summary['new_rows_written']} new rows")
    logger.info(f"   Evals: {summary.get('eval_rows', 0)} rows | Scripts: {summary.get('script_rows', 0)} rows")
    logger.info(f"   Email: {'sent' if summary['email_sent'] else 'not sent'}")
    if summary["errors"]:
        logger.warning(f"   Errors: {summary['errors']}")
    logger.info("=" * 60)

    return summary


def _save_json(listings: List[AuctionListing], path: str):
    """Save listings as JSON for audit / replay."""
    rows = []
    for l in listings:
        row = {
            "id":             l.listing_id,
            "source":         l.source,
            "city":           l.city,
            "location":       l.location,
            "property_type":  l.property_type,
            "reserve_price":  l.reserve_price,
            "market_price":   l.market_price,
            "discount_pct":   round(l.discount_pct * 100, 1) if l.discount_pct else None,
            "auction_date":   str(l.auction_date) if l.auction_date else None,
            "bank_name":      l.bank_name,
            "final_score":    l.final_score,
            "action":         l.action,
            "possession":     l.possession,
            "legal_status":   l.legal_status,
            "source_url":     l.source_url,
            "date_added":     l.date_added.isoformat(),
        }
        rows.append(row)
    with open(path, "w") as f:
        json.dump(rows, f, indent=2, default=str)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auction Engine Pipeline")
    parser.add_argument("--dry-run",  action="store_true", help="Scrape & score only, no Sheets/email")
    parser.add_argument("--sources",  nargs="+", choices=ALL_SOURCES, help="Run specific sources only")
    parser.add_argument("--top-n",    type=int, default=10, help="Number of top deals to alert")
    args = parser.parse_args()

    setup_logging()
    result = run_pipeline(
        sources=args.sources,
        dry_run=args.dry_run,
        top_n=args.top_n,
    )

    if result["errors"]:
        sys.exit(1)
