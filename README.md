# 🏦 Auction Engine
**Fully automated India bank auction lead sourcing system**

Scrapes 4+ major auction portals twice daily → scores every deal → syncs to Google Sheets → emails you a filtered digest of the best opportunities.

---

## What It Does

| Step | What happens |
|---|---|
| **Scrape** | Pulls listings from BankEAuctions, SBI, PNB, IBAPI, MSTC |
| **Score** | Every deal gets a weighted 1–10 score (discount + location + liquidity + risk) |
| **Filter** | Hard filters: city, budget, auction date window, minimum discount |
| **Sync** | Pushes all filtered deals to your Google Sheet with conditional formatting |
| **Alert** | Sends you a beautiful HTML email with today's top deals |
| **Schedule** | Repeats automatically at 9 AM + 6 PM IST every day |

---

## Quickstart (Local Machine)

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

You'll also need Chrome installed (for Selenium).

### 2. Configure settings
Edit `config/settings.py`:
```python
ALERT_EMAIL  = "you@gmail.com"
SMTP_USER    = "you@gmail.com"
SMTP_PASS    = "your-app-password"   # Gmail App Password, not your real password

TARGET_CITIES    = ["Mumbai", "Ahmedabad", "Vadodara"]
BUDGET_MIN_LAC   = 10   # ₹10L minimum
BUDGET_MAX_CR    = 5    # ₹5Cr maximum
MIN_DISCOUNT_PCT = 0.25 # 25% below market minimum
```

### 3. Gmail App Password setup
1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. Select app: Mail → Device: Other → type "Auction Engine"
3. Copy the 16-char password → paste as `SMTP_PASS`

### 4. Google Sheets setup (optional but recommended)
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable **Google Sheets API** + **Google Drive API**
3. Create a **Service Account** → download JSON key → save as `config/google_credentials.json`
4. Create a new Google Sheet, copy its ID from the URL
5. Share the sheet with the service account email (Editor access)
6. Set `SPREADSHEET_ID` in `config/settings.py`
7. First run will auto-create the header row

### 5. Run once (test)
```bash
python scheduler.py --once --dry-run     # scrape + score, no sheets/email
python scheduler.py --once               # full run including sheets + email
```

### 6. Run as daemon (stays running, triggers on schedule)
```bash
nohup python scheduler.py > logs/scheduler.log 2>&1 &
```

### 7. Or use cron
```bash
python scheduler.py --cron    # prints the exact crontab lines to add
crontab -e                    # paste them in
```

---

## Cloud Deployment (Recommended) — GitHub Actions

Runs in the cloud for free, no server needed.

### Setup
1. Push this folder to a **private** GitHub repo
2. Go to repo **Settings → Secrets and variables → Actions**
3. Add these secrets:

| Secret name | Value |
|---|---|
| `SMTP_USER` | your Gmail address |
| `SMTP_PASS` | your Gmail App Password |
| `ALERT_EMAIL` | where to receive alerts |
| `GOOGLE_CREDENTIALS` | paste full content of `google_credentials.json` |
| `SPREADSHEET_ID` | your Google Sheet ID |
| `IBAPI_PROXY` | (optional) residential proxy for IBAPI |

4. Copy `.github/workflows/auction_engine.yml` to your repo (or create the folder)
5. GitHub Actions will run at 3:30 AM UTC (9 AM IST) and 12:30 PM UTC (6 PM IST)

### Manual trigger
Go to **Actions → Auction Engine — Daily Run → Run workflow**

---

## Data Sources

| Source | Portal | Coverage | Notes |
|---|---|---|---|
| **BankEAuctions** | bankeauctions.com | 250K+ properties, all banks | Best breadth, AJAX-rendered |
| **SBI** | sbi.bank.in | SBI notices (Sarfaesi, DRT, Mega) | Batch PDFs, HTTP-only |
| **PNB** | pnb.bank.in | PNB properties by state | Searchable, ViewState forms |
| **IBAPI** | ibapi.in | All PSU banks aggregated | Blocked by WAF — needs proxy |
| **MSTC** | mstcecommerce.com | Currently suspended | Kept for resumption |

> **Proxy note:** IBAPI blocks datacenter IPs. For best results with IBAPI, use a residential Indian proxy (e.g. Bright Data, Oxylabs). Set `IBAPI_PROXY=http://user:pass@proxy:port` as environment variable.

---

## Deal Scoring

Every deal is scored 1–10 using:

```
Score = (Discount × 0.30) + (Liquidity × 0.20) + (Risk × 0.25) + (Location × 0.25)
```

| Score | Action |
|---|---|
| ≥ 8.0 | **BUY** — act fast |
| 6.0–7.9 | **WATCH** — investigate |
| < 6.0 | **IGNORE** |

**Discount** — normalized against a 50% discount = 10/10  
**Liquidity** — proximity to station/highway/main road  
**Risk** — possession status, legal flags, known disputes  
**Location** — area-level quality lookup (Mumbai zones, SG Highway, etc.)

You can tune all weights in `config/settings.py`.

---

## Google Sheet Structure

| Col | Field | Notes |
|---|---|---|
| A | Date Added | Timestamp of scrape |
| B | Source | IBAPI / BankEAuctions / SBI / PNB |
| C | City | Matched target city |
| D | Location | Full address |
| E | Property Type | Flat / Shop / Plot / Industrial |
| F | Area (sqft) | When available |
| G | Reserve Price | Auction asking price in ₹ |
| H | Market Price | From external lookup (manual/VA) |
| I | Discount % | (H-G)/H — color coded |
| J | Auction Date | Next bid deadline |
| K | Bank Name | |
| L | Contact Person | Authorised Officer |
| M | Contact Number | |
| N | Possession | Vacant / Occupied / Unknown |
| O | Legal Status | Clear / Dispute / Unknown |
| P | Liquidity Score | 1–10 |
| Q | Risk Score | 1–10 |
| R | **Final Score** | 1–10, color coded |
| S | **Action** | BUY / WATCH / IGNORE, color coded |
| T | Notes | Source type, flags |
| U | Source URL | Direct link to listing |

---

## Google Apps Script (Sheet-side Alerts)

Open your sheet → **Extensions → Apps Script** → paste `scripts/google_apps_script.js`

Then set two triggers:
- `sendTopDealsDigest` — daily at 10 AM (sends HTML email from the sheet)
- `sendWhatsAppAlert` — daily at 10 AM (sends WhatsApp via Twilio, if configured)
- `applySheetFormatting` — run once manually to apply colors

---

## Adding Market Prices

The system cannot auto-fill market prices without a paid API.  
Two options:

**Option A — Manual / VA**  
Add a `=GOOGLEFINANCE(...)` or look up 99acres/MagicBricks for 2–3 comps.  
Update column H manually for shortlisted deals.

**Option B — MagicBricks scraper (advanced)**  
The scoring engine will compute discount % automatically once column H is filled.

---

## Project Structure

```
auction-engine/
├── config/
│   └── settings.py              ← All your config here
├── scrapers/
│   ├── base.py                  ← Shared HTTP + Selenium helpers
│   ├── banke_scraper.py         ← BankEAuctions (AJAX, Selenium)
│   ├── ibapi_scraper.py         ← IBAPI (ASP.NET ViewState)
│   ├── sbi_scraper.py           ← SBI notice pages (HTTP)
│   ├── pnb_scraper.py           ← PNB property search (Selenium)
│   └── mstc_scraper.py          ← MSTC (suspended, standby)
├── engine/
│   ├── models.py                ← AuctionListing dataclass + scoring
│   ├── scorer.py                ← Filter + enrichment + scoring logic
│   ├── sheets.py                ← Google Sheets API sync
│   └── pipeline.py              ← Main orchestrator
├── alerts/
│   └── email_alert.py           ← HTML email digest builder + sender
├── scripts/
│   ├── google_apps_script.js    ← Apps Script for sheet-side alerts
│   └── auction_engine.yml       ← GitHub Actions workflow
├── data/                        ← JSON run archives (auto-created)
├── logs/                        ← Log files (auto-created)
├── scheduler.py                 ← Daemon / cron / one-shot runner
└── requirements.txt
```

---

## Daily Workflow (Your Part — 20 min)

After the system runs and you receive the email:

**Morning (15 min)**
- Open the sheet / email
- Sort by Final Score
- Shortlist top 3 deals

**Action on shortlist**
- Call the Authorised Officer (contact in sheet)
- Ask: Is possession vacant? Any legal issues? Can I visit?

**Evening**
- Visit shortlisted property OR send a trusted person

> Your real edge isn't the data — it's **speed + decision + negotiation**. The system handles the sourcing. You handle the closing.

---

## Limitations

- **IBAPI** blocks datacenter IPs — requires a residential/Indian proxy for reliable access
- **MSTC** is currently suspended (NPA auctions ceased)
- Market price (column H) is not auto-populated — needs manual input or a paid API
- SBI/PNB show batch-level notices, not always individual property records
- Possession/legal status is only available when explicitly listed — many will show "Unknown"

---

## License

MIT — free to use, modify, and deploy for personal or commercial purposes.
