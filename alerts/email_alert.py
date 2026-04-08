"""
Email Alert System
Sends a daily HTML digest of top auction deals via Gmail SMTP.

Uses Gmail App Passwords (not OAuth) for simplicity.
Set SMTP_USER, SMTP_PASS, ALERT_EMAIL in config/settings.py.
"""

import smtplib
import logging
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.models import AuctionListing
import config.settings as cfg

logger = logging.getLogger(__name__)


def _format_inr(amount: float | None) -> str:
    if not amount:
        return "N/A"
    if amount >= 10_000_000:
        return f"₹{amount/10_000_000:.2f} Cr"
    return f"₹{amount/100_000:.1f}L"


def _discount_bar(pct: float | None) -> str:
    if pct is None:
        return ""
    bars = int(pct * 20)  # 100% = 20 bars
    return "█" * bars + "░" * (20 - bars) + f"  {pct*100:.0f}%"


def _action_badge(action: str) -> str:
    colors = {
        "BUY":    ("#155724", "#d4edda"),
        "WATCH":  ("#856404", "#fff3cd"),
        "IGNORE": ("#6c757d", "#f8f9fa"),
    }
    fg, bg = colors.get(action, ("#333", "#eee"))
    return (
        f'<span style="background:{bg};color:{fg};padding:3px 10px;'
        f'border-radius:12px;font-weight:bold;font-size:0.85em;">{action}</span>'
    )


def _score_color(score: float) -> str:
    if score >= 8:
        return "#28a745"
    if score >= 6:
        return "#ffc107"
    return "#dc3545"


def _build_html(listings: List[AuctionListing], total_scraped: int) -> str:
    today = date.today().strftime("%d %b %Y")

    # Deal cards
    cards_html = ""
    for i, l in enumerate(listings, 1):
        discount_str = f"{l.discount_pct*100:.0f}%" if l.discount_pct else "N/A"
        score_color  = _score_color(l.final_score)

        cards_html += f"""
        <tr>
          <td style="padding:16px 20px;border-bottom:1px solid #eee;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="width:40px;vertical-align:top;">
                  <div style="background:#1d4f91;color:white;width:32px;height:32px;
                              border-radius:50%;text-align:center;line-height:32px;
                              font-weight:bold;">#{i}</div>
                </td>
                <td style="vertical-align:top;padding-left:12px;">
                  <div style="font-size:1.05em;font-weight:600;color:#1a1a2e;">
                    {l.location or l.title or "Property"}, {l.city}
                  </div>
                  <div style="color:#666;font-size:0.9em;margin-top:3px;">
                    {l.property_type or "Property"} &nbsp;|&nbsp; {l.bank_name}
                    {f" &nbsp;|&nbsp; {l.area_sqft} sqft" if l.area_sqft else ""}
                  </div>
                  <!-- Prices row -->
                  <table style="margin-top:10px;" cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="padding-right:24px;">
                        <div style="font-size:0.78em;color:#999;text-transform:uppercase;">Auction Price</div>
                        <div style="font-size:1.1em;font-weight:700;color:#dc3545;">
                          {_format_inr(l.reserve_price)}
                        </div>
                      </td>
                      <td style="padding-right:24px;">
                        <div style="font-size:0.78em;color:#999;text-transform:uppercase;">Market Price</div>
                        <div style="font-size:1.1em;font-weight:700;color:#28a745;">
                          {_format_inr(l.market_price)}
                        </div>
                      </td>
                      <td style="padding-right:24px;">
                        <div style="font-size:0.78em;color:#999;text-transform:uppercase;">Discount</div>
                        <div style="font-size:1.1em;font-weight:700;color:#1d4f91;">
                          {discount_str}
                        </div>
                      </td>
                      <td>
                        <div style="font-size:0.78em;color:#999;text-transform:uppercase;">Score</div>
                        <div style="font-size:1.1em;font-weight:700;color:{score_color};">
                          {l.final_score}/10
                        </div>
                      </td>
                    </tr>
                  </table>
                  <!-- Badges row -->
                  <div style="margin-top:10px;">
                    {_action_badge(l.action)}
                    &nbsp;
                    <span style="font-size:0.82em;color:#666;">
                      📅 {l.auction_date or "N/A"} &nbsp;|&nbsp;
                      🏠 Possession: {l.possession} &nbsp;|&nbsp;
                      ⚖️ Legal: {l.legal_status} &nbsp;|&nbsp;
                      🏘️ Competition: {l.competition or "N/A"}
                    </span>
                  </div>
                  {f'<div style="margin-top:8px;"><a href="{l.source_url}" style="color:#1d4f91;font-size:0.85em;">🔗 View Listing →</a></div>' if l.source_url else ""}
                </td>
              </tr>
            </table>
          </td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f7fb;padding:24px 0;">
    <tr><td align="center">
      <table width="620" cellpadding="0" cellspacing="0"
             style="background:white;border-radius:12px;overflow:hidden;
                    box-shadow:0 2px 12px rgba(0,0,0,0.08);">
        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#1d4f91,#2e78d8);
                     padding:28px 32px;color:white;">
            <div style="font-size:1.4em;font-weight:700;">🏦 Auction Engine</div>
            <div style="font-size:0.9em;opacity:0.85;margin-top:4px;">
              Daily Deal Digest — {today}
            </div>
          </td>
        </tr>
        <!-- Stats bar -->
        <tr>
          <td style="background:#f0f4ff;padding:14px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="text-align:center;">
                  <div style="font-size:1.4em;font-weight:700;color:#1d4f91;">{total_scraped}</div>
                  <div style="font-size:0.75em;color:#666;">Total Scanned</div>
                </td>
                <td style="text-align:center;border-left:1px solid #dce4f5;">
                  <div style="font-size:1.4em;font-weight:700;color:#28a745;">{len(listings)}</div>
                  <div style="font-size:0.75em;color:#666;">Top Deals</div>
                </td>
                <td style="text-align:center;border-left:1px solid #dce4f5;">
                  <div style="font-size:1.4em;font-weight:700;color:#dc3545;">
                    {sum(1 for l in listings if l.action == 'BUY')}
                  </div>
                  <div style="font-size:0.75em;color:#666;">BUY Signals</div>
                </td>
                <td style="text-align:center;border-left:1px solid #dce4f5;">
                  <div style="font-size:1.4em;font-weight:700;color:#856404;">
                    {sum(1 for l in listings if l.action == 'WATCH')}
                  </div>
                  <div style="font-size:0.75em;color:#666;">Watch List</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <!-- Deal cards -->
        <tr>
          <td style="padding:0 20px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              {cards_html if cards_html else
               '<tr><td style="padding:40px;text-align:center;color:#999;">No deals matched your filters today.</td></tr>'}
            </table>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="padding:20px 32px;background:#f8f9fa;
                     border-top:1px solid #eee;color:#888;font-size:0.8em;">
            <strong>⚠️ Reminder:</strong> Automation finds deals — profits come from your own
            verification (bank call, site visit, title check). Always verify before bidding.
            <br><br>
            Auction Engine © {date.today().year} — Running 2× daily (9 AM + 6 PM IST)
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _build_plaintext(listings: List[AuctionListing], total_scraped: int) -> str:
    today = date.today().strftime("%d %b %Y")
    lines = [
        f"🏦 AUCTION ENGINE — Daily Digest {today}",
        f"Scanned: {total_scraped} | Top deals: {len(listings)}",
        "=" * 60,
    ]
    for i, l in enumerate(listings, 1):
        discount_str = f"{l.discount_pct*100:.0f}%" if l.discount_pct else "N/A"
        lines.append(f"\n#{i} — {l.location or l.title}, {l.city}")
        lines.append(f"   {l.property_type} | {l.bank_name}")
        lines.append(f"   Auction: {_format_inr(l.reserve_price)} | Market: {_format_inr(l.market_price)}")
        lines.append(f"   Discount: {discount_str} | Score: {l.final_score}/10 | {l.action}")
        lines.append(f"   Date: {l.auction_date} | Possession: {l.possession}")
        if l.source_url:
            lines.append(f"   Link: {l.source_url}")
    lines.append("\n" + "=" * 60)
    lines.append("⚠️ Always verify before bidding: bank call + site visit + title check")
    return "\n".join(lines)


def send_daily_digest(
    top_listings: List[AuctionListing],
    total_scraped: int = 0,
) -> bool:
    """
    Send the daily deal digest email.
    Returns True on success, False on failure.
    """
    if not top_listings:
        logger.info("[Email] No qualifying deals — skipping email")
        return True

    subject = (
        f"🔥 {len(top_listings)} Auction Deals Today | "
        f"{sum(1 for l in top_listings if l.action == 'BUY')} BUY signals — "
        f"{date.today().strftime('%d %b')}"
    )

    html_body  = _build_html(top_listings, total_scraped)
    plain_body = _build_plaintext(top_listings, total_scraped)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = cfg.SMTP_USER
    msg["To"]      = cfg.ALERT_EMAIL

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body,  "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(cfg.SMTP_USER, cfg.SMTP_PASS)
            server.sendmail(cfg.SMTP_USER, cfg.ALERT_EMAIL, msg.as_string())
        logger.info(f"[Email] Sent digest to {cfg.ALERT_EMAIL}: {len(top_listings)} deals")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("[Email] SMTP authentication failed. Check SMTP_USER and SMTP_PASS in settings.py")
        return False
    except Exception as e:
        logger.error(f"[Email] Failed to send: {e}")
        return False
