import os
import re
import time
import json
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
        shipping_option = ((item.get("shippingOptions") or [{}])[0] or {})
        shipping_cost_value = _safe_float((shipping_option.get("shippingCost") or {}).get("value"))
        items.append({
            "title":     item.get("title"),
            "price":     price_value,
            "currency":  item.get("price", {}).get("currency"),
            "condition": item.get("condition"),
            "url":       item.get("itemWebUrl"),
            "image":     item.get("image", {}).get("imageUrl"),
            "seller":    item.get("seller", {}).get("username"),
            "shipping_cost": shipping_cost_value,
            "shipping_cost_type": shipping_option.get("shippingCostType"),
            "item_location": (item.get("itemLocation") or {}).get("country"),
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


def _normalize_filters(filters):
    if not isinstance(filters, dict):
        return {}
    clean = {}
    for key, value in filters.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
        clean[key] = value
    return clean


def _build_search_query(user_text, filters):
    tokens = []
    product_type = filters.get("product_type") or ""
    brand = filters.get("brand") or ""
    model = filters.get("model") or ""
    tier = filters.get("tier") or ""
    notes = filters.get("notes") or ""

    type_map = {
        "card": "graphics card",
        "desktop": "desktop pc",
        "laptop": "laptop",
    }
    if product_type in type_map:
        tokens.append(type_map[product_type])
    if brand and brand.lower() != "any":
        tokens.append(brand)
    if model:
        tokens.append(model)

    tier_map = {
        "budget": "budget",
        "mid": "mid range",
        "high": "high end",
        "enthusiast": "enthusiast",
    }
    if tier in tier_map:
        tokens.append(tier_map[tier])

    if notes:
        tokens.append(notes)

    combined = " ".join(token for token in tokens if token)
    base = (user_text or "").strip()
    if base and combined:
        return f"{base} {combined}".strip()
    if combined:
        return combined.strip()
    return base


def _build_required_tokens(filters):
    tokens = []
    brand = (filters.get("brand") or "").lower().strip()
    model = (filters.get("model") or "").lower().strip()
    if brand and brand != "any":
        tokens.append(brand)
    if model:
        tokens.append(model)
    return tokens


def _title_has_required_tokens(title, tokens):
    if not tokens:
        return True
    lowered = (title or "").lower()
    return all(token in lowered for token in tokens)


def _apply_post_filters(items, filters):
    required_tokens = _build_required_tokens(filters)
    condition = (filters.get("condition") or "").lower().strip()
    exclude_local = bool(filters.get("exclude_local"))
    min_price = _safe_float(filters.get("min_price"))
    max_price = _safe_float(filters.get("max_price"))

    filtered = []
    for item in items:
        title = item.get("title") or ""
        url = item.get("url") or ""

        if not title or not url or "ebay." not in url:
            continue
        if not _title_has_required_tokens(title, required_tokens):
            continue
        if condition and condition != "any":
            item_condition = (item.get("condition") or "").lower()
            if condition == "new" and "new" not in item_condition:
                continue
            if condition == "used" and "used" not in item_condition:
                continue
            if condition == "parts" and "parts" not in item_condition and "not working" not in item_condition:
                continue
        if exclude_local:
            title_lower = title.lower()
            if "local pickup" in title_lower or "pickup only" in title_lower:
                continue
        if min_price is not None and item.get("price") is not None and item["price"] < min_price:
            continue
        if max_price is not None and item.get("price") is not None and item["price"] > max_price:
            continue
        filtered.append(item)
    return filtered


def _collect_warnings(item):
    warnings = []
    title = (item.get("title") or "").lower()
    warn_terms = [
        "for parts",
        "parts only",
        "as-is",
        "as is",
        "not working",
        "broken",
        "untested",
        "no returns",
        "read description",
        "local pickup",
        "pickup only",
        "box only",
    ]
    for term in warn_terms:
        if term in title:
            warnings.append(term)
    if item.get("shipping_cost") is None:
        warnings.append("shipping cost not listed")
    return warnings


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


def _format_ebay_results(items, query, offset, limit, filters):
    if not items:
        return {
            "text": "No eBay results found. Try a more specific query.",
            "html": "No eBay results found. Try a more specific query.",
        }

    prices = [item.get("price") for item in items if item.get("price") is not None]
    median_price = _median(prices)

    filter_summary = []
    if filters.get("product_type"):
        filter_summary.append(f"Type: {filters['product_type']}")
    if filters.get("brand"):
        filter_summary.append(f"Brand: {filters['brand']}")
    if filters.get("model"):
        filter_summary.append(f"Model: {filters['model']}")
    if filters.get("tier"):
        filter_summary.append(f"Tier: {filters['tier']}")
    if filters.get("min_price") or filters.get("max_price"):
        filter_summary.append(f"Price: {filters.get('min_price','?')} - {filters.get('max_price','?')}")
    if filters.get("delivery_days"):
        filter_summary.append(f"Delivery: <= {filters['delivery_days']} days")
    if filters.get("condition"):
        filter_summary.append(f"Condition: {filters['condition']}")

    summary_text = " | ".join(filter_summary) if filter_summary else "Filters: none"

    lines = ["Top eBay results:"]
    html_lines = [
        "<div class=\"ebay-results\">",
        f"<div class=\"ebay-summary\">{html.escape(summary_text)}</div>",
        "<div class=\"ebay-summary\">Price efficiency compares each listing to the median of these results (same spec class).</div>",
        "<ol>",
    ]

    for idx, item in enumerate(items, 1):
        title = item.get("title") or "(no title)"
        price = item.get("price")
        currency = item.get("currency") or "USD"
        condition = item.get("condition") or "Unknown"
        url = item.get("url") or ""
        shipping_cost = item.get("shipping_cost")
        shipping_text = "Shipping: N/A" if shipping_cost is None else f"Shipping: ${shipping_cost:,.2f}"
        warnings = _collect_warnings(item)

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
            f"<div class=\"ebay-meta\">{html.escape(price_text)} | Condition: {html.escape(condition)} | {html.escape(shipping_text)}</div>"
            f"<div class=\"ebay-eff\">{html.escape(efficiency_text)}</div>"
            + (f"<div class=\"ebay-warn\">Flags: {html.escape(', '.join(warnings))}</div>" if warnings else "")
            + "</li>"
        )

    html_lines.append("</ol>")

    if len(items) == limit:
        next_offset = offset + limit
        html_lines.append(
            "<button class=\"load-more\" data-query=\""
            + html.escape(query, quote=True)
            + "\" data-offset=\""
            + str(next_offset)
            + "\" data-filters=\""
            + html.escape(json.dumps(filters), quote=True)
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
    filters = _normalize_filters(data.get("filters") or {})
    if not query:
        return jsonify({"error": "No query provided"}), 400

    try:
        results = search_ebay(query, limit=limit, offset=offset)
        filtered = _apply_post_filters(results, filters)
        formatted = _format_ebay_results(filtered, query=query, offset=offset, limit=limit, filters=filters)
        return jsonify({"results": results, "reply": formatted["text"], "reply_html": formatted["html"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    messages = data.get("messages", [])
    filters = _normalize_filters(data.get("filters") or {})
    user_text = _extract_user_text(messages)

    query = _build_search_query(user_text, filters)

    if _should_search_ebay(query):
        try:
            results = search_ebay(query, limit=5, offset=0)
            filtered = _apply_post_filters(results, filters)
            formatted = _format_ebay_results(filtered, query=query, offset=0, limit=5, filters=filters)
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
