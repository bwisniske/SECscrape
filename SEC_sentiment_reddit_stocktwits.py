import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import re
import yfinance as yf

import smtplib
import ssl
from email.message import EmailMessage

import praw
from textblob import TextBlob

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time

import pandas as pd
from datetime import datetime

def score_stocktwits(sent_str, vol_str):
    """Convert Stocktwits 0–10 gauges into a 0–30 point score (avg * 3)."""
    def to_num(s):
        try:
            return float(str(s).replace("+", "").strip())
        except:
            return 0.0
    sent = to_num(sent_str)   # e.g. "+3.9" -> 3.9
    vol  = to_num(vol_str)    # e.g. "+4.5" -> 4.5
    avg  = (sent + vol) / 2.0
    points = avg * 3.0        # 0–10 -> 0–30
    return max(0.0, min(30.0, round(points, 1)))

# --- Stocktwits Sentiment (Browser-Based) ---
def get_stocktwits_sentiment(ticker):
    url = f"https://stocktwits.com/symbol/{ticker.upper()}"
    options = Options()
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-site-isolation-trials")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("start-maximized")
    options.add_argument("disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("user-agent=Mozilla/5.0")
    options.add_argument("--headless")  # Optional for background running

    driver = webdriver.Chrome(options=options)
    driver.get(url)
    time.sleep(5)

    try:
        elements = driver.find_elements(By.CLASS_NAME, "GaugeScore_gaugeNumber__R1hoe")
        sentiment_score = int(elements[0].text) if len(elements) > 0 else None
        message_volume = int(elements[1].text) if len(elements) > 1 else None

        sentiment_signal = f"+{sentiment_score / 10:.1f}" if sentiment_score is not None else "Not found"
        volume_signal = f"+{message_volume / 10:.1f}" if message_volume is not None else "Not found"

        print(f"📊 Stocktwits for {ticker.upper()}: Sentiment = {sentiment_signal}, Volume = {volume_signal}")
        return sentiment_signal, volume_signal, url

    except Exception as e:
        print(f"❌ Error fetching Stocktwits sentiment for {ticker.upper()}: {e}")
        return "Error", "Error", url

    finally:
        driver.quit()

# --- Reddit Sentiment ---
reddit = praw.Reddit(
    client_id="jK85VOrpW9DEyhcutNrFHw",
    client_secret="n410I88Okn2kRVq_eNuMGeY4kzdtKw",
    user_agent="SentimentScanner",
    username="bwwisniske",
    password="Flydown1@"
)

def get_reddit_sentiment(ticker):
    subreddit_names = ["stocks", "wallstreetbets", "pennystocks", "investing"]
    mention_count = 0
    sentiment_score = 0
    reddit_urls = []

    print(f"🔎 Searching Reddit sentiment for: {ticker}")

    for subreddit_name in subreddit_names:
        subreddit = reddit.subreddit(subreddit_name)
        for submission in subreddit.search(f"${ticker}", limit=15, sort="new"):
            title_text = submission.title.lower()
            self_text = submission.selftext.lower()

            if ticker.lower() in title_text or ticker.lower() in self_text:
                mention_count += 1

                text = submission.title + " " + submission.selftext
                sentiment = TextBlob(text).sentiment.polarity

                if sentiment > 0.1:
                    sentiment_score += 2
                elif sentiment < -0.1:
                    sentiment_score -= 2

                reddit_urls.append(submission.url)

            if len(reddit_urls) >= 3:
                break

    if mention_count >= 10:
        sentiment_score += 10
    elif mention_count >= 5:
        sentiment_score += 5

    sentiment_score = max(0, min(30, sentiment_score))

    print(f"✅ Reddit Score for {ticker}: {sentiment_score}, Mentions: {mention_count}")
    return sentiment_score, reddit_urls[:3]

# --- Email (Optional) ---
def send_email_with_attachment(to_email, subject, body, attachment_path):
    from_email = "bwisniske@gmail.com"
    app_password = "jzyl auwl nagc bvzh"

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with open(attachment_path, "rb") as f:
        file_data = f.read()
        filename = attachment_path.split("\\")[-1]
        msg.add_attachment(file_data, maintype="application", subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=filename)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as smtp:
        smtp.login(from_email, app_password)
        smtp.send_message(msg)
        print(f"📧 Email sent to {to_email} with attachment: {filename}")

# --- Yahoo Technicals ---
def get_yahoo_technicals(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "Current Price": info.get("currentPrice", "N/A"),
            "52W Low": info.get("fiftyTwoWeekLow", "N/A"),
            "52W High": info.get("fiftyTwoWeekHigh", "N/A"),
            "Volume": info.get("volume", "N/A"),
            "Avg Volume": info.get("averageVolume", "N/A"),
            "Market Cap": info.get("marketCap", "N/A"),
            "Forward PE": info.get("forwardPE", "N/A")
        }
    except Exception as e:
        print(f"⚠️ Yahoo error for {ticker}: {e}")
        return {k: "Error" for k in ["Current Price", "52W Low", "52W High", "Volume", "Avg Volume", "Market Cap", "Forward PE"]}
# --- Headers for SEC ---
HEADERS = {
    "User-Agent": "InsiderTracker/1.0 (contact: your_email@example.com)"
}

# --- SEC Form 4 Feed Parser ---
def get_recent_form4_filings(limit=1000):
    feed_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&owner=only&count=1000&output=atom"
    resp = requests.get(feed_url, headers=HEADERS)
    root = ET.fromstring(resp.content)

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)

    seen_accession_numbers = set()
    xml_links = []

    for entry in entries:
        href = entry.find("atom:link", ns).attrib["href"]
        match = re.search(r'(\d{10}-\d{2}-\d{6})', href)
        if not match:
            continue

        accession = match.group(1)
        if accession in seen_accession_numbers:
            continue

        seen_accession_numbers.add(accession)

        cik_match = re.search(r'/edgar/data/(\d+)', href)
        if not cik_match:
            continue

        cik = cik_match.group(1)
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.htm"
        print(f"🔗 Visiting: {index_url}")

        try:
            filing_page = requests.get(index_url, headers=HEADERS)
            soup = BeautifulSoup(filing_page.content, "html.parser")
            table = soup.find("table", class_="tableFile")
            if not table:
                continue

            for row in table.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue

                doc_type = cols[3].text.strip()
                doc_name = cols[2].text.strip().lower()

                if doc_type == "4" and doc_name.endswith(".xml"):
                    xml_url = "https://www.sec.gov" + cols[2].find("a")["href"]
                    print(f"✅ Found XML: {xml_url}")
                    xml_links.append(xml_url)
                    break

            if len(xml_links) >= limit:
                break

        except Exception as e:
            print(f"⚠️ Error parsing index page: {e}")

    return xml_links

# --- SEC Form 4 XML Parser ---
def parse_form4_xml(xml_url):
    try:
        resp = requests.get(xml_url, headers=HEADERS, timeout=20)
        xml_data = resp.content

        if b"<html" in xml_data[:200].lower():
            print("⚠️ Skipped: HTML instead of XML.")
            return None

        lines = xml_data.decode("utf-8").splitlines()
        if lines and lines[0].startswith("<?xml-stylesheet"):
            xml_data = "\n".join(lines[1:]).encode("utf-8")

        root = ET.fromstring(xml_data)

        company_name = root.findtext(".//issuer/issuerName", default="Unknown")
        ticker = root.findtext(".//issuer/issuerTradingSymbol", default="N/A")
        name = root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName", default="Unknown")
        role = root.findtext(".//reportingOwner/reportingOwnerRelationship/officerTitle")
        role = role.strip() if role else "N/A"

        relationship_note = root.findtext(".//reportingOwner/reportingOwnerRelationship/otherText", default="").lower()
        is_trust = "trust" in relationship_note or "trust" in name.lower()

        trades = []
        for txn in root.findall(".//nonDerivativeTransaction"):
            security = txn.findtext(".//securityTitle/value", default="Unknown")
            if "common" not in security.lower():
                continue

            code = txn.findtext(".//transactionCoding/transactionCode")
            if code not in ("P", "I"):
                continue

            shares = txn.findtext(".//transactionAmounts/transactionShares/value")
            price = txn.findtext(".//transactionAmounts/transactionPricePerShare/value")
            ownership = txn.findtext(".//ownershipNature/directOrIndirectOwnership/value")

            if shares and ownership:
                shares = float(shares)
                price = float(price) if price else 0.0
                trades.append({
                    "code": code,
                    "shares": shares,
                    "price": price,
                    "ownership": ownership,
                    "security": security
                })

        if not trades:
            return {
                "company": company_name,
                "ticker": ticker,
                "name": name,
                "role": role,
                "is_trust": is_trust,
                "trades": [],
                "xml_url": xml_url
            }

        return {
            "company": company_name,
            "ticker": ticker,
            "name": name,
            "role": role,
            "is_trust": is_trust,
            "trades": trades,
            "xml_url": xml_url
        }

    except Exception as e:
        print(f"❌ Error parsing XML: {e}")
        return None

import re

def get_role_points(role: str) -> int:
    """
    Map messy Form 4 officer/board titles to points.
    Returns the *highest* matching tier.
    """
    if not role:
        return 0
    r = role.lower()
    # normalize punctuation/spacing
    r = re.sub(r"[\.\,\-/]+", " ", r)
    r = re.sub(r"\s+", " ", r).strip()

    # --- Tier A (18 pts): top leadership
    tier_a = [
        "chief executive", "ceo",
        "chief financial", "cfo",
        "chief operating", "coo",
        "president",
        "executive chairman", "chairman", "chair"
    ]

    # --- Tier B (16 pts): other C-level functional chiefs
    tier_b = [
        "chief technology", "cto",
        "chief information", "cio",
        "chief marketing", "cmo",
        "chief accounting", "cao",
        "chief legal", "clo",
        "general counsel",
        "chief human resources", "chro"
    ]

    # --- Tier C (12 pts): senior leadership & board leadership (not full chair)
    tier_c = [
        "executive vice president", "evp",
        "senior vice president", "svp",
        "lead independent director",
        "vice chairman",
        "managing director"
    ]

    # --- Tier D (10 pts): board/director, officer (generic), VP
    tier_d = [
        "director",
        "officer",      # generic officer flag
        "vice president", "vp"
    ]

    # --- Tier E (8 pts): corporate officers (non-C-level)
    tier_e = [
        "treasurer",
        "secretary",
        "controller"
    ]

    # --- Special owner flag (often appears as relationship checkbox text)
    # If you ever pass relationship text here, we’ll credit it:
    owner_10 = ["10% owner", "10 percent owner", "ten percent owner"]

    # helper to test any keyword present
    def has_any(keywords):
        return any(k in r for k in keywords)

    if has_any(tier_a): return 18
    if has_any(tier_b): return 16
    if has_any(tier_c): return 12
    if has_any(tier_d): return 10
    if has_any(tier_e): return 8
    if has_any(owner_10): return 12

    return 0

# --- Insider Signal Strength v2 (0–100) ---
def calculate_signal_strength_v2(
    trade_code,            # "P" or "I"
    role,                  # officerTitle string (may be "N/A")
    ownership,             # "D" or "I" (Direct / Indirect)
    is_trust,              # bool
    price,                 # float
    fifty_two_week_low,    # float
    trade_value,           # float
    volume,                # float
    avg_volume             # float
):
    score = 0.0

    # 1) Transaction Type
    if trade_code == "P":
        score += 20
    elif trade_code == "I":
        score += 10

    # 2) Insider Role (expanded mapping)
    score += get_role_points(role)

    # 3) Ownership (Direct > Indirect)
    if (ownership or "").strip().upper().startswith("D"):
        score += 8

    # 4) Trust (Non-trust preferred)
    if not is_trust:
        score += 7

    # 5) Price vs 52-Week Low
    try:
        p = float(price); lo = float(fifty_two_week_low)
        if lo > 0:
            if p <= lo * 1.05:
                score += 25
            elif p <= lo * 1.10:
                score += 15
    except:
        pass

    # 6) Trade Value
    try:
        tv = float(trade_value)
        if tv >= 500_000:
            score += 12
        elif tv >= 100_000:
            score += 8
        elif tv >= 25_000:
            score += 4
    except:
        pass

    # 7) Unusual Volume (today vs. avg)
    try:
        v = float(volume); av = float(avg_volume)
        if av > 0 and v >= av * 2.0:
            score += 10
    except:
        pass

    return max(0.0, min(100.0, round(score, 1)))

def main():
    print("\n📥 Fetching latest Form 4 filings...\n")
    xml_urls = get_recent_form4_filings(limit=1000)

    all_trades = []

    for xml_url in xml_urls:
        result = parse_form4_xml(xml_url)
        if not result:
            continue

        yahoo_data = get_yahoo_technicals(result["ticker"])

        for trade in result["trades"]:
            price = trade["price"]
            shares = trade["shares"]
            trade_value = shares * price if price and shares else 0

            try:
                fifty_two_low = float(yahoo_data["52W Low"])
                near_52w_low = "Yes" if price <= fifty_two_low * 1.05 else "No"
            except:
                near_52w_low = "N/A"

            large_trade = "Yes" if trade_value >= 100000 or shares >= 10000 else "No"

            signal_strength = calculate_signal_strength_v2(
                trade_code=trade["code"],
                role=result["role"],
                ownership=trade["ownership"],   # NEW
                is_trust=result["is_trust"],
                price=trade["price"],
                fifty_two_week_low=yahoo_data["52W Low"],
                trade_value=trade_value,
                volume=yahoo_data["Volume"],
                avg_volume=yahoo_data["Avg Volume"]
            )


            reddit_score, reddit_urls = get_reddit_sentiment(result["ticker"])
            stocktwits_sentiment, stocktwits_volume, stocktwits_link = get_stocktwits_sentiment(result["ticker"])

            # --- New, balanced sentiment & total scoring ---
            # 1) Convert Stocktwits gauges (0–10) -> points (0–30)
            stocktwits_points = score_stocktwits(stocktwits_sentiment, stocktwits_volume)

            # 2) Blend Reddit (up to 30) and Stocktwits (up to 30) into a single 0–30 sentiment score.
            #    Keep Stocktwits slightly heavier than Reddit (75/25), then cap at 30.
            combined_sentiment_score = reddit_score * 0.25 + stocktwits_points * 0.75
            combined_sentiment_score = round(min(30.0, combined_sentiment_score), 1)

            # 3) Compute a unified 0–100 TOTAL score with SEC weighted heavier (75/25 split).
            #    - SEC Signal (0–100) contributes up to 75 points
            #    - Sentiment (0–30) contributes up to 25 points
            total_score_100 = round(signal_strength * 0.75 + (combined_sentiment_score / 30.0) * 25.0, 1)

            def label_total(score_100):
                if score_100 >= 75:
                    return "🚀 High Conviction Buy"
                if score_100 >= 60:
                    return "✅ Solid Trade Opportunity"
                if score_100 >= 45:
                    return "👀 Speculative / Watch"
                return "❌ Ignore"



            label = label_total(total_score_100)

            all_trades.append({
                "Date": datetime.now().strftime("%Y-%m-%d"),
                "Company": result["company"],
                "Ticker": result["ticker"],
                "Insider": result["name"],
                "Role": result["role"],
                "Trust": "Yes" if result["is_trust"] else "No",
                "Type": trade["code"],
                "Security": trade["security"],
                "Shares": shares,
                "Price": price,
                "Ownership": trade["ownership"],
                "Trade Value ($)": round(trade_value, 2),
                "Near 52W Low": near_52w_low,
                "Large Trade": large_trade,
                "Signal Strength": signal_strength,
                "StockTwits Sentiment Gauge (0-10)": stocktwits_sentiment,
                "StockTwits Volume Gauge (0-10)": stocktwits_volume,
                #"StockTwits Points (0-30)": stocktwits_points,
                "Combined Sentiment (0-30)": combined_sentiment_score,
                "Reddit Sentiment Score (0-30)": reddit_score,
                "Total Score (0-100)": total_score_100,
                "Score Label": label,
                "Current Price": yahoo_data["Current Price"],
                "52W Low": yahoo_data["52W Low"],
                "52W High": yahoo_data["52W High"],
                "Volume": yahoo_data["Volume"],
                "Avg Volume": yahoo_data["Avg Volume"],
                "Market Cap": yahoo_data["Market Cap"],
                "Forward PE": yahoo_data["Forward PE"],
                "StockTwits Link": stocktwits_link,
                "Reddit Links": ", ".join(reddit_urls) if reddit_urls else "None",
                "Filing URL": result["xml_url"],
            })

        print(f"\n🏢 Company: {result['company']} ({result['ticker']})")
        print(f"👤 Insider: {result['name']} | Role: {result['role']}")
        for trade in result["trades"]:
            print(f"   🧾 {trade['code']} | {trade['shares']} shares @ ${trade['price']} | {trade['ownership']}")

    if all_trades:
        df = pd.DataFrame(all_trades)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        save_path = r"C:\\Users\\dusti\\Desktop\\Trading Code AI"
        csv_file = f"{save_path}\\form4_insider_trades{timestamp}.csv"
        xlsx_file = f"{save_path}\\form4_insider_trades{timestamp}.xlsx"

        #df.to_csv(csv_file, index=False)
        df.to_excel(xlsx_file, index=False, engine="openpyxl")

        print(f"\n📁 Exported {len(all_trades)} trades to:")
        #print(f"- {csv_file}")
        print(f"- {xlsx_file}")

        # send_email_with_attachment("bwisniske@gmail.com", "Daily Trade Report", "Attached is today's insider trade report.", xlsx_file)

    else:
        print("\n⚠️ No trades found.")

if __name__ == "__main__":
    main()
