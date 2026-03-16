import os
import re
import time
import json
import base64
import html
import requests
from urllib.parse import urlparse
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

GUIDE_SYSTEM_PROMPT = (
    "You are HardPulse, a hardware-finding assistant. Guide the user step-by-step before searching listings. "
    "Start by understanding what they want, then ask concise follow-up questions when needed (budget, condition, performance target, desktop/laptop/card, and urgency). "
    "Never provide direct product/listing links yourself. If the user wants listings, ask them to request an eBay search."
)

# ── eBay Browse API credentials ─────────────────────────────
EBAY_APP_ID = os.getenv("EBAY_APP_ID", "SurehSan-Browse-PRD-8bf3c6448-d5a65e34")
EBAY_CERT_ID = os.getenv("EBAY_CERT_ID", "PRD-bf3c64488749-d9ba-41cd-8bdd-0e0a")

EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

_NON_HARDWARE_TERMS = [
    "trading card",
    "pokemon",
    "yugioh",
    "magic the gathering",
    "mtg",
    "sports card",
    "baseball card",
    "basketball card",
    "football card",
    "psa",
    "graded card",
    "one piece card",
]

_HARDWARE_HINT_TERMS = [
    "rtx",
    "gtx",
    "rx ",
    "ryzen",
    "core i",
    "intel",
    "amd",
    "gpu",
    "graphics card",
    "desktop",
    "laptop",
    "motherboard",
    "ssd",
    "nvme",
    "ddr4",
    "ddr5",
    "psu",
    "watt",
    "pc",
    "computer",
]

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


def _extract_legacy_item_id(item):
    legacy_item_id = item.get("legacyItemId")
    if legacy_item_id:
        return str(legacy_item_id)

    raw_item_id = str(item.get("itemId") or "")
    # itemId can be in form: v1|123456789012|0
    match = re.search(r"\|(\d{9,})\|", raw_item_id)
    if match:
        return match.group(1)

    if raw_item_id.isdigit():
        return raw_item_id
    return ""


def _build_candidate_listing_urls(item):
    candidates = []
    for key in ["itemWebUrl", "itemAffiliateWebUrl"]:
        value = item.get(key)
        if isinstance(value, str) and value.startswith("http"):
            candidates.append(value)

    legacy_item_id = _extract_legacy_item_id(item)
    if legacy_item_id:
        candidates.append(f"https://www.ebay.com/itm/{legacy_item_id}")

    deduped = []
    seen = set()
    for value in candidates:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _is_ebay_listing_url(url):
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    return "ebay." in host and "/itm/" in path


def _canonicalize_listing_url(url):
    if not isinstance(url, str) or not url.startswith("http"):
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    host = (parsed.netloc or "").lower()
    if "ebay." not in host:
        return ""

    path = parsed.path or ""
    match = re.search(r"/itm/(\d{9,})", path)
    if match:
        return f"https://www.ebay.com/itm/{match.group(1)}"

    if "/itm/" in path:
        return f"https://www.ebay.com{path}"

    return ""


def _pick_live_listing_url(item):
    legacy_item_id = _extract_legacy_item_id(item)
    if legacy_item_id:
        return f"https://www.ebay.com/itm/{legacy_item_id}"

    for url in _build_candidate_listing_urls(item):
        canonical = _canonicalize_listing_url(url)
        if canonical:
            return canonical
    return ""


# ── eBay search (rate-limited to 5 RPM) ────────────────────
def search_ebay(query, limit=5, offset=0):
    """Search eBay using the Browse API and return a list of items."""
    time.sleep(12)  # stay within 5 RPM

    token = _get_ebay_token()
    page_limit = min(max(int(limit), 1), 50)
    resp = requests.get(
        EBAY_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            "Content-Type": "application/json",
        },
        params={
            "q": query,
            "limit": page_limit,
            "offset": offset,
            "fieldgroups": "MATCHING_ITEMS,EXTENDED",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    items = []
    for item in data.get("itemSummaries", []):
        live_url = _pick_live_listing_url(item)
        if not live_url:
            continue

        price_value = _safe_float(item.get("price", {}).get("value"))
        shipping_option = ((item.get("shippingOptions") or [{}])[0] or {})
        shipping_cost_value = _safe_float((shipping_option.get("shippingCost") or {}).get("value"))
        items.append({
            "title":     item.get("title"),
            "price":     price_value,
            "currency":  item.get("price", {}).get("currency"),
            "condition": item.get("condition"),
            "url":       live_url,
            "image":     item.get("image", {}).get("imageUrl"),
            "seller":    item.get("seller", {}).get("username"),
            "shipping_cost": shipping_cost_value,
            "shipping_cost_type": shipping_option.get("shippingCostType"),
            "item_location": (item.get("itemLocation") or {}).get("country"),
        })
        if len(items) >= limit:
            break
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
    triggers = [
        "search ebay",
        "search for",
        "find deals",
        "find me",
        "show listings",
        "search now",
        "look it up on ebay",
        "ebay search",
        "best price",
        "buy",
    ]
    if any(trigger in lowered for trigger in triggers):
        return True
    if re.fullmatch(r"\d{4}", lowered.strip()):
        return True
    if any(token in lowered for token in ["rtx", "gtx", "rx ", "ryzen", "core i", "gpu", "graphics card"]):
        return True
    return False


def _strip_links_from_text(text):
    cleaned = text or ""
    cleaned = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


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
        if isinstance(value, list):
            normalized_list = []
            for item in value:
                if isinstance(item, str):
                    item = item.strip()
                if item not in (None, "", []):
                    normalized_list.append(item)
            if normalized_list:
                clean[key] = normalized_list
            continue
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                continue
        clean[key] = value
    return clean


def _as_list(value):
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _build_search_query(user_text, filters):
    tokens = []
    product_types = _as_list(filters.get("product_type"))
    brands = _as_list(filters.get("brand"))
    model = filters.get("model") or ""
    tiers = _as_list(filters.get("tier"))
    notes = filters.get("notes") or ""

    type_map = {
        "card": "graphics card",
        "desktop": "desktop pc",
        "laptop": "laptop",
    }
    for product_type in product_types:
        if product_type in type_map:
            tokens.append(type_map[product_type])
    for brand in brands:
        if isinstance(brand, str) and brand.lower() != "any":
            tokens.append(brand)
    if model:
        tokens.append(model)

    tier_map = {
        "budget": "budget",
        "mid": "mid range",
        "high": "high end",
        "enthusiast": "enthusiast",
    }
    for tier in tiers:
        if tier in tier_map:
            tokens.append(tier_map[tier])

    if notes:
        tokens.append(notes)

    combined = " ".join(token for token in tokens if token)
    base = (user_text or "").strip()
    if base and combined:
        query = f"{base} {combined}".strip()
    elif combined:
        query = combined.strip()
    else:
        query = base

    return _normalize_ebay_query(query)


def _normalize_ebay_query(query):
    query = (query or "").strip()
    if not query:
        return query

    lowered = query.lower()
    for phrase in [
        "search ebay",
        "ebay search",
        "search now",
        "show listings",
        "find deals",
        "look it up on ebay",
    ]:
        lowered = lowered.replace(phrase, " ")

    lowered = re.sub(r"\s+", " ", lowered).strip()

    if re.fullmatch(r"\d{4}", lowered):
        return f"rtx {lowered} graphics card"

    if re.search(r"\b\d{4}\b", lowered) and not any(term in lowered for term in ["rtx", "gtx", "rx", "ryzen", "intel", "amd"]):
        lowered = f"{lowered} graphics card pc"

    if not any(term in lowered for term in _HARDWARE_HINT_TERMS):
        lowered = f"{lowered} computer hardware pc part"

    return lowered.strip()


def _is_hardware_listing(title):
    lowered = (title or "").lower()
    if any(term in lowered for term in _NON_HARDWARE_TERMS):
        return False
    if any(term in lowered for term in _HARDWARE_HINT_TERMS):
        return True
    return False


def _build_required_tokens(filters):
    tokens = []
    brands = [str(brand).lower().strip() for brand in _as_list(filters.get("brand"))]
    model = (filters.get("model") or "").lower().strip()
    for brand in brands:
        if brand and brand != "any":
            tokens.append(brand)
    if model:
        for token in re.split(r"\s*,\s*", model):
            if token:
                tokens.append(token)
    return tokens


def _title_has_required_tokens(title, tokens):
    if not tokens:
        return True
    lowered = (title or "").lower()
    return all(token in lowered for token in tokens)


def _apply_post_filters(items, filters):
    required_tokens = _build_required_tokens(filters)
    condition_values = [str(c).lower().strip() for c in _as_list(filters.get("condition")) if str(c).strip()]
    exclude_local = bool(filters.get("exclude_local"))
    min_price = _safe_float(filters.get("min_price"))
    max_price = _safe_float(filters.get("max_price"))

    filtered = []
    for item in items:
        title = item.get("title") or ""
        url = item.get("url") or ""

        if not title or not url or "ebay." not in url:
            continue
        if not _is_hardware_listing(title):
            continue
        if not _title_has_required_tokens(title, required_tokens):
            continue
        active_conditions = [c for c in condition_values if c != "any"]
        if active_conditions:
            item_condition = (item.get("condition") or "").lower()
            condition_match = False
            for condition in active_conditions:
                if condition == "new" and "new" in item_condition:
                    condition_match = True
                elif condition == "used" and "used" in item_condition:
                    condition_match = True
                elif condition == "parts" and ("parts" in item_condition or "not working" in item_condition):
                    condition_match = True
            if not condition_match:
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
    product_types = _as_list(filters.get("product_type"))
    brands = _as_list(filters.get("brand"))
    tiers = _as_list(filters.get("tier"))
    conditions = _as_list(filters.get("condition"))

    if product_types:
        filter_summary.append(f"Type: {', '.join(str(value) for value in product_types)}")
    if brands:
        filter_summary.append(f"Brand: {', '.join(str(value) for value in brands)}")
    if filters.get("model"):
        filter_summary.append(f"Model: {filters['model']}")
    if tiers:
        filter_summary.append(f"Tier: {', '.join(str(value) for value in tiers)}")
    if filters.get("min_price") or filters.get("max_price"):
        filter_summary.append(f"Price: {filters.get('min_price','?')} - {filters.get('max_price','?')}")
    if conditions:
        filter_summary.append(f"Condition: {', '.join(str(value) for value in conditions)}")

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


def _build_ebay_thinking_payload(query, limit, offset, filters):
    return {
        "endpoint": EBAY_SEARCH_URL,
        "method": "GET",
        "params": {
            "q": query,
            "limit": limit,
            "offset": offset,
            "fieldgroups": "MATCHING_ITEMS,EXTENDED",
        },
        "marketplace": "EBAY_US",
        "active_filters": filters,
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
        thinking = _build_ebay_thinking_payload(query=query, limit=limit, offset=offset, filters=filters)
        return jsonify({
            "results": results,
            "reply": formatted["text"],
            "reply_html": formatted["html"],
            "thinking": thinking,
        })
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
            thinking = _build_ebay_thinking_payload(query=query, limit=5, offset=0, filters=filters)
            return jsonify({"reply": formatted["text"], "reply_html": formatted["html"], "thinking": thinking})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    try:
        model_messages = [{"role": "system", "content": GUIDE_SYSTEM_PROMPT}] + messages
        response = client.chat.completions.create(
            model=MODEL,
            messages=model_messages,
        )
        reply = _strip_links_from_text(response.choices[0].message.content)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(port=5000, debug=True)
