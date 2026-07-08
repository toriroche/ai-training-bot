import urllib.request
import urllib.parse
import json
import pandas as pd
from datetime import datetime, timedelta
import os
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

# =============================================
# CONNECTIONS
# =============================================
ALPACA_KEY         = os.environ.get("ALPACA_API_KEY")
ALPACA_SECRET      = os.environ.get("ALPACA_SECRET_KEY")
ALPACA_URL         = "https://paper-api.alpaca.markets"
EMAIL_ADDRESS      = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD     = os.environ.get("EMAIL_PASSWORD")
FINNHUB_KEY        = os.environ.get("FINNHUB_API_KEY")
ALPHA_VANTAGE_KEY  = os.environ.get("ALPHA_VANTAGE_API_KEY")

# =============================================
# SETTINGS
# =============================================
WEEKLY_BUDGET      = 100
STOP_LOSS          = 0.005   # 0.5% — tighter stop
TAKE_PROFIT        = 0.01    # 1% — faster wins
MAX_POSITIONS      = 6
MIN_ORDER          = 1.00
RSI_PERIOD         = 10
RSI_OVERBOUGHT     = 60      # More aggressive
RSI_OVERSOLD       = 40      # More aggressive
VOLUME_CONFIRM     = 1.2     # Less strict
EARNINGS_SAFE_DAYS = 5
SCREENER_MAX       = 5
ET                 = ZoneInfo("America/New_York")
EARLY_CLOSE_DATES  = ["07-03", "07-04", "11-28", "12-24"]

# Rate limiting — max calls per day
YAHOO_MAX_CALLS    = 500
FINNHUB_MAX_CALLS  = 200
ALPHA_MAX_CALLS    = 100

# Call counters file
COUNTER_FILE = "/home/ubuntu/.api_counters"
SENT_FILE    = "/home/ubuntu/.bot_sent"

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
# RATE LIMITING
# =============================================
def load_counters():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(COUNTER_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") != today:
            return {"date": today, "yahoo": 0, "finnhub": 0, "alpha": 0}
        return data
    except Exception:
        return {"date": today, "yahoo": 0, "finnhub": 0, "alpha": 0}

def save_counters(counters):
    with open(COUNTER_FILE, "w") as f:
        json.dump(counters, f)

def can_call(source):
    counters = load_counters()
    limits   = {"yahoo": YAHOO_MAX_CALLS, "finnhub": FINNHUB_MAX_CALLS, "alpha": ALPHA_MAX_CALLS}
    return counters.get(source, 0) < limits.get(source, 100)

def increment_counter(source):
    counters = load_counters()
    counters[source] = counters.get(source, 0) + 1
    save_counters(counters)

# =============================================
# MARKET TIMING
# =============================================
def is_early_close():
    return datetime.now(ET).strftime("%m-%d") in EARLY_CLOSE_DATES

def get_market_close_time():
    now_et = datetime.now(ET)
    if is_early_close():
        return now_et.replace(hour=13, minute=0, second=0, microsecond=0)
    return now_et.replace(hour=16, minute=30, second=0, microsecond=0)

def is_market_open():
    now_et  = datetime.now(ET)
    weekday = now_et.weekday()
    if weekday >= 5:
        return False, f"Weekend — {now_et.strftime('%A')}"
    market_open  = now_et.replace(hour=9,  minute=0, second=0, microsecond=0)
    market_close = get_market_close_time()
    if now_et < market_open:
        return False, f"Pre-market (now {now_et.strftime('%I:%M %p')} ET)"
    if now_et >= market_close:
        close_str = "1:00pm" if is_early_close() else "4:30pm"
        return False, f"After hours — closed {close_str} ET"
    return True, f"OPEN — {now_et.strftime('%I:%M %p')} ET"

def is_pre_market():
    now_et  = datetime.now(ET)
    weekday = now_et.weekday()
    if weekday >= 5:
        return False
    pre_start = now_et.replace(hour=8,  minute=30, second=0, microsecond=0)
    pre_end   = now_et.replace(hour=9,  minute=0,  second=0, microsecond=0)
    return pre_start <= now_et < pre_end

def is_end_of_day():
    now_et       = datetime.now(ET)
    market_close = get_market_close_time()
    close_30min  = market_close - timedelta(minutes=30)
    return now_et >= close_30min

def is_overnight():
    now_et  = datetime.now(ET)
    weekday = now_et.weekday()
    if weekday >= 5:
        return True
    return now_et.hour < 8 or now_et.hour >= 21

def already_sent_today(email_type="eod"):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(f"{SENT_FILE}_{email_type}", "r") as f:
            return f.read().strip() == today
    except Exception:
        return False

def mark_sent_today(email_type="eod"):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    with open(f"{SENT_FILE}_{email_type}", "w") as f:
        f.write(today)

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

def cancel_all_orders():
    try:
        result = alpaca_request("DELETE", "/v2/orders")
        return len(result) if isinstance(result, list) else 0
    except Exception:
        return 0

def place_fractional_order(symbol, dollars, side):
    if dollars < MIN_ORDER:
        return None
    return alpaca_request("POST", "/v2/orders", {
        "symbol":        symbol,
        "notional":      str(round(dollars, 2)),
        "side":          side,
        "type":          "market",
        "time_in_force": "day"
    })

def close_position_safely(symbol, market_value, unrealized_pl):
    try:
        result = place_fractional_order(symbol, float(market_value), "sell")
        if result:
            return True, float(unrealized_pl)
    except Exception as e:
        if "403" in str(e) or "Forbidden" in str(e) or "insufficient" in str(e.lower() if hasattr(e, 'lower') else str(e)):
            try:
                alpaca_request("DELETE", f"/v2/positions/{symbol}")
                return True, float(unrealized_pl)
            except Exception:
                return False, 0
    return False, 0

def close_all_positions(report):
    report.append(f"\n🔔 Closing all positions")

    # Step 1 — Cancel all pending orders first
    cancelled = cancel_all_orders()
    report.append(f"   📋 Cancelled {cancelled} pending orders")
    time.sleep(2)  # Wait for cancellations to process

    # Step 2 — Close all positions
    try:
        positions = get_positions()
        if not positions:
            report.append(f"   — No open positions")
            return 0
        total_pl = 0
        for pos in positions:
            symbol     = pos["symbol"]
            market_val = float(pos["market_value"])
            pl         = float(pos["unrealized_pl"])
            success, closed_pl = close_position_safely(symbol, market_val, pl)
            if success:
                total_pl += closed_pl
                emoji = "💰" if closed_pl >= 0 else "🛑"
                report.append(f"   {emoji} Closed {symbol}: ${closed_pl:+.2f}")
            else:
                report.append(f"   ⚠️ Could not close {symbol} — check Alpaca")
        report.append(f"   📊 Total P&L: ${total_pl:+.2f}")
        return total_pl
    except Exception as e:
        report.append(f"   ⚠️ Error: {e}")
        return 0

# =============================================
# DATA SOURCES
# =============================================

# Yahoo Finance — Primary price data
def get_stock_data_yahoo(symbol):
    if not can_call("yahoo"):
        return None, None
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=3mo"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        result  = data["chart"]["result"][0]
        quotes  = result["indicators"]["quote"][0]
        closes  = [p for p in quotes["close"]  if p is not None]
        volumes = [v for v in quotes["volume"] if v is not None]
        increment_counter("yahoo")
        return closes, volumes
    except Exception:
        return None, None

# Alpha Vantage — Backup price data
def get_stock_data_alpha(symbol):
    if not can_call("alpha") or not ALPHA_VANTAGE_KEY:
        return None, None
    try:
        url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}&apikey={ALPHA_VANTAGE_KEY}&outputsize=compact"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        ts     = data.get("Time Series (Daily)", {})
        closes = [float(v["4. close"]) for v in list(ts.values())[:60]]
        closes.reverse()
        increment_counter("alpha")
        return closes, None
    except Exception:
        return None, None

def get_stock_data(symbol):
    # Try Yahoo first
    closes, volumes = get_stock_data_yahoo(symbol)
    if closes and len(closes) >= 20:
        return closes, volumes
    # Fallback to Alpha Vantage
    closes, volumes = get_stock_data_alpha(symbol)
    if closes and len(closes) >= 20:
        return closes, volumes or []
    return None, None

# Finnhub — News sentiment
def get_news_finnhub(symbol):
    if not can_call("finnhub") or not FINNHUB_KEY:
        return "NEUTRAL", []
    try:
        today     = datetime.now(ET)
        week_ago  = today - timedelta(days=7)
        from_date = week_ago.strftime("%Y-%m-%d")
        to_date   = today.strftime("%Y-%m-%d")
        url = f"https://finnhub.io/api/v1/company-news?symbol={symbol}&from={from_date}&to={to_date}&token={FINNHUB_KEY}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            articles = json.loads(r.read())
        increment_counter("finnhub")

        positive_words = ["surge","soar","jump","gain","beat","strong","growth","profit","record","bull","upgrade","positive","win","boost","rally","up"]
        negative_words = ["drop","fall","plunge","loss","miss","weak","decline","bear","downgrade","negative","crash","risk","warn","cut","layoff","lawsuit","down"]

        pos_count = 0
        neg_count = 0
        headlines = []
        for article in articles[:10]:
            headline = article.get("headline", "").lower()
            headlines.append(article.get("headline", ""))
            pos_count += sum(headline.count(w) for w in positive_words)
            neg_count += sum(headline.count(w) for w in negative_words)

        if pos_count > neg_count * 1.5:   return "POSITIVE", headlines[:3]
        elif neg_count > pos_count * 1.5: return "NEGATIVE", headlines[:3]
        return "NEUTRAL", headlines[:3]
    except Exception:
        return get_news_yahoo(symbol), []

# Yahoo News — Fallback
def get_news_yahoo(symbol):
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

# Finnhub — Earnings calendar
def get_earnings_finnhub(symbol):
    if not can_call("finnhub") or not FINNHUB_KEY:
        return has_upcoming_earnings_yahoo(symbol)
    try:
        today     = datetime.now(ET)
        future    = today + timedelta(days=EARNINGS_SAFE_DAYS)
        from_date = today.strftime("%Y-%m-%d")
        to_date   = future.strftime("%Y-%m-%d")
        url = f"https://finnhub.io/api/v1/calendar/earnings?from={from_date}&to={to_date}&symbol={symbol}&token={FINNHUB_KEY}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        increment_counter("finnhub")
        earnings = data.get("earningsCalendar", [])
        if earnings:
            date_str = earnings[0].get("date", "")
            return True, f"Earnings {date_str}"
        return False, "Clear"
    except Exception:
        return has_upcoming_earnings_yahoo(symbol)

def has_upcoming_earnings_yahoo(symbol):
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

# Finnhub — Market news (financial only, used internally not emailed)
def get_market_news():
    if not can_call("finnhub") or not FINNHUB_KEY:
        return []
    try:
        url = f"https://finnhub.io/api/v1/news?category=merger&token={FINNHUB_KEY}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            articles = json.loads(r.read())
        increment_counter("finnhub")
        return [a.get("headline", "") for a in articles[:10]]
    except Exception:
        return []

# Finnhub — Weekly earnings calendar
def get_weekly_earnings():
    if not can_call("finnhub") or not FINNHUB_KEY:
        return []
    try:
        today   = datetime.now(ET)
        friday  = today + timedelta(days=(4 - today.weekday()) % 7)
        url = f"https://finnhub.io/api/v1/calendar/earnings?from={today.strftime('%Y-%m-%d')}&to={friday.strftime('%Y-%m-%d')}&token={FINNHUB_KEY}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        increment_counter("finnhub")
        earnings = data.get("earningsCalendar", [])
        return [{
            "symbol": e.get("symbol"),
            "date":   e.get("date"),
            "estimate": e.get("epsEstimate"),
        } for e in earnings[:20]]
    except Exception:
        return []

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
    if not volumes or len(volumes) < 20:
        return True, 1.0
    avg_volume    = sum(volumes[-20:]) / 20
    latest_volume = volumes[-1]
    ratio         = latest_volume / avg_volume if avg_volume > 0 else 1.0
    return ratio >= VOLUME_CONFIRM, round(ratio, 2)

def get_momentum_score(prices):
    if len(prices) < 10:
        return 0
    day1 = (prices[-1] - prices[-2]) / prices[-2] * 100
    day3 = (prices[-1] - prices[-4]) / prices[-4] * 100
    day5 = (prices[-1] - prices[-6]) / prices[-6] * 100
    return round((day1 * 0.5) + (day3 * 0.3) + (day5 * 0.2), 4)

def full_analysis(symbol):
    prices, volumes          = get_stock_data(symbol)
    if not prices or len(prices) < 20:
        return None
    ma_signal, strength      = get_ma_signal(prices)
    rsi                      = get_rsi(prices)
    vol_confirmed, vol_ratio = get_volume_signal(volumes or [])
    news, headlines          = get_news_finnhub(symbol)
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
    if score >= 40:    final_signal = "BUY"
    elif score <= -20: final_signal = "SELL"
    else:              final_signal = "HOLD"
    return {
        "symbol":    symbol,
        "price":     price,
        "signal":    final_signal,
        "score":     score,
        "ma":        ma_signal,
        "rsi":       rsi,
        "volume":    vol_ratio,
        "news":      news,
        "momentum":  momentum,
        "headlines": headlines,
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
            prices, volumes  = get_stock_data(symbol)
            if not prices or len(prices) < 20: continue
            ma_signal, _     = get_ma_signal(prices)
            rsi              = get_rsi(prices)
            vol_confirmed, _ = get_volume_signal(volumes or [])
            momentum         = get_momentum_score(prices)
            earnings, _      = get_earnings_finnhub(symbol)
            price            = prices[-1]
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
        body  = "\n".join(report_lines)
        msg   = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_ADDRESS
        msg["To"]      = EMAIL_ADDRESS
        color = "#ff4444" if is_error else "#00ff00"
        html  = f"""
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
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, EMAIL_ADDRESS, msg.as_string())
        print(f"📧 Email sent: {subject}")
        return True
    except Exception as e:
        print(f"⚠️ Email failed: {e}")
        return False

# =============================================
# LEVEL 1 — PRE-MARKET BRIEFING (8:30am)
# =============================================
def send_premarket_briefing():
    if already_sent_today("premarket"):
        return
    now_et = datetime.now(ET)
    report = []
    report.append(f"🌅 PRE-MARKET BRIEFING")
    report.append(f"📅 {now_et.strftime('%A %B %d, %Y')}")
    report.append(f"⏰ {now_et.strftime('%I:%M %p')} ET — Market opens in 30 minutes")
    report.append("="*45)

    # Get earnings for today
    earnings = get_weekly_earnings()
    today_str = now_et.strftime("%Y-%m-%d")
    today_earnings = [e for e in earnings if e.get("date") == today_str]

    if today_earnings:
        report.append(f"\n⚠️ EARNINGS TODAY — Avoid these stocks:")
        for e in today_earnings:
            report.append(f"   • {e['symbol']} — Est: ${e.get('estimate', 'N/A')}")
    else:
        report.append(f"\n✅ No major earnings today")

    # Market news
    news = get_market_news()
    if news:
        report.append(f"\n📰 OVERNIGHT MARKET NEWS:")
        for headline in news[:5]:
            report.append(f"   • {headline}")

    # Pre-scan top opportunities
    report.append(f"\n🔍 TOP OPPORTUNITIES AT OPEN:")
    opportunities = []
    for symbol in WATCHLIST[:8]:
        try:
            a = full_analysis(symbol)
            if a and a["signal"] == "BUY":
                opportunities.append(a)
        except Exception:
            continue

    opportunities.sort(key=lambda x: x["score"], reverse=True)
    if opportunities:
        for opp in opportunities[:5]:
            report.append(f"   🟢 {opp['symbol']} @ ${opp['price']:.2f} | Score: {opp['score']}/100 | Mom: {opp['momentum']:+.2f}%")
    else:
        report.append(f"   — No strong BUY signals pre-market")

    report.append(f"\n{'='*45}")
    report.append(f"💰 Budget: ${WEEKLY_BUDGET} | Stop: {STOP_LOSS*100}% | Target: {TAKE_PROFIT*100}%")
    report.append(f"🔔 Bot will start trading at 9:00am ET")
    report.append(f"{'='*45}")

    if send_email(f"🌅 Pre-Market Briefing — {now_et.strftime('%b %d')}", report):
        mark_sent_today("premarket")

# =============================================
# LEVEL 2 — OVERNIGHT NEWS MONITOR
# =============================================
def send_overnight_update():
    if already_sent_today("overnight"):
        return
    now_et = datetime.now(ET)
    if now_et.hour != 0:  # Only at midnight
        return
    report = []
    report.append(f"🌙 OVERNIGHT MARKET UPDATE")
    report.append(f"📅 {now_et.strftime('%A %B %d, %Y — %I:%M %p')} ET")
    report.append("="*45)

    news = get_market_news()
    if news:
        report.append(f"\n📰 LATEST MARKET NEWS:")
        for headline in news[:8]:
            report.append(f"   • {headline}")
    else:
        report.append(f"\n   — No major news at this time")

    # Check watchlist news
    report.append(f"\n📊 WATCHLIST NEWS SCAN:")
    for symbol in ["NVDA", "AAPL", "MSFT", "GOOGL", "META"]:
        try:
            sentiment, headlines = get_news_finnhub(symbol)
            if sentiment != "NEUTRAL" and headlines:
                emoji = "🟢" if sentiment == "POSITIVE" else "🔴"
                report.append(f"   {emoji} {symbol} — {sentiment}")
                report.append(f"      {headlines[0][:80]}...")
        except Exception:
            continue

    report.append(f"\n{'='*45}")
    report.append(f"💤 Bot continues monitoring overnight")
    report.append(f"🌅 Pre-market briefing at 8:30am ET")
    report.append(f"{'='*45}")

    if send_email(f"🌙 Overnight Update — {now_et.strftime('%b %d')}", report):
        mark_sent_today("overnight")

# =============================================
# LEVEL 3 — WEEKEND RESEARCH
# =============================================
def send_weekend_research():
    now_et  = datetime.now(ET)
    weekday = now_et.weekday()

    # Saturday — weekly recap
    if weekday == 5 and not already_sent_today("weekend_sat"):
        report = []
        report.append(f"📊 WEEKEND RESEARCH — Saturday Recap")
        report.append(f"📅 {now_et.strftime('%B %d, %Y')}")
        report.append("="*45)

        # Weekly earnings coming up
        earnings = get_weekly_earnings()
        if earnings:
            report.append(f"\n📅 EARNINGS NEXT WEEK:")
            for e in earnings[:10]:
                report.append(f"   • {e['symbol']} — {e['date']} | Est: ${e.get('estimate', 'N/A')}")

        # Market news recap
        news = get_market_news()
        report.append(f"\n📰 MARKET HEADLINES:")
        for headline in news[:5]:
            report.append(f"   • {headline}")

        report.append(f"\n{'='*45}")
        report.append(f"📋 Full Monday strategy coming Sunday")
        report.append(f"{'='*45}")

        if send_email(f"📊 Weekend Research — {now_et.strftime('%b %d')}", report):
            mark_sent_today("weekend_sat")

    # Sunday — Monday strategy
    elif weekday == 6 and not already_sent_today("weekend_sun"):
        report = []
        report.append(f"🗓️ MONDAY MORNING STRATEGY")
        report.append(f"📅 {now_et.strftime('%B %d, %Y')} — Preparing for Monday open")
        report.append("="*45)

        # Earnings to avoid Monday
        earnings = get_weekly_earnings()
        monday   = (now_et + timedelta(days=1)).strftime("%Y-%m-%d")
        monday_earnings = [e for e in earnings if e.get("date") == monday]

        if monday_earnings:
            report.append(f"\n⚠️ MONDAY EARNINGS — Bot will avoid:")
            for e in monday_earnings:
                report.append(f"   🚫 {e['symbol']} — reporting Monday")

        # Pre-scan for Monday opportunities
        report.append(f"\n🎯 STOCKS TO WATCH MONDAY:")
        opportunities = []
        for symbol in WATCHLIST:
            try:
                a = full_analysis(symbol)
                if a and a["score"] >= 40:
                    opportunities.append(a)
            except Exception:
                continue

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        for opp in opportunities[:8]:
            emoji = "🟢" if opp["signal"] == "BUY" else "⏳"
            report.append(f"   {emoji} {opp['symbol']} @ ${opp['price']:.2f} | Score: {opp['score']}/100")

        report.append(f"\n{'='*45}")
        report.append(f"💰 Budget: ${WEEKLY_BUDGET} ready to deploy")
        report.append(f"🔔 Bot starts trading Monday 9:00am ET")
        report.append(f"{'='*45}")

        if send_email(f"🗓️ Monday Strategy — {now_et.strftime('%b %d')}", report):
            mark_sent_today("weekend_sun")

# =============================================
# MAIN BOT
# =============================================
def run():
    now_et      = datetime.now(ET)
    weekday     = now_et.weekday()
    early_close = is_early_close()

    # ── WEEKEND — silent intel gathering, NO emails ──
    if weekday >= 5:
        print(f"Weekend — bot monitoring silently. No email until weekday close.")
        get_weekly_earnings()  # Gather data silently
        return

    # ── OVERNIGHT WEEKDAY — silent monitoring ──
    if is_overnight():
        print(f"Overnight — bot monitoring silently")
        return

    # ── MARKET IS CLOSED (after hours) ───────
    market_open, market_msg = is_market_open()

    if not market_open and now_et.hour >= 9:
        if already_sent_today("eod"):
            print(f"📧 EOD email already sent today")
            return

        report = []
        report.append(f"🤖 AI Trading Bot — End of Day Report")
        report.append(f"📅 {now_et.strftime('%A %B %d, %Y')}")
        report.append(f"⏰ {now_et.strftime('%I:%M %p')} ET")
        report.append(f"{'⚠️ EARLY CLOSE DAY' if early_close else '📅 Regular day'}")
        report.append("="*45)
        report.append(f"🕐 {market_msg}")

        profit    = 0
        portfolio = 100000
        try:
            account   = get_account()
            portfolio = float(account["portfolio_value"])
            cash      = float(account["cash"])
            profit    = portfolio - 100000
            report.append(f"💼 Portfolio: ${portfolio:,.2f}")
            report.append(f"💵 Cash:      ${cash:,.2f}")
            report.append(f"📈 Total P&L: ${profit:+,.2f}")
        except Exception as e:
            report.append(f"⚠️ Account error: {e}")

        # API usage summary
        counters = load_counters()
        report.append(f"\n📊 API USAGE TODAY:")
        report.append(f"   Yahoo Finance:  {counters.get('yahoo', 0)}/{YAHOO_MAX_CALLS} calls")
        report.append(f"   Finnhub:        {counters.get('finnhub', 0)}/{FINNHUB_MAX_CALLS} calls")
        report.append(f"   Alpha Vantage:  {counters.get('alpha', 0)}/{ALPHA_MAX_CALLS} calls")

        report.append(f"\n{'='*45}")
        report.append(f"✅ Market closed — see you tomorrow!")
        report.append(f"🌅 Pre-market briefing at 8:30am ET")
        report.append(f"{'='*45}")

        print("\n".join(report))
        subject = f"📊 EOD Report — {now_et.strftime('%b %d')} | P&L: ${profit:+,.2f}"
        if send_email(subject, report):
            mark_sent_today("eod")
        return

    # ── MARKET IS OPEN ────────────────────────
    report = []
    report.append(f"🤖 AI Trading Bot — Aggressive Intraday")
    report.append(f"📅 {now_et.strftime('%A %B %d, %Y')}")
    report.append(f"⏰ {now_et.strftime('%I:%M %p')} ET")
    report.append(f"{'⚠️ EARLY CLOSE — 1pm ET' if early_close else '📅 Regular trading day'}")
    report.append(f"💰 Budget: ${WEEKLY_BUDGET} | Stop: {STOP_LOSS*100}% | Target: {TAKE_PROFIT*100}%")
    report.append(f"🧠 MA + RSI + Volume + Momentum + Finnhub + Earnings + Screener")
    report.append("="*45)
    report.append(f"🕐 Market {market_msg}")
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
        send_email(f"🚨 Bot Error — {now_et.strftime('%b %d %I:%M %p')} ET", report, is_error=True)
        return

    # Get positions
    try:
        positions = get_positions()
        held      = {p["symbol"]: p for p in positions}
    except Exception as e:
        report.append(f"⚠️ Positions error: {e}")
        held = {}

    # End of day — cancel orders + close positions
    if is_end_of_day():
        close_time = "12:30pm" if early_close else "3:30pm"
        report.append(f"\n⏰ {close_time} ET — End of day")
        close_all_positions(report)
        report.append(f"{'='*45}")
        report.append(f"✅ Positions closed — awaiting market close")
        report.append(f"{'='*45}")
        print("\n".join(report))
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
            a = full_analysis(symbol)
            if not a:
                continue
            emoji = "🟢" if a["signal"] == "BUY" else "🔴" if a["signal"] == "SELL" else "⏳"
            earnings_soon, e_msg = get_earnings_finnhub(symbol)
            if a["signal"] == "BUY" and earnings_soon:
                a["signal"] = "HOLD"
                report.append(f"   ⚠️ {symbol} — BUY blocked: {e_msg}")
            else:
                report.append(f"   {emoji} {symbol} @ ${a['price']:.2f} | Score: {a['score']}/100 | Mom: {a['momentum']:+.2f}% | News: {a['news']}")
            if a["signal"] == "BUY" and symbol not in held:
                buy_signals.append(a)
            elif a["signal"] == "SELL" and symbol in held:
                sell_signals.append(symbol)
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")

    # Position management
    report.append(f"\n{'='*45}")
    report.append(f"📦 POSITIONS")
    report.append(f"{'='*45}")

    for symbol, pos in held.items():
        try:
            unrealized = float(pos["unrealized_pl"])
            gain_pct   = float(pos["unrealized_plpc"])
            if gain_pct >= TAKE_PROFIT:
                success, pl = close_position_safely(symbol, pos["market_value"], unrealized)
                if success:
                    report.append(f"   💰 TAKE PROFIT {symbol}: +${unrealized:.2f} ({gain_pct*100:+.2f}%)")
                else:
                    report.append(f"   ⚠️ {symbol}: Could not close — check Alpaca")
            elif gain_pct <= -STOP_LOSS:
                success, pl = close_position_safely(symbol, pos["market_value"], unrealized)
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
            report.append(f"   ⛔ Max positions — skipping {symbol}")
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
    report.append(f"✅ Next run in 10 mins")
    report.append(f"{'='*45}")

    print("\n".join(report))

# Run once then exit
run()
