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
MAX_POSITIONS  = 3       # Max 3 stocks at once
STOCKS         = ["MSFT", "NVDA", "AMD"]

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
    # Buy/sell by dollar amount instead of share quantity
    return alpaca_request("POST", "/v2/orders", {
        "symbol":        symbol,
        "notional":      str(round(dollars, 2)),  # Dollar amount
        "side":          side,
        "type":          "market",
        "time_in_force": "day"
    })

# =============================================
# MARKET DATA
# =============================================
def get_stock(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=6mo"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    prices = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    return [p for p in prices if p is not None]

# =============================================
# BOT BRAIN — MOVING AVERAGE STRATEGY
# =============================================
def analyze(prices):
    df = pd.DataFrame(prices, columns=["close"])
    df["short"] = df["close"].rolling(5).mean()
    df["long"]  = df["close"].rolling(15).mean()
    l = df.iloc[-1]
    p = df.iloc[-2]
    if p["short"] <= p["long"] and l["short"] > l["long"]:
        return "BUY"
    elif p["short"] >= p["long"] and l["short"] < l["long"]:
        return "SELL"
    return "HOLD"

# =============================================
# MAIN BOT
# =============================================
def run():
    report = []
    report.append(f"🤖 AI Trading Bot Report")
    report.append(f"📅 {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
    report.append(f"💰 Weekly Budget: ${WEEKLY_BUDGET:,}")
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

    # Get positions
    try:
        positions = get_positions()
        held      = {p["symbol"]: p for p in positions}
    except Exception as e:
        report.append(f"⚠️ Positions error: {e}")
        held = {}

    # Budget per stock
    budget_per_stock = round(WEEKLY_BUDGET / MAX_POSITIONS, 2)
    report.append(f"📊 Per stock budget: ${budget_per_stock:.2f}")
    report.append("="*45)

    # Track session
    buys     = 0
    sells    = 0
    holds    = 0
    total_pl = 0

    for symbol in STOCKS:
        report.append(f"\n📊 {symbol}")
        try:
            prices = get_stock(symbol)
            signal = analyze(prices)
            price  = prices[-1]
            report.append(f"   Price:  ${price:.2f}")
            report.append(f"   Signal: {signal}")

            # BUY — fractional by dollar amount
            if signal == "BUY" and symbol not in held:
                if cash >= budget_per_stock:
                    place_fractional_order(symbol, budget_per_stock, "buy")
                    report.append(f"   📈 BUY: ${budget_per_stock:.2f} worth of {symbol}")
                    report.append(f"   📈 Approx {budget_per_stock/price:.4f} shares @ ${price:.2f}")
                    buys += 1
                else:
                    report.append(f"   ⚠️ Not enough cash available")
                    holds += 1

            # SELL — take profit or stop loss
            elif symbol in held:
                qty        = held[symbol]["qty"]
                unrealized = float(held[symbol]["unrealized_pl"])
                gain_pct   = float(held[symbol]["unrealized_plpc"])
                total_pl  += unrealized

                if gain_pct >= TAKE_PROFIT:
                    place_fractional_order(symbol, float(held[symbol]["market_value"]), "sell")
                    report.append(f"   💰 SELL (take profit): P&L: +${unrealized:.2f} ({gain_pct*100:+.2f}%)")
                    sells += 1
                elif gain_pct <= -STOP_LOSS:
                    place_fractional_order(symbol, float(held[symbol]["market_value"]), "sell")
                    report.append(f"   🛑 SELL (stop loss): P&L: ${unrealized:.2f} ({gain_pct*100:+.2f}%)")
                    sells += 1
                else:
                    report.append(f"   📦 Holding {qty} shares")
                    report.append(f"   📦 P&L: ${unrealized:+.2f} ({gain_pct*100:+.2f}%)")
                    holds += 1

            # HOLD
            else:
                report.append(f"   ⏳ Waiting for BUY signal...")
                holds += 1

        except Exception as e:
            report.append(f"   ⚠️ Error: {e}")

    # Summary
    report.append(f"\n{'='*45}")
    report.append(f"📊 SESSION SUMMARY")
    report.append(f"{'='*45}")
    report.append(f"   Buys executed:  {buys}")
    report.append(f"   Sells executed: {sells}")
    report.append(f"   Holding:        {holds}")
    report.append(f"   Open P&L:       ${total_pl:+.2f}")
    report.append(f"{'='*45}")
    report.append(f"✅ Bot cycle complete")
    report.append(f"⏰ Next run in 30 minutes")
    report.append(f"{'='*45}")

    print("\n".join(report))

run()
