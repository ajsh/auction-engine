/**
 * Google Apps Script — Auction Engine Sheet Alerts
 * ──────────────────────────────────────────────────
 * Paste this entire file into your Google Sheet:
 *   Extensions → Apps Script → paste → Save → Set triggers
 *
 * SETUP:
 *   1. Open your AUCTION ENGINE Google Sheet
 *   2. Extensions → Apps Script
 *   3. Paste this code, replacing "YOUR_EMAIL@gmail.com"
 *   4. Click Save (💾)
 *   5. Click Triggers (⏰ icon) → Add Trigger:
 *        Function: sendTopDealsDigest
 *        Event: Time-driven → Day timer → 10am–11am
 *   6. Authorize when prompted
 *
 * OPTIONAL — WhatsApp via Twilio:
 *   Fill in TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, WHATSAPP_TO
 *   Then add a trigger for sendWhatsAppAlert as well.
 */

// ── CONFIG ────────────────────────────────────────────────────────────────────
const ALERT_EMAIL     = "YOUR_EMAIL@gmail.com";  // ← change this
const SHEET_NAME      = "AUCTION ENGINE";
const MIN_SCORE       = 7.0;                     // Only alert on score ≥ this
const MAX_DEALS_EMAIL = 10;                      // Max deals in email

// WhatsApp via Twilio (optional — leave blank to skip)
const TWILIO_ACCOUNT_SID   = "";
const TWILIO_AUTH_TOKEN    = "";
const TWILIO_WHATSAPP_FROM = "whatsapp:+14155238886";  // Twilio sandbox number
const WHATSAPP_TO          = "whatsapp:+91XXXXXXXXXX"; // Your number with country code

// ── COLUMN INDICES (0-based, matching your sheet) ────────────────────────────
const COL = {
  DATE_ADDED:     0,   // A
  SOURCE:         1,   // B
  CITY:           2,   // C
  LOCATION:       3,   // D
  PROP_TYPE:      4,   // E
  AREA:           5,   // F
  RESERVE_PRICE:  6,   // G
  MARKET_PRICE:   7,   // H
  DISCOUNT:       8,   // I
  AUCTION_DATE:   9,   // J
  BANK_NAME:      10,  // K
  CONTACT_PERSON: 11,  // L
  CONTACT_NUMBER: 12,  // M
  POSSESSION:     13,  // N
  LEGAL_STATUS:   14,  // O
  LIQUIDITY:      15,  // P
  RISK:           16,  // Q
  FINAL_SCORE:    17,  // R
  ACTION:         18,  // S
  NOTES:          19,  // T
  SOURCE_URL:     20,  // U
};

// ── HELPERS ──────────────────────────────────────────────────────────────────

function getSheetData() {
  const ss    = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) throw new Error(`Sheet "${SHEET_NAME}" not found`);
  const data = sheet.getDataRange().getValues();
  return data.slice(1); // Skip header row
}

function formatPrice(val) {
  if (!val || isNaN(val)) return "N/A";
  const num = parseFloat(val);
  if (num >= 10000000) return `₹${(num/10000000).toFixed(2)} Cr`;
  return `₹${(num/100000).toFixed(1)}L`;
}

function getActionEmoji(action) {
  if (action === "BUY")    return "🟢";
  if (action === "WATCH")  return "🟡";
  return "⚪";
}

// ── MAIN EMAIL DIGEST ────────────────────────────────────────────────────────

function sendTopDealsDigest() {
  const data  = getSheetData();
  const today = new Date().toLocaleDateString("en-IN", { day:"2-digit", month:"short", year:"numeric" });

  // Filter: score >= MIN_SCORE + action != IGNORE, sorted by score desc
  const qualifying = data
    .filter(row => {
      const score  = parseFloat(row[COL.FINAL_SCORE]) || 0;
      const action = String(row[COL.ACTION]).trim().toUpperCase();
      // Also filter to rows added today or yesterday
      const dateAdded = new Date(row[COL.DATE_ADDED]);
      const cutoff    = new Date(); cutoff.setDate(cutoff.getDate() - 1);
      return score >= MIN_SCORE && action !== "IGNORE" && dateAdded >= cutoff;
    })
    .sort((a, b) => (parseFloat(b[COL.FINAL_SCORE]) || 0) - (parseFloat(a[COL.FINAL_SCORE]) || 0))
    .slice(0, MAX_DEALS_EMAIL);

  if (qualifying.length === 0) {
    console.log("No qualifying deals today — skipping email");
    return;
  }

  const buyCount   = qualifying.filter(r => r[COL.ACTION] === "BUY").length;
  const watchCount = qualifying.filter(r => r[COL.ACTION] === "WATCH").length;

  // Build HTML email
  let dealRows = "";
  qualifying.forEach((row, i) => {
    const action    = String(row[COL.ACTION]).trim().toUpperCase();
    const score     = parseFloat(row[COL.FINAL_SCORE]).toFixed(1);
    const discount  = row[COL.DISCOUNT] || "N/A";
    const actionBg  = action === "BUY" ? "#d4edda" : action === "WATCH" ? "#fff3cd" : "#f8f9fa";
    const actionFg  = action === "BUY" ? "#155724" : action === "WATCH" ? "#856404" : "#6c757d";
    const url       = row[COL.SOURCE_URL] || "";
    const linkHtml  = url ? `<a href="${url}" style="color:#1d4f91;font-size:0.82em;">🔗 View</a>` : "";

    dealRows += `
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:12px 8px;font-weight:600;color:#1a1a2e;">${i+1}</td>
        <td style="padding:12px 8px;">
          <strong>${row[COL.LOCATION] || row[COL.CITY]}</strong><br>
          <span style="color:#666;font-size:0.85em;">${row[COL.PROP_TYPE] || ""} · ${row[COL.BANK_NAME] || ""}</span>
        </td>
        <td style="padding:12px 8px;text-align:center;">${row[COL.CITY] || ""}</td>
        <td style="padding:12px 8px;text-align:right;color:#dc3545;font-weight:700;">
          ${formatPrice(row[COL.RESERVE_PRICE])}
        </td>
        <td style="padding:12px 8px;text-align:center;color:#1d4f91;font-weight:700;">${discount}</td>
        <td style="padding:12px 8px;text-align:center;font-weight:700;color:${score >= 8 ? '#28a745' : '#ffc107'}">
          ${score}
        </td>
        <td style="padding:12px 8px;text-align:center;">
          <span style="background:${actionBg};color:${actionFg};padding:2px 8px;border-radius:10px;font-size:0.8em;font-weight:bold;">
            ${action}
          </span>
        </td>
        <td style="padding:12px 8px;text-align:center;">${row[COL.AUCTION_DATE] || ""}</td>
        <td style="padding:12px 8px;">${linkHtml}</td>
      </tr>`;
  });

  const htmlBody = `
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:900px;margin:0 auto;">
      <div style="background:linear-gradient(135deg,#1d4f91,#2e78d8);color:white;padding:24px 28px;border-radius:10px 10px 0 0;">
        <h2 style="margin:0;">🏦 Auction Engine — Sheet Digest</h2>
        <p style="margin:6px 0 0;opacity:0.85;">${today} · ${qualifying.length} deals · ${buyCount} BUY · ${watchCount} WATCH</p>
      </div>
      <table style="width:100%;border-collapse:collapse;background:white;">
        <thead style="background:#f0f4ff;">
          <tr>
            <th style="padding:10px 8px;">#</th>
            <th style="padding:10px 8px;text-align:left;">Property</th>
            <th style="padding:10px 8px;">City</th>
            <th style="padding:10px 8px;">Price</th>
            <th style="padding:10px 8px;">Discount</th>
            <th style="padding:10px 8px;">Score</th>
            <th style="padding:10px 8px;">Action</th>
            <th style="padding:10px 8px;">Date</th>
            <th style="padding:10px 8px;">Link</th>
          </tr>
        </thead>
        <tbody>${dealRows}</tbody>
      </table>
      <div style="background:#f8f9fa;padding:14px 20px;border-radius:0 0 10px 10px;color:#888;font-size:0.8em;">
        ⚠️ Always verify: bank call + site visit + title check before bidding.
        <a href="${SpreadsheetApp.getActiveSpreadsheet().getUrl()}" style="color:#1d4f91;">Open Sheet →</a>
      </div>
    </div>`;

  const subject = `🔥 ${qualifying.length} Auction Deals — ${buyCount} BUY signals · ${today}`;

  MailApp.sendEmail({
    to:       ALERT_EMAIL,
    subject:  subject,
    htmlBody: htmlBody,
    body:     `${qualifying.length} deals (${buyCount} BUY, ${watchCount} WATCH) — open sheet: ${SpreadsheetApp.getActiveSpreadsheet().getUrl()}`
  });

  console.log(`Email sent: ${qualifying.length} deals`);
}

// ── WHATSAPP ALERT (TWILIO) ───────────────────────────────────────────────────

function sendWhatsAppAlert() {
  if (!TWILIO_ACCOUNT_SID || !TWILIO_AUTH_TOKEN) {
    console.log("Twilio not configured — skipping WhatsApp");
    return;
  }

  const data = getSheetData();
  const today = new Date(); today.setDate(today.getDate() - 1);

  const topDeals = data
    .filter(row => {
      const score     = parseFloat(row[COL.FINAL_SCORE]) || 0;
      const action    = String(row[COL.ACTION]).toUpperCase();
      const dateAdded = new Date(row[COL.DATE_ADDED]);
      return score >= 8 && action === "BUY" && dateAdded >= today;
    })
    .sort((a, b) => parseFloat(b[COL.FINAL_SCORE]) - parseFloat(a[COL.FINAL_SCORE]))
    .slice(0, 5);

  if (topDeals.length === 0) return;

  let msg = `🔥 *TOP AUCTION DEALS TODAY*\n\n`;
  topDeals.forEach((row, i) => {
    const discount = row[COL.DISCOUNT] || "N/A";
    msg += `*#${i+1}* ${row[COL.LOCATION] || row[COL.CITY]}\n`;
    msg += `💰 ${formatPrice(row[COL.RESERVE_PRICE])} | 📉 ${discount}\n`;
    msg += `⭐ Score: ${row[COL.FINAL_SCORE]} | 📅 ${row[COL.AUCTION_DATE] || "N/A"}\n`;
    msg += `🏦 ${row[COL.BANK_NAME]}\n\n`;
  });

  const url = `https://api.twilio.com/2010-04-01/Accounts/${TWILIO_ACCOUNT_SID}/Messages.json`;
  const payload = `From=${encodeURIComponent(TWILIO_WHATSAPP_FROM)}&To=${encodeURIComponent(WHATSAPP_TO)}&Body=${encodeURIComponent(msg)}`;
  const auth    = Utilities.base64Encode(`${TWILIO_ACCOUNT_SID}:${TWILIO_AUTH_TOKEN}`);

  const options = {
    method:  "post",
    contentType: "application/x-www-form-urlencoded",
    headers: { "Authorization": `Basic ${auth}` },
    payload: payload,
    muteHttpExceptions: true,
  };

  const response = UrlFetchApp.fetch(url, options);
  console.log(`WhatsApp sent: ${response.getResponseCode()} — ${response.getContentText().slice(0, 100)}`);
}

// ── CONDITIONAL FORMATTING (run once manually) ────────────────────────────────

function applySheetFormatting() {
  const ss    = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) return;

  const lastRow = Math.max(sheet.getLastRow(), 2);

  // Action column (S = col 19)
  const actionRange = sheet.getRange(2, 19, lastRow - 1, 1);

  // Freeze header
  sheet.setFrozenRows(1);

  // Auto-resize all columns
  for (let c = 1; c <= 21; c++) {
    sheet.autoResizeColumn(c);
  }

  // BUY = green background
  const buyRule = SpreadsheetApp.newConditionalFormatRule()
    .whenTextEqualTo("BUY")
    .setBackground("#d4edda")
    .setFontColor("#155724")
    .setBold(true)
    .setRanges([actionRange])
    .build();

  // WATCH = yellow
  const watchRule = SpreadsheetApp.newConditionalFormatRule()
    .whenTextEqualTo("WATCH")
    .setBackground("#fff3cd")
    .setFontColor("#856404")
    .setRanges([actionRange])
    .build();

  // Score column (R = col 18) — high scores green
  const scoreRange = sheet.getRange(2, 18, lastRow - 1, 1);
  const highScore = SpreadsheetApp.newConditionalFormatRule()
    .whenNumberGreaterThanOrEqualTo(8)
    .setBackground("#d4edda")
    .setFontColor("#155724")
    .setBold(true)
    .setRanges([scoreRange])
    .build();

  // Discount column (I = col 9) — high discount green
  const discountRange = sheet.getRange(2, 9, lastRow - 1, 1);
  const highDiscount = SpreadsheetApp.newConditionalFormatRule()
    .whenTextContains("3")   // ≥30%
    .setBackground("#d4edda")
    .setRanges([discountRange])
    .build();

  sheet.setConditionalFormatRules([buyRule, watchRule, highScore, highDiscount]);

  SpreadsheetApp.getUi().alert("✅ Formatting applied!");
}
