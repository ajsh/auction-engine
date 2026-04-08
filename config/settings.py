"""
Auction Engine — Central Configuration
Edit this file to customize your targets, filters, and alerts.
"""

# ─── ALERT EMAIL ────────────────────────────────────────────────────────────
ALERT_EMAIL = "arshaharjun@gmail.com"          # Where to send daily deal summaries
SMTP_USER   = "arshaharjun@gmail.com"          # Gmail address for sending alerts
SMTP_PASS   = "nspi oqdg dfsg yvhq"           # Gmail App Password

# ─── GOOGLE SHEETS ──────────────────────────────────────────────────────────
GOOGLE_SHEETS_ENABLED  = True
GOOGLE_CREDENTIALS_FILE = "config/google_credentials.json"  # Service account JSON
SPREADSHEET_ID         = "1uSxiopClCQ0FfjM8zNWymnRa3dQkZkbrJiVtLzXUQzQ"  # From Sheet URL
SHEET_NAME             = "AUCTION ENGINE"

# ─── DEAL FILTERS ───────────────────────────────────────────────────────────
TARGET_CITIES = [
    "Mumbai", "Thane", "Navi Mumbai",
    "Ahmedabad", "Vadodara", "Baroda",
    "Gujarat", "Surat", "Rajkot",
]

PROPERTY_TYPES = ["Flat", "Shop", "Plot", "Industrial", "Commercial", "Residential"]

BUDGET_MIN_LAC = 10       # ₹10 Lakh minimum
BUDGET_MAX_CR  = 10       # ₹10 Crore maximum

MIN_DISCOUNT_PCT = 0.20   # Only show deals ≥20% below market
MIN_SCORE        = 6.0    # Only alert on deals scoring ≥6

AUCTION_DAYS_AHEAD = 45   # Include auctions happening within next N days

# ─── SCORING WEIGHTS ────────────────────────────────────────────────────────
# Must sum to 1.0
WEIGHT_DISCOUNT  = 0.30
WEIGHT_LIQUIDITY = 0.20
WEIGHT_RISK      = 0.25
WEIGHT_LOCATION  = 0.25

# ─── SCHEDULER ──────────────────────────────────────────────────────────────
# Two daily runs: 9 AM and 6 PM IST
SCHEDULE_TIMES_IST = ["09:00", "18:00"]

# ─── SCRAPER SETTINGS ───────────────────────────────────────────────────────
REQUEST_DELAY_SECONDS = 2     # Polite delay between requests
MAX_RETRIES           = 3
REQUEST_TIMEOUT       = 30
USE_HEADLESS_BROWSER  = True  # Selenium for JS-heavy sites

# ─── DATA ───────────────────────────────────────────────────────────────────
DATA_DIR = "data"
LOG_DIR  = "logs"
