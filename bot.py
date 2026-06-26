import urllib.request
import json
import pandas as pd
from datetime import datetime
import os

ALPACA_KEY    = os.environ.get("ALPACA_API_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY")
ALPACA_URL    = "https://paper-api.alpaca.markets"

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

def place_order(symbol, qty, side):
    return alpaca_request("POST", "/v2/orders", {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "type": "market",
        "time_in_force": "day"
    })

def get_stock(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=6mo"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    prices = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    return [p for p in prices if p is not None]

def analyze(prices):
    df = pd.DataFrame(prices, columns=["close"])
    df["short"] = df["close"].rolling(5).mean()
    df["long"] = df["close"].rolling(15).mean()
    l = df.iloc[-1]
    p = df.iloc[-2]
    if p["short"] <= p["long"] and l["short"] > l["long"]:
        return "BUY"
    elif p["short"] >= p["long"] and l["short"] < l["long"]:
        return "SELL"
    return "HOLD"

def run():
    print(f"\n🤖 AI Trading Bot Report")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
    print("="*45)

    account = None
    try:
        account = get_account()
        print(f"💰 Portfolio Value: ${float(account['portfolio_value']):,.2f}")
        print(f"💵 Cash Available:  ${float(account['cash']):,.2f}")
    except Exception as e:
        print(f"⚠️ Account error: {e}")
        return

    held = {}
    try:
        positions = get_positions()
        held = {p["symbol"]: p for p in positions}
    except Exception as e:
        print(f"⚠️ Positions error: {e}")

    stocks = ["MSFT", "NVDA", "AMD"]

    for symbol in stocks:
        print(f"\n📊 {symbol}")
        try:
            prices = get_stock(symbol)
            signal = analyze(prices)
            price = prices[-1]
            print(f"   Price:  ${price:.2f}")
            print(f"   Signal: {signal}")

            if signal == "BUY" and symbol not in held:
                cash = float(account["cash"])
                qty = int((cash * 0.1) / price)
                if qty > 0:
                    place_order(symbol, qty, "buy")
                    print(f"   📈 BUY ORDER: {qty} shares @ ${price:.2f}")
                else:
                    print(f"   ⚠️ Not enough cash")

            elif signal == "SELL" and symbol in held:
                qty = int(float(held[symbol]["qty"]))
                if qty > 0:
                    place_order(symbol, qty, "sell")
                    profit = float(held[symbol]["unrealized_pl"])
                    print(f"   💰 SELL ORDER: {qty} shares | P&L: ${profit:+,.2f}")

            elif symbol in held:
                profit = float(held[symbol]["unrealized_pl"])
                print(f"   📦 Holding {held[symbol]['qty']} shares | P&L: ${profit:+,.2f}")

            else:
                print(f"   ⏳ Waiting for signal...")

        except Exception as e:
            print(f"   ⚠️ Error: {e}")

    print(f"\n{'='*45}")
    print(f"✅ Bot cycle complete")
    print(f"⏰ Next run in 30 minutes")
    print(f"{'='*45}\n")

run()
