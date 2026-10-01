import urllib.request
import json
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
ALPACA_KEY    = os.environ.get("ALPACA_API_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY")
ALPACA_URL    = "https://paper-api.alpaca.markets"
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD= os.environ.get("EMAIL_PASSWORD")
FINNHUB_KEY   = os.environ.get("FINNHUB_API_KEY")

# =============================================
# SETTINGS — SIMPLE & PROVEN
# =============================================
BUDGET         = 250
TAKE_PROFIT    = 0.01    # 1% — quick wins
STOP_LOSS      = 0.005   # 0.5% — tight cuts
DAILY_LOSS_MAX = 5.00    # Stop if down $5
MAX_POSITIONS  = 3
MIN_ORDER      = 1.00
PROFIT_GOAL    = 50.00
GOAL_START     = "2026-10-01"
ET             = ZoneInfo("America/New_York")
EARLY_CLOSES   = ["07-03", "11-28", "12-24"]

# File paths
SENT_FILE   = "/home/ubuntu/.bot_sent"
TRADES_FILE = "/home/ubuntu/.bot_trades"
LOSS_FILE   = "/home/ubuntu/.bot_daily_loss"
EOD_DONE    = "/home/ubuntu/.bot_eod_done"
PEAK_FILE   = "/home/ubuntu/.bot_peak"

# =============================================
# SIMPLE WATCHLIST — Proven movers
# =============================================
WATCHLIST = [
    "MSFT", "AAPL", "NVDA", "AMZN", "META",
    "GOOGL", "AMD", "TSLA", "GLD", "BAC",
]

# =============================================
# MARKET TIMING
# =============================================
def is_early_close():
    return datetime.now(ET).strftime("%m-%d") in EARLY_CLOSES

def market_close_time():
    n = datetime.now(ET)
    return n.replace(hour=13 if is_early_close() else 16,
                     minute=30, second=0, microsecond=0)

def is_market_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False, f"Weekend"
    open_  = now.replace(hour=9, minute=0, second=0, microsecond=0)
    close_ = market_close_time()
    if now < open_:
        return False, f"Pre-market"
    if now >= close_:
        return False, f"After hours"
    return True, f"OPEN {now.strftime('%I:%M %p')} ET"

def is_trade_window():
    """Only trade 9:45-11am and 2-3pm"""
    now = datetime.now(ET)
    h, m = now.hour, now.minute
    w1 = (h == 9 and m >= 45) or (h == 10)
    w2 = h == 14
    return w1 or w2

def is_orb_window():
    """9:00-9:45am — watch, don't trade"""
    now = datetime.now(ET)
    return now.hour == 9 and now.minute < 45

def is_eod():
    """Close everything at 3:30pm"""
    now = datetime.now(ET)
    return now >= now.replace(hour=15, minute=30, second=0, microsecond=0)

def eod_done():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(EOD_DONE) as f:
            return f.read().strip() == today
    except:
        return False

def mark_eod():
    with open(EOD_DONE, "w") as f:
        f.write(datetime.now(ET).strftime("%Y-%m-%d"))

def sent_today(tag="eod"):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(f"{SENT_FILE}_{tag}") as f:
            return f.read().strip() == today
    except:
        return False

def mark_sent(tag="eod"):
    with open(f"{SENT_FILE}_{tag}", "w") as f:
        f.write(datetime.now(ET).strftime("%Y-%m-%d"))

# =============================================
# LOSS & PEAK TRACKING
# =============================================
def get_loss():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(LOSS_FILE) as f:
            d = json.load(f)
        return d.get("loss", 0.0) if d.get("date") == today else 0.0
    except:
        return 0.0

def add_loss(amount):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    loss  = get_loss() + abs(amount)
    with open(LOSS_FILE, "w") as f:
        json.dump({"date": today, "loss": round(loss, 4)}, f)

def loss_exceeded():
    return get_loss() >= DAILY_LOSS_MAX

def get_peak():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(PEAK_FILE) as f:
            d = json.load(f)
        return d.get("peak", 0.0) if d.get("date") == today else 0.0
    except:
        return 0.0

def update_peak(pl):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    peak  = max(get_peak(), pl)
    with open(PEAK_FILE, "w") as f:
        json.dump({"date": today, "peak": round(peak, 4)}, f)
    return peak

# =============================================
# TRADE LOG
# =============================================
def log_trade(symbol, action, price, amount, pl=0, strategy=""):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    now   = datetime.now(ET).strftime("%I:%M %p")
    try:
        with open(TRADES_FILE) as f:
            data = json.load(f)
        if data.get("date") != today:
            data = {"date": today, "trades": []}
    except:
        data = {"date": today, "trades": []}
    data["trades"].append({
        "time": now, "symbol": symbol, "action": action,
        "price": round(price, 2), "amount": round(amount, 2),
        "pl": round(pl, 4), "strategy": strategy,
    })
    with open(TRADES_FILE, "w") as f:
        json.dump(data, f)

def get_trades():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    try:
        with open(TRADES_FILE) as f:
            data = json.load(f)
        return data.get("trades", []) if data.get("date") == today else []
    except:
        return []

def get_losers():
    """Stocks that hit stop loss today — never rebuy"""
    return {t["symbol"] for t in get_trades()
            if t.get("action") == "SELL SL" and t.get("pl", 0) < 0}

def get_traded_symbols():
    """All symbols bought today"""
    return {t["symbol"] for t in get_trades() if t.get("action") == "BUY"}

# =============================================
# ALPACA API
# =============================================
def alpaca(method, endpoint, data=None, retries=3):
    url = f"{ALPACA_URL}{endpoint}"
    last_err = None
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
            last_err = e
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 5)
    raise last_err

def place_order(symbol, dollars, side):
    if dollars < MIN_ORDER:
        return None
    return alpaca("POST", "/v2/orders", {
        "symbol": symbol, "notional": str(round(dollars, 2)),
        "side": side, "type": "market", "time_in_force": "day",
    })

def close_pos(symbol, mval, pl):
    try:
        place_order(symbol, float(mval), "sell")
        return True, float(pl)
    except Exception as e:
        if "403" in str(e) or "forbidden" in str(e).lower():
            try:
                alpaca("DELETE", f"/v2/positions/{symbol}")
                return True, float(pl)
            except:
                return False, 0
    return False, 0

def close_all(report):
    if eod_done():
        return 0
    report.append("\n🔔 Closing all positions at 3:30pm")
    try:
        alpaca("DELETE", "/v2/orders")
    except:
        pass
    time.sleep(2)
    try:
        positions = alpaca("GET", "/v2/positions")
        if not positions:
            report.append("   — No open positions")
            mark_eod()
            return 0
        total = 0
        for p in positions:
            sym  = p["symbol"]
            mval = float(p["market_value"])
            pl   = float(p["unrealized_pl"])
            if mval < 1.00:
                continue
            ok, closed_pl = close_pos(sym, mval, pl)
            if ok:
                total += closed_pl
                report.append(f"   {'💰' if closed_pl >= 0 else '🛑'} "
                             f"Closed {sym}: ${closed_pl:+.2f}")
                log_trade(sym, "CLOSE EOD", float(p["current_price"]),
                         mval, closed_pl, "EOD")
                if closed_pl < 0:
                    add_loss(abs(closed_pl))
        report.append(f"   Total: ${total:+.2f}")
        mark_eod()
        return total
    except Exception as e:
        report.append(f"   Error: {e}")
        return 0

# =============================================
# MARKET DATA
# =============================================
def get_price(symbol):
    for attempt in range(3):
        try:
            url = (f"https://data.alpaca.markets/v2/stocks/"
                   f"{symbol}/trades/latest")
            req = urllib.request.Request(url)
            req.add_header("APCA-API-KEY-ID", ALPACA_KEY)
            req.add_header("APCA-API-SECRET-KEY", ALPACA_SECRET)
            with urllib.request.urlopen(req, timeout=10) as r:
                return float(json.loads(r.read())["trade"]["p"])
        except:
            if attempt < 2:
                time.sleep(2)
    # Yahoo fallback
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{symbol}?interval=1m&range=1d")
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c]
        return closes[-1] if closes else None
    except:
        return None

def get_day_change(symbol):
    """Get today's % change and volume ratio"""
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{symbol}?interval=5m&range=1d")
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        result  = data["chart"]["result"][0]
        quotes  = result["indicators"]["quote"][0]
        closes  = [c for c in quotes["close"]  if c]
        volumes = [v for v in quotes["volume"] if v]
        if len(closes) < 2:
            return 0, 0
        open_price   = closes[0]
        curr_price   = closes[-1]
        change_pct   = ((curr_price - open_price) / open_price) * 100
        avg_vol      = sum(volumes[:-1]) / max(len(volumes)-1, 1)
        vol_ratio    = volumes[-1] / avg_vol if avg_vol > 0 else 0
        return change_pct, vol_ratio
    except:
        return 0, 0

# =============================================
# THE SIMPLE STRATEGY
# Buy stocks up 2-5% by 10am with 3x volume
# =============================================
def find_signals(held, losers, traded_today, report):
    report.append(f"\n📊 SCANNING FOR SIGNALS")
    buys = []
    remaining = 3 - len(traded_today - set(held.keys()))

    if remaining <= 0:
        report.append(f"   ⛔ Max 3 stocks per day reached")
        return buys

    for symbol in WATCHLIST:
        if symbol in held:
            continue
        if symbol in losers:
            report.append(f"   🚫 {symbol}: stop loss today — blocked")
            continue
        if symbol not in traded_today and len(traded_today) >= 3:
            continue
        try:
            change_pct, vol_ratio = get_day_change(symbol)
            price = get_price(symbol)
            if not price:
                continue

            # THE SIMPLE RULE:
            # Up 2-5% today with 3x+ volume = BUY signal
            if 2.0 <= change_pct <= 5.0 and vol_ratio >= 3.0:
                report.append(
                    f"   🚀 {symbol} @ ${price:.2f} | "
                    f"+{change_pct:.1f}% today | "
                    f"Vol: {vol_ratio:.1f}x — BUY SIGNAL!")
                buys.append({
                    "symbol":   symbol,
                    "price":    price,
                    "change":   change_pct,
                    "volume":   vol_ratio,
                    "strategy": "Momentum (2-5% + 3x vol)",
                })
            elif 1.0 <= change_pct < 2.0 and vol_ratio >= 2.0:
                report.append(
                    f"   👀 {symbol} @ ${price:.2f} | "
                    f"+{change_pct:.1f}% | "
                    f"Vol: {vol_ratio:.1f}x — watching")
            else:
                report.append(
                    f"   ⏳ {symbol} @ ${price:.2f} | "
                    f"{change_pct:+.1f}% | "
                    f"Vol: {vol_ratio:.1f}x")
        except:
            continue

    buys.sort(key=lambda x: x["volume"], reverse=True)
    return buys

# =============================================
# POSITION MANAGEMENT
# =============================================
def manage_positions(held, report):
    sells = 0
    freed = 0
    report.append(f"\n📦 OPEN POSITIONS")
    if not held:
        report.append("   — No open positions")
        return 0, 0
    for sym, pos in held.items():
        try:
            pl   = float(pos["unrealized_pl"])
            pct  = float(pos["unrealized_plpc"])
            mval = float(pos["market_value"])
            curr = float(pos["current_price"])
            if pct >= TAKE_PROFIT:
                ok, closed_pl = close_pos(sym, mval, pl)
                if ok:
                    report.append(
                        f"   💰 TAKE PROFIT {sym}: "
                        f"+${pl:.2f} (+{pct*100:.2f}%) ✅")
                    log_trade(sym, "SELL TP", curr, mval, pl, "Take Profit")
                    freed += mval
                    sells += 1
            elif pct <= -STOP_LOSS:
                ok, closed_pl = close_pos(sym, mval, pl)
                if ok:
                    report.append(
                        f"   🛑 STOP LOSS {sym}: "
                        f"${pl:.2f} ({pct*100:.2f}%) ✅")
                    log_trade(sym, "SELL SL", curr, mval, pl, "Stop Loss")
                    add_loss(abs(pl))
                    freed += mval
                    sells += 1
            else:
                report.append(
                    f"   📦 {sym}: ${pl:+.2f} ({pct*100:+.2f}%) | "
                    f"TP: +{TAKE_PROFIT*100}% "
                    f"SL: -{STOP_LOSS*100}%")
        except Exception as e:
            report.append(f"   ⚠️ {sym}: {e}")
    return sells, freed

# =============================================
# EXECUTE BUYS
# =============================================
def execute_buys(signals, held, losers, traded_today, cash, report):
    buys = 0
    report.append(f"\n📥 BUYING")
    for s in signals:
        sym = s["symbol"]
        if len(held) + buys >= MAX_POSITIONS:
            report.append(f"   ⛔ Max {MAX_POSITIONS} positions")
            break
        if sym in held or sym in losers:
            continue
        if sym not in traded_today and len(traded_today) >= 3:
            report.append(f"   ⛔ Max 3 stocks per day")
            break
        if loss_exceeded():
            report.append(f"   🚫 Daily loss limit reached")
            break
        budget = round(BUDGET / MAX_POSITIONS, 2)
        if cash < budget:
            report.append(f"   ⚠️ Not enough cash")
            continue
        try:
            result = place_order(sym, budget, "buy")
            if result:
                report.append(
                    f"   📈 BOUGHT {sym} @ ${s['price']:.2f} | "
                    f"${budget:.2f} | {s['strategy']}")
                log_trade(sym, "BUY", s["price"], budget, 0, s["strategy"])
                cash -= budget
                buys += 1
        except Exception as e:
            if "403" in str(e) or "forbidden" in str(e).lower():
                report.append(f"   ⚠️ {sym}: not tradeable")
            else:
                report.append(f"   ⚠️ {sym}: {e}")
    if buys == 0:
        report.append("   — No buys this cycle")
    return buys, cash

# =============================================
# GOAL TRACKER
# =============================================
def goal_tracker(profit):
    from datetime import date
    today   = datetime.now(ET).date()
    start   = datetime.strptime(GOAL_START, "%Y-%m-%d").date()
    elapsed = max(1, (today - start).days + 1)
    remaining = max(0, PROFIT_GOAL - profit)
    daily_avg = profit / elapsed
    return {
        "profit":    round(profit, 2),
        "remaining": round(remaining, 2),
        "pct":       round(min(100, profit / PROFIT_GOAL * 100), 1),
        "day":       elapsed,
        "avg":       round(daily_avg, 2),
        "proj_30":   round(daily_avg * 30, 2),
    }

# =============================================
# EMAIL
# =============================================
def send_email(subject, lines):
    try:
        body = "\n".join(lines)
        msg  = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_ADDRESS
        msg["To"]      = EMAIL_ADDRESS
        html = f"""<html><body style="font-family:monospace;
            background:#0a0a0a;color:#00ff00;padding:20px;">
            <div style="max-width:600px;margin:0 auto;background:#111;
            padding:20px;border-radius:10px;border:1px solid #00ff00;">
            <pre style="color:#00ff00;font-size:12px;
            line-height:1.6;">{body}</pre>
            <p style="color:#555;font-size:11px;">
            Paper Trading — No real money at risk</p>
            </div></body></html>"""
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            s.sendmail(EMAIL_ADDRESS, EMAIL_ADDRESS, msg.as_string())
        print("📧 Email sent!")
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False

# =============================================
# MAIN
# =============================================
def run():
    now     = datetime.now(ET)
    weekday = now.weekday()

    if weekday >= 5:
        print("Weekend — sleeping")
        return

    open_, status = is_market_open()

    # EOD EMAIL
    if not open_ and now.hour >= 9:
        if sent_today("eod"):
            print("EOD email sent — sleeping")
            return
        report = []
        report.append("🙏 TO GOD BE ALL THE GLORY 🙏")
        report.append("="*45)
        report.append("🤖 AI Trading Bot — End of Day Report")
        report.append(f"📅 {now.strftime('%A %B %d, %Y')}")
        report.append(f"⏰ {now.strftime('%I:%M %p')} ET")
        report.append("="*45)
        profit = 0
        try:
            acct      = alpaca("GET", "/v2/account")
            portfolio = float(acct["portfolio_value"])
            cash      = float(acct["cash"])
            profit    = portfolio - 100000
            report.append(f"💼 Portfolio: ${portfolio:,.2f}")
            report.append(f"💵 Cash:      ${cash:,.2f}")
            report.append(f"📈 P&L:       ${profit:+,.2f}")
            report.append(f"🏆 Peak:      ${get_peak():+,.2f}")
        except Exception as e:
            report.append(f"Account error: {e}")

        trades = get_trades()
        if trades:
            wins   = [t for t in trades if t.get("pl", 0) > 0]
            losses = [t for t in trades if t.get("pl", 0) < 0]
            meaningful = [t for t in trades if "EOD" not in t.get("action","")]
            report.append(f"\n📋 TODAY'S TRADES ({len(meaningful)} total):")
            report.append(f"   ✅ Wins: {len(wins)} | ❌ Losses: {len(losses)}")
            for t in meaningful:
                pl_str = f"${t['pl']:+.2f}" if t["pl"] else ""
                report.append(f"   {t['time']} {t['action']} "
                             f"{t['symbol']} @ ${t['price']:.2f} "
                             f"{pl_str}")
            losers = get_losers()
            if losers:
                report.append(f"\n🚫 Blocked (no rebuy): {', '.join(sorted(losers))}")
        else:
            report.append("\n📋 No trades today")

        g = goal_tracker(profit)
        report.append(f"\n🎯 GOAL TRACKER")
        report.append("="*45)
        if profit >= PROFIT_GOAL:
            report.append(f"   🏆 GOAL REACHED! ${profit:+.2f}")
        else:
            report.append(f"   P&L:         ${g['profit']:+.2f}")
            report.append(f"   Target:      ${PROFIT_GOAL:.2f}")
            report.append(f"   Remaining:   ${g['remaining']:.2f}")
            report.append(f"   Progress:    {g['pct']}%")
            report.append(f"   Day:         {g['day']}")
            report.append(f"   Avg/day:     ${g['avg']:.2f}")
            report.append(f"   Proj 30d:    ${g['proj_30']:.2f}")
        report.append("="*45)
        report.append(f"\n🛡 Daily loss: ${get_loss():.2f} / ${DAILY_LOSS_MAX:.2f}")
        report.append("\n✅ Market closed — see you tomorrow!")

        print("\n".join(report))
        trades = get_trades()
        wins   = len([t for t in trades if t.get("pl", 0) > 0])
        subj   = (f"📊 EOD {now.strftime('%b %d')} | "
                 f"P&L: ${profit:+,.2f} | Wins: {wins}")
        if send_email(subj, report):
            mark_sent("eod")
        return

    if not open_:
        print("Pre-market — sleeping")
        return

    # MARKET OPEN
    report = []
    report.append("🙏 TO GOD BE ALL THE GLORY 🙏")
    report.append("="*45)
    report.append("🤖 AI Trading Bot — Simple Momentum")
    report.append(f"📅 {now.strftime('%A %B %d, %Y')}")
    report.append(f"⏰ {now.strftime('%I:%M %p')} ET")
    report.append(f"💰 Budget: ${BUDGET} | "
                 f"TP: {TAKE_PROFIT*100}% | "
                 f"SL: {STOP_LOSS*100}% | "
                 f"Max loss: ${DAILY_LOSS_MAX}")
    report.append(f"📌 Strategy: Buy stocks up 2-5% with 3x volume")
    report.append("="*45)

    if loss_exceeded():
        report.append(f"🚫 Daily loss limit ${DAILY_LOSS_MAX} reached — stopped")
        print("\n".join(report))
        return

    # Get account
    profit = 0
    try:
        acct      = alpaca("GET", "/v2/account")
        portfolio = float(acct["portfolio_value"])
        cash      = float(acct["cash"])
        profit    = portfolio - 100000
        peak      = update_peak(profit)
        report.append(f"🕐 {status}")
        report.append(f"💼 Portfolio: ${portfolio:,.2f}")
        report.append(f"📈 P&L:       ${profit:+,.2f}")
        report.append(f"🏆 Peak:      ${peak:+,.2f}")
        report.append(f"🛡 Daily loss: ${get_loss():.2f} / ${DAILY_LOSS_MAX}")
        g = goal_tracker(profit)
        report.append(f"🎯 Goal: ${g['remaining']:.2f} left | "
                     f"Day {g['day']} | Avg ${g['avg']:.2f}/day")
    except Exception as e:
        report.append(f"Account error: {e}")
        print("\n".join(report))
        mark_sent("error")
        return

    report.append("="*45)

    losers       = get_losers()
    traded_today = get_traded_symbols()
    if losers:
        report.append(f"🚫 Blocked: {', '.join(sorted(losers))}")
    report.append(f"📊 Stocks today: {len(traded_today)}/3")

    # Get positions — auto-clear micros
    try:
        positions = alpaca("GET", "/v2/positions")
        held = {}
        for p in positions:
            mval = float(p["market_value"])
            sym  = p["symbol"]
            if mval >= 1.00:
                held[sym] = p
            else:
                try:
                    alpaca("DELETE", f"/v2/positions/{sym}")
                    print(f"Cleared micro: {sym}")
                except:
                    pass
    except Exception as e:
        report.append(f"Positions error: {e}")
        held = {}

    # EOD close at 3:30pm
    if is_eod():
        if eod_done():
            print("EOD already done")
            return
        close_all(report)
        report.append("✅ Positions closed — EOD email after 4:30pm")
        print("\n".join(report))
        return

    report.append(f"📊 Per position: ${BUDGET/MAX_POSITIONS:.2f}")
    report.append("="*45)

    # ORB window — watch only
    if is_orb_window():
        report.append("\n⏳ 9:00-9:45am — Watching market, not trading yet")
        report.append("   Waiting for opening volatility to settle...")
        print("\n".join(report))
        return

    # Manage positions
    sells, freed = manage_positions(held, report)
    cash += freed
    losers = get_losers()

    # Only trade in windows
    if not is_trade_window():
        t = now.strftime("%I:%M %p")
        report.append(f"\n⏸ {t} — Outside trade windows (9:45-11am, 2-3pm)")
        report.append("   Managing positions only")
        print("\n".join(report))
        return

    # Find and execute signals
    signals = find_signals(held, losers, traded_today, report)
    buys, cash = execute_buys(
        signals, held, losers, traded_today, cash, report)

    # Summary
    report.append(f"\n{'='*45}")
    report.append(f"📊 SUMMARY")
    report.append(f"{'='*45}")
    report.append(f"   Held: {len(held)} | Bought: {buys} | Sold: {sells}")
    report.append(f"   Signals: {len(signals)}")
    report.append(f"   Stocks today: {len(traded_today)}/3")
    report.append(f"   P&L: ${profit:+,.2f} | Peak: ${get_peak():+,.2f}")
    report.append(f"   Daily loss: ${get_loss():.2f} / ${DAILY_LOSS_MAX}")
    report.append(f"{'='*45}")
    report.append(f"✅ Next run in 1 min")
    report.append(f"{'='*45}")

    print("\n".join(report))

run()
