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

# RSI Settings
RSI_PERIOD     = 14      # Standard RSI period
RSI_OVERBOUGHT = 70      # Don't buy above this
RSI_OVERSOLD   = 30      # Good buy zone below this

# Volume Settings
VOLUME_CONFIRM = 1.2     # Volume must be 20% above average to confirm

# =============================================
# ALL STOCKS TO MONITOR
# =============================================
WATCHLIST = [
    "MSFT", "AAPL", "GOOGL", "AMZN", "META",
    "NVDA", "AMD", "TSLA", "CRM", "SHOP",
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
def get_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=6mo"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    result = data["chart"]["result"][0]
    quotes = result["indicators"]["quote"][0]
    closes  = [p for p in quotes["close"]  if p is not None]
    volumes = [v for v in quotes["volume"] if v is not None]
    return closes, volumes

# =============================================
# TECHNICAL INDICATORS
# =============================================

# Moving Average Signal
def get_ma_signal(prices):
    df = pd.DataFrame(prices, columns=["close"])
    df["short"] = df["close"].rolling(5).mean()
    df["long"]  = df["close"].rolling(15).mean()
    l = df.iloc[-1]
    p = df.iloc[-2]
    strength = ((l["short"] - l["long"]) / l["long"]) * 100
    if p["short"] <= p["long"] and l["short"] > l["long"]:
        return "BUY", round(strength, 4)
    elif p["short"] >= p["long"] and l["short"] < l["long"]:
        return "SELL", round(strength, 4)
    return "HOLD", round(strength, 4)

# RSI — Relative Strength Index
# Tells us if stock is overbought or oversold
def get_rsi(prices):
    df     = pd.DataFrame(prices, columns=["close"])
    delta  = df["close"].diff()
    gain   = delta.where(delta > 0, 0)
    loss   = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(RSI_PERIOD).mean()
    avg_loss = loss.rolling(RSI_PERIOD).mean()
    rs  = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi.iloc[-1], 2)

# Volume Confirmation
# Checks if today's volume is above average — confirms signal is real
def get_volume_signal(volumes):
    if len(volumes) < 20:
        return True, 1.0
    avg_volume    = sum(volumes[-20:]) / 20
    latest_volume = volumes[-1]
    ratio = latest_volume / avg_volume if avg_volume > 0 else 1.0
    confirmed = ratio >= VOLUME_CONFIRM
    return confirmed, round(ratio, 2)

# =============================================
# NEWS SENTIMENT
# Uses Yahoo Finance RSS feed — free, no API key
# =============================================
def get_news_sentiment(symbol):
    try:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            content = r.read().decode("utf-8")

        # Count positive and negative words in headlines
        positive_words = [
            "surge", "soar", "jump", "gain", "rise", "up", "high",
            "beat", "strong", "growth", "profit", "record", "bull",
            "rally", "buy", "upgrade", "positive", "win", "boost"
        ]
        negative_words = [
            "drop", "fall", "plunge", "loss", "down", "low", "miss",
            "weak", "decline", "bear", "sell", "downgrade", "negative",
            "crash", "risk", "warn", "cut", "layoff", "lawsuit"
        ]

        content_lower = content.lower()
        pos_count = sum(content_lower.count(w) for w in positive_words)
        neg_count = sum(content_lower.count(w) for w in negative_words)

        if pos_count > neg_count * 1.5:
            return "POSITIVE", pos_count, neg_count
        elif neg_count > pos_count * 1.5:
            return "NEGATIVE", pos_count, neg_count
        else:
            return "NEUTRAL", pos_count, neg_count

    except Exception:
        return "NEUTRAL", 0, 0

# =============================================
# COMBINED ANALYSIS
# All three indicators must agree to buy
# =============================================
def full_analysis(symbol):
    prices, volumes = get_stock_data(symbol)

    # Get all three signals
    ma_signal, strength      = get_ma_signal(prices)
    rsi                      = get_rsi(prices)
    vol_confirmed, vol_ratio = get_volume_signal(volumes)
    news_sentiment, pos, neg = get_news_sentiment(symbol)
    price                    = prices[-1]

    # Score the stock 0-100
    score = 0

    # MA Signal (40 points)
    if ma_signal == "BUY":
        score += 40
    elif ma_signal == "SELL":
        score -= 40

    # RSI Score (30 points)
    if rsi < RSI_OVERSOLD:
        score += 30      # Oversold = great buy opportunity
    elif rsi < 50:
        score += 15      # Below midpoint = decent
    elif rsi > RSI_OVERBOUGHT:
        score -= 30      # Overbought = avoid buying
    else:
        score += 5

    # Volume Confirmation (20 points)
    if vol_confirmed:
        score += 20      # High volume confirms the move
    else:
        score += 5       # Low volume = less confident

    # News Sentiment (10 points)
    if news_sentiment == "POSITIVE":
        score += 10
    elif news_sentiment == "NEGATIVE":
        score -= 10

    # Final decision based on score
    if score >= 60:
        final_signal = "BUY"
    elif score <= -20:
        final_signal = "SELL"
    else:
        final_signal = "HOLD"

    return {
        "symbol":    symbol,
        "price":     price,
        "signal":    final_signal,
        "score":     score,
        "ma":        ma_signal,
        "rsi":       rsi,
        "volume":    vol_ratio,
        "news":      news_sentiment,
        "strength":  strength,
    }

# =============================================
# MAIN BOT
# =============================================
def run():
    report = []
    report.append(f"🤖 AI Trading Bot Report")
    report.append(f"📅 {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
    report.append(f"💰 Weekly Budget: ${WEEKLY_BUDGET:,}")
    report.append(f"👁 Watching {len(WATCHLIST)} stocks")
    report.append(f"🧠 Using: MA + RSI + Volume + News")
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

    budget_per_stock = round(WEEKLY_BUDGET / MAX_POSITIONS, 2)
    report.append(f"📊 Per position: ${budget_per_stock:.2f}")
    report.append("="*45)

    # ── SCAN ALL STOCKS ──────────────────────
    report.append(f"\n🔍 SCANNING {len(WATCHLIST)} STOCKS...\n")

    buy_signals  = []
    sell_signals = []

    for symbol in WATCHLIST:
        try:
            a = full_analysis(symbol)

            # RSI label
            if a["rsi"] < RSI_OVERSOLD:
                rsi_label = "oversold 🟢"
            elif a["rsi"] > RSI_OVERBOUGHT:
                rsi_label = "overbought 🔴"
            else:
                rsi_label = "normal ⚪"

            # Volume label
            vol_label = "✅ confirmed" if a["volume"] >= VOLUME_CONFIRM else "⚠️ low"

            report.append(f"   {'🟢' if a['signal'] == 'BUY' else '🔴' if a['signal'] == 'SELL' else '⏳'} {symbol} @ ${a['price']:.2f}")
            report.append(f"      Score: {a['score']}/100 | Signal: {a['signal']}")
            report.append(f"      MA: {a['ma']} | RSI: {a['rsi']} ({rsi_label})")
            report.append(f"      Volume: {a['volume']}x avg ({vol_label})")
            report.append(f"      News: {a['news']} 📰")
            report.append("")

            if a["signal"] == "BUY" and symbol not in held:
                buy_signals.append(a)
            elif a["signal"] == "SELL" and symbol in held:
                sell_signals.append(symbol)

        except Exception as e:
            report.append(f"   ⚠️ {symbol}: Error — {e}")

    # ── MANAGE EXISTING POSITIONS ─────────────
    report.append(f"{'='*45}")
    report.append(f"📦 POSITION MANAGEMENT")
    report.append(f"{'='*45}")

    for symbol, pos in held.items():
        try:
            unrealized = float(pos["unrealized_pl"])
            gain_pct   = float(pos["unrealized_plpc"])

            if gain_pct >= TAKE_PROFIT:
                place_fractional_order(
                    symbol, float(pos["market_value"]), "sell")
                report.append(f"   💰 TAKE PROFIT {symbol}: +${unrealized:.2f} ({gain_pct*100:+.2f}%)")
            elif gain_pct <= -STOP_LOSS:
                place_fractional_order(
                    symbol, float(pos["market_value"]), "sell")
                report.append(f"   🛑 STOP LOSS {symbol}: ${unrealized:.2f} ({gain_pct*100:+.2f}%)")
            else:
                report.append(f"   📦 {symbol}: ${unrealized:+.2f} ({gain_pct*100:+.2f}%) — holding")
        except Exception as e:
            report.append(f"   ⚠️ {symbol}: {e}")

    # ── SELL SIGNALS ─────────────────────────
    report.append(f"\n{'='*45}")
    report.append(f"📤 SELLING")
    report.append(f"{'='*45}")

    sells = 0
    for symbol in sell_signals:
        if symbol in held:
            try:
                place_fractional_order(
                    symbol, float(held[symbol]["market_value"]), "sell")
                report.append(f"   🔴 SOLD {symbol} — all indicators say sell")
                sells += 1
            except Exception as e:
                report.append(f"   ⚠️ Sell error {symbol}: {e}")

    if sells == 0:
        report.append(f"   — Nothing to sell this cycle")

    # ── BUY BEST SIGNALS ─────────────────────
    report.append(f"\n{'='*45}")
    report.append(f"📥 BUYING")
    report.append(f"{'='*45}")

    # Sort by score — buy highest scored first
    buy_signals.sort(key=lambda x: x["score"], reverse=True)

    buys = 0
    for signal in buy_signals:
        symbol = signal["symbol"]

        if len(held) + buys >= MAX_POSITIONS:
            report.append(f"   ⛔ Max positions reached — skipping {symbol}")
            continue

        if cash < budget_per_stock:
            report.append(f"   ⚠️ Not enough cash for {symbol}")
            continue

        try:
            place_fractional_order(symbol, budget_per_stock, "buy")
            report.append(f"   📈 BOUGHT {symbol} @ ${signal['price']:.2f}")
            report.append(f"   📈 ${budget_per_stock:.2f} invested")
            report.append(f"   📈 Score: {signal['score']}/100 | RSI: {signal['rsi']} | News: {signal['news']}")
            cash -= budget_per_stock
            buys += 1
        except Exception as e:
            report.append(f"   ⚠️ Buy error {symbol}: {e}")

    if buys == 0:
        report.append(f"   — No strong BUY signals this cycle")

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
