import urllib.request
import json
import pandas as pd
from datetime import datetime
import os

# =============================================
# ALPACA CONNECTION
# =============================================
ALPACA_KEY    = os.environ.get("ALPACA_API_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY")
ALPACA_URL    = "https://paper-api.alpaca.markets"

# =============================================
# WEEKLY TEST BUDGET — ONLY CHANGE THIS
# =============================================
WEEKLY_BUDGET  = 100     # Week 1: $100
                         # Week 2: change to 1000
                         # Week 3: change to 2000
                         # Week 4: change to 3000

STOP_LOSS      = 0.02    # Sell if down 2%
TAKE_PROFIT    = 0.04    # Sell if up 4%
MAX_POSITIONS  = 3       # Max 3 positions at once

# =============================================
# ALL STOCKS TO MONITOR
# =============================================
WATCHLIST = [
    # Stable & Safe
    "MSFT", "AAPL", "GOOGL", "AMZN", "META",
    # Medium Risk
    "NVDA", "AMD", "TSLA", "CRM", "SHOP",
    # Lower Price
    "PLTR", "SOFI", "BAC", "F",
]

# =============================================
# ALPACA API FUNCTIONS
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
def get_stock(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=6mo"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    prices = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    return [p for p in prices if p is not None]

# =============================================
# BOT BRAIN — MOVING AVERAGE + STRENGTH SCORE
# =============================================
def analyze(prices):
    df = pd.DataFrame(prices, columns=["close"])
    df["short"] = df["close"].rolling(5).mean()
    df["long"]  = df["close"].rolling(15).mean()
    l = df.iloc[-1]
    p = df.iloc[-2]

    # Calculate signal strength — how far apart the averages are
    strength = ((l["short"] - l["long"]) / l["long"]) * 100

    if p["short"] <= p["long"] and l["short"] > l["long"]:
        return "BUY", round(strength, 4)
    elif p["short"] >= p["long"] and l["short"] < l["long"]:
        return "SELL", round(strength, 4)
    return "HOLD", round(strength, 4)

# =============================================
# MAIN BOT
# =============================================
def run():
    report = []
    report.append(f"🤖 AI Trading Bot Report")
    report.append(f"📅 {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
    report.append(f"💰 Weekly Budget: ${WEEKLY_BUDGET:,}")
    report.append(f"👁 Watching {len(WATCHLIST)} stocks")
    report.append("="*45)

    # Get account
    try:
        account   = get_account()
        portfolio = float(account["portfolio_value"])
        cash      = float(account["cash"])
        report.append(f"💼 Portfolio Value: ${portfolio:,.2f}")
        report.append(f"💵 Cash Available:  ${cash:,.2f}")
    except Exception as e:
        report.append(f"⚠️ Account error: {e}")
        print("\n".join(report))
        return

    # Get current positions
    try:
        positions = get_positions()
        held      = {p["symbol"]: p for p in positions}
    except Exception as e:
        report.append(f"⚠️ Positions error: {e}")
        held = {}

    budget_per_stock = round(WEEKLY_BUDGET / MAX_POSITIONS, 2)
    report.append(f"📊 Per position: ${budget_per_stock:.2f}")
    report.append("="*45)

    # ── SCAN ALL STOCKS ──────────────────────
    buy_signals  = []  # Stocks signaling BUY
    hold_signals = []  # Stocks signaling HOLD
    sell_signals = []  # Stocks to sell

    report.append(f"\n🔍 SCANNING {len(WATCHLIST)} STOCKS...\n")

    for symbol in WATCHLIST:
        try:
            prices        = get_stock(symbol)
            signal, strength = analyze(prices)
            price         = prices[-1]

            if signal == "BUY" and symbol not in held:
                buy_signals.append({
                    "symbol":   symbol,
                    "price":    price,
                    "strength": strength
                })
                report.append(f"   🟢 {symbol} @ ${price:.2f} — BUY (strength: {strength}%)")

            elif signal == "SELL" and symbol in held:
                sell_signals.append(symbol)
                report.append(f"   🔴 {symbol} @ ${price:.2f} — SELL signal")

            elif symbol in held:
                unrealized = float(held[symbol]["unrealized_pl"])
                gain_pct   = float(held[symbol]["unrealized_plpc"])
                report.append(f"   📦 {symbol} @ ${price:.2f} — HOLDING | P&L: ${unrealized:+.2f} ({gain_pct*100:+.2f}%)")

            else:
                hold_signals.append(symbol)
                report.append(f"   ⏳ {symbol} @ ${price:.2f} — HOLD")

        except Exception as e:
            report.append(f"   ⚠️ {symbol}: Error — {e}")

    # ── SELL FIRST ───────────────────────────
    report.append(f"\n{'='*45}")
    report.append(f"📤 SELLING")
    report.append(f"{'='*45}")

    sells = 0
    for symbol in sell_signals:
        try:
            unrealized = float(held[symbol]["unrealized_pl"])
            gain_pct   = float(held[symbol]["unrealized_plpc"])

            if gain_pct >= TAKE_PROFIT:
                place_fractional_order(
                    symbol, float(held[symbol]["market_value"]), "sell")
                report.append(f"   💰 SOLD {symbol} — Profit: +${unrealized:.2f}")
                sells += 1
            elif gain_pct <= -STOP_LOSS:
                place_fractional_order(
                    symbol, float(held[symbol]["market_value"]), "sell")
                report.append(f"   🛑 SOLD {symbol} — Loss: ${unrealized:.2f}")
                sells += 1
            else:
                report.append(f"   ⏳ {symbol} — Signal says sell but P&L not at threshold yet")
        except Exception as e:
            report.append(f"   ⚠️ Sell error {symbol}: {e}")

    if sells == 0:
        report.append(f"   — Nothing to sell this cycle")

    # ── BUY BEST SIGNALS ─────────────────────
    report.append(f"\n{'='*45}")
    report.append(f"📥 BUYING")
    report.append(f"{'='*45}")

    # Sort by signal strength — buy strongest first
    buy_signals.sort(key=lambda x: x["strength"], reverse=True)

    buys = 0
    for signal in buy_signals:
        symbol = signal["symbol"]

        # Stop if max positions reached
        if len(held) + buys >= MAX_POSITIONS:
            report.append(f"   ⛔ Max positions reached — skipping {symbol}")
            continue

        # Stop if no cash
        if cash < budget_per_stock:
            report.append(f"   ⚠️ Not enough cash for {symbol}")
            continue

        try:
            place_fractional_order(symbol, budget_per_stock, "buy")
            report.append(f"   📈 BOUGHT {symbol} @ ${signal['price']:.2f}")
            report.append(f"   📈 ${budget_per_stock:.2f} invested (strength: {signal['strength']}%)")
            cash -= budget_per_stock
            buys += 1
        except Exception as e:
            report.append(f"   ⚠️ Buy error {symbol}: {e}")

    if buys == 0:
        report.append(f"   — No strong BUY signals this cycle")

    # ── MANAGE EXISTING POSITIONS ─────────────
    report.append(f"\n{'='*45}")
    report.append(f"📊 POSITION MANAGEMENT")
    report.append(f"{'='*45}")

    for symbol, pos in held.items():
        if symbol in sell_signals:
            continue
        try:
            unrealized = float(pos["unrealized_pl"])
            gain_pct   = float(pos["unrealized_plpc"])

            if gain_pct >= TAKE_PROFIT:
                place_fractional_order(
                    symbol, float(pos["market_value"]), "sell")
                report.append(f"   💰 TAKE PROFIT {symbol}: +${unrealized:.2f}")
            elif gain_pct <= -STOP_LOSS:
                place_fractional_order(
                    symbol, float(pos["market_value"]), "sell")
                report.append(f"   🛑 STOP LOSS {symbol}: ${unrealized:.2f}")
            else:
                report.append(f"   📦 {symbol}: ${unrealized:+.2f} ({gain_pct*100:+.2f}%) — holding")
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")

    # ── FINAL SUMMARY ─────────────────────────
    report.append(f"\n{'='*45}")
    report.append(f"📊 SESSION SUMMARY")
    report.append(f"{'='*45}")
    report.append(f"   Stocks monitored: {len(WATCHLIST)}")
    report.append(f"   BUY signals:      {len(buy_signals)}")
    report.append(f"   Buys executed:    {buys}")
    report.append(f"   Sells executed:   {sells}")
    report.append(f"   Positions held:   {len(held)}")
    report.append(f"{'='*45}")
    report.append(f"✅ Bot cycle complete")
    report.append(f"⏰ Next run in 30 minutes")
    report.append(f"{'='*45}")

    print("\n".join(report))

run()
