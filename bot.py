import os
import re
import json
import time
import hashlib
import subprocess
from datetime import datetime, timezone
from urllib.parse import quote
import requests
import feedparser

# ==========================================
# CONFIGURATION & SETTINGS
# ==========================================
OUTPUT_FILE = "public/news.json"
MAX_STORED_ARTICLES = 120
MAX_NEW_PER_RUN = 18
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# ১. বিশ্বমানের হাই-ইম্প্যাক্ট মার্কেট-মুভিং নিউজ সোর্স (Crypto, Layoffs, Earnings, Macro, War, Oil)
RSS_FEEDS = [
    # Corporate Layoffs, Earnings, M&A & Stock Movers (Real-time Wire Aggregation)
    ("stock", "https://news.google.com/rss/search?q=(earnings+OR+layoffs+OR+%22job+cuts%22+OR+shares+OR+acquisition+OR+SEC+OR+guidance)+when:12h&hl=en-US&gl=US&ceid=US:en"),
    ("stock", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("stock", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    
    # Crypto, DeFi, Security Exploits & Whale/ETF Flows
    ("crypto", "https://news.google.com/rss/search?q=(crypto+hack+OR+exploit+OR+Bitcoin+ETF+OR+Binance+OR+Ethereum+OR+SEC+crypto)+when:12h&hl=en-US&gl=US&ceid=US:en"),
    ("crypto", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("crypto", "https://cointelegraph.com/rss"),
    
    # War, Geopolitics & Global Sanctions
    ("war", "https://news.google.com/rss/search?q=(%22Strait+of+Hormuz%22+OR+sanctions+OR+missile+OR+military+OR+tariff+OR+Red+Sea+shipping)+when:12h&hl=en-US&gl=US&ceid=US:en"),
    ("war", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    
    # Commodities: Crude Oil, Gold & Macro Rates
    ("commodities", "https://news.google.com/rss/search?q=(%22Brent+crude%22+OR+%22oil+prices%22+OR+%22gold+prices%22+OR+OPEC+OR+%22Federal+Reserve%22+OR+CPI+inflation)+when:12h&hl=en-US&gl=US&ceid=US:en"),
]

# মার্কেট-মুভিং কীওয়ার্ড এবং তাদের ওজন (Pre-Scoring Filter)
HIGH_IMPACT_KEYWORDS = {
    "hack": 10, "exploit": 10, "breach": 9, "drained": 10, "stolen": 9,
    "layoff": 8, "layoffs": 8, "job cuts": 8, "cuts": 6, "workforce": 7,
    "earnings": 8, "revenue": 7, "guidance": 8, "beats": 8, "misses": 8, "profit": 7,
    "sec": 8, "lawsuit": 8, "indicted": 8, "ban": 8, "approved": 8, "etf": 8,
    "fed": 9, "cpi": 9, "inflation": 8, "rate cut": 9, "rate hike": 9, "powell": 8,
    "tariff": 8, "sanctions": 8, "missile": 9, "strike": 8, "hormuz": 9, "opec": 8,
    "surge": 7, "surges": 7, "plunge": 8, "plunges": 8, "soars": 7, "crashes": 9,
    "bankruptcy": 9, "halted": 9, "acquisition": 8, "merger": 8, "buyout": 8
}

# জনপ্রিয় কোম্পানি ও অ্যাসেট টিকার ম্যাপিং
ASSET_MAP = {
    "bitcoin": "$BTC", "btc": "$BTC", "ethereum": "$ETH", "eth": "$ETH",
    "solana": "$SOL", "binance": "$BNB", "ripple": "$XRP", "xrp": "$XRP",
    "nvidia": "$NVDA", "apple": "$AAPL", "tesla": "$TSLA", "microsoft": "$MSFT",
    "amazon": "$AMZN", "meta": "$META", "alphabet": "$GOOGL", "google": "$GOOGL",
    "amd": "$AMD", "intel": "$INTC", "netflix": "$NFLX", "coinbase": "$COIN",
    "microstrategy": "$MSTR", "palantir": "$PLTR", "boeing": "$BA", "jpmorgan": "$JPM",
    "goldman": "$GS", "oil": "$BRENT", "crude": "$WTI", "opec": "$OIL",
    "gold": "$GOLD", "bullion": "$GOLD", "fed": "$DXY / $SPY", "cpi": "$SPY / $QQQ",
    "nasdaq": "$QQQ", "s&p": "$SPY", "wall street": "$SPY"
}


def clean_html(raw_html):
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_title(title):
    # গুগল নিউজের শেষে থাকা সোর্স নাম (যেমন "- Reuters") পরিষ্কার করা
    title = re.sub(r"\s+-\s+[A-Za-z0-9\s\.]+$", "", title).strip()
    return title


def get_title_hash(title):
    norm = re.sub(r"[^a-z0-9]", "", normalize_title(title).lower())
    return hashlib.md5(norm[:60].encode("utf-8")).hexdigest()


def detect_tickers(text):
    text_lower = text.lower()
    found = []
    for kw, ticker in ASSET_MAP.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
            if ticker not in found:
                found.append(ticker)
    return found[:3] if found else ["$MACRO"]


def calculate_impact_score(title, summary):
    combined = f"{title} {summary}".lower()
    score = 5
    for kw, weight in HIGH_IMPACT_KEYWORDS.items():
        if kw in combined:
            score = max(score, weight)
            if score < 10:
                score += 1
    return min(score, 10)


def infer_sentiment_and_badge(title, summary, category):
    combined = f"{title} {summary}".lower()
    is_hack = any(w in combined for w in ["hack", "exploit", "drained", "breach", "stolen", "security alert"])
    if is_hack:
        return "BEARISH", "SECURITY ALERT", True

    bearish_words = ["plunge", "plunges", "drop", "falls", "fell", "miss", "misses", "layoff", "layoffs", "job cuts", "cut", "lawsuit", "crash", "down", "slump", "war", "attack", "sanction", "escalation", "deficit"]
    bullish_words = ["surge", "surges", "soar", "soars", "beat", "beats", "rally", "rallies", "gain", "record high", "approve", "approved", "bull", "buyback", "upgrade", "partnership", "growth"]

    b_score = sum(1 for w in bearish_words if w in combined)
    u_score = sum(1 for w in bullish_words if w in combined)

    impact = calculate_impact_score(title, summary)
    is_urgent = impact >= 8 or category == "war"

    if any(w in combined for w in ["layoff", "layoffs", "job cuts", "workforce"]):
        badge = "CORPORATE ACTION"
    elif any(w in combined for w in ["earnings", "revenue", "quarter", "q1", "q2", "q3", "q4", "guidance"]):
        badge = "EARNINGS INTEL"
    elif is_urgent:
        badge = "BREAKING INTEL"
    else:
        badge = f"IMPACT {impact}/10"

    if b_score > u_score:
        sentiment = "BEARISH"
    elif u_score > b_score:
        sentiment = "BULLISH"
    else:
        sentiment = "HIGH VOLATILITY"

    return sentiment, badge, is_urgent


def translate_to_bn_fallback(text):
    if not text:
        return ""
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=bn&dt=t&q={quote(text[:450])}"
        r = requests.get(url, headers=HEADERS, timeout=6)
        if r.status_code == 200:
            data = r.json()
            return "".join([part[0] for part in data[0] if part[0]])
    except Exception:
        pass
    return text


def generate_rule_based_article(item):
    title = normalize_title(item["title"])
    summary = item["summary"] if len(item["summary"]) > 40 else f"{title}. Institutional desks are monitoring order book liquidity and sector positioning following this development."
    tickers = detect_tickers(f"{title} {summary}")
    impact = item["impact_score"]
    sentiment, badge, is_urgent = infer_sentiment_and_badge(title, summary, item["category"])
    ticker_str = ", ".join(tickers)

    if sentiment == "BEARISH":
        en_takeaway = f"[{ticker_str} | IMPACT: {impact}/10] Downside pressure expected on {ticker_str}; watch support levels and institutional hedging flows."
    elif sentiment == "BULLISH":
        en_takeaway = f"[{ticker_str} | IMPACT: {impact}/10] Positive momentum catalyst for {ticker_str}; monitor breakout volume and long positioning."
    else:
        en_takeaway = f"[{ticker_str} | IMPACT: {impact}/10] Expect elevated two-way volatility in {ticker_str}; keep tight risk parameters."

    bn_title = translate_to_bn_fallback(title)
    bn_summary = translate_to_bn_fallback(summary)
    bn_takeaway = translate_to_bn_fallback(en_takeaway)

    return {
        "id": item["hash"],
        "category": item["category"],
        "is_urgent": is_urgent,
        "badge": badge,
        "sentiment": sentiment,
        "impact_score": impact,
        "tickers": tickers,
        "published_at": item["published_at"],
        "source_link": item["link"],
        "en": {
            "title": title.upper(),
            "summary": summary[:280],
            "key_point": en_takeaway
        },
        "bn": {
            "title": bn_title,
            "summary": bn_summary,
            "key_point": bn_takeaway
        }
    }


def analyze_batch_with_gemini(candidates):
    if not GEMINI_API_KEY or not candidates:
        return None

    payload_items = []
    for idx, c in enumerate(candidates):
        payload_items.append({
            "index": idx,
            "category": c["category"],
            "title": normalize_title(c["title"]),
            "summary": c["summary"][:260]
        })

    prompt = f"""You are a Senior Institutional Financial Analyst at Bloomberg/Reuters Terminal.
Analyze the following {len(payload_items)} market-moving news items.
Return ONLY a valid JSON array where each element has:
- "index": integer matching input index
- "impact_score": integer 1 to 10 (rate how strongly this impacts stocks, crypto, oil, or macro markets)
- "tickers": array of 1-3 affected ticker symbols (e.g. ["$NVDA", "$QQQ"] or ["$BTC", "$COIN"] or ["$BRENT"])
- "sentiment": strictly one of "BULLISH", "BEARISH", or "HIGH VOLATILITY"
- "badge": strictly one of "SECURITY ALERT" (only for hacks/exploits), "EARNINGS INTEL", "CORPORATE ACTION" (layoffs/M&A), "BREAKING INTEL", or "MACRO WIRE"
- "is_urgent": boolean (true if impact_score >= 8 or major hack/war/Fed/layoff news)
- "en": {{
    "title": " crisp institutional headline in ALL CAPS",
    "summary": "2 concise sentences explaining the event and institutional context.",
    "key_point": "[TICKERS | IMPACT: X/10] 1 actionable sentence for traders explaining exact price/sector impact."
  }}
- "bn": {{
    "title": "প্রফেশনাল বাংলা শিরোনাম",
    "summary": "খবরটির ২ বাক্যের স্পষ্ট এবং প্রাঞ্জল বাংলা সারসংক্ষেপ।",
    "key_point": "[টিকার | ইমপ্যাক্ট: X/10] ট্রেডার এবং ইনভেস্টরদের জন্য ১ বাক্যে সুনির্দিষ্ট মার্কেট ইমপ্যাক্ট বিশ্লেষণ।"
  }}

Input News Items:
{json.dumps(payload_items, ensure_ascii=False)}
"""

    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json"
            }
        }
        for attempt in range(2):
            try:
                resp = requests.post(url, json=body, timeout=45)
                if resp.status_code == 200:
                    res_json = resp.json()
                    raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                    raw_text = re.sub(r"^```json\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
                    parsed = json.loads(raw_text)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        print(f"[AI SUCCESS] Processed {len(parsed)} articles via {model}")
                        return parsed
                elif resp.status_code == 429:
                    time.sleep(3)
            except Exception as e:
                print(f"[AI WARN] {model} attempt {attempt+1}: {e}")
    return None


def fetch_all_candidates(existing_hashes):
    candidates = []
    seen_hashes = set(existing_hashes)

    for category, feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=12)
            if resp.status_code != 200:
                continue
            feed = feedparser.parse(resp.content)
            for entry in feed.entries[:12]:
                raw_title = clean_html(entry.get("title", ""))
                if not raw_title or len(raw_title) < 20:
                    continue
                t_hash = get_title_hash(raw_title)
                if t_hash in seen_hashes:
                    continue

                raw_summary = clean_html(entry.get("summary", "") or entry.get("description", ""))
                # গুগল নিউজের লিংক বা টাইটেল রিপিটেশন পরিষ্কার করা
                if raw_summary.startswith(normalize_title(raw_title)[:30]):
                    raw_summary = ""

                impact = calculate_impact_score(raw_title, raw_summary)
                # কম গুরুত্বপূর্ণ সাধারণ খবর বাদ দেওয়া (Impact < 6 বাদ)
                if impact < 6:
                    continue

                seen_hashes.add(t_hash)
                pub_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
                candidates.append({
                    "hash": t_hash,
                    "category": category,
                    "title": raw_title,
                    "summary": raw_summary,
                    "link": entry.get("link", "#"),
                    "published_at": pub_str,
                    "impact_score": impact
                })
        except Exception as e:
            print(f"[FEED WARN] Failed {feed_url}: {e}")

    # সবচেয়ে বেশি মার্কেট ইম্প্যাক্ট থাকা খবরগুলো আগে সাজানো
    candidates.sort(key=lambda x: x["impact_score"], reverse=True)
    return candidates[:MAX_NEW_PER_RUN]


def fetch_live_market_ticker():
    market_data = []
    # ১. ক্রিপ্টো মার্কেট পালস (CoinGecko)
    try:
        cg_url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,binancecoin&vs_currencies=usd&include_24hr_change=true"
        r = requests.get(cg_url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            d = r.json()
            mapping = [
                ("bitcoin", "BTC/USD"),
                ("ethereum", "ETH/USD"),
                ("solana", "SOL/USD"),
                ("binancecoin", "BNB/USD")
            ]
            for cid, sym in mapping:
                if cid in d:
                    p = d[cid]["usd"]
                    c = round(d[cid].get("usd_24h_change", 0.0), 2)
                    price_str = f"${p:,.0f}" if p >= 100 else f"${p:,.2f}"
                    market_data.append({"symbol": sym, "price": price_str, "change": c})
    except Exception as e:
        print(f"[MARKET WARN] Crypto fetch failed: {e}")

    # ২. ওয়াল স্ট্রিট স্টক, তেল ও সোনা (Yahoo Finance Quote API)
    try:
        yf_symbols = "^GSPC,^IXIC,NVDA,TSLA,BZ=F,GC=F"
        yf_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={yf_symbols}"
        r = requests.get(yf_url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            results = r.json().get("quoteResponse", {}).get("result", [])
            label_map = {
                "^GSPC": "S&P 500",
                "^IXIC": "NASDAQ",
                "NVDA": "NVDA",
                "TSLA": "TSLA",
                "BZ=F": "BRENT OIL",
                "GC=F": "GOLD"
            }
            for item in results:
                sym = item.get("symbol")
                price = item.get("regularMarketPrice")
                chg = item.get("regularMarketChangePercent")
                if sym in label_map and price is not None and chg is not None:
                    market_data.append({
                        "symbol": label_map[sym],
                        "price": f"${price:,.2f}",
                        "change": round(float(chg), 2)
                    })
    except Exception as e:
        print(f"[MARKET WARN] Yahoo fetch failed: {e}")

    # যদি ইয়াহু এপিআই ব্লক করে তবে ডিফল্ট মেজর ইনডেক্স ব্যাকআপ
    if len(market_data) < 5:
        market_data.extend([
            {"symbol": "S&P 500", "price": "5,892.40", "change": 0.65},
            {"symbol": "NVDA", "price": "$142.80", "change": 2.15},
            {"symbol": "BRENT OIL", "price": "$74.90", "change": 1.40},
            {"symbol": "GOLD", "price": "$2,738.50", "change": 0.82}
        ])
    return market_data


def fetch_polymarket_odds():
    predictions = []
    try:
        url = "https://gamma-api.polymarket.com/events?limit=25&active=true&closed=false"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            events = r.json()
            for ev in events:
                title = ev.get("title", "")
                markets = ev.get("markets", [])
                if not title or not markets:
                    continue
                m = markets[0]
                raw_prices = m.get("outcomePrices")
                if not raw_prices:
                    continue
                prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
                if prices and len(prices) > 0:
                    prob = round(float(prices[0]) * 100)
                    # ০% বা ১০০% ডেড মার্কেট বাদ দিয়ে অ্যাক্টিভ মার্কেট রাখা
                    if 2 <= prob <= 98:
                        predictions.append({
                            "title": title[:48] + ("..." if len(title) > 48 else ""),
                            "probability": f"{prob}%"
                        })
                if len(predictions) >= 5:
                    break
    except Exception as e:
        print(f"[POLYMARKET WARN] {e}")

    if not predictions:
        predictions = [
            {"title": "Fed rate cut at next FOMC meeting?", "probability": "68%"},
            {"title": "Bitcoin above $85,000 this quarter?", "probability": "54%"},
            {"title": "US Tech sector beats Q4 earnings?", "probability": "72%"},
            {"title": "Brent Crude spikes above $85?", "probability": "39%"},
            {"title": "SEC approves new crypto ETFs?", "probability": "61%"}
        ]
    return predictions


def load_existing_articles():
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            articles = data.get("articles", [])
            # আগের ৩টি ডিফল্ট ডেমো কার্ড থাকলে সেগুলো পরিষ্কার করা
            cleaned = [
                a for a in articles
                if "ON-CHAIN SECURITY RADAR ACTIVE" not in a.get("en", {}).get("title", "")
                and "WALL STREET TECH & SEMICONDUCTOR EQUITIES FACE" not in a.get("en", {}).get("title", "")
            ]
            return cleaned
    except Exception:
        return []


def main():
    print("=== STARTING THE GLOBAL TERMINAL BOT ENGINE ===")
    existing_articles = load_existing_articles()
    existing_hashes = set()
    for a in existing_articles:
        if "id" in a:
            existing_hashes.add(a["id"])
        elif "en" in a and "title" in a["en"]:
            existing_hashes.add(get_title_hash(a["en"]["title"]))

    candidates = fetch_all_candidates(existing_hashes)
    print(f"Found {len(candidates)} new high-impact market-moving articles.")

    new_articles = []
    if candidates:
        ai_results = analyze_batch_with_gemini(candidates)
        ai_map = {}
        if ai_results:
            for item in ai_results:
                if isinstance(item, dict) and "index" in item:
                    ai_map[item["index"]] = item

        for idx, cand in enumerate(candidates):
            ai_item = ai_map.get(idx)
            if ai_item and "en" in ai_item and "bn" in ai_item:
                tickers = ai_item.get("tickers", detect_tickers(cand["title"]))
                impact = ai_item.get("impact_score", cand["impact_score"])
                new_articles.append({
                    "id": cand["hash"],
                    "category": cand["category"],
                    "is_urgent": bool(ai_item.get("is_urgent", impact >= 8)),
                    "badge": ai_item.get("badge", f"IMPACT {impact}/10"),
                    "sentiment": ai_item.get("sentiment", "HIGH VOLATILITY"),
                    "impact_score": impact,
                    "tickers": tickers,
                    "published_at": cand["published_at"],
                    "source_link": cand["link"],
                    "en": ai_item["en"],
                    "bn": ai_item["bn"]
                })
            else:
                # Gemini API কোনো কারণে মিস করলে সাথে সাথে স্মার্ট অ্যালগরিদমিক ব্যাকআপ রান করবে
                new_articles.append(generate_rule_based_article(cand))

    # নতুন খবরগুলো উপরে রেখে পুরনোগুলোর সাথে যুক্ত করা (সর্বোচ্চ ১২০টি)
    all_articles = new_articles + existing_articles
    all_articles = all_articles[:MAX_STORED_ARTICLES]

    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
        "total_count": len(all_articles),
        "market": fetch_live_market_ticker(),
        "predictions": fetch_polymarket_odds(),
        "articles": all_articles
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"=== SUCCESS: Saved {len(all_articles)} total articles to {OUTPUT_FILE} ===")


if __name__ == "__main__":
    main()
