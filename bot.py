import urllib.request
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
ALPACA_KEY        = os.environ.get("ALPACA_API_KEY")
ALPACA_SECRET     = os.environ.get("ALPACA_SECRET_KEY")
ALPACA_URL        = "https://paper-api.alpaca.markets"
EMAIL_ADDRESS     = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD    = os.environ.get("EMAIL_PASSWORD")
FINNHUB_KEY       = os.environ.get("FINNHUB_API_KEY")
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY")

# =============================================
# SETTINGS
# =============================================
WEEKLY_BUDGET      = 100
TAKE_PROFIT        = 0.0075   # 0.75%
STOP_LOSS          = 0.003    # 0.3%
DAILY_LOSS_LIMIT   = 2.00     # Stop trading if down $2
MAX_POSITIONS      = 5
MIN_ORDER          = 1.00
MIN_MOMENTUM       = 0.003    # 0.3% move to trigger scalp
EARNINGS_SAFE_DAYS = 5
SCREENER_MAX       = 5
ET                 = ZoneInfo("America/New_York")
EARLY_CLOSE_DATES  = ["07-03", "07-04", "11-28", "12-24"]

# File paths
SENT_FILE    = "/home/ubuntu/.bot_sent"
ORB_FILE     = "/home/ubuntu/.orb_ranges"
TRADES_FILE  = "/home/ubuntu/.bot_trades"
LOSS_FILE    = "/home/ubuntu/.bot_daily_loss"
EOD_DONE     = "/home/ubuntu/.bot_eod_done"

# =============================================
# WATCHLIST
# =============================================
WATCHLIST = [
    "MSFT", "AAPL", "GOOGL", "AMZN", "META",
    "NVDA", "AMD", "TSLA", "CRM", "SHOP",
    "PLTR", "SOFI", "BAC", "F", "AEM", "GLD",
]

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

def is_end_of_day():
    now_et       = datetime.now(ET)
    market_close = get_market_close_time()
    return now_et >= market_close - timedelta(minutes=15)

def eod_close_already_done():
    """Prevent EOD close from running multiple times"""
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(EOD_DONE, "r") as f:
            return f.read().strip() == today
    except Exception:
        return False

def mark_eod_done():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    with open(EOD_DONE, "w") as f:
        f.write(today)

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
# DAILY LOSS TRACKING
# =============================================
def get_daily_loss():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(LOSS_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") != today:
            return 0.0
        return data.get("loss", 0.0)
    except Exception:
        return 0.0

def add_daily_loss(amount):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    loss  = get_daily_loss() + abs(amount)
    with open(LOSS_FILE, "w") as f:
        json.dump({"date": today, "loss": round(loss, 4)}, f)
    return loss

def daily_loss_exceeded():
    return get_daily_loss() >= DAILY_LOSS_LIMIT

# =============================================
# TRADE LOG
# =============================================
def log_trade(symbol, action, price, amount, pl=0, strategy=""):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    now   = datetime.now(ET).strftime("%I:%M %p")
    try:
        with open(TRADES_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") != today:
            data = {"date": today, "trades": []}
    except Exception:
        data = {"date": today, "trades": []}
    data["trades"].append({
        "time":     now,
        "symbol":   symbol,
        "action":   action,
        "price":    round(price, 2),
        "amount":   round(amount, 2),
        "pl":       round(pl, 4),
        "strategy": strategy,
    })
    with open(TRADES_FILE, "w") as f:
        json.dump(data, f)

def get_todays_trades():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(TRADES_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") != today:
            return []
        return data.get("trades", [])
    except Exception:
        return []

# =============================================
# ALPACA API
# =============================================
def alpaca_request(method, endpoint, data=None, retries=3):
    """Alpaca API with automatic retry on timeout"""
    url        = f"{ALPACA_URL}{endpoint}"
    last_error = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, method=method)
            req.add_header("APCA-API-KEY-ID", ALPACA_KEY)
            req.add_header("APCA-API-SECRET-KEY", ALPACA_SECRET)
            req.add_header("Content-Type", "application/json")
            if data:
                req.data = json.dumps(data).encode()
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                wait = (attempt + 1) * 5  # 5s, 10s, 15s
                print(f"⚠️ Alpaca attempt {attempt+1} failed — "
                      f"retrying in {wait}s...")
                time.sleep(wait)
    raise last_error

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

def place_order(symbol, dollars, side):
    if dollars < MIN_ORDER:
        return None
    return alpaca_request("POST", "/v2/orders", {
        "symbol":        symbol,
        "notional":      str(round(dollars, 2)),
        "side":          side,
        "type":          "market",
        "time_in_force": "day",
    })

def close_position_safely(symbol, market_value, unrealized_pl):
    try:
        result = place_order(symbol, float(market_value), "sell")
        if result:
            return True, float(unrealized_pl)
    except Exception as e:
        err = str(e).lower()
        if "403" in err or "forbidden" in err or "insufficient" in err:
            try:
                alpaca_request("DELETE", f"/v2/positions/{symbol}")
                return True, float(unrealized_pl)
            except Exception:
                return False, 0
    return False, 0

def close_all_positions(report):
    """Close all positions ONCE at end of day"""
    if eod_close_already_done():
        report.append(f"   ✅ EOD close already completed today")
        return 0

    report.append(f"\n🔔 Closing all positions for the day")
    cancelled = cancel_all_orders()
    report.append(f"   📋 Cancelled {cancelled} pending orders")
    time.sleep(2)

    try:
        positions = get_positions()
        if not positions:
            report.append(f"   — No open positions")
            mark_eod_done()
            return 0
        total_pl = 0
        for pos in positions:
            symbol = pos["symbol"]
            pl     = float(pos["unrealized_pl"])
            price  = float(pos["current_price"])
            mval   = float(pos["market_value"])
            success, closed_pl = close_position_safely(symbol, mval, pl)
            if success:
                total_pl += closed_pl
                emoji = "💰" if closed_pl >= 0 else "🛑"
                report.append(f"   {emoji} Closed {symbol}: ${closed_pl:+.2f}")
                log_trade(symbol, "CLOSE EOD", price, mval, closed_pl, "End of Day")
                if closed_pl < 0:
                    add_daily_loss(abs(closed_pl))
            else:
                report.append(f"   ⚠️ Could not close {symbol}")
        report.append(f"   📊 Total EOD P&L: ${total_pl:+.2f}")
        mark_eod_done()  # Mark done so it never runs again today
        return total_pl
    except Exception as e:
        report.append(f"   ⚠️ Error: {e}")
        return 0

# =============================================
# REAL-TIME DATA — ALPACA
# =============================================
def get_latest_price(symbol, retries=3):
    last_error = None
    for attempt in range(retries):
        try:
            url = f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest"
            req = urllib.request.Request(url)
            req.add_header("APCA-API-KEY-ID", ALPACA_KEY)
            req.add_header("APCA-API-SECRET-KEY", ALPACA_SECRET)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return float(data["trade"]["p"])
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(3)
    return get_price_yahoo(symbol)

def get_price_yahoo(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return closes[-1] if closes else None
    except Exception:
        return None

def get_bars(symbol, timeframe="1Min", limit=30, retries=3):
    last_error = None
    for attempt in range(retries):
        try:
            end   = datetime.now(ET)
            start = end - timedelta(hours=2)
            url   = (f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
                     f"?timeframe={timeframe}"
                     f"&start={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                     f"&end={end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                     f"&limit={limit}")
            req = urllib.request.Request(url)
            req.add_header("APCA-API-KEY-ID", ALPACA_KEY)
            req.add_header("APCA-API-SECRET-KEY", ALPACA_SECRET)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return data.get("bars", [])
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(3)
    return []

# =============================================
# STRATEGY 1 — OPENING RANGE BREAKOUT (ORB)
# =============================================
def update_orb_ranges(report):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(ORB_FILE, "r") as f:
            orb_data = json.load(f)
        if orb_data.get("date") != today:
            orb_data = {"date": today, "ranges": {}}
    except Exception:
        orb_data = {"date": today, "ranges": {}}

    report.append(f"\n📐 BUILDING OPENING RANGE (9:00-9:30am)")
    for symbol in WATCHLIST:
        try:
            bars = get_bars(symbol, "1Min", 35)
            if not bars:
                continue
            now_et    = datetime.now(ET)
            open_time = now_et.replace(hour=9, minute=0, second=0, microsecond=0)
            orb_bars  = [b for b in bars
                        if b.get("t", "") >= open_time.strftime("%Y-%m-%dT%H:%M")]
            if not orb_bars:
                continue
            highs = [b["h"] for b in orb_bars]
            lows  = [b["l"] for b in orb_bars]
            orb_data["ranges"][symbol] = {
                "high":   max(highs),
                "low":    min(lows),
                "volume": sum(b.get("v", 0) for b in orb_bars),
            }
        except Exception:
            continue

    with open(ORB_FILE, "w") as f:
        json.dump(orb_data, f)
    report.append(f"   ✅ Ranges set for {len(orb_data['ranges'])} stocks")
    return orb_data["ranges"]

def check_orb_breakouts(held, report):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    buys  = []
    try:
        with open(ORB_FILE, "r") as f:
            orb_data = json.load(f)
        if orb_data.get("date") != today:
            return buys
        ranges = orb_data.get("ranges", {})
    except Exception:
        return buys

    report.append(f"\n📐 ORB BREAKOUT SCAN")
    for symbol, r in ranges.items():
        if symbol in held:
            continue
        try:
            price    = get_latest_price(symbol)
            if not price:
                continue
            orb_high = r["high"]
            breakout_pct = ((price - orb_high) / orb_high) * 100

            # Stronger signal — need 0.15% above ORB high not just 0.1%
            if price > orb_high * 1.0015:
                report.append(f"   🚀 {symbol} @ ${price:.2f} "
                             f"broke ORB ${orb_high:.2f} (+{breakout_pct:.2f}%)")
                buys.append({
                    "symbol":   symbol,
                    "price":    price,
                    "score":    90,
                    "strategy": "ORB Breakout",
                    "detail":   f"+{breakout_pct:.2f}% above ORB",
                })
            else:
                report.append(f"   ⏳ {symbol} @ ${price:.2f} | "
                             f"ORB: ${orb_high:.2f} | "
                             f"{breakout_pct:+.2f}%")
        except Exception:
            continue
    return buys

# =============================================
# STRATEGY 2 — NEWS CATALYST
# =============================================
def check_news_catalysts(held, report):
    if not FINNHUB_KEY:
        return []
    buys = []
    report.append(f"\n📰 NEWS CATALYST SCAN")
    today    = datetime.now(ET)
    week_ago = today - timedelta(hours=4)

    for symbol in WATCHLIST[:8]:
        if symbol in held:
            continue
        try:
            url = (f"https://finnhub.io/api/v1/company-news?symbol={symbol}"
                   f"&from={week_ago.strftime('%Y-%m-%d')}"
                   f"&to={today.strftime('%Y-%m-%d')}"
                   f"&token={FINNHUB_KEY}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                articles = json.loads(r.read())

            if not articles:
                continue

            positive_words = ["beat","surge","soar","jump","record","upgrade",
                            "profit","growth","strong","win","boost","rally"]
            negative_words = ["miss","drop","fall","plunge","loss","downgrade",
                            "weak","decline","crash","warn","cut","layoff"]

            recent = articles[:5]
            pos = sum(1 for a in recent for w in positive_words
                     if w in a.get("headline", "").lower())
            neg = sum(1 for a in recent for w in negative_words
                     if w in a.get("headline", "").lower())

            # Stronger signal — need pos >= 3 not just 2
            if pos > neg and pos >= 3:
                price = get_latest_price(symbol)
                if price:
                    headline = recent[0].get("headline", "")[:60]
                    report.append(f"   📢 {symbol} @ ${price:.2f} — {headline}...")
                    buys.append({
                        "symbol":   symbol,
                        "price":    price,
                        "score":    85,
                        "strategy": "News Catalyst",
                        "detail":   headline,
                    })
            else:
                report.append(f"   — {symbol}: no strong catalyst")
        except Exception:
            continue
    return buys

# =============================================
# STRATEGY 3 — MOMENTUM SCALPING
# =============================================
def check_momentum_scalps(held, report):
    buys = []
    report.append(f"\n⚡ MOMENTUM SCALP SCAN")

    for symbol in WATCHLIST:
        if symbol in held:
            continue
        try:
            bars = get_bars(symbol, "1Min", 10)
            if len(bars) < 6:
                continue

            prices  = [b["c"] for b in bars]
            volumes = [b["v"] for b in bars]
            current      = prices[-1]
            price_5m_ago = prices[-5]
            avg_volume   = sum(volumes[:-1]) / max(len(volumes)-1, 1)
            latest_vol   = volumes[-1]
            move_pct     = (current - price_5m_ago) / price_5m_ago

            # Stronger signal — need 0.5% move not 0.3% AND 1.5x volume
            if move_pct >= 0.005 and latest_vol > avg_volume * 1.5:
                report.append(f"   ⚡ {symbol} @ ${current:.2f} "
                             f"+{move_pct*100:.2f}% in 5min | "
                             f"Vol: {latest_vol/avg_volume:.1f}x avg")
                buys.append({
                    "symbol":   symbol,
                    "price":    current,
                    "score":    70 + min(int(move_pct * 1000), 20),
                    "strategy": "Momentum Scalp",
                    "detail":   f"+{move_pct*100:.2f}% in 5min",
                })
            else:
                report.append(f"   ⏳ {symbol} @ ${current:.2f} | "
                             f"Move: {move_pct*100:+.2f}%")
        except Exception:
            continue
    return buys

# =============================================
# EARNINGS CHECK
# =============================================
def has_upcoming_earnings(symbol):
    try:
        if not FINNHUB_KEY:
            return False, "Unknown"
        today  = datetime.now(ET)
        future = today + timedelta(days=EARNINGS_SAFE_DAYS)
        url = (f"https://finnhub.io/api/v1/calendar/earnings"
               f"?from={today.strftime('%Y-%m-%d')}"
               f"&to={future.strftime('%Y-%m-%d')}"
               f"&symbol={symbol}&token={FINNHUB_KEY}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        earnings = data.get("earningsCalendar", [])
        if earnings:
            return True, f"Earnings {earnings[0].get('date', 'soon')}"
        return False, "Clear"
    except Exception:
        return False, "Unknown"

# =============================================
# POSITION MANAGEMENT
# =============================================
def manage_positions(held, report):
    sells      = 0
    freed_cash = 0
    report.append(f"\n📦 POSITION MANAGEMENT")

    for symbol, pos in held.items():
        if float(pos["market_value"]) < 1.00:
            continue  # Skip micro positions
        try:
            unrealized  = float(pos["unrealized_pl"])
            gain_pct    = float(pos["unrealized_plpc"])
            market_val  = float(pos["market_value"])
            curr_price  = float(pos["current_price"])

            if gain_pct >= TAKE_PROFIT:
                success, pl = close_position_safely(symbol, market_val, unrealized)
                if success:
                    report.append(f"   💰 TAKE PROFIT {symbol}: "
                                 f"+${unrealized:.2f} ({gain_pct*100:+.2f}%) ✅")
                    log_trade(symbol, "SELL TP", curr_price,
                             market_val, unrealized, "Take Profit")
                    freed_cash += market_val
                    sells += 1

            elif gain_pct <= -STOP_LOSS:
                success, pl = close_position_safely(symbol, market_val, unrealized)
                if success:
                    report.append(f"   🛑 STOP LOSS {symbol}: "
                                 f"${unrealized:.2f} ({gain_pct*100:+.2f}%) ✅")
                    log_trade(symbol, "SELL SL", curr_price,
                             market_val, unrealized, "Stop Loss")
                    add_daily_loss(abs(unrealized))
                    freed_cash += market_val
                    sells += 1

            else:
                report.append(f"   📦 {symbol}: ${unrealized:+.2f} "
                             f"({gain_pct*100:+.2f}%) — holding | "
                             f"Target: +{TAKE_PROFIT*100}% "
                             f"Stop: -{STOP_LOSS*100}%")
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")

    return sells, freed_cash

# =============================================
# BUYING
# =============================================
def execute_buys(buy_signals, held, cash, report):
    buys = 0
    buy_signals.sort(key=lambda x: x["score"], reverse=True)
    report.append(f"\n📥 BUYING")

    # Remove duplicates
    seen = {}
    for b in buy_signals:
        sym = b["symbol"]
        if sym not in seen or b["score"] > seen[sym]["score"]:
            seen[sym] = b
    buy_signals = list(seen.values())

    for signal in buy_signals:
        symbol   = signal["symbol"]
        strategy = signal["strategy"]

        if len(held) + buys >= MAX_POSITIONS:
            report.append(f"   ⛔ Max {MAX_POSITIONS} positions — skipping {symbol}")
            continue
        if symbol in held:
            continue
        if daily_loss_exceeded():
            report.append(f"   🚫 Daily loss limit ${DAILY_LOSS_LIMIT} reached — "
                         f"no more buys today")
            break

        earnings, e_msg = has_upcoming_earnings(symbol)
        if earnings:
            report.append(f"   ⚠️ {symbol} blocked — {e_msg}")
            continue

        budget = round(WEEKLY_BUDGET / MAX_POSITIONS, 2)
        if cash < budget or budget < MIN_ORDER:
            report.append(f"   ⚠️ Not enough cash")
            continue

        try:
            result = place_order(symbol, budget, "buy")
            if result:
                report.append(f"   📈 BOUGHT {symbol} @ ${signal['price']:.2f} | "
                             f"${budget:.2f} | {strategy} | Score: {signal['score']}")
                log_trade(symbol, "BUY", signal["price"], budget, 0, strategy)
                cash -= budget
                buys += 1
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")

    if buys == 0:
        report.append(f"   — No buys this cycle")

    return buys, cash

# =============================================
# EMAIL
# =============================================
def send_email(subject, report_lines, is_error=False):
    try:
        if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
            return False
        body  = "\n".join(report_lines)
        msg   = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_ADDRESS
        msg["To"]      = EMAIL_ADDRESS
        color = "#ff4444" if is_error else "#00ff00"
        html  = f"""
        <html><body style="font-family:monospace;background:#0a0a0a;
                           color:{color};padding:20px;">
            <div style="max-width:600px;margin:0 auto;background:#111;
                        padding:20px;border-radius:10px;
                        border:1px solid {color};">
                <h2 style="color:{color};">🤖 AI Trading Bot</h2>
                <pre style="color:{color};font-size:12px;
                            line-height:1.6;">{body}</pre>
                <hr style="border-color:{color};">
                <p style="color:#555;font-size:11px;">
                    Paper Trading — No real money at risk
                </p>
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
# MAIN BOT
# =============================================
def run():
    now_et      = datetime.now(ET)
    weekday     = now_et.weekday()
    early_close = is_early_close()

    # ── WEEKEND — silent ──────────────────────
    if weekday >= 5:
        print(f"Weekend — bot monitoring silently")
        return

    market_open, market_msg = is_market_open()

    # ── AFTER MARKET CLOSE — send ONE EOD email ──
    if not market_open and now_et.hour >= 9:
        if already_sent_today("eod"):
            print(f"EOD email already sent — sleeping")
            return

        report = []
        report.append(f"🤖 AI Trading Bot — End of Day Report")
        report.append(f"📅 {now_et.strftime('%A %B %d, %Y')}")
        report.append(f"⏰ {now_et.strftime('%I:%M %p')} ET")
        report.append("="*45)

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

        # Today's trades
        trades = get_todays_trades()
        if trades:
            wins   = [t for t in trades if t.get("pl", 0) > 0]
            losses = [t for t in trades if t.get("pl", 0) < 0]
            report.append(f"\n📋 TODAY'S TRADES ({len(trades)} total):")
            report.append(f"   ✅ Wins: {len(wins)} | ❌ Losses: {len(losses)}")
            # Only show meaningful trades not EOD closes
            meaningful = [t for t in trades if "EOD" not in t.get("action", "")]
            for t in meaningful:
                pl_str = f"${t['pl']:+.2f}" if t['pl'] != 0 else ""
                report.append(f"   {t['time']} {t['action']} {t['symbol']} "
                             f"@ ${t['price']:.2f} {pl_str} [{t['strategy']}]")
        else:
            report.append(f"\n📋 No trades executed today")

        # Earnings tomorrow
        tomorrow = (now_et + timedelta(days=1)).strftime("%Y-%m-%d")
        earnings_tmrw = []
        for symbol in WATCHLIST[:8]:
            try:
                has_e, e_msg = has_upcoming_earnings(symbol)
                if has_e and tomorrow in e_msg:
                    earnings_tmrw.append(symbol)
            except Exception:
                continue
        if earnings_tmrw:
            report.append(f"\n⚠️ EARNINGS TOMORROW — Bot will avoid:")
            for s in earnings_tmrw:
                report.append(f"   🚫 {s}")

        daily_loss = get_daily_loss()
        report.append(f"\n🛡 Daily loss: ${daily_loss:.2f} / ${DAILY_LOSS_LIMIT:.2f}")
        report.append(f"\n{'='*45}")
        report.append(f"✅ Market closed — see you tomorrow!")
        report.append(f"{'='*45}")

        print("\n".join(report))
        trades    = get_todays_trades()
        wins      = len([t for t in trades if t.get("pl", 0) > 0])
        subject   = (f"📊 EOD Report — {now_et.strftime('%b %d')} | "
                    f"P&L: ${profit:+,.2f} | "
                    f"Trades: {len(trades)} | Wins: {wins}")
        if send_email(subject, report):
            mark_sent_today("eod")
        return

    # ── PRE-MARKET — silent ───────────────────
    if not market_open:
        print(f"Pre-market — monitoring silently")
        return

    # ── MARKET IS OPEN ────────────────────────
    report = []
    report.append(f"🤖 AI Trading Bot — Aggressive Intraday")
    report.append(f"📅 {now_et.strftime('%A %B %d, %Y')}")
    report.append(f"⏰ {now_et.strftime('%I:%M %p')} ET")
    report.append(f"{'⚠️ EARLY CLOSE — 1pm ET' if early_close else '📅 Regular trading day'}")
    report.append(f"💰 Budget: ${WEEKLY_BUDGET} | "
                 f"TP: {TAKE_PROFIT*100}% | "
                 f"SL: {STOP_LOSS*100}% | "
                 f"Max loss/day: ${DAILY_LOSS_LIMIT}")
    report.append(f"🧠 ORB + News Catalyst + Momentum Scalping")
    report.append("="*45)

    # Daily loss limit check
    if daily_loss_exceeded():
        report.append(f"🚫 DAILY LOSS LIMIT ${DAILY_LOSS_LIMIT} REACHED — "
                     f"bot stopped for today")
        print("\n".join(report))
        return

    report.append(f"🕐 Market {market_msg}")
    report.append(f"🛡 Daily loss: ${get_daily_loss():.2f} / ${DAILY_LOSS_LIMIT}")
    report.append("="*45)

    # Get account
    profit = 0
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
        mark_sent_today("error")  # Silent — no error email spam
        return

    # Get positions
    try:
        positions = get_positions()
        held      = {p["symbol"]: p for p in positions}
    except Exception as e:
        report.append(f"⚠️ Positions error: {e}")
        held = {}

    # End of day — close everything ONCE
    if is_end_of_day():
        if eod_close_already_done():
            report.append(f"✅ EOD close already done — waiting for market close")
            print("\n".join(report))
            return
        close_time = "12:45pm" if early_close else "4:15pm"
        report.append(f"\n⏰ {close_time} — Closing all positions")
        close_all_positions(report)
        report.append(f"{'='*45}")
        report.append(f"✅ Positions closed — EOD email coming after close")
        report.append(f"{'='*45}")
        print("\n".join(report))
        return

    budget_per_stock = round(WEEKLY_BUDGET / MAX_POSITIONS, 2)
    report.append(f"📊 Per position: ${budget_per_stock:.2f}")
    report.append("="*45)

    # ORB window — build ranges, don't trade yet
    now_et  = datetime.now(ET)
    orb_end = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    if now_et < orb_end:
        update_orb_ranges(report)
        report.append(f"\n⏳ Opening range window — watching, not trading yet")
        print("\n".join(report))
        return

    # Manage existing positions
    sells, freed_cash = manage_positions(held, report)
    cash += freed_cash

    # Run all 3 strategies
    buy_signals = []
    orb_buys    = check_orb_breakouts(held, report)
    news_buys   = check_news_catalysts(held, report)
    mom_buys    = check_momentum_scalps(held, report)
    buy_signals.extend(orb_buys)
    buy_signals.extend(news_buys)
    buy_signals.extend(mom_buys)

    # Execute buys
    buys, cash = execute_buys(buy_signals, held, cash, report)

    # Summary
    report.append(f"\n{'='*45}")
    report.append(f"📊 CYCLE SUMMARY")
    report.append(f"{'='*45}")
    report.append(f"   Positions: {len(held)} held | {buys} bought | {sells} sold")
    report.append(f"   Signals:   {len(orb_buys)} ORB | "
                 f"{len(news_buys)} News | {len(mom_buys)} Momentum")
    report.append(f"   P&L:       ${profit:+,.2f}")
    report.append(f"   Daily loss: ${get_daily_loss():.2f} / ${DAILY_LOSS_LIMIT}")
    report.append(f"{'='*45}")
    report.append(f"✅ Next run in 1 min")
    report.append(f"{'='*45}")

    print("\n".join(report))

# Run once then exit
run()
