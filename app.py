import os
import re
import time
import base64
import html
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__, static_folder=".")
CORS(app)

client = OpenAI(
    base_url=os.getenv("AMAZON_NOVA_BASE_URL", "https://api.nova.amazon.com/v1"),
    api_key=os.getenv("AMAZON_NOVA_API_KEY", "705c5381-57b8-4f02-b8a0-5d75988c32d4"),
)

MODEL = "AGENT-0145989dba254b77afd38709902f002c"

# ── eBay Browse API credentials ─────────────────────────────
EBAY_APP_ID = os.getenv("EBAY_APP_ID", "SurehSan-Browse-PRD-8bf3c6448-d5a65e34")
EBAY_CERT_ID = os.getenv("EBAY_CERT_ID", "PRD-bf3c64488749-d9ba-41cd-8bdd-0e0a")

EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# Simple in-memory token cache
_ebay_token_cache = {"access_token": None, "expires_at": 0}


def _get_ebay_token():
    """Fetch (or return cached) eBay OAuth Application token."""
    now = time.time()
    if _ebay_token_cache["access_token"] and now < _ebay_token_cache["expires_at"]:
        return _ebay_token_cache["access_token"]

    credentials = base64.b64encode(
        f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()
    ).decode()

    resp = requests.post(
        EBAY_TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=10,
    )
    resp.raise_for_status()
    token_data = resp.json()

    _ebay_token_cache["access_token"] = token_data["access_token"]
    # Subtract 60 s as a safety buffer
    _ebay_token_cache["expires_at"] = now + token_data.get("expires_in", 7200) - 60
    return _ebay_token_cache["access_token"]


# ── eBay search (rate-limited to 5 RPM) ────────────────────
def search_ebay(query, limit=5, offset=0):
    """Search eBay using the Browse API and return a list of items."""
    time.sleep(12)  # stay within 5 RPM

    token = _get_ebay_token()
    resp = requests.get(
        EBAY_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            "Content-Type": "application/json",
        },
        params={
            "q": query,
            "limit": limit,
            "offset": offset,
            "fieldgroups": "MATCHING_ITEMS,EXTENDED",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    items = []
    for item in data.get("itemSummaries", []):
        price_value = _safe_float(item.get("price", {}).get("value"))
        items.append({
            "title":     item.get("title"),
            "price":     price_value,
            "currency":  item.get("price", {}).get("currency"),
            "condition": item.get("condition"),
            "url":       item.get("itemWebUrl"),
            "image":     item.get("image", {}).get("imageUrl"),
            "seller":    item.get("seller", {}).get("username"),
        })
    return items


def _extract_user_text(messages):
    if not messages:
        return ""
    last = messages[-1] or {}
    content = last.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    parts.append(item["text"])
                elif item.get("text"):
                    parts.append(item["text"])
        return " ".join(parts).strip()
    return ""


def _should_search_ebay(text):
    if not text:
        return False
    lowered = text.lower()
    triggers = ["search", "find", "deal", "deals", "price", "prices", "ebay"]
    if any(trigger in lowered for trigger in triggers):
        return True
    if re.search(r"\b(rtx|rx|ryzen|core i[3579]|i[3579]-\d{4,5})\b", lowered):
        return True
    return False


def _safe_float(value):
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _median(values):
    if not values:
        return None
    values_sorted = sorted(values)
    mid = len(values_sorted) // 2
    if len(values_sorted) % 2 == 1:
        return values_sorted[mid]
    return (values_sorted[mid - 1] + values_sorted[mid]) / 2


def _deal_label(delta_pct):
    if delta_pct <= -20:
        return "Great deal"
    if delta_pct <= -10:
        return "Good deal"
    if delta_pct <= -5:
        return "Fair"
    if delta_pct <= 10:
        return "Slightly high"
    return "Overpriced"


def _format_ebay_results(items, query, offset, limit):
    if not items:
        return {
            "text": "No eBay results found. Try a more specific query.",
            "html": "No eBay results found. Try a more specific query.",
        }

    prices = [item.get("price") for item in items if item.get("price") is not None]
    median_price = _median(prices)

    lines = ["Top eBay results:"]
    html_lines = [
        "<div class=\"ebay-results\">",
        "<div class=\"ebay-summary\">Price efficiency compares each listing to the median of these results (same spec class).</div>",
        "<ol>",
    ]

    for idx, item in enumerate(items, 1):
        title = item.get("title") or "(no title)"
        price = item.get("price")
        currency = item.get("currency") or "USD"
        condition = item.get("condition") or "Unknown"
        url = item.get("url") or ""

        price_text = f"${price:,.2f} {currency}" if price is not None else "Price N/A"
        lines.append(f"{idx}. {title}\n   {price_text}\n   {url}")

        efficiency_text = "Efficiency: N/A"
        if price is not None and median_price:
            delta_pct = ((price - median_price) / median_price) * 100
            direction = "below" if delta_pct < 0 else "above"
            efficiency_text = f"Efficiency: {abs(delta_pct):.1f}% {direction} median ({_deal_label(delta_pct)})"

        html_lines.append(
            "<li>"
            f"<div class=\"ebay-title\"><a href=\"{html.escape(url, quote=True)}\" target=\"_blank\" rel=\"noopener\">{html.escape(title)}</a></div>"
            f"<div class=\"ebay-meta\">{html.escape(price_text)} | Condition: {html.escape(condition)}</div>"
            f"<div class=\"ebay-eff\">{html.escape(efficiency_text)}</div>"
            "</li>"
        )

    html_lines.append("</ol>")

    if len(items) == limit:
        next_offset = offset + limit
        html_lines.append(
            "<button class=\"load-more\" data-query=\""
            + html.escape(query, quote=True)
            + "\" data-offset=\""
            + str(next_offset)
            + "\">Load more results</button>"
        )
    html_lines.append("</div>")

    return {
        "text": "\n".join(lines),
        "html": "".join(html_lines),
    }


@app.route("/")
def index():
    return send_from_directory(".", "chat.html")


@app.route("/api/ebay", methods=["POST"])
def ebay_search():
    data = request.get_json()
    query = data.get("query", "")
    offset = int(data.get("offset", 0) or 0)
    limit = int(data.get("limit", 5) or 5)
    if not query:
        return jsonify({"error": "No query provided"}), 400

    try:
        results = search_ebay(query, limit=limit, offset=offset)
        formatted = _format_ebay_results(results, query=query, offset=offset, limit=limit)
        return jsonify({"results": results, "reply": formatted["text"], "reply_html": formatted["html"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    messages = data.get("messages", [])
    user_text = _extract_user_text(messages)

    if _should_search_ebay(user_text):
        try:
            results = search_ebay(user_text, limit=5, offset=0)
            formatted = _format_ebay_results(results, query=user_text, offset=0, limit=5)
            return jsonify({"reply": formatted["text"], "reply_html": formatted["html"]})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
        )
        reply = response.choices[0].message.content
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(port=5000, debug=True)
