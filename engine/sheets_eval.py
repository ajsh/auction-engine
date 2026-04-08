"""
Google Sheets — Deal Evaluations Tab
Writes full due diligence reports + call scripts to a second sheet tab.
Also updates the Action/Notes columns on the main AUCTION ENGINE tab.
"""

import logging
import time
from typing import List, Tuple

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.models import AuctionListing
from engine.evaluator import DealEvaluation, EVAL_SHEET_HEADERS
import config.settings as cfg

logger = logging.getLogger(__name__)

EVAL_SHEET_NAME   = "DEAL EVALUATIONS"
CALL_SHEET_NAME   = "CALL SCRIPTS"


def _get_service():
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds  = Credentials.from_service_account_file(
            cfg.GOOGLE_CREDENTIALS_FILE, scopes=scopes
        )
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.error(f"[SheetsEval] Auth failed: {e}")
        return None


def _sheet_range(name: str, r: str) -> str:
    return f"{name}!{r}"


def _ensure_tab(service, spreadsheet_id: str, title: str,
                headers: list, color: dict = None) -> int:
    """Create tab if it doesn't exist. Returns sheetId."""
    meta  = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"]
                for s in meta.get("sheets", [])}

    if title in existing:
        return existing[title]

    # Create new tab
    req = {"addSheet": {"properties": {"title": title}}}
    resp = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [req]}
    ).execute()
    sheet_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]

    # Write headers
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=_sheet_range(title, "A1"),
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()

    # Style header row
    bg = color or {"red": 0.13, "green": 0.13, "blue": 0.13}
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id,
                               "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount"
            }},
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": bg,
                    "textFormat": {
                        "foregroundColor": {"red":1,"green":1,"blue":1},
                        "bold": True
                    }
                }},
                "fields": "userEnteredFormat(backgroundColor,textFormat)"
            }},
        ]}
    ).execute()

    logger.info(f"[SheetsEval] Created tab: {title}")
    return sheet_id


def _apply_verdict_formatting(service, spreadsheet_id: str, sheet_id: int,
                               last_row: int):
    """Color-code the VERDICT column (col 21 = index 20)."""
    if last_row < 2:
        return
    verdict_range = {
        "sheetId": sheet_id,
        "startRowIndex": 1,
        "endRowIndex": last_row,
        "startColumnIndex": 20,
        "endColumnIndex": 21,
    }
    requests = [
        {"addConditionalFormatRule": {
            "rule": {
                "ranges": [verdict_range],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ",
                                  "values": [{"userEnteredValue": "BID"}]},
                    "format": {"backgroundColor": {"red":0.2,"green":0.7,"blue":0.2},
                               "textFormat": {"bold": True,
                                              "foregroundColor": {"red":1,"green":1,"blue":1}}}
                }
            }, "index": 0
        }},
        {"addConditionalFormatRule": {
            "rule": {
                "ranges": [verdict_range],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ",
                                  "values": [{"userEnteredValue": "INVESTIGATE"}]},
                    "format": {"backgroundColor": {"red":1.0,"green":0.85,"blue":0.2}}
                }
            }, "index": 1
        }},
        {"addConditionalFormatRule": {
            "rule": {
                "ranges": [verdict_range],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ",
                                  "values": [{"userEnteredValue": "PASS"}]},
                    "format": {"backgroundColor": {"red":0.95,"green":0.95,"blue":0.95},
                               "textFormat": {"foregroundColor": {"red":0.6,"green":0.6,"blue":0.6}}}
                }
            }, "index": 2
        }},
    ]
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests}
        ).execute()
    except Exception as e:
        logger.warning(f"[SheetsEval] Formatting failed (non-critical): {e}")


def write_evaluations(
    results: List[Tuple[AuctionListing, DealEvaluation]]
) -> dict:
    """
    Write evaluation results to two tabs:
      - DEAL EVALUATIONS: structured data per deal
      - CALL SCRIPTS: full call script text per BID/INVESTIGATE deal
    Returns counts written.
    """
    if not results:
        logger.info("[SheetsEval] No evaluations to write")
        return {"eval_rows": 0, "script_rows": 0}

    if not cfg.GOOGLE_SHEETS_ENABLED:
        return {"eval_rows": 0, "script_rows": 0}

    service = _get_service()
    if not service:
        return {"eval_rows": 0, "script_rows": 0}

    sid = cfg.SPREADSHEET_ID

    # ── Create/ensure tabs ────────────────────────────────────────────────────
    eval_sheet_id = _ensure_tab(
        service, sid, EVAL_SHEET_NAME, EVAL_SHEET_HEADERS,
        color={"red": 0.18, "green": 0.33, "blue": 0.18}
    )
    call_sheet_id = _ensure_tab(
        service, sid, CALL_SHEET_NAME,
        ["Listing ID", "City", "Location", "Bank", "Reserve Price",
         "Verdict", "Verdict Reason", "Call Script"],
        color={"red": 0.33, "green": 0.18, "blue": 0.18}
    )

    # ── Get existing listing IDs to deduplicate ───────────────────────────────
    def get_existing_ids(sheet_name: str) -> set:
        try:
            r = service.spreadsheets().values().get(
                spreadsheetId=sid,
                range=_sheet_range(sheet_name, "A:A")
            ).execute()
            return {row[0] for row in r.get("values", []) if row}
        except Exception:
            return set()

    existing_eval_ids  = get_existing_ids(EVAL_SHEET_NAME)
    existing_call_ids  = get_existing_ids(CALL_SHEET_NAME)

    # ── Build rows ────────────────────────────────────────────────────────────
    eval_rows  = []
    call_rows  = []

    for listing, ev in results:
        if ev.listing_id not in existing_eval_ids:
            eval_rows.append(ev.to_sheet_row())

        if ev.verdict in ("BID", "INVESTIGATE") and ev.listing_id not in existing_call_ids:
            price_str = f"₹{listing.reserve_price/100_000:.1f}L" if listing.reserve_price else "N/A"
            call_rows.append([
                ev.listing_id,
                listing.city,
                listing.location or listing.title,
                listing.bank_name,
                price_str,
                ev.verdict,
                ev.verdict_reason,
                ev.call_script,
            ])

    # ── Write eval rows ───────────────────────────────────────────────────────
    eval_written = 0
    if eval_rows:
        try:
            service.spreadsheets().values().append(
                spreadsheetId=sid,
                range=_sheet_range(EVAL_SHEET_NAME, "A:V"),
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": eval_rows},
            ).execute()
            eval_written = len(eval_rows)
            # Apply verdict coloring
            _apply_verdict_formatting(service, sid, eval_sheet_id,
                                       len(existing_eval_ids) + eval_written + 1)
        except Exception as e:
            logger.error(f"[SheetsEval] Eval write failed: {e}")

    # ── Write call scripts ────────────────────────────────────────────────────
    call_written = 0
    if call_rows:
        try:
            service.spreadsheets().values().append(
                spreadsheetId=sid,
                range=_sheet_range(CALL_SHEET_NAME, "A:H"),
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": call_rows},
            ).execute()
            call_written = len(call_rows)
            # Auto-resize call script column (H)
            service.spreadsheets().batchUpdate(
                spreadsheetId=sid,
                body={"requests": [{
                    "updateDimensionProperties": {
                        "range": {"sheetId": call_sheet_id,
                                  "dimension": "COLUMNS",
                                  "startIndex": 7, "endIndex": 8},
                        "properties": {"pixelSize": 600},
                        "fields": "pixelSize"
                    }
                }]}
            ).execute()
        except Exception as e:
            logger.error(f"[SheetsEval] Call script write failed: {e}")

    logger.info(f"[SheetsEval] Wrote {eval_written} eval rows, {call_written} call scripts")
    return {"eval_rows": eval_written, "script_rows": call_written}
