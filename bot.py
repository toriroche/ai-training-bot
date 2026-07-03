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
# AGGRESSIVE INTRADAY SETTINGS
# =============================================
WEEKLY_BUDGET  = 100
STOP_LOSS      = 0.01
TAKE_PROFIT    = 0.02
MAX_POSITIONS  = 3
MIN_ORDER      = 1.00

# RSI Settings
RSI_PERIOD     = 10
RSI_OVERBOUGHT = 65
RSI_OVERSOLD   = 35

# Volume Settings
VOLUME_CONFIRM = 1.5

# Earnings Safety
EARNINGS_SAFE_DAYS = 5

# Screener
SCREENER_MAX = 5

# Timezone
ET = ZoneInfo("America/New_York")

# Early close dates (MM-DD format)
EARLY_CLOSE_DATES = ["07-03", "07-04", "11-28", "12-24"]

# =============================================
# WATCHLIST
# =============================================
WATCHLIST = [
    "MSFT", "AAPL", "GOOGL", "AMZN", "META",
    "NVDA", "AMD", "TSLA", "CRM", "SHOP",
    "PLTR", "SOFI", "BAC", "F",
    "AEM", "GLD",
]

# =============================================
# MARKET HOURS & TIMING
# =============================================
def is_early_close():
    return datetime.now(ET).strftime("%m-%d") in EARLY_CLOSE_DATES

def is_market_open():
    now_et  = datetime.now(ET)
    weekday = now_et.weekday()
    if weekday >= 5:
        return False, f"Market closed — {now_et.strftime('%A')} is a weekend"
    market_open  = now_et.replace(hour=9,  minute=0,  second=0, microsecond=0)
    if is_early_close():
        market_close = now_et.replace(hour=13, minute=0, second=0, microsecond=0)
    else:
        market_close = now_et.replace(hour=16, minute=30, second=0, microsecond=0)
    if now_et < market_open:
        return False, f"Market not open yet — opens 9:00am ET (now {now_et.strftime('%I:%M %p')} ET)"
    if now_et > market_close:
        close_str = "1:00pm" if is_early_close() else "4:30pm"
        return False, f"Market closed — closed {close_str} ET (now {now_et.strftime('%I:%M %p')} ET)"
    return True, f"Market OPEN — {now_et.strftime('%I:%M %p')} ET"

def is_end_of_day():
    now_et = datetime.now(ET)
    if is_early_close():
        return (now_et.hour == 12 and now_et.minute >= 30) or now_et.hour > 12
    else:
        return (now_et.hour == 15 and now_et.minute >= 30) or now_et.hour > 15

def should_send_email():
    now_et = datetime.now(ET)
    if is_early_close():
        return now_et.hour == 13  # 1-2pm ET on early close days
    else:
        return now_et.hour == 16  # 4-5pm ET on normal days

def email_window_str():
    if is_early_close():
        return "1-2pm ET (early close day)"
    else:
        return "4-5pm ET"

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

def place_fractional_order(symbol, dollars, side):
    if dollars < MIN_ORDER:
        print(f"   ⚠️ {symbol}: ${dollars:.2f} below minimum — skipping")
        return None
    return alpaca_request("POST", "/v2/orders", {
        "symbol":        symbol,
        "notional":      str(round(dollars, 2)),
        "side":          side,
        "type":          "market",
        "time_in_force": "day"
    })

def close_position_safely(symbol, market_value, unrealized_pl):
    """Close a position with error handling — fixes TSLA 403 error"""
    try:
        # Try fractional order first
        result = place_fractional_order(symbol, float(market_value), "sell")
        if result:
            return True, float(unrealized_pl)
    except Exception as e:
        if "403" in str(e) or "Forbidden" in str(e):
            try:
                # Fallback — close by shares instead of dollars
                pos = alpaca_request("DELETE", f"/v2/positions/{symbol}")
                return True, float(unrealized_pl)
            except Exception as e2:
                return False, 0
        return False, 0
    return False, 0

def close_all_positions(report):
    report.append(f"\n🔔 END OF DAY — Closing all positions")
    try:
        positions = get_positions()
        if not positions:
            report.append(f"   — No open positions to close")
            return 0
        total_pl = 0
        for pos in positions:
            symbol     = pos["symbol"]
            market_val = float(pos["market_value"])
            pl         = float(pos["unrealized_pl"])
            success, closed_pl = close_position_safely(
                symbol, market_val, pl)
            if success:
                total_pl += closed_pl
                emoji = "💰" if closed_pl >= 0 else "🛑"
                report.append(f"   {emoji} Closed {symbol}: P&L ${closed_pl:+.2f}")
            else:
                report.append(f"   ⚠️ Could not close {symbol} — check Alpaca manually")
        report.append(f"   📊 Total closed P&L: ${total_pl:+.2f}")
        return total_pl
    except Exception as e:
        report.append(f"   ⚠️ Error closing positions: {e}")
        return 0

# =============================================
# MARKET DATA
# =============================================
def get_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=3mo"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    result  = data["chart"]["result"][0]
    quotes  = result["indicators"]["quote"][0]
    closes  = [p for p in quotes["close"]  if p is not None]
    volumes = [v for v in quotes["volume"] if v is not None]
    return closes, volumes

# =============================================
# TECHNICAL INDICATORS
# =============================================
def get_ma_signal(prices):
    df = pd.DataFrame(prices, columns=["close"])
    df["short"] = df["close"].rolling(5).mean()
    df["long"]  = df["close"].rolling(15).mean()
    df["mom"]   = df["close"].pct_change(3)
    l = df.iloc[-1]
    p = df.iloc[-2]
    strength = ((l["short"] - l["long"]) / l["long"]) * 100
    if p["short"] <= p["long"] and l["short"] > l["long"] and l["mom"] > 0:
        return "BUY", round(strength, 4)
    elif p["short"] >= p["long"] and l["short"] < l["long"] and l["mom"] < 0:
        return "SELL", round(strength, 4)
    elif l["mom"] > 0.02 and l["short"] > l["long"]:
        return "BUY", round(strength, 4)
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

def get_momentum_score(prices):
    if len(prices) < 10:
        return 0
    day1  = (prices[-1] - prices[-2]) / prices[-2] * 100
    day3  = (prices[-1] - prices[-4]) / prices[-4] * 100
    day5  = (prices[-1] - prices[-6]) / prices[-6] * 100
    return round((day1 * 0.5) + (day3 * 0.3) + (day5 * 0.2), 4)

def has_upcoming_earnings(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=calendarEvents"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        events   = data.get("quoteSummary", {}).get("result", [{}])[0]
        earnings = events.get("calendarEvents", {}).get("earnings", {})
        dates    = earnings.get("earningsDate", [])
        if not dates:
            return False, "Clear"
        now_et    = datetime.now(ET)
        safe_date = now_et + timedelta(days=EARNINGS_SAFE_DAYS)
        for d in dates:
            date = datetime.fromtimestamp(d.get("raw", 0), tz=ET)
            if now_et <= date <= safe_date:
                return True, f"Earnings {date.strftime('%b %d')}"
        return False, "Clear"
    except Exception:
        return False, "Unknown"

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
        if pos_count > neg_count * 1.5:   return "POSITIVE"
        elif neg_count > pos_count * 1.5: return "NEGATIVE"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"

def full_analysis(symbol):
    prices, volumes          = get_stock_data(symbol)
    ma_signal, strength      = get_ma_signal(prices)
    rsi                      = get_rsi(prices)
    vol_confirmed, vol_ratio = get_volume_signal(volumes)
    news                     = get_news_sentiment(symbol)
    momentum                 = get_momentum_score(prices)
    price                    = prices[-1]
    score = 0
    if ma_signal == "BUY":      score += 35
    elif ma_signal == "SELL":   score -= 35
    if rsi < RSI_OVERSOLD:      score += 25
    elif rsi < 45:              score += 12
    elif rsi > RSI_OVERBOUGHT:  score -= 25
    else:                       score += 5
    if vol_confirmed:           score += 20
    else:                       score += 3
    if momentum > 1.0:          score += 15
    elif momentum > 0.5:        score += 8
    elif momentum < -1.0:       score -= 15
    elif momentum < -0.5:       score -= 8
    if news == "POSITIVE":      score += 5
    elif news == "NEGATIVE":    score -= 5
    if score >= 55:    final_signal = "BUY"
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
        "news":     news,
        "momentum": momentum,
    }

def screen_new_stocks(held, report):
    report.append(f"\n🔭 SCREENER")
    report.append(f"{'='*45}")
    candidates = set()
    for scrId in ["day_gainers", "most_actives"]:
        try:
            url = f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds={scrId}&count=10"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
            quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
            for q in quotes:
                sym = q.get("symbol", "")
                if sym and sym not in WATCHLIST and sym not in held:
                    candidates.add(sym)
            report.append(f"   📊 {scrId}: {len(quotes)} found")
        except Exception as e:
            report.append(f"   ⚠️ {scrId}: {e}")
    new_stocks = []
    for symbol in list(candidates)[:10]:
        try:
            prices, volumes      = get_stock_data(symbol)
            if len(prices) < 20: continue
            ma_signal, _         = get_ma_signal(prices)
            rsi                  = get_rsi(prices)
            vol_confirmed, _     = get_volume_signal(volumes)
            momentum             = get_momentum_score(prices)
            earnings, _          = has_upcoming_earnings(symbol)
            price                = prices[-1]
            score = 0
            if ma_signal == "BUY": score += 35
            if rsi < RSI_OVERSOLD: score += 25
            if vol_confirmed:      score += 20
            if momentum > 0.5:     score += 15
            if earnings:           score -= 50
            if score >= 55:
                new_stocks.append({"symbol": symbol, "price": price, "score": score})
                report.append(f"   🌟 {symbol} @ ${price:.2f} — Score: {score}")
        except Exception:
            continue
    if not new_stocks:
        report.append(f"   — No strong candidates")
    return [s["symbol"] for s in sorted(new_stocks, key=lambda x: x["score"], reverse=True)[:SCREENER_MAX]]

# =============================================
# EMAIL
# =============================================
def send_email(subject, report_lines, is_error=False):
    try:
        if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
            print("⚠️ Email credentials not set")
            return False
        body      = "\n".join(report_lines)
        msg       = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_ADDRESS
        msg["To"]      = EMAIL_ADDRESS
        color     = "#ff4444" if is_error else "#00ff00"
        html_body = f"""
        <html><body style="font-family:monospace;background:#0a0a0a;color:{color};padding:20px;">
            <div style="max-width:600px;margin:0 auto;background:#111;padding:20px;
                        border-radius:10px;border:1px solid {color};">
                <h2 style="color:{color};">🤖 AI Trading Bot</h2>
                <pre style="color:{color};font-size:13px;line-height:1.6;">{body}</pre>
                <hr style="border-color:{color};">
                <p style="color:#555;font-size:11px;">Paper Trading — No real money at risk</p>
            </div>
        </body></html>"""
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, EMAIL_ADDRESS, msg.as_string())
        print(f"📧 Email sent!")
        return True
    except Exception as e:
        print(f"⚠️ Email failed: {e}")
        return False

# =============================================
# MAIN BOT
# =============================================
def run():
    now_et     = datetime.now(ET)
    early_close = is_early_close()
    report     = []
    report.append(f"🤖 AI Trading Bot — Aggressive Intraday")
    report.append(f"📅 {now_et.strftime('%A %B %d, %Y')}")
    report.append(f"⏰ {now_et.strftime('%I:%M %p')} ET")
    report.append(f"{'⚠️ EARLY CLOSE DAY — Market closes 1pm ET' if early_close else '📅 Regular trading day'}")
    report.append(f"💰 Budget: ${WEEKLY_BUDGET} | Stop: {STOP_LOSS*100}% | Target: {TAKE_PROFIT*100}%")
    report.append(f"🧠 MA + RSI + Volume + Momentum + News + Earnings + Screener")
    report.append("="*45)

    # Market hours check
    market_open, market_msg = is_market_open()
    report.append(f"🕐 {market_msg}")

    if not market_open:
        report.append(f"🛑 Market closed — bot exiting")
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
        report.append(f"💼 Portfolio: ${portfolio:,.2f}")
        report.append(f"💵 Cash:      ${cash:,.2f}")
        report.append(f"📈 P&L:       ${profit:+,.2f}")
    except Exception as e:
        report.append(f"⚠️ Account error: {e}")
        print("\n".join(report))
        send_email(
            f"🚨 Bot Error — {now_et.strftime('%b %d %I:%M %p')} ET",
            report, is_error=True)
        return

    # Get positions
    try:
        positions = get_positions()
        held      = {p["symbol"]: p for p in positions}
    except Exception as e:
        report.append(f"⚠️ Positions error: {e}")
        held = {}

    # End of day — close all positions
    if is_end_of_day():
        close_time = "12:30pm" if early_close else "3:30pm"
        report.append(f"\n⏰ {close_time} ET — Closing all positions")
        close_all_positions(report)
        report.append(f"{'='*45}")
        report.append(f"✅ End of day complete")
        report.append(f"{'='*45}")
        print("\n".join(report))
        subject = f"📊 EOD Report — {now_et.strftime('%b %d')} | P&L: ${profit:+,.2f}"
        send_email(subject, report)
        return

    budget_per_stock = round(WEEKLY_BUDGET / MAX_POSITIONS, 2)
    report.append(f"📊 Per position: ${budget_per_stock:.2f}")
    report.append("="*45)

    # Screener
    screener_stocks = screen_new_stocks(held, report)
    full_watchlist  = list(WATCHLIST) + screener_stocks
    report.append(f"\n🔍 SCANNING {len(full_watchlist)} STOCKS...\n")

    buy_signals  = []
    sell_signals = []

    for symbol in full_watchlist:
        try:
            a             = full_analysis(symbol)
            emoji         = "🟢" if a["signal"] == "BUY" else "🔴" if a["signal"] == "SELL" else "⏳"
            earnings_soon, e_msg = has_upcoming_earnings(symbol)

            if a["signal"] == "BUY" and earnings_soon:
                a["signal"] = "HOLD"
                report.append(f"   ⚠️ {symbol} — BUY blocked: {e_msg}")
            else:
                report.append(f"   {emoji} {symbol} @ ${a['price']:.2f} | Score: {a['score']}/100 | Mom: {a['momentum']:+.2f}%")

            if a["signal"] == "BUY" and symbol not in held:
                buy_signals.append(a)
            elif a["signal"] == "SELL" and symbol in held:
                sell_signals.append(symbol)
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")

    # Position management with safe close
    report.append(f"\n{'='*45}")
    report.append(f"📦 POSITIONS")
    report.append(f"{'='*45}")

    for symbol, pos in held.items():
        try:
            unrealized = float(pos["unrealized_pl"])
            gain_pct   = float(pos["unrealized_plpc"])

            if gain_pct >= TAKE_PROFIT:
                success, pl = close_position_safely(
                    symbol, pos["market_value"], unrealized)
                if success:
                    report.append(f"   💰 TAKE PROFIT {symbol}: +${unrealized:.2f} ({gain_pct*100:+.2f}%)")
                else:
                    report.append(f"   ⚠️ {symbol}: Could not close — check Alpaca")

            elif gain_pct <= -STOP_LOSS:
                success, pl = close_position_safely(
                    symbol, pos["market_value"], unrealized)
                if success:
                    report.append(f"   🛑 STOP LOSS {symbol}: ${unrealized:.2f} ({gain_pct*100:+.2f}%)")
                else:
                    report.append(f"   ⚠️ {symbol}: Could not close — check Alpaca")
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
                success, pl = close_position_safely(
                    symbol, held[symbol]["market_value"],
                    held[symbol]["unrealized_pl"])
                if success:
                    report.append(f"   🔴 SOLD {symbol}")
                    sells += 1
                else:
                    report.append(f"   ⚠️ Could not sell {symbol}")
            except Exception as e:
                report.append(f"   ⚠️ {symbol}: {e}")
    if sells == 0:
        report.append(f"   — Nothing to sell")

    # Buys
    report.append(f"\n{'='*45}")
    report.append(f"📥 BUYING")
    report.append(f"{'='*45}")
    buy_signals.sort(key=lambda x: x["score"], reverse=True)
    buys = 0
    for signal in buy_signals:
        symbol = signal["symbol"]
        if len(held) + buys >= MAX_POSITIONS:
            report.append(f"   ⛔ Max positions — skipping {signal['symbol']}")
            continue
        if cash < budget_per_stock:
            report.append(f"   ⚠️ Not enough cash")
            continue
        if budget_per_stock < MIN_ORDER:
            continue
        try:
            result = place_fractional_order(symbol, budget_per_stock, "buy")
            if result:
                report.append(f"   📈 BOUGHT {symbol} @ ${signal['price']:.2f} | Score: {signal['score']} | Mom: {signal['momentum']:+.2f}%")
                cash -= budget_per_stock
                buys += 1
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")
    if buys == 0:
        report.append(f"   — No strong signals this cycle")

    # Summary
    report.append(f"\n{'='*45}")
    report.append(f"📊 SUMMARY")
    report.append(f"{'='*45}")
    report.append(f"   Scanned:  {len(full_watchlist)} stocks")
    report.append(f"   Bought:   {buys}")
    report.append(f"   Sold:     {sells}")
    report.append(f"   Held:     {len(held)}")
    report.append(f"   P&L:      ${profit:+,.2f}")
    report.append(f"{'='*45}")
    report.append(f"✅ Next run in 30 mins")
    report.append(f"{'='*45}")

    print("\n".join(report))

    # Send email at market close window
    if should_send_email():
        subject = f"📊 Daily Report — {now_et.strftime('%b %d')} | P&L: ${profit:+,.2f} | Buys: {buys} Sells: {sells}"
        send_email(subject, report)
        print(f"📧 Daily report sent!")
    else:
        print(f"📧 No email — sends {email_window_str()} (now {now_et.strftime('%I:%M %p')} ET)")

# SAFETY RULE 2 — Runs once then exits
run()
