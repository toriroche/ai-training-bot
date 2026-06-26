import urllib.request
import json
import pandas as pd
from datetime import datetime
import os

# Alpaca Paper Trading Connection
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
        "symbol":        symbol,
        "qty":           qty,
        "side":          side,
        "type":          "market",
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
    df["long"]  = df["close"].rolling(15).mean()
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

    # Get account info
    try:
        account = get_account()
        print(f"💰 Portfolio Value: ${float(account['portfolio_value']):,.2f}")
        print(f"💵 Cash Available:  ${float(account['cash']):,.2f}")
    except Exception as e:
        print(f"⚠️ Account error: {e}")
        return

    # Get current positions
    try:
        positions = get_positions()
        held = {p["symbol"]: p for p in positions}
    except:
        held = {}

    stocks = ["MSFT", "NVDA", "AMD"]

    for symbol in stocks:
        print(f"\n📊 {symbol}")
        try:
