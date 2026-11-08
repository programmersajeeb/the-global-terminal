import os
import json
import time
import requests
import feedparser
from datetime import datetime, timezone
from google import genai

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_KEY:
    raise ValueError("GEMINI_API_KEY পাওয়া যায়নি! GitHub Secrets চেক করুন।")

client = genai.Client(api_key=GEMINI_KEY)
DATA_FILE = "public/news.json"

RSS_FEEDS = {
    "crypto": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://rekt.news/rss/feed.xml"
    ],
    "stock": [
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "https://finance.yahoo.com/news/rssindex"
    ],
    "war": [
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.twz.com/feed"
    ],
    "commodities": [
        "https://oilprice.com/rss/main",
        "https://gcaptain.com/feed/",
        "https://www.investing.com/rss/news_11.rss"
    ]
}

SECURITY_KEYWORDS = [
    "hack", "hacked", "exploit", "exploited", "drained", "stolen",
    "breach", "rug pull", "vulnerability", "phishing", "private key", "halted"
]

URGENT_KEYWORDS = SECURITY_KEYWORDS + [
    "sec ", "lawsuit", "cpi", "fomc", "fed ", "rate cut", "rate hike",
    "missile", "strike", "invasion", "sanction", "liquidation", "crash",
    "emergency", "ceasefire", "tariff", "etf"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TheGlobalTerminalBot/2.0; +https://theglobalterminal.com)"
}

def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "articles" in data:
                    return data
        except Exception as e:
            print(f"Existing file read error: {e}")
    return {"updated_at": "", "market": [], "predictions": [], "articles": []}

def classify_urgency(title, summary):
    text = f"{title} {summary}".lower()
    is_security = any(k in text for k in SECURITY_KEYWORDS)
    is_urgent = is_security or any(k in text for k in URGENT_KEYWORDS)
    if is_security:
        return True, "SECURITY ALERT"
    if is_urgent:
        return True, "BREAKING INTEL"
    return False, "MARKET WIRE"

def fetch_live_market_pulse():
    pulse = []
    try:
        cg_url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,binancecoin&vs_currencies=usd&include_24hr_change=true"
        res = requests.get(cg_url, headers=HEADERS, timeout=10).json()
        mapping = [
            ("bitcoin", "BTC/USD"),
            ("ethereum", "ETH/USD"),
            ("solana", "SOL/USD"),
            ("binancecoin", "BNB/USD")
        ]
        for key, label in mapping:
            if key in res:
                price = res[key]["usd"]
                change = res[key].get("usd_24h_change", 0.0)
                pulse.append({
                    "symbol": label,
                    "price": f"${price:,.2f}" if price < 100 else f"${price:,.0f}",
                    "change": round(change, 2)
                })
    except Exception as e:
        print(f"CoinGecko error: {e}")

    yf_symbols = [
        ("^GSPC", "S&P 500", ""),
        ("^IXIC", "NASDAQ", ""),
        ("BZ=F", "BRENT CRUDE", "$"),
        ("GC=F", "GOLD", "$")
    ]
    for ticker, label, prefix in yf_symbols:
        try:
            yf_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            r = requests.get(yf_url, headers=HEADERS, timeout=10).json()
            meta = r["chart"]["result"][0]["meta"]
            price = meta["regularMarketPrice"]
            prev = meta.get("chartPreviousClose", price)
            change = ((price - prev) / prev) * 100 if prev else 0.0
            pulse.append({
                "symbol": label,
                "price": f"{prefix}{price:,.2f}",
                "change": round(change, 2)
            })
        except Exception as e:
            print(f"Yahoo Finance error ({ticker}): {e}")

    return pulse

def fetch_polymarket_odds():
    predictions = []
    try:
        url = "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=6"
        events = requests.get(url, headers=HEADERS, timeout=10).json()
        for ev in events[:5]:
            title = ev.get("title", "")
            markets = ev.get("markets", [])
            if title and markets:
                m = markets[0]
                prices = json.loads(m.get("outcomePrices", '["0.5", "0.5"]'))
                yes_prob = round(float(prices[0]) * 100)
                predictions.append({
                    "title": title,
                    "probability": f"{yes_prob}%"
                })
    except Exception as e:
        print(f"Polymarket error: {e}")
    return predictions

def fetch_gdelt_alerts():
    alerts = []
    queries = [
        ("crypto", "(crypto%20OR%20exchange%20OR%20defi)%20(hack%20OR%20exploit%20OR%20stolen%20OR%20breach)%20sourcelang:english"),
        ("war", "(war%20OR%20missile%20OR%20strait%20OR%20airstrike)%20sourcelang:english")
    ]
    for cat, q in queries:
        try:
            url = f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&maxrecords=3&format=json"
            res = requests.get(url, headers=HEADERS, timeout=10).json()
            for art in res.get("articles", [])[:2]:
                title = art.get("title", "").strip()
                if title:
                    alerts.append({
                        "category": cat,
                        "title": title,
                        "summary": title,
                        "link": art.get("url", "")
                    })
        except Exception as e:
            print(f"GDELT fetch error ({cat}): {e}")
    return alerts

def fetch_rss_candidates():
    candidates = []
    for category, urls in RSS_FEEDS.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:3]:
                    title = getattr(entry, "title", "").strip()
                    summary = getattr(entry, "summary", title)[:500]
                    link = getattr(entry, "link", "#")
                    if title:
                        candidates.append({
                            "category": category,
                            "title": title,
                            "summary": summary,
                            "link": link
                        })
            except Exception as e:
                print(f"RSS error ({url}): {e}")
    return candidates

def generate_bilingual_intel(category, raw_title, raw_summary, alert_badge):
    prompt = f"""
    You are the Chief Market Intelligence Officer at 'The Global Terminal', a real-time institutional trading & geopolitical terminal.
    Analyze this news item for active crypto, stock, and commodity traders.
    Category: {category}
    Alert Type: {alert_badge}
    Raw Headline: {raw_title}
    Raw Context: {raw_summary}

    Return ONLY a valid JSON object with this exact structure (no markdown formatting, no backticks):
    {{
      "sentiment": "BULLISH or BEARISH or HIGH VOLATILITY",
      "en": {{
        "title": "Sharp Bloomberg-style English headline in UPPERCASE",
        "summary": "50-70 word institutional breakdown of what happened and why it matters right now.",
        "key_point": "Direct 1-sentence actionable trading impact (e.g., affected assets, support/resistance risk, or sector impact)."
      }},
      "bn": {{
        "title": "সংক্ষিপ্ত ও প্রাতিষ্ঠানিক মানের ব্রেকিং বাংলা শিরোনাম",
        "summary": "৬০-৭৫ শব্দের মধ্যে মূল ঘটনা এবং মার্কেটে এর তাৎক্ষণিক প্রভাবের বিশ্লেষণ।",
        "key_point": "ট্রেডার ও ইনভেস্টরদের জন্য ১ বাক্যে সরাসরি মার্কেট ইমপ্যাক্ট ও করণীয় সতর্কতা।"
      }}
    }}
    """
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return json.loads(text.strip())
    except Exception as e:
        print(f"Gemini API error: {e}")
        return None

def main():
    os.makedirs("public", exist_ok=True)
    db = load_existing_data()
    existing_articles = db.get("articles", [])
    seen_titles = {a.get("source_title") for a in existing_articles if a.get("source_title")}

    live_market = fetch_live_market_pulse()
    live_predictions = fetch_polymarket_odds()
    raw_candidates = fetch_gdelt_alerts() + fetch_rss_candidates()

    scored_candidates = []
    for item in raw_candidates:
        if not item["title"] or item["title"] in seen_titles:
            continue
        is_urgent, badge = classify_urgency(item["title"], item["summary"])
        item["is_urgent"] = is_urgent
        item["badge"] = badge
        scored_candidates.append(item)
        seen_titles.add(item["title"])

    # ব্রেকিং ও হ্যাক অ্যালার্টগুলো সবার আগে প্রসেস করার জন্য সর্টিং
    scored_candidates.sort(key=lambda x: (0 if x["badge"] == "SECURITY ALERT" else (1 if x["is_urgent"] else 2)))

    new_articles = []
    for item in scored_candidates[:12]:
        intel = generate_bilingual_intel(item["category"], item["title"], item["summary"], item["badge"])
        if intel and "en" in intel and "bn" in intel:
            article_obj = {
                "id": int(time.time() * 1000),
                "category": item["category"],
                "is_urgent": item["is_urgent"],
                "badge": item["badge"],
                "sentiment": intel.get("sentiment", "HIGH VOLATILITY"),
                "source_title": item["title"],
                "source_link": item["link"],
                "published_at": datetime.now(timezone.utc).strftime("%d %b %Y • %H:%M UTC"),
                "en": intel["en"],
                "bn": intel["bn"]
            }
            new_articles.append(article_obj)
            time.sleep(4)

    combined = new_articles + existing_articles
    # ব্রেকিং নিউজগুলো যেন সবসময় উপরে থাকে এবং মোট ১২০টি খবর সংরক্ষিত থাকে
    final_articles = combined[:120]

    output_payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
        "market": live_market if live_market else db.get("market", []),
        "predictions": live_predictions if live_predictions else db.get("predictions", []),
        "articles": final_articles
    }

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
