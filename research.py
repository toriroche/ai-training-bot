#!/usr/bin/env python3
"""
Agent 1 — Research Bot
Runs at 8:00am ET every trading day
Researches top stock picks and emails them to you
Writes picks.txt for the Trading Bot to read
"""
import urllib.request
import json
import os
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

# =============================================
# CONNECTIONS
# =============================================
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
FINNHUB_KEY   = os.environ.get("FINNHUB_API_KEY")
ALPACA_KEY    = os.environ.get("ALPACA_API_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY")
ET            = ZoneInfo("America/New_York")

# =============================================
# SETTINGS
# =============================================
WATCHLIST = [
    "MSFT", "AAPL", "NVDA", "AMZN", "META",
    "GOOGL", "AMD", "TSLA", "GLD", "BAC",
    "CRM", "PLTR", "SHOP", "SOFI", "F",
]
PICKS_FILE    = "/home/ubuntu/picks.txt"
APPROVED_FILE = "/home/ubuntu/approved_picks.txt"
REPLY_DEADLINE = 9  # 9:30am — auto-approve if no reply

# =============================================
# GET PRE-MARKET DATA
# =============================================
def get_premarket_data(symbol):
    """Get pre-market price change and volume"""
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{symbol}?interval=1m&range=1d&prePost=true")
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        result = data["chart"]["result"][0]
        quotes = result["indicators"]["quote"][0]
        closes = [c for c in quotes["close"] if c]
        prev_close = result.get("meta", {}).get("previousClose", 0)
        if not closes or not prev_close:
            return 0, 0
        current    = closes[-1]
        change_pct = ((current - prev_close) / prev_close) * 100
        volumes    = [v for v in quotes["volume"] if v]
        avg_vol    = sum(volumes) / len(volumes) if volumes else 0
        latest_vol = volumes[-1] if volumes else 0
        vol_ratio  = latest_vol / avg_vol if avg_vol > 0 else 0
        return round(change_pct, 2), round(vol_ratio, 1)
    except:
        return 0, 0

# =============================================
# GET NEWS SENTIMENT
# =============================================
def get_news_score(symbol):
    """Score stock based on recent news"""
    if not FINNHUB_KEY:
        return 0, "No news API"
    try:
        today    = datetime.now(ET)
        yesterday = today - timedelta(days=1)
        url = (f"https://finnhub.io/api/v1/company-news"
               f"?symbol={symbol}"
               f"&from={yesterday.strftime('%Y-%m-%d')}"
               f"&to={today.strftime('%Y-%m-%d')}"
               f"&token={FINNHUB_KEY}")
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            articles = json.loads(r.read())
        if not articles:
            return 0, "No recent news"

        pos_words = ["beat","surge","soar","jump","record","upgrade",
                    "profit","growth","strong","win","boost","rally",
                    "launch","partnership","deal","buyback","dividend"]
        neg_words = ["miss","drop","fall","plunge","loss","downgrade",
                    "weak","decline","crash","warn","cut","layoff",
                    "recall","investigation","lawsuit","debt"]

        recent  = articles[:5]
        pos     = sum(1 for a in recent for w in pos_words
                     if w in a.get("headline","").lower())
        neg     = sum(1 for a in recent for w in neg_words
                     if w in a.get("headline","").lower())
        score   = pos - neg
        headline = recent[0].get("headline","No headline")[:80]
        return score, headline
    except:
        return 0, "Error fetching news"

# =============================================
# GET EARNINGS RISK
# =============================================
def get_earnings_risk(symbol):
    """Check if earnings are coming up"""
    if not FINNHUB_KEY:
        return False, ""
    try:
        today  = datetime.now(ET)
        future = today + timedelta(days=5)
        url = (f"https://finnhub.io/api/v1/calendar/earnings"
               f"?from={today.strftime('%Y-%m-%d')}"
               f"&to={future.strftime('%Y-%m-%d')}"
               f"&symbol={symbol}&token={FINNHUB_KEY}")
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        earnings = data.get("earningsCalendar", [])
        if earnings:
            return True, f"Earnings {earnings[0].get('date','soon')}"
        return False, ""
    except:
        return False, ""

# =============================================
# GET SPY DIRECTION
# =============================================
def get_market_mood():
    """Check overall market direction"""
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               "SPY?interval=1m&range=1d&prePost=true")
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        result     = data["chart"]["result"][0]
        prev_close = result.get("meta", {}).get("previousClose", 0)
        closes     = [c for c in result["indicators"]["quote"][0]["close"] if c]
        if not closes or not prev_close:
            return "UNKNOWN", 0
        change = ((closes[-1] - prev_close) / prev_close) * 100
        if change > 0.3:
            mood = "BULLISH 📈"
        elif change < -0.3:
            mood = "BEARISH 📉"
        else:
            mood = "NEUTRAL ➡️"
        return mood, round(change, 2)
    except:
        return "UNKNOWN", 0

# =============================================
# SCORE AND RANK STOCKS
# =============================================
def research_all_stocks():
    """Research and score all watchlist stocks"""
    results = []
    print(f"Researching {len(WATCHLIST)} stocks...")

    for symbol in WATCHLIST:
        print(f"  Analyzing {symbol}...")
        try:
            change_pct, vol_ratio   = get_premarket_data(symbol)
            news_score, headline    = get_news_score(symbol)
            has_earnings, earn_msg  = get_earnings_risk(symbol)
            time.sleep(0.5)  # Rate limiting

            # Scoring system (max 10)
            score = 0

            # Pre-market momentum (0-4 points)
            if change_pct >= 2.0:   score += 4
            elif change_pct >= 1.0: score += 3
            elif change_pct >= 0.5: score += 2
            elif change_pct > 0:    score += 1

            # Volume (0-3 points)
            if vol_ratio >= 3.0:    score += 3
            elif vol_ratio >= 2.0:  score += 2
            elif vol_ratio >= 1.5:  score += 1

            # News sentiment (0-3 points)
            if news_score >= 3:     score += 3
            elif news_score >= 2:   score += 2
            elif news_score >= 1:   score += 1
            elif news_score < 0:    score -= 1

            # Earnings risk penalty
            if has_earnings:
                score -= 3

            score = max(0, min(10, score))

            results.append({
                "symbol":       symbol,
                "score":        score,
                "change_pct":   change_pct,
                "vol_ratio":    vol_ratio,
                "news_score":   news_score,
                "headline":     headline,
                "has_earnings": has_earnings,
                "earn_msg":     earn_msg,
            })
        except Exception as e:
            print(f"  Error analyzing {symbol}: {e}")
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    return results

# =============================================
# WRITE PICKS FILE
# =============================================
def write_picks(top_picks):
    """Write top picks to file for Trading Bot to read"""
    today = datetime.now(ET).strftime("%Y-%m-%d")
    with open(PICKS_FILE, "w") as f:
        f.write(f"date={today}\n")
        f.write(f"source=agent1\n")
        f.write(f"status=pending\n")
        for p in top_picks[:3]:
            f.write(f"pick={p['symbol']}\n")
    print(f"Picks written to {PICKS_FILE}")

# =============================================
# SEND RESEARCH EMAIL
# =============================================
def send_research_email(results, market_mood, market_change):
    top3  = results[:3]
    alts  = results[3:6]
    today = datetime.now(ET).strftime("%A %B %d, %Y")
    now   = datetime.now(ET).strftime("%I:%M %p")

    # Plain text version
    lines = []
    lines.append("🙏 TO GOD BE ALL THE GLORY 🙏")
    lines.append("="*50)
    lines.append(f"📊 MORNING RESEARCH REPORT")
    lines.append(f"📅 {today} | ⏰ {now} ET")
    lines.append("="*50)
    lines.append(f"\n🌍 MARKET MOOD: {market_mood} ({market_change:+.2f}%)")
    lines.append("\n" + "="*50)
    lines.append("🏆 TOP 3 PICKS FOR TODAY")
    lines.append("="*50)

    for i, p in enumerate(top3, 1):
        earn_warn = f" ⚠️ {p['earn_msg']}" if p["has_earnings"] else ""
        lines.append(f"\n{i}. {p['symbol']} — Score: {p['score']}/10{earn_warn}")
        lines.append(f"   Pre-market: {p['change_pct']:+.2f}% | "
                    f"Volume: {p['vol_ratio']:.1f}x normal")
        lines.append(f"   News: {p['headline'][:70]}")

    lines.append("\n" + "="*50)
    lines.append("📋 ALTERNATIVES")
    lines.append("="*50)
    for p in alts:
        lines.append(f"\n• {p['symbol']} — Score: {p['score']}/10 | "
                    f"{p['change_pct']:+.2f}% pre-market")
        lines.append(f"  {p['headline'][:70]}")

    lines.append("\n" + "="*50)
    lines.append("📬 HOW TO RESPOND")
    lines.append("="*50)
    lines.append(f"\nReply to this email by 9:30am ET:")
    lines.append(f"  • APPROVED — use top 3 picks as shown")
    lines.append(f"  • Use NVDA AAPL — override with your picks")
    lines.append(f"\nNo reply by 9:30am = auto-approved top 3")
    lines.append("\n" + "="*50)
    lines.append("✅ Trading Bot starts at 9:45am ET")
    lines.append("="*50)

    body = "\n".join(lines)

    # HTML version
    top3_html = ""
    for i, p in enumerate(top3, 1):
        color = "#00cc66" if p["score"] >= 7 else "#ffaa00" if p["score"] >= 5 else "#ff4444"
        earn_warn = f"<br>⚠️ <b>{p['earn_msg']}</b>" if p["has_earnings"] else ""
        top3_html += f"""
        <div style="background:#1a1a2e;border-left:4px solid {color};
                    padding:12px;margin-bottom:12px;border-radius:4px;">
            <div style="font-size:18px;font-weight:bold;color:{color};">
                {i}. {p['symbol']}
                <span style="font-size:14px;color:#888;float:right;">
                    Score: {p['score']}/10
                </span>
            </div>
            <div style="color:#aaa;margin-top:6px;">
                📈 Pre-market: <b style="color:{color};">
                {p['change_pct']:+.2f}%</b> &nbsp;|&nbsp;
                📊 Volume: <b>{p['vol_ratio']:.1f}x</b> normal
                {earn_warn}
            </div>
            <div style="color:#777;margin-top:4px;font-size:12px;">
                📰 {p['headline'][:80]}
            </div>
        </div>"""

    alts_html = ""
    for p in alts:
        alts_html += f"""
        <div style="padding:8px 0;border-bottom:1px solid #333;">
            <b style="color:#aaa;">{p['symbol']}</b>
            <span style="color:#666;font-size:12px;"> — Score: {p['score']}/10 |
            {p['change_pct']:+.2f}% pre-market</span>
            <div style="color:#555;font-size:11px;">{p['headline'][:70]}</div>
        </div>"""

    mood_color = "#00cc66" if "BULL" in market_mood else "#ff4444" if "BEAR" in market_mood else "#ffaa00"
    top3_symbols = ", ".join(p["symbol"] for p in top3)

    html = f"""
    <html><body style="font-family:Arial,sans-serif;background:#0a0a0a;
                       color:#ffffff;padding:20px;margin:0;">
    <div style="max-width:600px;margin:0 auto;">

        <div style="text-align:center;padding:20px 0;
                    border-bottom:2px solid #00cc66;">
            <div style="font-size:20px;color:#00cc66;font-weight:bold;">
                🙏 TO GOD BE ALL THE GLORY 🙏
            </div>
            <div style="font-size:24px;font-weight:bold;margin-top:8px;">
                📊 Morning Research Report
            </div>
            <div style="color:#888;margin-top:4px;">
                {today} | {now} ET
            </div>
        </div>

        <div style="background:#1a1a2e;padding:12px;margin:16px 0;
                    border-radius:8px;text-align:center;">
            <span style="font-size:16px;">🌍 Market Mood: </span>
            <span style="font-size:18px;font-weight:bold;color:{mood_color};">
                {market_mood} ({market_change:+.2f}%)
            </span>
        </div>

        <h2 style="color:#00cc66;border-bottom:1px solid #333;
                   padding-bottom:8px;">🏆 Top 3 Picks Today</h2>
        {top3_html}

        <h2 style="color:#ffaa00;border-bottom:1px solid #333;
                   padding-bottom:8px;">📋 Alternatives</h2>
        {alts_html}

        <div style="background:#1a1a2e;border:2px solid #00cc66;
                    padding:16px;margin-top:20px;border-radius:8px;">
            <h3 style="color:#00cc66;margin:0 0 12px 0;">
                📬 How To Respond
            </h3>
            <p style="color:#aaa;margin:8px 0;">
                Reply to this email by <b style="color:#fff;">9:30am ET:</b>
            </p>
            <div style="background:#0d0d1a;padding:10px;border-radius:4px;
                        margin:8px 0;">
                <code style="color:#00cc66;">APPROVED</code>
                <span style="color:#888;"> — use top 3: {top3_symbols}</span>
            </div>
            <div style="background:#0d0d1a;padding:10px;border-radius:4px;
                        margin:8px 0;">
                <code style="color:#ffaa00;">Use NVDA AAPL</code>
                <span style="color:#888;"> — override with your picks</span>
            </div>
            <p style="color:#666;font-size:12px;margin-top:12px;">
                ⏰ No reply by 9:30am = auto-approved top 3 picks
            </p>
        </div>

        <div style="text-align:center;color:#444;font-size:11px;
                    margin-top:20px;padding-top:12px;
                    border-top:1px solid #222;">
            Trading Bot starts at 9:45am ET |
            Paper Trading — No real money at risk
        </div>
    </div>
    </body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = (f"📊 Research Report {datetime.now(ET).strftime('%b %d')} | "
                         f"Top picks: {top3_symbols} | Reply by 9:30am")
        msg["From"]    = EMAIL_ADDRESS
        msg["To"]      = EMAIL_ADDRESS
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            s.sendmail(EMAIL_ADDRESS, EMAIL_ADDRESS, msg.as_string())
        print("📧 Research email sent!")
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False

# =============================================
# MAIN
# =============================================
def run():
    now = datetime.now(ET)

    # Only run on weekdays
    if now.weekday() >= 5:
        print("Weekend — research bot sleeping")
        return

    # Only run between 7am-9am
    if now.hour < 7 or now.hour >= 9:
        print(f"Not research time ({now.strftime('%I:%M %p')} ET)")
        return

    print("="*50)
    print("🙏 TO GOD BE ALL THE GLORY 🙏")
    print("Agent 1 — Research Bot Starting")
    print(f"📅 {now.strftime('%A %B %d, %Y %I:%M %p')} ET")
    print("="*50)

    # Get market mood
    print("\nChecking market mood...")
    market_mood, market_change = get_market_mood()
    print(f"Market: {market_mood} ({market_change:+.2f}%)")

    # Research all stocks
    print("\nResearching stocks...")
    results = research_all_stocks()

    # Write picks file
    write_picks(results[:3])

    # Send email
    print("\nSending research email...")
    send_research_email(results, market_mood, market_change)

    print("\n✅ Research complete!")
    print(f"Top picks: {', '.join(r['symbol'] for r in results[:3])}")

run()
