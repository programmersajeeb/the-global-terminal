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
        "https://decrypt.co/feed"
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TheGlobalTerminalBot/1.0; +https://theglobalterminal.com)"
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

def fetch_live_market_pulse():
    pulse = []
    try:
        cg_url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
        res = requests.get(cg_url, headers=HEADERS, timeout=10).json()
        mapping = [("bitcoin", "BTC/USD"), ("ethereum", "ETH/USD"), ("solana", "SOL/USD")]
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
        url = "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=5"
        events = requests.get(url, headers=HEADERS, timeout=10).json()
        for ev in events[:4]:
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

def fetch_gdelt_conflict_alerts():
    alerts = []
    try:
        url = "https://api.gdeltproject.org/api/v2/doc/doc?query=(war%20OR%20missile%20OR%20strait%20OR%20military)%20sourcelang:english&mode=artlist&maxrecords=3&format=json"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        for art in res.get("articles", [])[:2]:
            alerts.append({
                "category": "war",
                "title": art.get("title", ""),
                "summary": art.get("title", ""),
                "link": art.get("url", "")
            })
    except Exception as e:
        print(f"GDELT fetch error: {e}")
    return alerts

def fetch_rss_candidates():
    candidates = []
    for category, urls in RSS_FEEDS.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:1]:
                    candidates.append({
                        "category": category,
                        "title": entry.title,
                        "summary": getattr(entry, "summary", entry.title)[:500],
                        "link": entry.link
                    })
            except Exception as e:
                print(f"RSS error ({url}): {e}")
    return candidates

def generate_bilingual_intel(category, raw_title, raw_summary):
    prompt = f"""
    You are the Chief Intelligence Editor at 'The Global Terminal' (theglobalterminal.com), an institutional-grade financial and geopolitical news portal.
    Analyze this raw news item and generate a high-impact bilingual (English and Bengali) intelligence brief.
    Category: {category}
    Raw Headline: {raw_title}
    Raw Context: {raw_summary}

    Return ONLY a valid JSON object with this exact structure (no markdown formatting, no backticks):
    {{
      "en": {{
        "title": "Authoritative Bloomberg-style English headline in UPPERCASE",
        "summary": "60-80 word sharp, institutional analysis of the event and market impact.",
        "key_point": "1-sentence executive takeaway for traders and investors."
      }},
      "bn": {{
        "title": "আকর্ষণীয় ও প্রাতিষ্ঠানিক মানের বাংলা শিরোনাম",
        "summary": "৭০-৯০ শব্দের মধ্যে সংবাদের মূল বিশ্লেষণ এবং বিশ্ববাজারে এর প্রভাব।",
        "key_point": "বিনিয়োগকারী ও পাঠকদের জন্য ১ বাক্যের মূল টেকঅ্যাওয়ে।"
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
    seen_titles = {a.get("source_title") for a in existing_articles}

    live_market = fetch_live_market_pulse()
    live_predictions = fetch_polymarket_odds()
    candidates = fetch_rss_candidates() + fetch_gdelt_conflict_alerts()
    new_articles = []

    for item in candidates:
        if not item["title"] or item["title"] in seen_titles:
            continue

        intel = generate_bilingual_intel(item["category"], item["title"], item["summary"])
        if intel and "en" in intel and "bn" in intel:
            article_obj = {
                "id": int(time.time() * 1000),
                "category": item["category"],
                "source_title": item["title"],
                "source_link": item["link"],
                "published_at": datetime.now(timezone.utc).strftime("%d %b %Y • %H:%M UTC"),
                "en": intel["en"],
                "bn": intel["bn"]
            }
            new_articles.append(article_obj)
            seen_titles.add(item["title"])
            time.sleep(2)

        if len(new_articles) >= 5:
            break

    final_articles = (new_articles + existing_articles)[:60]
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
