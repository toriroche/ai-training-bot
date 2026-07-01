import urllib.request
import json
import pandas as pd
from datetime import datetime, timedelta
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

# =============================================
# ALPACA CONNECTION
# =============================================
ALPACA_KEY    = os.environ.get("ALPACA_API_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY")
ALPACA_URL    = "https://paper-api.alpaca.markets"

# =============================================
# EMAIL SETTINGS
# =============================================
EMAIL_ADDRESS  = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")

# =============================================
# WEEKLY TEST BUDGET — ONLY CHANGE THIS
# =============================================
WEEKLY_BUDGET  = 100
STOP_LOSS      = 0.02
TAKE_PROFIT    = 0.04
MAX_POSITIONS  = 3
MIN_ORDER      = 1.00

# RSI Settings
RSI_PERIOD     = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD   = 30

# Volume Settings
VOLUME_CONFIRM = 1.2

# Earnings Safety — avoid buying X days before earnings
EARNINGS_SAFE_DAYS = 5

# Stock Screener — max new stocks to add per run
SCREENER_MAX = 5

# Timezone
ET = ZoneInfo("America/New_York")

# =============================================
# CORE WATCHLIST — Always monitored
# =============================================
WATCHLIST = [
    "MSFT", "AAPL", "GOOGL", "AMZN", "META",
    "NVDA", "AMD", "TSLA", "CRM", "SHOP",
    "PLTR", "SOFI", "BAC", "F",
    "AEM", "GLD",
]

# =============================================
# SAFETY RULE 1 — MARKET HOURS FILTER (ET)
# Extended: 9:00am to 4:30pm ET
# =============================================
def is_market_open():
    now_et  = datetime.now(ET)
    weekday = now_et.weekday()
    if weekday >= 5:
        return False, f"Market closed — {now_et.strftime('%A')} is a weekend"
    market_open  = now_et.replace(hour=9,  minute=0,  second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=30, second=0, microsecond=0)
    if now_et < market_open:
        return False, f"Market not open yet — opens 9:00am ET (now {now_et.strftime('%I:%M %p')} ET)"
    if now_et > market_close:
        return False, f"Market closed for the day — closed 4:30pm ET (now {now_et.strftime('%I:%M %p')} ET)"
    return True, f"Market OPEN — {now_et.strftime('%I:%M %p')} ET"

# =============================================
# ALPACA API
# =============================================
def alpaca_request(method, endpoint, data=None):
    url = f"{ALPACA_URL}{endpoint}"
    req = urllib.request.Request(url, method=method)
    req.add_header("APCA-API-KEY-ID", ALPACA_KEY)
    req.add_header("APCA-API-SECRET-KEY", ALPACA_SECRET)
    req.add_header("Content-Type", "application/json")
    if data:
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def get_account():
    return alpaca_request("GET", "/v2/account")

def get_positions():
    return alpaca_request("GET", "/v2/positions")

# SAFETY RULE 3 — Minimum $1.00 notional
def place_fractional_order(symbol, dollars, side):
    if dollars < MIN_ORDER:
        print(f"   ⚠️ {symbol}: ${dollars:.2f} below minimum ${MIN_ORDER:.2f} — skipping")
        return None
    return alpaca_request("POST", "/v2/orders", {
        "symbol":        symbol,
        "notional":      str(round(dollars, 2)),
        "side":          side,
        "type":          "market",
        "time_in_force": "day"
    })

# =============================================
# MARKET DATA
# =============================================
def get_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=6mo"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    result  = data["chart"]["result"][0]
    quotes  = result["indicators"]["quote"][0]
    closes  = [p for p in quotes["close"]  if p is not None]
    volumes = [v for v in quotes["volume"] if v is not None]
    return closes, volumes

# =============================================
# EARNINGS AWARENESS
# Checks if stock has earnings in next 5 days
# Uses Yahoo Finance earnings calendar — free
# =============================================
def has_upcoming_earnings(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=calendarEvents"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())

        events = data.get("quoteSummary", {}).get("result", [{}])[0]
        earnings = events.get("calendarEvents", {}).get("earnings", {})
        dates = earnings.get("earningsDate", [])

        if not dates:
            return False, "No earnings date found"

        now_et    = datetime.now(ET)
        safe_date = now_et + timedelta(days=EARNINGS_SAFE_DAYS)

        for d in dates:
            raw  = d.get("raw", 0)
            date = datetime.fromtimestamp(raw, tz=ET)
            if now_et <= date <= safe_date:
                return True, f"Earnings on {date.strftime('%b %d')}"

        return False, "No earnings in next 5 days"

    except Exception:
        return False, "Earnings check unavailable"

# =============================================
# STOCK SCREENER
# Finds new opportunities beyond watchlist
# Checks S&P 500 top movers + high volume
# =============================================
def screen_new_stocks(held, report):
    report.append(f"\n🔭 STOCK SCREENER — Finding New Opportunities")
    report.append(f"{'='*45}")

    candidates = set()

    # Source 1 — S&P 500 Top Movers via Yahoo Finance
    try:
        movers_url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=day_gainers&count=10"
        req = urllib.request.Request(movers_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
        for q in quotes:
            sym = q.get("symbol", "")
            if sym and sym not in WATCHLIST and sym not in held:
                candidates.add(sym)
        report.append(f"   📈 Top movers found: {len(quotes)} stocks")
    except Exception as e:
        report.append(f"   ⚠️ Top movers unavailable: {e}")

    # Source 2 — High Volume Stocks via Yahoo Finance
    try:
        volume_url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=most_actives&count=10"
        req = urllib.request.Request(volume_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
        for q in quotes:
            sym = q.get("symbol", "")
            if sym and sym not in WATCHLIST and sym not in held:
                candidates.add(sym)
        report.append(f"   📊 High volume found: {len(quotes)} stocks")
    except Exception as e:
        report.append(f"   ⚠️ High volume unavailable: {e}")

    # Analyze candidates
    new_stocks = []
    report.append(f"   🔍 Analyzing {len(candidates)} candidates...\n")

    for symbol in list(candidates)[:10]:
        try:
            prices, volumes = get_stock_data(symbol)
            if len(prices) < 20:
                continue

            ma_signal, strength = get_ma_signal(prices)
            rsi                 = get_rsi(prices)
            vol_confirmed, ratio = get_volume_signal(volumes)
            earnings, e_msg     = has_upcoming_earnings(symbol)
            price               = prices[-1]

            # Score
            score = 0
            if ma_signal == "BUY":   score += 40
            if rsi < RSI_OVERSOLD:   score += 30
            elif rsi < 50:           score += 15
            if vol_confirmed:        score += 20
            if earnings:             score -= 50  # Heavy penalty

            if score >= 60:
                new_stocks.append({
                    "symbol":   symbol,
                    "price":    price,
                    "score":    score,
                    "rsi":      rsi,
                    "earnings": earnings,
                    "e_msg":    e_msg,
                })
                report.append(f"   🌟 {symbol} @ ${price:.2f} — Score: {score}/100 | RSI: {rsi} | {e_msg}")

        except Exception:
            continue

    if not new_stocks:
        report.append(f"   — No strong candidates found this cycle")
    else:
        report.append(f"\n   ✅ {len(new_stocks)} new stock(s) added to this cycle's watchlist")

    return [s["symbol"] for s in sorted(new_stocks, key=lambda x: x["score"], reverse=True)[:SCREENER_MAX]]

# =============================================
# TECHNICAL INDICATORS
# =============================================
def get_ma_signal(prices):
    df = pd.DataFrame(prices, columns=["close"])
    df["short"] = df["close"].rolling(5).mean()
    df["long"]  = df["close"].rolling(15).mean()
    l = df.iloc[-1]
    p = df.iloc[-2]
    strength = ((l["short"] - l["long"]) / l["long"]) * 100
    if p["short"] <= p["long"] and l["short"] > l["long"]:
        return "BUY", round(strength, 4)
    elif p["short"] >= p["long"] and l["short"] < l["long"]:
        return "SELL", round(strength, 4)
    return "HOLD", round(strength, 4)

def get_rsi(prices):
    df       = pd.DataFrame(prices, columns=["close"])
    delta    = df["close"].diff()
    gain     = delta.where(delta > 0, 0)
    loss     = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(RSI_PERIOD).mean()
    avg_loss = loss.rolling(RSI_PERIOD).mean()
    rs       = avg_gain / avg_loss
    rsi      = 100 - (100 / (1 + rs))
    return round(rsi.iloc[-1], 2)

def get_volume_signal(volumes):
    if len(volumes) < 20:
        return True, 1.0
    avg_volume    = sum(volumes[-20:]) / 20
    latest_volume = volumes[-1]
    ratio         = latest_volume / avg_volume if avg_volume > 0 else 1.0
    return ratio >= VOLUME_CONFIRM, round(ratio, 2)

# SAFETY RULE 4 — News API Failsafe
def get_news_sentiment(symbol):
    try:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            content = r.read().decode("utf-8")
        positive_words = ["surge","soar","jump","gain","rise","up","high","beat","strong","growth","profit","record","bull","rally","buy","upgrade","positive","win","boost"]
        negative_words = ["drop","fall","plunge","loss","down","low","miss","weak","decline","bear","sell","downgrade","negative","crash","risk","warn","cut","layoff","lawsuit"]
        content_lower = content.lower()
        pos_count = sum(content_lower.count(w) for w in positive_words)
        neg_count = sum(content_lower.count(w) for w in negative_words)
        if pos_count > neg_count * 1.5:
            return "POSITIVE", pos_count, neg_count
        elif neg_count > pos_count * 1.5:
            return "NEGATIVE", pos_count, neg_count
        return "NEUTRAL", pos_count, neg_count
    except Exception:
        return "NEUTRAL", 0, 0

def full_analysis(symbol):
    prices, volumes          = get_stock_data(symbol)
    ma_signal, strength      = get_ma_signal(prices)
    rsi                      = get_rsi(prices)
    vol_confirmed, vol_ratio = get_volume_signal(volumes)
    news_sentiment, pos, neg = get_news_sentiment(symbol)
    price                    = prices[-1]
    score = 0
    if ma_signal == "BUY":       score += 40
    elif ma_signal == "SELL":    score -= 40
    if rsi < RSI_OVERSOLD:       score += 30
    elif rsi < 50:               score += 15
    elif rsi > RSI_OVERBOUGHT:   score -= 30
    else:                        score += 5
    if vol_confirmed:            score += 20
    else:                        score += 5
    if news_sentiment == "POSITIVE":  score += 10
    elif news_sentiment == "NEGATIVE": score -= 10
    if score >= 60:   final_signal = "BUY"
    elif score <= -20: final_signal = "SELL"
    else:              final_signal = "HOLD"
    return {
        "symbol":   symbol,
        "price":    price,
        "signal":   final_signal,
        "score":    score,
        "ma":       ma_signal,
        "rsi":      rsi,
        "volume":   vol_ratio,
        "news":     news_sentiment,
        "strength": strength,
    }

# =============================================
# EMAIL REPORT
# =============================================
def send_email(subject, report_lines):
    try:
        if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
            print("⚠️ Email credentials not set — skipping email")
            return False
        body      = "\n".join(report_lines)
        msg       = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_ADDRESS
        msg["To"]      = EMAIL_ADDRESS
        text_part = MIMEText(body, "plain")
        html_body = f"""
        <html>
        <body style="font-family:monospace;background:#0a0a0a;color:#00ff00;padding:20px;">
            <div style="max-width:600px;margin:0 auto;background:#111;padding:20px;
                        border-radius:10px;border:1px solid #00ff00;">
                <h2 style="color:#00ff00;">🤖 AI Trading Bot — Daily Report</h2>
                <pre style="color:#00ff00;font-size:13px;line-height:1.6;">{body}</pre>
                <hr style="border-color:#00ff00;">
                <p style="color:#555;font-size:11px;">
                    Sent automatically by your AI Trading Bot<br>
                    Paper Trading — No real money at risk
                </p>
            </div>
        </body>
        </html>"""
        html_part = MIMEText(html_body, "html")
        msg.attach(text_part)
        msg.attach(html_part)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, EMAIL_ADDRESS, msg.as_string())
        print(f"📧 Daily report sent to {EMAIL_ADDRESS}")
        return True
    except Exception as e:
        print(f"⚠️ Email failed: {e}")
        return False

# =============================================
# MAIN BOT — SAFETY RULE 2: Runs once exits
# =============================================
def run():
    now_et = datetime.now(ET)
    report = []
    report.append(f"🤖 AI Trading Bot — Daily Report")
    report.append(f"📅 {now_et.strftime('%A %B %d, %Y')}")
    report.append(f"⏰ Generated: {now_et.strftime('%I:%M %p')} ET")
    report.append(f"💰 Weekly Budget: ${WEEKLY_BUDGET:,}")
    report.append(f"👁 Core watchlist: {len(WATCHLIST)} stocks")
    report.append(f"🧠 MA + RSI + Volume + News + Earnings + Screener")
    report.append("="*45)

    # SAFETY RULE 1 — Market hours
    market_open, market_msg = is_market_open()
    report.append(f"🕐 {market_msg}")

    if not market_open:
        report.append(f"🛑 Bot exiting — market is closed")
        report.append(f"{'='*45}")
        print("\n".join(report))
        return

    report.append("="*45)

    # Get account
    account = None
    profit  = 0
    try:
        account   = get_account()
        portfolio = float(account["portfolio_value"])
        cash      = float(account["cash"])
        profit    = portfolio - 100000
        report.append(f"💼 Portfolio Value: ${portfolio:,.2f}")
        report.append(f"💵 Cash Available:  ${cash:,.2f}")
        report.append(f"📈 Total P&L:       ${profit:+,.2f}")
    except Exception as e:
        report.append(f"⚠️ Account error: {e}")
        print("\n".join(report))
        return

    # Get positions
    try:
        positions = get_positions()
        held      = {p["symbol"]: p for p in positions}
    except Exception as e:
        report.append(f"⚠️ Positions error: {e}")
        held = {}

    budget_per_stock = round(WEEKLY_BUDGET / MAX_POSITIONS, 2)
    report.append(f"📊 Per position:    ${budget_per_stock:.2f}")
    report.append(f"🛡 Min order:       ${MIN_ORDER:.2f}")
    report.append(f"📅 Earnings buffer: {EARNINGS_SAFE_DAYS} days")
    report.append("="*45)

    # Run stock screener to find new opportunities
    screener_stocks = screen_new_stocks(held, report)

    # Combine watchlist + screener results
    full_watchlist = list(WATCHLIST) + screener_stocks
    report.append(f"\n🔍 SCANNING {len(full_watchlist)} STOCKS ({len(WATCHLIST)} core + {len(screener_stocks)} screener)...\n")

    buy_signals  = []
    sell_signals = []

    for symbol in full_watchlist:
        try:
            a         = full_analysis(symbol)
            rsi_label = "oversold 🟢" if a["rsi"] < RSI_OVERSOLD else "overbought 🔴" if a["rsi"] > RSI_OVERBOUGHT else "normal ⚪"
            vol_label = "✅ confirmed" if a["volume"] >= VOLUME_CONFIRM else "⚠️ low"
            emoji     = "🟢" if a["signal"] == "BUY" else "🔴" if a["signal"] == "SELL" else "⏳"

            # Earnings check before buying
            earnings_soon, e_msg = has_upcoming_earnings(symbol)
            if a["signal"] == "BUY" and earnings_soon:
                a["signal"] = "HOLD"
                report.append(f"   ⚠️ {symbol} @ ${a['price']:.2f} — BUY blocked: {e_msg}")
            else:
                report.append(f"   {emoji} {symbol} @ ${a['price']:.2f}")
                report.append(f"      Score: {a['score']}/100 | Signal: {a['signal']}")
                report.append(f"      MA: {a['ma']} | RSI: {a['rsi']} ({rsi_label})")
                report.append(f"      Volume: {a['volume']}x avg ({vol_label})")
                report.append(f"      News: {a['news']} 📰")
                report.append(f"      Earnings: {e_msg} 📅")
                report.append("")

            if a["signal"] == "BUY" and symbol not in held:
                buy_signals.append(a)
            elif a["signal"] == "SELL" and symbol in held:
                sell_signals.append(symbol)

        except Exception as e:
            report.append(f"   ⚠️ {symbol}: Error — {e}")

    # Position management
    report.append(f"{'='*45}")
    report.append(f"📦 POSITION MANAGEMENT")
    report.append(f"{'='*45}")

    for symbol, pos in held.items():
        try:
            unrealized = float(pos["unrealized_pl"])
            gain_pct   = float(pos["unrealized_plpc"])
            if gain_pct >= TAKE_PROFIT:
                result = place_fractional_order(symbol, float(pos["market_value"]), "sell")
                if result:
                    report.append(f"   💰 TAKE PROFIT {symbol}: +${unrealized:.2f} ({gain_pct*100:+.2f}%)")
            elif gain_pct <= -STOP_LOSS:
                result = place_fractional_order(symbol, float(pos["market_value"]), "sell")
                if result:
                    report.append(f"   🛑 STOP LOSS {symbol}: ${unrealized:.2f} ({gain_pct*100:+.2f}%)")
            else:
                report.append(f"   📦 {symbol}: ${unrealized:+.2f} ({gain_pct*100:+.2f}%) — holding")
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")

    # Sells
    report.append(f"\n{'='*45}")
    report.append(f"📤 SELLING")
    report.append(f"{'='*45}")
    sells = 0
    for symbol in sell_signals:
        if symbol in held:
            try:
                result = place_fractional_order(symbol, float(held[symbol]["market_value"]), "sell")
                if result:
                    report.append(f"   🔴 SOLD {symbol}")
                    sells += 1
            except Exception as e:
                report.append(f"   ⚠️ {symbol}: {e}")
    if sells == 0:
        report.append(f"   — Nothing to sell this cycle")

    # Buys
    report.append(f"\n{'='*45}")
    report.append(f"📥 BUYING")
    report.append(f"{'='*45}")
    buy_signals.sort(key=lambda x: x["score"], reverse=True)
    buys = 0
    for signal in buy_signals:
        symbol = signal["symbol"]
        if len(held) + buys >= MAX_POSITIONS:
            report.append(f"   ⛔ Max positions — skipping {symbol}")
            continue
        if cash < budget_per_stock:
            report.append(f"   ⚠️ Not enough cash for {symbol}")
            continue
        if budget_per_stock < MIN_ORDER:
            report.append(f"   ⚠️ ${budget_per_stock:.2f} below minimum — skipping {symbol}")
            continue
        try:
            result = place_fractional_order(symbol, budget_per_stock, "buy")
            if result:
                report.append(f"   📈 BOUGHT {symbol} @ ${signal['price']:.2f}")
                report.append(f"   📈 ${budget_per_stock:.2f} | Score: {signal['score']}/100 | RSI: {signal['rsi']} | News: {signal['news']}")
                cash -= budget_per_stock
                buys += 1
        except Exception as e:
            report.append(f"   ⚠️ Buy error {symbol}: {e}")
    if buys == 0:
        report.append(f"   — No strong BUY signals this cycle")

    # Final summary
    report.append(f"\n{'='*45}")
    report.append(f"📊 END OF DAY SUMMARY")
    report.append(f"{'='*45}")
    report.append(f"   Core stocks:      {len(WATCHLIST)}")
    report.append(f"   Screener finds:   {len(screener_stocks)}")
    report.append(f"   Total scanned:    {len(full_watchlist)}")
    report.append(f"   BUY signals:      {len(buy_signals)}")
    report.append(f"   Buys executed:    {buys}")
    report.append(f"   Sells executed:   {sells}")
    report.append(f"   Positions held:   {len(held)}")
    report.append(f"   Total P&L:        ${profit:+,.2f}")
    report.append(f"{'='*45}")
    report.append(f"✅ See you tomorrow!")
    report.append(f"{'='*45}")

    # Print to logs
    print("\n".join(report))

    # Send ONE email at 4:00pm ET run only
    if now_et.hour == 16 and now_et.minute == 0:
        subject = f"📊 Daily Bot Report — {now_et.strftime('%b %d')} | P&L: ${profit:+,.2f} | Buys: {buys} Sells: {sells}"
        send_email(subject, report)
        print(f"📧 End of day report sent!")
    else:
        print(f"📧 No email this run — sends at 4:00pm ET (now {now_et.strftime('%I:%M %p')} ET)")

# SAFETY RULE 2 — Runs exactly once then exits
run()
