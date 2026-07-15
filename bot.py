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
TAKE_PROFIT        = 0.0075
STOP_LOSS          = 0.003
DAILY_LOSS_LIMIT   = 2.00
MAX_POSITIONS      = 5
MIN_ORDER          = 1.00
MIN_MOMENTUM       = 0.005
EARNINGS_SAFE_DAYS = 5
SCREENER_MAX       = 5
PROFIT_TRAIL       = 0.25
PROFIT_GOAL        = 50.00    # Minimum target — no ceiling, shoot higher!
GOAL_DAYS          = 10       # 10 trading days = 2 weeks
GOAL_START_DATE    = "2026-07-15"  # Resets every 2 weeks automatically
ET                 = ZoneInfo("America/New_York")
EARLY_CLOSE_DATES  = ["07-03", "07-04", "11-28", "12-24"]

# File paths
SENT_FILE  = "/home/ubuntu/.bot_sent"
ORB_FILE   = "/home/ubuntu/.orb_ranges"
TRADES_FILE= "/home/ubuntu/.bot_trades"
LOSS_FILE  = "/home/ubuntu/.bot_daily_loss"
EOD_DONE   = "/home/ubuntu/.bot_eod_done"
PEAK_FILE  = "/home/ubuntu/.bot_peak_profit"
FLOOR_FILE = "/home/ubuntu/.bot_cumulative_floor"

# =============================================
# WATCHLIST
# =============================================
WATCHLIST = [
    "MSFT","AAPL","GOOGL","AMZN","META",
    "NVDA","AMD","TSLA","CRM","SHOP",
    "PLTR","SOFI","BAC","F","AEM","GLD",
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
# GOAL TRACKER + GOAL-DRIVEN AGGRESSION
# =============================================
def get_goal_tracker(current_profit):
    today        = datetime.now(ET).date()
    start        = datetime.strptime(GOAL_START_DATE, "%Y-%m-%d").date()
    total_days   = (today - start).days

    # Auto-reset every 10 trading days (2 weeks)
    cycle_num        = total_days // GOAL_DAYS
    cycle_start_days = cycle_num * GOAL_DAYS
    days_in_cycle    = total_days - cycle_start_days
    days_elapsed     = min(days_in_cycle + 1, GOAL_DAYS)
    days_remaining   = max(0, GOAL_DAYS - days_elapsed)

    # No ceiling — always push higher
    remaining_to_min = max(0, PROFIT_GOAL - current_profit)
    needed_per_day   = remaining_to_min / max(days_remaining, 1)
    daily_avg        = current_profit / max(days_elapsed, 1)
    pct_to_min       = min(100, (current_profit / PROFIT_GOAL) * 100)
    days_to_goal     = remaining_to_min / max(daily_avg, 0.01)
    on_track         = needed_per_day <= daily_avg
    cycle_label      = f"Cycle {cycle_num + 1}"

    # Project best case
    projected_2wk = daily_avg * GOAL_DAYS

    return {
        "total_profit":    round(current_profit, 2),
        "goal":            PROFIT_GOAL,
        "remaining":       round(remaining_to_min, 2),
        "pct_complete":    round(pct_to_min, 1),
        "days_elapsed":    days_elapsed,
        "days_remaining":  days_remaining,
        "needed_per_day":  round(needed_per_day, 2),
        "daily_avg":       round(daily_avg, 2),
        "days_to_goal":    round(days_to_goal, 0),
        "on_track":        on_track,
        "cycle":           cycle_label,
        "projected_2wk":   round(projected_2wk, 2),
    }

def get_goal_driven_settings(profit):
    """Adjust bot aggression based on $50 goal progress"""
    g          = get_goal_tracker(profit)
    daily_need = g["needed_per_day"]
    daily_avg  = g["daily_avg"]

    if daily_need > daily_avg * 2:
        # Far behind — maximum aggression
        return {
            "score_threshold": 70,
            "max_positions":   6,
            "trail_pct":       0.30,
            "mode":            "🔥 MAX AGGRESSIVE — far behind pace",
        }
    elif daily_need > daily_avg * 1.5:
        # Behind — more aggressive
        return {
            "score_threshold": 75,
            "max_positions":   6,
            "trail_pct":       0.30,
            "mode":            "⚡ AGGRESSIVE — behind pace",
        }
    elif daily_need <= daily_avg:
        # On pace — normal
        return {
            "score_threshold": 85,
            "max_positions":   5,
            "trail_pct":       0.25,
            "mode":            "✅ NORMAL — on pace",
        }
    else:
        # Slightly behind — moderate
        return {
            "score_threshold": 80,
            "max_positions":   5,
            "trail_pct":       0.25,
            "mode":            "📈 MODERATE — slightly behind",
        }

# =============================================
# PROFIT PROTECTION — RULE 1 (DAILY TRAIL)
# =============================================
def get_peak_profit():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(PEAK_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") != today:
            return 0.0
        return data.get("peak", 0.0)
    except Exception:
        return 0.0

def update_peak_profit(current_pl):
    today    = datetime.now(ET).strftime("%Y-%m-%d")
    old_peak = get_peak_profit()
    new_peak = max(old_peak, current_pl)
    if new_peak > old_peak:
        with open(PEAK_FILE, "w") as f:
            json.dump({"date": today, "peak": round(new_peak, 4)}, f)
    return new_peak

def daily_trail_breached(current_pl, trail_pct):
    peak = get_peak_profit()
    if peak <= 0:
        return False
    floor = peak * (1 - trail_pct)
    return current_pl < floor

# =============================================
# PROFIT PROTECTION — RULE 2 (CUMULATIVE)
# =============================================
def get_cumulative_floor():
    try:
        with open(FLOOR_FILE, "r") as f:
            data = json.load(f)
        return data.get("floor", 100000.0)
    except Exception:
        return 100000.0

def update_cumulative_floor(portfolio_value):
    current_floor = get_cumulative_floor()
    new_floor     = max(current_floor, portfolio_value)
    if new_floor > current_floor:
        with open(FLOOR_FILE, "w") as f:
            json.dump({"floor": round(new_floor, 4)}, f)
    return new_floor

def cumulative_floor_breached(portfolio_value):
    return portfolio_value < get_cumulative_floor()

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
        "time": now, "symbol": symbol, "action": action,
        "price": round(price, 2), "amount": round(amount, 2),
        "pl": round(pl, 4), "strategy": strategy,
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
# ALPACA API — WITH RETRY
# =============================================
def alpaca_request(method, endpoint, data=None, retries=3):
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
                wait = (attempt + 1) * 5
                print(f"Alpaca attempt {attempt+1} failed — retrying in {wait}s...")
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
        "symbol": symbol, "notional": str(round(dollars, 2)),
        "side": side, "type": "market", "time_in_force": "day",
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
    if eod_close_already_done():
        report.append("   EOD close already done today")
        return 0
    report.append(f"\n Closing all positions for the day")
    cancelled = cancel_all_orders()
    report.append(f"   Cancelled {cancelled} pending orders")
    time.sleep(2)
    try:
        positions = get_positions()
        if not positions:
            report.append("   No open positions")
            mark_eod_done()
            return 0
        total_pl = 0
        for pos in positions:
            symbol = pos["symbol"]
            pl     = float(pos["unrealized_pl"])
            price  = float(pos["current_price"])
            mval   = float(pos["market_value"])
            if mval < 1.00:
                continue
            success, closed_pl = close_position_safely(symbol, mval, pl)
            if success:
                total_pl += closed_pl
                emoji = "💰" if closed_pl >= 0 else "🛑"
                report.append(f"   {emoji} Closed {symbol}: ${closed_pl:+.2f}")
                log_trade(symbol, "CLOSE EOD", price, mval, closed_pl, "End of Day")
                if closed_pl < 0:
                    add_daily_loss(abs(closed_pl))
            else:
                report.append(f"   Could not close {symbol}")
        report.append(f"   Total EOD P&L: ${total_pl:+.2f}")
        mark_eod_done()
        return total_pl
    except Exception as e:
        report.append(f"   Error: {e}")
        return 0

# =============================================
# REAL-TIME DATA — ALPACA WITH RETRY
# =============================================
def get_latest_price(symbol, retries=3):
    for attempt in range(retries):
        try:
            url = f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest"
            req = urllib.request.Request(url)
            req.add_header("APCA-API-KEY-ID", ALPACA_KEY)
            req.add_header("APCA-API-SECRET-KEY", ALPACA_SECRET)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return float(data["trade"]["p"])
        except Exception:
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
        except Exception:
            if attempt < retries - 1:
                time.sleep(3)
    return []

# =============================================
# STRATEGY 1 — OPENING RANGE BREAKOUT
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
                        if b.get("t","") >= open_time.strftime("%Y-%m-%dT%H:%M")]
            if not orb_bars:
                continue
            orb_data["ranges"][symbol] = {
                "high":   max(b["h"] for b in orb_bars),
                "low":    min(b["l"] for b in orb_bars),
                "volume": sum(b.get("v",0) for b in orb_bars),
            }
        except Exception:
            continue
    with open(ORB_FILE, "w") as f:
        json.dump(orb_data, f)
    report.append(f"   Ranges set for {len(orb_data['ranges'])} stocks")
    return orb_data["ranges"]

def check_orb_breakouts(held, report, score_threshold=85):
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
            price        = get_latest_price(symbol)
            if not price:
                continue
            orb_high     = r["high"]
            breakout_pct = ((price - orb_high) / orb_high) * 100
            if price > orb_high * 1.0015:
                report.append(f"   🚀 {symbol} @ ${price:.2f} "
                             f"broke ORB ${orb_high:.2f} (+{breakout_pct:.2f}%)")
                buys.append({"symbol": symbol, "price": price,
                            "score": 90, "strategy": "ORB Breakout",
                            "detail": f"+{breakout_pct:.2f}%"})
            else:
                report.append(f"   ⏳ {symbol} @ ${price:.2f} | "
                             f"ORB: ${orb_high:.2f} | {breakout_pct:+.2f}%")
        except Exception:
            continue
    return buys

# =============================================
# STRATEGY 2 — NEWS CATALYST
# =============================================
def check_news_catalysts(held, report, score_threshold=85):
    if not FINNHUB_KEY:
        return []
    buys  = []
    today = datetime.now(ET)
    report.append(f"\n📰 NEWS CATALYST SCAN")
    for symbol in WATCHLIST[:8]:
        if symbol in held:
            continue
        try:
            week_ago = today - timedelta(hours=4)
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
                     if w in a.get("headline","").lower())
            neg = sum(1 for a in recent for w in negative_words
                     if w in a.get("headline","").lower())
            min_pos = 2 if score_threshold <= 75 else 3
            if pos > neg and pos >= min_pos:
                price = get_latest_price(symbol)
                if price:
                    headline = recent[0].get("headline","")[:60]
                    report.append(f"   📢 {symbol} @ ${price:.2f} — {headline}...")
                    buys.append({"symbol": symbol, "price": price,
                                "score": 85, "strategy": "News Catalyst",
                                "detail": headline})
            else:
                report.append(f"   — {symbol}: no strong catalyst")
        except Exception:
            continue
    return buys

# =============================================
# STRATEGY 3 — MOMENTUM SCALPING
# =============================================
def check_momentum_scalps(held, report, score_threshold=85):
    buys = []
    report.append(f"\n⚡ MOMENTUM SCALP SCAN")
    min_move = 0.003 if score_threshold <= 75 else MIN_MOMENTUM
    for symbol in WATCHLIST:
        if symbol in held:
            continue
        try:
            bars = get_bars(symbol, "1Min", 10)
            if len(bars) < 6:
                continue
            prices       = [b["c"] for b in bars]
            volumes      = [b["v"] for b in bars]
            current      = prices[-1]
            price_5m_ago = prices[-5]
            avg_volume   = sum(volumes[:-1]) / max(len(volumes)-1, 1)
            latest_vol   = volumes[-1]
            move_pct     = (current - price_5m_ago) / price_5m_ago
            vol_mult     = 1.3 if score_threshold <= 75 else 1.5
            if move_pct >= min_move and latest_vol > avg_volume * vol_mult:
                report.append(f"   ⚡ {symbol} @ ${current:.2f} "
                             f"+{move_pct*100:.2f}% | "
                             f"Vol: {latest_vol/avg_volume:.1f}x")
                buys.append({"symbol": symbol, "price": current,
                            "score": 70 + min(int(move_pct*1000), 20),
                            "strategy": "Momentum Scalp",
                            "detail": f"+{move_pct*100:.2f}% in 5min"})
            else:
                report.append(f"   ⏳ {symbol} @ ${current:.2f} | "
                             f"Move: {move_pct*100:+.2f}%")
        except Exception:
            continue
    return buys

# =============================================
# EXPANDED MARKET SCREENER
# Scans far beyond the watchlist
# =============================================
def screen_full_market(held, report):
    """Scan entire market for best opportunities"""
    report.append(f"\n🔭 FULL MARKET SCAN")
    candidates = {}

    # Source 1 — Top 50 day gainers (not just 10)
    try:
        url = ("https://query1.finance.yahoo.com/v1/finance/screener/"
               "predefined/saved?scrIds=day_gainers&count=50")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        quotes = (data.get("finance", {})
                 .get("result", [{}])[0].get("quotes", []))
        for q in quotes:
            sym = q.get("symbol", "")
            if sym and sym not in held:
                candidates[sym] = {
                    "change_pct": q.get("regularMarketChangePercent", 0),
                    "volume":     q.get("regularMarketVolume", 0),
                    "price":      q.get("regularMarketPrice", 0),
                    "source":     "Day Gainer",
                }
        report.append(f"   📈 Day gainers: {len(quotes)} stocks")
    except Exception as e:
        report.append(f"   ⚠️ Day gainers: {e}")

    # Source 2 — Top 50 most active by volume
    try:
        url = ("https://query1.finance.yahoo.com/v1/finance/screener/"
               "predefined/saved?scrIds=most_actives&count=50")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        quotes = (data.get("finance", {})
                 .get("result", [{}])[0].get("quotes", []))
        for q in quotes:
            sym = q.get("symbol", "")
            if sym and sym not in held:
                if sym in candidates:
                    candidates[sym]["source"] += " + Most Active"
                else:
                    candidates[sym] = {
                        "change_pct": q.get("regularMarketChangePercent", 0),
                        "volume":     q.get("regularMarketVolume", 0),
                        "price":      q.get("regularMarketPrice", 0),
                        "source":     "Most Active",
                    }
        report.append(f"   📊 Most active: {len(quotes)} stocks")
    except Exception as e:
        report.append(f"   ⚠️ Most active: {e}")

    # Source 3 — Small cap gainers (underdogs!)
    try:
        url = ("https://query1.finance.yahoo.com/v1/finance/screener/"
               "predefined/saved?scrIds=small_cap_gainers&count=25")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        quotes = (data.get("finance", {})
                 .get("result", [{}])[0].get("quotes", []))
        for q in quotes:
            sym = q.get("symbol", "")
            if sym and sym not in held:
                if sym in candidates:
                    candidates[sym]["source"] += " + Small Cap"
                else:
                    candidates[sym] = {
                        "change_pct": q.get("regularMarketChangePercent", 0),
                        "volume":     q.get("regularMarketVolume", 0),
                        "price":      q.get("regularMarketPrice", 0),
                        "source":     "Small Cap Gainer",
                    }
        report.append(f"   🔬 Small cap gainers: {len(quotes)} stocks")
    except Exception as e:
        report.append(f"   ⚠️ Small cap: {e}")

    # Source 4 — Growth technology stocks
    try:
        url = ("https://query1.finance.yahoo.com/v1/finance/screener/"
               "predefined/saved?scrIds=growth_technology_stocks&count=25")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        quotes = (data.get("finance", {})
                 .get("result", [{}])[0].get("quotes", []))
        for q in quotes:
            sym = q.get("symbol", "")
            if sym and sym not in held:
                if sym in candidates:
                    candidates[sym]["source"] += " + Growth Tech"
                else:
                    candidates[sym] = {
                        "change_pct": q.get("regularMarketChangePercent", 0),
                        "volume":     q.get("regularMarketVolume", 0),
                        "price":      q.get("regularMarketPrice", 0),
                        "source":     "Growth Tech",
                    }
        report.append(f"   💻 Growth tech: {len(quotes)} stocks")
    except Exception as e:
        report.append(f"   ⚠️ Growth tech: {e}")

    # Filter — only stocks moving up with volume
    strong_candidates = {
        sym: data for sym, data in candidates.items()
        if data["change_pct"] > 0.5      # Moving up 0.5%+
        and data["volume"] > 100000       # Decent volume
        and 1.00 < data["price"] < 500    # Reasonable price range
        and sym not in WATCHLIST          # Not already in watchlist
    }

    report.append(f"   🎯 Strong candidates: {len(strong_candidates)} stocks")

    # Analyze top candidates
    new_stocks = []
    sorted_candidates = sorted(
        strong_candidates.items(),
        key=lambda x: x[1]["change_pct"],
        reverse=True
    )[:20]  # Analyze top 20

    for sym, info in sorted_candidates:
        try:
            bars = get_bars(sym, "1Min", 10)
            if len(bars) < 5:
                continue

            prices     = [b["c"] for b in bars]
            volumes    = [b["v"] for b in bars]
            current    = prices[-1]
            avg_vol    = sum(volumes[:-1]) / max(len(volumes)-1, 1)
            latest_vol = volumes[-1]
            move_5m    = (prices[-1] - prices[-5]) / prices[-5] * 100

            score = 0
            if info["change_pct"] > 3:   score += 40
            elif info["change_pct"] > 1: score += 25
            elif info["change_pct"] > 0.5: score += 15
            if latest_vol > avg_vol * 2: score += 30
            elif latest_vol > avg_vol:   score += 15
            if move_5m > 0.3:            score += 20
            if move_5m > 0.1:            score += 10

            if score >= 50:
                report.append(
                    f"   🌟 {sym} @ ${current:.2f} | "
                    f"+{info['change_pct']:.1f}% today | "
                    f"Vol: {latest_vol/avg_vol:.1f}x | "
                    f"Score: {score} | [{info['source']}]"
                )
                new_stocks.append({
                    "symbol":   sym,
                    "price":    current,
                    "score":    score,
                    "strategy": f"Screener ({info['source']})",
                    "detail":   f"+{info['change_pct']:.1f}% today",
                })
        except Exception:
            continue

    if not new_stocks:
        report.append("   — No strong candidates found beyond watchlist")

    # Return as buy signals sorted by score
    return sorted(new_stocks, key=lambda x: x["score"], reverse=True)[:SCREENER_MAX]

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
            return True, f"Earnings {earnings[0].get('date','soon')}"
        return False, "Clear"
    except Exception:
        return False, "Unknown"

# =============================================
# POSITION MANAGEMENT
# =============================================
def manage_positions(held, report):
    sells = 0
    freed_cash = 0
    report.append(f"\n📦 POSITION MANAGEMENT")
    for symbol, pos in held.items():
        if float(pos["market_value"]) < 1.00:
            continue
        try:
            unrealized = float(pos["unrealized_pl"])
            gain_pct   = float(pos["unrealized_plpc"])
            market_val = float(pos["market_value"])
            curr_price = float(pos["current_price"])
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
                             f"TP: +{TAKE_PROFIT*100}% SL: -{STOP_LOSS*100}%")
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")
    return sells, freed_cash

# =============================================
# POSITION ROTATION
# =============================================
def rotate_positions(held, buy_signals, report):
    if not buy_signals or not held:
        return held, 0
    freed = 0
    report.append(f"\n🔄 POSITION ROTATION CHECK")
    best_score = max(s["score"] for s in buy_signals)
    for symbol, pos in list(held.items()):
        if float(pos["market_value"]) < 1.00:
            continue
        unrealized = float(pos["unrealized_pl"])
        gain_pct   = float(pos["unrealized_plpc"])
        market_val = float(pos["market_value"])
        curr_price = float(pos["current_price"])
        if gain_pct < 0 and best_score >= 85:
            success, pl = close_position_safely(symbol, market_val, unrealized)
            if success:
                report.append(f"   🔄 ROTATED OUT {symbol}: "
                             f"${unrealized:.2f} ({gain_pct*100:+.2f}%) "
                             f"→ score {best_score} signal available")
                log_trade(symbol, "SELL ROT", curr_price,
                         market_val, unrealized, "Position Rotation")
                if unrealized < 0:
                    add_daily_loss(abs(unrealized))
                del held[symbol]
                freed += 1
    if freed == 0:
        report.append("   — No rotation needed this cycle")
    return held, freed

# =============================================
# BUYING
# =============================================
def execute_buys(buy_signals, held, cash, report,
                 score_threshold=85, max_pos=5):
    buys = 0
    buy_signals.sort(key=lambda x: x["score"], reverse=True)
    report.append(f"\n📥 BUYING")
    seen = {}
    for b in buy_signals:
        sym = b["symbol"]
        if sym not in seen or b["score"] > seen[sym]["score"]:
            seen[sym] = b
    buy_signals = [b for b in seen.values() if b["score"] >= score_threshold]
    buy_signals.sort(key=lambda x: x["score"], reverse=True)
    for signal in buy_signals:
        symbol   = signal["symbol"]
        strategy = signal["strategy"]
        if len(held) + buys >= max_pos:
            report.append(f"   ⛔ Max {max_pos} positions — skipping {symbol}")
            continue
        if symbol in held:
            continue
        if daily_loss_exceeded():
            report.append(f"   🚫 Daily loss limit reached — no more buys")
            break
        earnings, e_msg = has_upcoming_earnings(symbol)
        if earnings:
            report.append(f"   ⚠️ {symbol} blocked — {e_msg}")
            continue
        budget = round(WEEKLY_BUDGET / max_pos, 2)
        if cash < budget or budget < MIN_ORDER:
            report.append(f"   ⚠️ Not enough cash")
            continue
        try:
            result = place_order(symbol, budget, "buy")
            if result:
                report.append(f"   📈 BOUGHT {symbol} @ ${signal['price']:.2f} | "
                             f"${budget:.2f} | {strategy} | Score: {signal['score']}")
                log_trade(symbol, "BUY", signal["price"],
                         budget, 0, strategy)
                cash -= budget
                buys += 1
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")
    if buys == 0:
        report.append("   — No buys this cycle")
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
        print(f"Email failed: {e}")
        return False

# =============================================
# MAIN BOT
# =============================================
def run():
    now_et      = datetime.now(ET)
    weekday     = now_et.weekday()
    early_close = is_early_close()

    if weekday >= 5:
        print("Weekend — bot monitoring silently")
        return

    market_open, market_msg = is_market_open()

    # ── AFTER MARKET CLOSE — EOD email ───────
    if not market_open and now_et.hour >= 9:
        if already_sent_today("eod"):
            print("EOD email already sent — sleeping")
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
            report.append(f"💼 Portfolio:  ${portfolio:,.2f}")
            report.append(f"💵 Cash:       ${cash:,.2f}")
            report.append(f"📈 Total P&L:  ${profit:+,.2f}")
            report.append(f"🏆 Peak today: ${get_peak_profit():+,.2f}")
            report.append(f"🔒 Cum. floor: ${get_cumulative_floor():,.2f}")
        except Exception as e:
            report.append(f"Account error: {e}")

        trades = get_todays_trades()
        if trades:
            wins   = [t for t in trades if t.get("pl",0) > 0]
            losses = [t for t in trades if t.get("pl",0) < 0]
            report.append(f"\n📋 TODAY'S TRADES ({len(trades)} total):")
            report.append(f"   ✅ Wins: {len(wins)} | ❌ Losses: {len(losses)}")
            meaningful = [t for t in trades if "EOD" not in t.get("action","")]
            for t in meaningful:
                pl_str = f"${t['pl']:+.2f}" if t["pl"] != 0 else ""
                report.append(f"   {t['time']} {t['action']} {t['symbol']} "
                             f"@ ${t['price']:.2f} {pl_str} [{t['strategy']}]")
        else:
            report.append(f"\n📋 No trades executed today")

        # Goal tracker
        goal = get_goal_tracker(profit)
        report.append(f"\n🎯 GOAL TRACKER — {goal['cycle']}")
        report.append(f"{'='*45}")
        if profit >= PROFIT_GOAL:
            report.append(f"   ✅ MINIMUM GOAL HIT! ${profit:+.2f}")
            report.append(f"   🚀 Keep pushing — no ceiling!")
            report.append(f"   📈 Projected 2-week total: ${goal['projected_2wk']:.2f}")
        else:
            report.append(f"   Total profit:     ${goal['total_profit']:+.2f}")
            report.append(f"   Minimum target:   ${goal['goal']:.2f} (no ceiling!)")
            report.append(f"   Remaining to min: ${goal['remaining']:.2f}")
            report.append(f"   Progress:         {goal['pct_complete']}%")
            report.append(f"   Day:              {goal['days_elapsed']} of {GOAL_DAYS}")
            report.append(f"   Days left:        {goal['days_remaining']}")
            report.append(f"   Need per day:     ${goal['needed_per_day']:.2f}")
            report.append(f"   Avg per day:      ${goal['daily_avg']:.2f}")
            report.append(f"   On track:         "
                         f"{'✅ YES!' if goal['on_track'] else '❌ NO — scale budget'}")
            report.append(f"   Projected 2wk:    ${goal['projected_2wk']:.2f}")
            report.append(f"   At current pace:  "
                         f"{int(goal['days_to_goal'])} days to ${PROFIT_GOAL:.0f}")
        report.append(f"{'='*45}")

        daily_loss = get_daily_loss()
        report.append(f"\n🛡 Daily loss: ${daily_loss:.2f} / ${DAILY_LOSS_LIMIT:.2f}")
        report.append(f"\n{'='*45}")
        report.append(f"✅ Market closed — see you tomorrow!")
        report.append(f"{'='*45}")

        print("\n".join(report))
        update_cumulative_floor(portfolio)

        trades  = get_todays_trades()
        wins    = len([t for t in trades if t.get("pl",0) > 0])
        subject = (f"📊 EOD {now_et.strftime('%b %d')} | "
                  f"P&L: ${profit:+,.2f} | "
                  f"Goal: {goal['pct_complete']}% | "
                  f"Wins: {wins}/{len(trades)}")
        if send_email(subject, report):
            mark_sent_today("eod")
        return

    if not market_open:
        print("Pre-market — monitoring silently")
        return

    # ── MARKET IS OPEN ────────────────────────
    report = []

    # Get account first
    profit    = 0
    portfolio = 100000
    try:
        account   = get_account()
        portfolio = float(account["portfolio_value"])
        cash      = float(account["cash"])
        profit    = portfolio - 100000
    except Exception as e:
        print(f"Account error: {e}")
        mark_sent_today("error")
        return

    # Update peak
    peak  = update_peak_profit(profit)

    # Get goal-driven settings
    settings        = get_goal_driven_settings(profit)
    score_threshold = settings["score_threshold"]
    max_pos         = settings["max_positions"]
    trail_pct       = settings["trail_pct"]
    floor           = peak * (1 - trail_pct)

    report.append(f"🤖 AI Trading Bot — {settings['mode']}")
    report.append(f"📅 {now_et.strftime('%A %B %d, %Y')}")
    report.append(f"⏰ {now_et.strftime('%I:%M %p')} ET")
    report.append(f"{'⚠️ EARLY CLOSE' if early_close else '📅 Regular day'}")
    report.append(f"💰 Budget: ${WEEKLY_BUDGET} | "
                 f"TP: {TAKE_PROFIT*100}% | "
                 f"SL: {STOP_LOSS*100}% | "
                 f"Max loss: ${DAILY_LOSS_LIMIT}")
    report.append(f"🎯 Goal mode: {settings['mode']}")
    report.append(f"   Score threshold: {score_threshold} | "
                 f"Max positions: {max_pos} | "
                 f"Trail: {trail_pct*100}%")
    report.append("="*45)

    # Daily loss limit
    if daily_loss_exceeded():
        report.append(f"🚫 DAILY LOSS LIMIT ${DAILY_LOSS_LIMIT} REACHED — stopped")
        print("\n".join(report))
        return

    report.append(f"🕐 Market {market_msg}")
    report.append(f"💼 Portfolio: ${portfolio:,.2f}")
    report.append(f"💵 Cash:      ${cash:,.2f}")
    report.append(f"📈 P&L:       ${profit:+,.2f}")
    report.append(f"🏆 Peak:      ${peak:+,.2f}")
    report.append(f"🔒 Floor:     ${floor:+,.2f} ({trail_pct*100:.0f}% below peak)")
    report.append(f"🛡 Cum. floor: ${get_cumulative_floor():,.2f}")

    # Goal progress intraday
    goal = get_goal_tracker(profit)
    report.append(f"🎯 Goal:      ${goal['remaining']:.2f} to min | "
                 f"Projected 2wk: ${goal['projected_2wk']:.2f} | "
                 f"{goal['pct_complete']}% | {goal['cycle']}")
    report.append("="*45)

    # Profit floor — close losers, keep hunting
    if daily_trail_breached(profit, trail_pct):
        report.append(f"🔒 PROFIT FLOOR HIT — closing losers, hunting recovery!")
        report.append(f"   Peak: ${peak:.2f} | Floor: ${floor:.2f} | Now: ${profit:.2f}")
        try:
            positions = get_positions()
            for pos in positions:
                sym      = pos["symbol"]
                gain_pct = float(pos["unrealized_plpc"])
                mval     = float(pos["market_value"])
                pl       = float(pos["unrealized_pl"])
                if mval < 1.00:
                    continue
                if gain_pct < 0:
                    success, _ = close_position_safely(sym, mval, pl)
                    if success:
                        report.append(f"   🔄 Closed loser {sym}: "
                                     f"${pl:.2f} ({gain_pct*100:+.2f}%)")
                        add_daily_loss(abs(pl))
                else:
                    report.append(f"   ✅ Keeping winner {sym}: "
                                 f"${pl:+.2f} ({gain_pct*100:+.2f}%)")
        except Exception as e:
            report.append(f"   Error: {e}")
        report.append("   Continuing to scan for recovery...")

    # Cumulative floor
    if cumulative_floor_breached(portfolio):
        cum_floor = get_cumulative_floor()
        report.append(f"🔒 CUMULATIVE FLOOR HIT — protecting all gains!")
        report.append(f"   Floor: ${cum_floor:,.2f} | Now: ${portfolio:,.2f}")
        print("\n".join(report))
        return

    report.append(f"🛡 Daily loss: ${get_daily_loss():.2f} / ${DAILY_LOSS_LIMIT}")

    # Get positions
    try:
        positions = get_positions()
        held      = {p["symbol"]: p for p in positions}
    except Exception as e:
        report.append(f"Positions error: {e}")
        held = {}

    # End of day
    if is_end_of_day():
        if eod_close_already_done():
            report.append("EOD close already done")
            print("\n".join(report))
            return
        close_time = "12:45pm" if early_close else "4:15pm"
        report.append(f"\n⏰ {close_time} — Closing all positions")
        close_all_positions(report)
        report.append("✅ Positions closed — EOD email coming")
        print("\n".join(report))
        return

    budget_per_stock = round(WEEKLY_BUDGET / max_pos, 2)
    report.append(f"📊 Per position: ${budget_per_stock:.2f}")
    report.append("="*45)

    # ORB window
    orb_end = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    if now_et < orb_end:
        update_orb_ranges(report)
        report.append("⏳ Opening range window — watching, not trading yet")
        print("\n".join(report))
        return

    # Manage positions
    sells, freed_cash = manage_positions(held, report)
    cash += freed_cash

    # Run strategies with goal-driven settings
    orb_buys    = check_orb_breakouts(held, report, score_threshold)
    news_buys   = check_news_catalysts(held, report, score_threshold)
    mom_buys    = check_momentum_scalps(held, report, score_threshold)
    market_buys = screen_full_market(held, report)
    buy_signals = orb_buys + news_buys + mom_buys + market_buys

    # Remove duplicates
    seen = {}
    for b in buy_signals:
        sym = b["symbol"]
        if sym not in seen or b["score"] > seen[sym]["score"]:
            seen[sym] = b
    buy_signals = list(seen.values())
    buy_signals.sort(key=lambda x: x["score"], reverse=True)

    # Rotate weak positions
    if buy_signals:
        held, rotated = rotate_positions(held, buy_signals, report)
        cash += rotated * budget_per_stock
    else:
        rotated = 0

    # Execute buys
    buys, cash = execute_buys(
        buy_signals, held, cash, report,
        score_threshold, max_pos)

    # Summary
    report.append(f"\n{'='*45}")
    report.append(f"📊 CYCLE SUMMARY")
    report.append(f"{'='*45}")
    report.append(f"   Mode:      {settings['mode']}")
    report.append(f"   Positions: {len(held)} held | {buys} bought | {sells} sold")
    report.append(f"   Rotated:   {rotated} weak → strong")
    report.append(f"   Signals:   {len(orb_buys)} ORB | "
                 f"{len(news_buys)} News | {len(mom_buys)} Momentum | "
                 f"{len(market_buys)} Market")
    report.append(f"   P&L:       ${profit:+,.2f}")
    report.append(f"   Peak:      ${peak:+,.2f}")
    report.append(f"   Floor:     ${floor:+,.2f}")
    report.append(f"   Goal:      ${goal['remaining']:.2f} to min | "
                 f"Projected: ${goal['projected_2wk']:.2f} | "
                 f"{goal['cycle']}")
    report.append(f"   Daily loss: ${get_daily_loss():.2f} / ${DAILY_LOSS_LIMIT}")
    report.append(f"{'='*45}")
    report.append(f"✅ Next run in 1 min")
    report.append(f"{'='*45}")

    print("\n".join(report))

# Run once then exit
run()
