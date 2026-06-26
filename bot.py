import urllib.request
import json
import pandas as pd
from datetime import datetime

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
    portfolio = {
        "MSFT": {"balance": 33333, "shares": 0, "buy_price": 0, "wins": 0, "losses": 0},
        "NVDA": {"balance": 33333, "shares": 0, "buy_price": 0, "wins": 0, "losses": 0},
        "AMD":  {"balance": 33334, "shares": 0, "buy_price": 0, "wins": 0, "losses": 0},
    }

    print(f"\n🤖 AI Trading Bot Report")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
    print("="*45)

    total_profit = 0

    for symbol, data in portfolio.items():
        try:
            prices = get_stock(symbol)
            signal = analyze(prices)
            price  = prices[-1]

            print(f"\n📊 {symbol} @ ${price:.2f}")
            print(f"   Signal: {signal}")

            if signal == "BUY" and data["shares"] == 0:
                shares = int(333 / price)
                if shares > 0:
                    data["shares"]    = shares
                    data["buy_price"] = price
                    data["balance"]  -= shares * price
                    print(f"   📈 BOUGHT {shares} shares @ ${price:.2f}")

            elif data["shares"] > 0:
                gain = (price - data["buy_price"]) / data["buy_price"]
                print(f"   📉 Holding {data['shares']} shares | Gain: {gain*100:+.2f}%")

                if gain >= 0.04:
                    profit = data["shares"] * (price - data["buy_price"])
                    data["balance"] += data["shares"] * price
                    data["wins"]    += 1
                    total_profit    += profit
                    print(f"   💰 SOLD — Profit: ${profit:.2f}")
                    data["shares"]    = 0
                    data["buy_price"] = 0

                elif gain <= -0.02:
                    loss = data["shares"] * (price - data["buy_price"])
                    data["balance"] += data["shares"] * price
                    data["losses"]  += 1
                    total_profit    += loss
                    print(f"   🛑 STOP LOSS — Loss: ${loss:.2f}")
                    data["shares"]    = 0
                    data["buy_price"] = 0

        except Exception as e:
            print(f"   ⚠️ Error: {e}")

    print(f"\n{'='*45}")
    print(f"💼 Portfolio Summary")
    print(f"   Session profit: ${total_profit:+,.2f}")
    print(f"   Time: {datetime.now().strftime('%I:%M %p')}")
    print(f"{'='*45}\n")

run()
