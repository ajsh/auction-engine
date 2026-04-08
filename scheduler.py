"""
Scheduler — runs the pipeline twice daily at 9 AM and 6 PM IST.

Usage:
    python scheduler.py            # start the daemon (runs forever)
    python scheduler.py --once     # run immediately once and exit
    python scheduler.py --cron     # print crontab line and exit

Deployment options:
    A) Run directly:   nohup python scheduler.py > logs/scheduler.log 2>&1 &
    B) Use cron:       python scheduler.py --cron  → add printed line to crontab
    C) systemd:        See scripts/auction-engine.service
    D) GitHub Actions: See scripts/auction_engine.yml
"""

import argparse
import logging
import sys
import os
import time
import signal
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

import config.settings as cfg
from engine.pipeline import setup_logging, run_pipeline

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> datetime:
    return datetime.now(IST)


def _next_run_time(schedule_times: list) -> datetime:
    """Find the next scheduled run time from now (IST)."""
    now = _now_ist()
    candidates = []

    for t_str in schedule_times:
        h, m = map(int, t_str.split(":"))
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            # Already passed today — schedule for tomorrow
            candidate += timedelta(days=1)
        candidates.append(candidate)

    return min(candidates)


def _seconds_until(dt: datetime) -> float:
    now = _now_ist()
    delta = (dt - now).total_seconds()
    return max(0, delta)


def run_once():
    """Execute the pipeline once immediately."""
    logger.info(f"[Scheduler] Manual run at {_now_ist().strftime('%Y-%m-%d %H:%M IST')}")
    return run_pipeline()


def run_daemon():
    """Run as a background daemon, executing at each scheduled time."""
    logger.info("[Scheduler] Starting auction engine daemon")
    logger.info(f"[Scheduler] Scheduled times (IST): {cfg.SCHEDULE_TIMES_IST}")

    def _handle_signal(sig, frame):
        logger.info("[Scheduler] Shutdown signal received — exiting")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while True:
        next_run = _next_run_time(cfg.SCHEDULE_TIMES_IST)
        wait_secs = _seconds_until(next_run)
        logger.info(
            f"[Scheduler] Next run: {next_run.strftime('%Y-%m-%d %H:%M IST')} "
            f"(in {wait_secs/3600:.1f}h)"
        )

        # Sleep in chunks (so we can handle signals)
        while wait_secs > 0:
            sleep_chunk = min(60, wait_secs)
            time.sleep(sleep_chunk)
            wait_secs -= sleep_chunk

        logger.info(f"[Scheduler] 🚀 Triggering pipeline at {_now_ist().strftime('%H:%M IST')}")
        try:
            summary = run_pipeline()
            logger.info(
                f"[Scheduler] ✅ Complete — "
                f"{summary['total_scraped']} scraped, "
                f"{len(summary['top_deals'])} top deals, "
                f"email={'sent' if summary['email_sent'] else 'skipped'}"
            )
        except Exception as e:
            logger.error(f"[Scheduler] ❌ Pipeline failed: {e}", exc_info=True)

        # Brief sleep to avoid double-triggering on exact minute boundary
        time.sleep(90)


def print_cron_lines():
    """Print crontab entries for both daily run times."""
    script_path = os.path.abspath(__file__)
    python_path = sys.executable
    log_path    = os.path.join(os.path.dirname(__file__), "logs", "cron.log")

    print("\n# Auction Engine — add these lines to your crontab (crontab -e)")
    print("# Times are in UTC. IST = UTC+5:30")
    print()

    for t_str in cfg.SCHEDULE_TIMES_IST:
        h_ist, m_ist = map(int, t_str.split(":"))
        # Convert IST to UTC
        total_mins = h_ist * 60 + m_ist - 330  # subtract 5h30m
        if total_mins < 0:
            total_mins += 24 * 60
        h_utc, m_utc = divmod(total_mins, 60)
        print(
            f"{m_utc} {h_utc} * * *  cd {os.path.dirname(__file__)} && "
            f"{python_path} {script_path} --once >> {log_path} 2>&1"
        )
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auction Engine Scheduler")
    parser.add_argument("--once",  action="store_true", help="Run pipeline once and exit")
    parser.add_argument("--cron",  action="store_true", help="Print crontab lines and exit")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon (default)")
    args = parser.parse_args()

    setup_logging()

    if args.cron:
        print_cron_lines()
    elif args.once:
        result = run_once()
        sys.exit(0 if not result["errors"] else 1)
    else:
        run_daemon()
