"""
Google Sheets Sync Engine
Upserts auction listings into the AUCTION ENGINE sheet via the Sheets API v4.

Setup:
  1. Create a Google Cloud project
  2. Enable Google Sheets API
  3. Create a Service Account → download JSON key → save as config/google_credentials.json
  4. Share your spreadsheet with the service account email
  5. Set SPREADSHEET_ID in config/settings.py

Sheet columns (must match this exact order):
  A  Date Added
  B  Source
  C  City
  D  Location
  E  Property Type
  F  Area (sqft)
  G  Reserve Price
  H  Market Price
  I  Discount %
  J  Auction Date
  K  Bank Name
  L  Contact Person
  M  Contact Number
  N  Possession
  O  Legal Status
  P  Liquidity Score
  Q  Risk Score
  R  Final Score
  S  Action
  T  Notes
  U  Source URL
"""

import logging
import time
from typing import List

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.models import AuctionListing
import config.settings as cfg

logger = logging.getLogger(__name__)

SHEET_HEADERS = [
    "Date Added", "Source", "City", "Location", "Property Type",
    "Area (sqft)", "Reserve Price (₹)", "Market Price (₹)", "Discount %",
    "Auction Date", "Bank Name", "Contact Person", "Contact Number",
    "Possession", "Legal Status", "Liquidity Score", "Risk Score",
    "Final Score", "Action", "Notes", "Source URL"
]


def _get_service():
    """Build and return authenticated Google Sheets service."""
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds_path = cfg.GOOGLE_CREDENTIALS_FILE
        if not os.path.exists(creds_path):
            logger.error(f"Google credentials not found at {creds_path}")
            return None

        creds   = Credentials.from_service_account_file(creds_path, scopes=scopes)
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return service
    except ImportError:
        logger.error("google-api-python-client not installed. Run: pip install google-api-python-client google-auth")
        return None
    except Exception as e:
        logger.error(f"Sheets auth failed: {e}")
        return None


def _sheet_range(sheet_name: str, range_str: str) -> str:
    """Build a Sheets API A1 range string."""
    # The google-api-python-client handles URL-encoding internally,
    # so we pass the name as-is (no manual quoting needed).
    return f"{sheet_name}!{range_str}"


def _ensure_header_row(service, spreadsheet_id: str, sheet_name: str):
    """Create header row if the sheet is empty."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=_sheet_range(sheet_name, "A1:U1"),
        ).execute()
        existing = result.get("values", [])
        if not existing:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=_sheet_range(sheet_name, "A1"),
                valueInputOption="RAW",
                body={"values": [SHEET_HEADERS]},
            ).execute()
            logger.info("[Sheets] Header row created")
    except Exception as e:
        logger.warning(f"[Sheets] Header check failed: {e}")


def _get_existing_ids(service, spreadsheet_id: str, sheet_name: str) -> set:
    """Get all Source URLs already in the sheet (for deduplication)."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=_sheet_range(sheet_name, "U:U"),
        ).execute()
        values = result.get("values", [])
        return {row[0] for row in values if row}
    except Exception as e:
        logger.warning(f"[Sheets] Could not fetch existing IDs: {e}")
        return set()


def _apply_conditional_formatting(service, spreadsheet_id: str, sheet_id: int):
    """Apply color-coding to the Discount % column (I) and Final Score column (R)."""
    requests_body = {
        "requests": [
            # Green: Discount ≥ 30%
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{"sheetId": sheet_id, "startColumnIndex": 8, "endColumnIndex": 9}],
                        "booleanRule": {
                            "condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "%"}]},
                            "format": {}
                        }
                    },
                    "index": 0
                }
            },
            # Highlight BUY in green
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{"sheetId": sheet_id, "startColumnIndex": 18, "endColumnIndex": 19}],
                        "booleanRule": {
                            "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "BUY"}]},
                            "format": {"backgroundColor": {"red": 0.2, "green": 0.7, "blue": 0.2}}
                        }
                    },
                    "index": 1
                }
            },
            # WATCH in yellow
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{"sheetId": sheet_id, "startColumnIndex": 18, "endColumnIndex": 19}],
                        "booleanRule": {
                            "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "WATCH"}]},
                            "format": {"backgroundColor": {"red": 1.0, "green": 0.9, "blue": 0.2}}
                        }
                    },
                    "index": 2
                }
            },
        ]
    }
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=requests_body,
        ).execute()
        logger.info("[Sheets] Conditional formatting applied")
    except Exception as e:
        logger.warning(f"[Sheets] Conditional formatting failed (non-critical): {e}")


def _get_sheet_id(service, spreadsheet_id: str, sheet_name: str) -> int:
    """Get the sheetId (integer) for a named sheet."""
    try:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in meta.get("sheets", []):
            if sheet["properties"]["title"] == sheet_name:
                return sheet["properties"]["sheetId"]
    except Exception:
        pass
    return 0


def upsert_listings(listings: List[AuctionListing]) -> int:
    """
    Write new listings to the Google Sheet.
    Deduplicates by Source URL — skips listings already present.
    Returns count of new rows written.
    """
    if not cfg.GOOGLE_SHEETS_ENABLED:
        logger.info("[Sheets] Disabled in settings — skipping")
        return 0

    service = _get_service()
    if not service:
        return 0

    spreadsheet_id = cfg.SPREADSHEET_ID
    sheet_name     = cfg.SHEET_NAME

    _ensure_header_row(service, spreadsheet_id, sheet_name)
    existing_urls = _get_existing_ids(service, spreadsheet_id, sheet_name)

    new_rows = []
    for l in listings:
        if l.source_url and l.source_url in existing_urls:
            continue
        new_rows.append(l.to_sheet_row())

    if not new_rows:
        logger.info("[Sheets] No new listings to write")
        return 0

    # Batch write in chunks of 100
    chunk_size = 100
    written = 0
    for i in range(0, len(new_rows), chunk_size):
        chunk = new_rows[i:i + chunk_size]
        try:
            service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=_sheet_range(sheet_name, "A:U"),
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": chunk},
            ).execute()
            written += len(chunk)
            time.sleep(0.5)  # Rate limit
        except Exception as e:
            logger.error(f"[Sheets] Batch write failed: {e}")

    # Apply formatting (on first write only)
    if written > 0:
        sheet_id = _get_sheet_id(service, spreadsheet_id, sheet_name)
        _apply_conditional_formatting(service, spreadsheet_id, sheet_id)

    logger.info(f"[Sheets] Wrote {written} new rows")
    return written


def create_spreadsheet() -> str:
    """
    Create a new AUCTION ENGINE spreadsheet and return its ID.
    Call this once during setup.
    """
    service = _get_service()
    if not service:
        raise RuntimeError("Sheets service unavailable")

    spreadsheet = {
        "properties": {"title": "AUCTION ENGINE"},
        "sheets": [
            {"properties": {"title": cfg.SHEET_NAME, "gridProperties": {"frozenRowCount": 1}}}
        ]
    }
    result = service.spreadsheets().create(body=spreadsheet).execute()
    sid = result["spreadsheetId"]
    logger.info(f"[Sheets] Created spreadsheet: https://docs.google.com/spreadsheets/d/{sid}")
    _ensure_header_row(service, sid, cfg.SHEET_NAME)
    return sid
