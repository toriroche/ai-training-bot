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

# =============================================
# SETTINGS — STABLE & PROVEN
# =============================================
WEEKLY_BUDGET      = 100
TAKE_PROFIT        = 0.0075   # 0.75%
STOP_LOSS          = 0.003    # 0.3%
DAILY_LOSS_LIMIT   = 2.00     # Hard stop if down $2
MAX_POSITIONS      = 5
MIN_ORDER          = 1.00
EARNINGS_SAFE_DAYS = 5
PROFIT_GOAL        = 50.00
GOAL_DAYS          = 10
GOAL_START_DATE    = "2026-07-16"
ET                 = ZoneInfo("America/New_York")
EARLY_CLOSE_DATES  = ["07-03", "07-04", "11-28", "12-24"]

# File paths
SENT_FILE   = "/home/ubuntu/.bot_sent"
ORB_FILE    = "/home/ubuntu/.orb_ranges"
TRADES_FILE = "/home/ubuntu/.bot_trades"
LOSS_FILE   = "/home/ubuntu/.bot_daily_loss"
EOD_DONE    = "/home/ubuntu/.bot_eod_done"
PEAK_FILE   = "/home/ubuntu/.bot_peak_profit"
FLOOR_FILE  = "/home/ubuntu/.bot_cumulative_floor"

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
# GOAL TRACKER
# =============================================
def get_goal_tracker(current_profit):
    today            = datetime.now(ET).date()
    start            = datetime.strptime(GOAL_START_DATE, "%Y-%m-%d").date()
    days_elapsed     = max(1, (today - start).days + 1)
    cycle_day        = ((days_elapsed - 1) % GOAL_DAYS) + 1
    days_remaining   = max(0, GOAL_DAYS - cycle_day)
    remaining_profit = max(0, PROFIT_GOAL - current_profit)
    needed_per_day   = remaining_profit / max(days_remaining, 1)
    daily_avg        = current_profit / max(cycle_day, 1)
    pct_complete     = min(100, (current_profit / PROFIT_GOAL) * 100)
    days_to_goal     = remaining_profit / max(daily_avg, 0.01)
    on_track         = needed_per_day <= daily_avg
    projected_2wk    = daily_avg * GOAL_DAYS
    cycle_num        = ((days_elapsed - 1) // GOAL_DAYS) + 1
    return {
        "total_profit":   round(current_profit, 2),
        "goal":           PROFIT_GOAL,
        "remaining":      round(remaining_profit, 2),
        "pct_complete":   round(pct_complete, 1),
        "cycle_day":      cycle_day,
        "days_remaining": days_remaining,
        "needed_per_day": round(needed_per_day, 2),
        "daily_avg":      round(daily_avg, 2),
        "days_to_goal":   round(days_to_goal, 0),
        "on_track":       on_track,
        "projected_2wk":  round(projected_2wk, 2),
        "cycle":          f"Cycle {cycle_num}",
    }

# =============================================
# PEAK PROFIT TRACKING
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
    # Initialize peak on first read of day even if negative
    try:
        with open(PEAK_FILE, "r") as f:
            data = json.load(f)
        initialized = data.get("date") == today
    except Exception:
        initialized = False

    if not initialized:
        # First read of the day — set peak to current P&L
        with open(PEAK_FILE, "w") as f:
            json.dump({"date": today, "peak": round(current_pl, 4)}, f)
        return current_pl

    new_peak = max(old_peak, current_pl)
    if new_peak > old_peak:
        with open(PEAK_FILE, "w") as f:
            json.dump({"date": today, "peak": round(new_peak, 4)}, f)
    return new_peak

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
                print(f"Alpaca retry {attempt+1} in {wait}s...")
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
        return 0
    report.append(f"\n🔔 Closing all positions")
    cancel_all_orders()
    time.sleep(2)
    try:
        positions = get_positions()
        if not positions:
            report.append("   — No open positions")
            mark_eod_done()
            return 0
        total_pl = 0
        for pos in positions:
            symbol = pos["symbol"]
            pl     = float(pos["unrealized_pl"])
            mval   = float(pos["market_value"])
            price  = float(pos["current_price"])
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
                report.append(f"   ⚠️ Could not close {symbol}")
        report.append(f"   Total EOD P&L: ${total_pl:+.2f}")
        mark_eod_done()
        return total_pl
    except Exception as e:
        report.append(f"   Error: {e}")
        return 0

# =============================================
# REAL-TIME DATA
# =============================================
def get_latest_price(symbol):
    for attempt in range(3):
        try:
            url = f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest"
            req = urllib.request.Request(url)
            req.add_header("APCA-API-KEY-ID", ALPACA_KEY)
            req.add_header("APCA-API-SECRET-KEY", ALPACA_SECRET)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return float(data["trade"]["p"])
        except Exception:
            if attempt < 2:
                time.sleep(2)
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

def get_bars(symbol, timeframe="1Min", limit=30):
    for attempt in range(3):
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
            if attempt < 2:
                time.sleep(2)
    return []

# =============================================
# STRATEGY 1 — OPENING RANGE BREAKOUT
# =============================================
def update_orb_ranges(report):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    # Always start fresh each day
    try:
        with open(ORB_FILE, "r") as f:
            orb_data = json.load(f)
        if orb_data.get("date") != today:
            orb_data = {"date": today, "ranges": {}}
            print("ORB ranges reset for new day")
    except Exception:
        orb_data = {"date": today, "ranges": {}}
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
                "high": max(b["h"] for b in orb_bars),
                "low":  min(b["l"] for b in orb_bars),
            }
        except Exception:
            continue
    with open(ORB_FILE, "w") as f:
        json.dump(orb_data, f)
    report.append(f"   ORB ranges set for {len(orb_data['ranges'])} stocks")

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
            pct      = ((price - orb_high) / orb_high) * 100
            if price > orb_high * 1.002:  # 0.2% above ORB
                report.append(f"   🚀 {symbol} @ ${price:.2f} "
                             f"broke ORB ${orb_high:.2f} (+{pct:.2f}%)")
                buys.append({"symbol": symbol, "price": price,
                            "score": 90, "strategy": "ORB Breakout"})
            else:
                report.append(f"   ⏳ {symbol} @ ${price:.2f} | "
                             f"ORB: ${orb_high:.2f} | {pct:+.2f}%")
        except Exception:
            continue
    return buys

# =============================================
# STRATEGY 2 — NEWS CATALYST
# =============================================
def check_news_catalysts(held, report):
    if not FINNHUB_KEY:
        return []
    buys  = []
    today = datetime.now(ET)
    report.append(f"\n📰 NEWS CATALYST SCAN")
    for symbol in WATCHLIST[:8]:
        if symbol in held:
            continue
        try:
            from_date = (today - timedelta(hours=4)).strftime("%Y-%m-%d")
            url = (f"https://finnhub.io/api/v1/company-news?symbol={symbol}"
                   f"&from={from_date}&to={today.strftime('%Y-%m-%d')}"
                   f"&token={FINNHUB_KEY}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                articles = json.loads(r.read())
            if not articles:
                continue
            pos_words = ["beat","surge","soar","jump","record","upgrade",
                        "profit","growth","strong","win","boost","rally"]
            neg_words = ["miss","drop","fall","plunge","loss","downgrade",
                        "weak","decline","crash","warn","cut","layoff"]
            recent = articles[:5]
            pos = sum(1 for a in recent for w in pos_words
                     if w in a.get("headline","").lower())
            neg = sum(1 for a in recent for w in neg_words
                     if w in a.get("headline","").lower())
            if pos >= 3 and pos > neg:
                price = get_latest_price(symbol)
                if price:
                    report.append(f"   📢 {symbol} @ ${price:.2f} — "
                                 f"{recent[0].get('headline','')[:50]}...")
                    buys.append({"symbol": symbol, "price": price,
                                "score": 85, "strategy": "News Catalyst"})
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
    report.append(f"\n⚡ MOMENTUM SCAN")
    for symbol in WATCHLIST:
        if symbol in held:
            continue
        try:
            bars = get_bars(symbol, "1Min", 10)
            if len(bars) < 6:
                continue
            prices     = [b["c"] for b in bars]
            volumes    = [b["v"] for b in bars]
            current    = prices[-1]
            move_pct   = (prices[-1] - prices[-5]) / prices[-5]
            avg_vol    = sum(volumes[:-1]) / max(len(volumes)-1, 1)
            latest_vol = volumes[-1]
            if move_pct >= 0.005 and latest_vol > avg_vol * 1.5:
                report.append(f"   ⚡ {symbol} @ ${current:.2f} "
                             f"+{move_pct*100:.2f}% | "
                             f"Vol: {latest_vol/avg_vol:.1f}x")
                buys.append({"symbol": symbol, "price": current,
                            "score": 75, "strategy": "Momentum Scalp"})
            else:
                report.append(f"   ⏳ {symbol} @ ${current:.2f} | "
                             f"Move: {move_pct*100:+.2f}%")
        except Exception:
            continue
    return buys

# =============================================
# STRATEGY 4 — FULL MARKET SCREENER
# =============================================
def screen_full_market(held, report):
    report.append(f"\n🔭 MARKET SCREENER")
    candidates = {}

    for scrId, label, count in [
        ("day_gainers",       "Day Gainers",  50),
        ("most_actives",      "Most Active",  50),
        ("small_cap_gainers", "Small Caps",   25),
    ]:
        try:
            url = (f"https://query1.finance.yahoo.com/v1/finance/screener/"
                   f"predefined/saved?scrIds={scrId}&count={count}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            quotes = (data.get("finance", {})
                     .get("result", [{}])[0].get("quotes", []))
            added = 0
            for q in quotes:
                sym = q.get("symbol", "")
                if sym and sym not in held and sym not in WATCHLIST:
                    if sym not in candidates:
                        candidates[sym] = {
                            "change_pct": q.get("regularMarketChangePercent", 0),
                            "volume":     q.get("regularMarketVolume", 0),
                            "price":      q.get("regularMarketPrice", 0),
                            "source":     label,
                        }
                        added += 1
                    else:
                        candidates[sym]["source"] += f" + {label}"
            report.append(f"   📊 {label}: {len(quotes)} scanned, {added} new candidates")
        except Exception as e:
            report.append(f"   ⚠️ {label}: {e}")

    # Filter — only stocks with meaningful daily gain AND volume
    # Cap at 5% max gain — avoid stocks already at peak
    strong = {
        sym: d for sym, d in candidates.items()
        if 0.5 < d["change_pct"] < 5.0   # Between 0.5% and 5% — not at peak
        and d["volume"] > 200000           # Decent volume
        and 2.00 < d["price"] < 500        # Reasonable price
    }

    new_stocks = []
    sorted_candidates = sorted(
        strong.items(),
        key=lambda x: x[1]["change_pct"],
        reverse=True
    )[:10]

    for sym, info in sorted_candidates:
        try:
            # Use MA/RSI signals — same as original bot
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=3mo"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            result  = data["chart"]["result"][0]
            quotes  = result["indicators"]["quote"][0]
            prices  = [p for p in quotes["close"]  if p is not None]
            volumes = [v for v in quotes["volume"] if v is not None]

            if len(prices) < 20:
                continue

            # MA signal
            import pandas as pd
            df = pd.DataFrame(prices, columns=["close"])
            df["short"] = df["close"].rolling(5).mean()
            df["long"]  = df["close"].rolling(15).mean()
            df["mom"]   = df["close"].pct_change(3)
            l = df.iloc[-1]
            p = df.iloc[-2]

            ma_buy = (p["short"] <= p["long"] and l["short"] > l["long"] and l["mom"] > 0) or \
                     (l["mom"] > 0.02 and l["short"] > l["long"])

            # RSI
            delta    = df["close"].diff()
            gain     = delta.where(delta > 0, 0)
            loss     = -delta.where(delta < 0, 0)
            avg_gain = gain.rolling(10).mean()
            avg_loss = loss.rolling(10).mean()
            rs       = avg_gain / avg_loss
            rsi_val  = round((100 - (100 / (1 + rs))).iloc[-1], 2)

            # Volume
            avg_vol    = sum(volumes[-20:]) / 20
            latest_vol = volumes[-1]
            vol_ok     = latest_vol > avg_vol * 1.5

            score = 0
            if ma_buy:      score += 35
            if rsi_val < 35: score += 25
            elif rsi_val < 45: score += 12
            if vol_ok:      score += 20

            if score >= 55:
                current = prices[-1]
                report.append(
                    f"   🌟 {sym} @ ${current:.2f} | "
                    f"+{info['change_pct']:.1f}% today | "
                    f"Score: {score} | RSI: {rsi_val} | [{info['source']}]"
                )
                new_stocks.append({
                    "symbol":   sym,
                    "price":    current,
                    "score":    score,
                    "strategy": f"Screener ({info['source']})",
                    "momentum": info["change_pct"],
                })
        except Exception:
            continue

    if not new_stocks:
        report.append("   — No strong candidates beyond watchlist")

    return sorted(new_stocks, key=lambda x: x["score"], reverse=True)[:5]

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
            return True, f"Earnings {earnings[0].get('date','soon')}"
        return False, "Clear"
    except Exception:
        return False, "Unknown"

# =============================================
# POSITION MANAGEMENT — SIMPLE & STABLE
# =============================================
def manage_positions(held, report):
    sells = 0
    freed = 0
    report.append(f"\n📦 POSITIONS")
    for symbol, pos in held.items():
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
                    freed += market_val
                    sells += 1
            elif gain_pct <= -STOP_LOSS:
                success, pl = close_position_safely(symbol, market_val, unrealized)
                if success:
                    report.append(f"   🛑 STOP LOSS {symbol}: "
                                 f"${unrealized:.2f} ({gain_pct*100:+.2f}%) ✅")
                    log_trade(symbol, "SELL SL", curr_price,
                             market_val, unrealized, "Stop Loss")
                    add_daily_loss(abs(unrealized))
                    freed += market_val
                    sells += 1
            else:
                report.append(f"   📦 {symbol}: ${unrealized:+.2f} "
                             f"({gain_pct*100:+.2f}%) | "
                             f"TP: +{TAKE_PROFIT*100}% "
                             f"SL: -{STOP_LOSS*100}%")
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")
    return sells, freed

# =============================================
# BUYING — SIMPLE & STABLE
# =============================================
def execute_buys(buy_signals, held, cash, report):
    buys = 0
    buy_signals.sort(key=lambda x: x["score"], reverse=True)

    # Remove duplicates
    seen = {}
    for b in buy_signals:
        sym = b["symbol"]
        if sym not in seen or b["score"] > seen[sym]["score"]:
            seen[sym] = b
    buy_signals = list(seen.values())

    report.append(f"\n📥 BUYING")
    for signal in buy_signals:
        symbol = signal["symbol"]
        if len(held) + buys >= MAX_POSITIONS:
            report.append(f"   ⛔ Max {MAX_POSITIONS} positions")
            break
        if symbol in held:
            continue
        if daily_loss_exceeded():
            report.append(f"   🚫 Daily loss limit reached")
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
                             f"${budget:.2f} | {signal['strategy']}")
                log_trade(symbol, "BUY", signal["price"],
                         budget, 0, signal["strategy"])
                cash -= budget
                buys += 1
        except Exception as e:
            err = str(e).lower()
            if "403" in err or "forbidden" in err or "not tradable" in err:
                report.append(f"   ⚠️ {symbol}: Not tradeable on Alpaca — skipping")
            else:
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
        print(f"📧 Email sent!")
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

    # Weekend — silent
    if weekday >= 5:
        print("Weekend — bot monitoring silently")
        return

    market_open, market_msg = is_market_open()

    # After market close — ONE EOD email
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

        # Trades
        trades = get_todays_trades()
        if trades:
            wins   = [t for t in trades if t.get("pl",0) > 0]
            losses = [t for t in trades if t.get("pl",0) < 0]
            meaningful = [t for t in trades if "EOD" not in t.get("action","")]
            report.append(f"\n📋 TODAY'S TRADES ({len(meaningful)} total):")
            report.append(f"   ✅ Wins: {len(wins)} | ❌ Losses: {len(losses)}")
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
            report.append(f"   🏆 GOAL HIT! ${profit:+.2f} — Keep pushing!")
        else:
            report.append(f"   Total P&L:       ${goal['total_profit']:+.2f}")
            report.append(f"   Target:          ${goal['goal']:.2f} (no ceiling!)")
            report.append(f"   Remaining:       ${goal['remaining']:.2f}")
            report.append(f"   Progress:        {goal['pct_complete']}%")
            report.append(f"   Day:             {goal['cycle_day']} of {GOAL_DAYS}")
            report.append(f"   Days left:       {goal['days_remaining']}")
            report.append(f"   Need/day:        ${goal['needed_per_day']:.2f}")
            report.append(f"   Avg/day:         ${goal['daily_avg']:.2f}")
            report.append(f"   Projected 2wk:   ${goal['projected_2wk']:.2f}")
            report.append(f"   On track:        "
                         f"{'✅ YES' if goal['on_track'] else '❌ NO — need more budget'}")
        report.append(f"{'='*45}")
        report.append(f"\n🛡 Daily loss: ${get_daily_loss():.2f} / ${DAILY_LOSS_LIMIT:.2f}")
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
                  f"Wins: {wins}")
        if send_email(subject, report):
            mark_sent_today("eod")
        return

    # Pre-market — silent
    if not market_open:
        print("Pre-market — monitoring silently")
        return

    # ── MARKET IS OPEN ────────────────────────
    report = []
    report.append(f"🤖 AI Trading Bot — Intraday")
    report.append(f"📅 {now_et.strftime('%A %B %d, %Y')}")
    report.append(f"⏰ {now_et.strftime('%I:%M %p')} ET")
    report.append(f"{'⚠️ EARLY CLOSE' if early_close else '📅 Regular day'}")
    report.append(f"💰 Budget: ${WEEKLY_BUDGET} | "
                 f"TP: {TAKE_PROFIT*100}% | "
                 f"SL: {STOP_LOSS*100}% | "
                 f"Max loss: ${DAILY_LOSS_LIMIT}")
    report.append("="*45)

    # Daily loss check
    if daily_loss_exceeded():
        report.append(f"🚫 DAILY LOSS LIMIT REACHED — stopped for today")
        print("\n".join(report))
        return

    # Get account
    profit = 0
    try:
        account   = get_account()
        portfolio = float(account["portfolio_value"])
        cash      = float(account["cash"])
        profit    = portfolio - 100000
        peak      = update_peak_profit(profit)
        # Also track if we started negative — peak starts from actual P&L
        report.append(f"🕐 Market {market_msg}")
        report.append(f"💼 Portfolio: ${portfolio:,.2f}")
        report.append(f"💵 Cash:      ${cash:,.2f}")
        report.append(f"📈 P&L:       ${profit:+,.2f}")
        report.append(f"🏆 Peak:      ${peak:+,.2f}")
        report.append(f"🛡 Daily loss: ${get_daily_loss():.2f} / ${DAILY_LOSS_LIMIT}")

        # Goal progress
        goal = get_goal_tracker(profit)
        report.append(f"🎯 Goal:      ${goal['remaining']:.2f} remaining | "
                     f"Day {goal['cycle_day']}/{GOAL_DAYS} | "
                     f"{goal['cycle']}")
    except Exception as e:
        report.append(f"Account error: {e}")
        print("\n".join(report))
        mark_sent_today("error")
        return

    report.append("="*45)

    # Get positions — exclude micros from trading logic
    # AND automatically clean up micro positions
    try:
        positions = get_positions()
        held      = {}
        for p in positions:
            mval = float(p["market_value"])
            sym  = p["symbol"]
            if mval >= 1.00:
                held[sym] = p
            else:
                # Auto-delete micro positions silently
                try:
                    alpaca_request("DELETE", f"/v2/positions/{sym}")
                    print(f"Auto-cleared micro: {sym} (${mval:.4f})")
                except Exception:
                    pass
    except Exception as e:
        report.append(f"Positions error: {e}")
        held = {}

    # End of day close
    if is_end_of_day():
        if eod_close_already_done():
            print("EOD close already done")
            return
        report.append(f"\n⏰ EOD — Closing all positions")
        close_all_positions(report)
        report.append("✅ Done — EOD email coming after close")
        print("\n".join(report))
        return

    budget_per_stock = round(WEEKLY_BUDGET / MAX_POSITIONS, 2)
    report.append(f"📊 Per position: ${budget_per_stock:.2f}")
    report.append("="*45)

    # ORB window — build ranges
    orb_end = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    if now_et < orb_end:
        report.append(f"\n📐 BUILDING OPENING RANGE...")
        update_orb_ranges(report)
        report.append(f"⏳ Watching 9:00-9:30am — trading starts at 9:30am")
        print("\n".join(report))
        return

    # Manage existing positions
    sells, freed_cash = manage_positions(held, report)
    cash += freed_cash

    # Run all strategies
    orb_buys    = check_orb_breakouts(held, report)
    news_buys   = check_news_catalysts(held, report)
    mom_buys    = check_momentum_scalps(held, report)
    market_buys = screen_full_market(held, report)

    buy_signals = orb_buys + news_buys + mom_buys + market_buys

    # Execute buys
    buys, cash = execute_buys(buy_signals, held, cash, report)

    # Summary
    report.append(f"\n{'='*45}")
    report.append(f"📊 SUMMARY")
    report.append(f"{'='*45}")
    report.append(f"   Held: {len(held)} | Bought: {buys} | Sold: {sells}")
    report.append(f"   Signals: {len(orb_buys)} ORB | "
                 f"{len(news_buys)} News | "
                 f"{len(mom_buys)} Mom | "
                 f"{len(market_buys)} Market")
    report.append(f"   P&L: ${profit:+,.2f} | "
                 f"Peak: ${get_peak_profit():+,.2f}")
    report.append(f"   Daily loss: ${get_daily_loss():.2f} / ${DAILY_LOSS_LIMIT}")
    report.append(f"{'='*45}")
    report.append(f"✅ Next run in 1 min")
    report.append(f"{'='*45}")

    print("\n".join(report))

# Run once then exit
run()
