import os
import re
import time
import json
import base64
import html
import requests
from urllib.parse import urlparse
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from openai import OpenAI

load_dotenv()

app = Flask(__name__, static_folder=".")
CORS(app)

client = OpenAI(
    base_url=os.getenv("AMAZON_NOVA_BASE_URL", "https://api.nova.amazon.com/v1"),
    api_key=os.getenv("AMAZON_NOVA_API_KEY"),
)

MODEL = "AGENT-0145989dba254b77afd38709902f002c"

GUIDE_SYSTEM_PROMPT = (
    "You are CacheHunt, a hardware-finding assistant. Guide the user step-by-step before searching listings. "
    "Start by understanding what they want, then ask concise follow-up questions when needed (budget, condition, performance target, desktop/laptop/card, and urgency). "
    "Never provide direct product/listing links yourself and never output marketplace results. "
    "In every response, include one short line: 'When you're ready for live deals, type: Search now.'"
)

# ── eBay Browse API credentials ─────────────────────────────
EBAY_APP_ID = os.getenv("EBAY_APP_ID")
EBAY_CERT_ID = os.getenv("EBAY_CERT_ID")

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
def search_ebay(query, limit=5, offset=0, filters=None):
    """Search eBay using the Browse API and return a list of items."""
    time.sleep(12)  # stay within 5 RPM

    token = _get_ebay_token()
    page_limit = min(max(int(limit), 1), 50)

    # Build eBay filter string for price range and condition
    ebay_filters = []
    if filters:
        min_price = _safe_float(filters.get("min_price"))
        max_price = _safe_float(filters.get("max_price"))
        if min_price is not None and max_price is not None:
            ebay_filters.append(f"price:[{min_price}..{max_price}],priceCurrency:USD")
        elif min_price is not None:
            ebay_filters.append(f"price:[{min_price}],priceCurrency:USD")
        elif max_price is not None:
            ebay_filters.append(f"price:[..{max_price}],priceCurrency:USD")

        condition_map = {"new": "NEW", "used": "USED", "refurbished": "REFURBISHED"}
        condition_values = _as_list(filters.get("condition"))
        ebay_conditions = []
        for c in condition_values:
            mapped = condition_map.get(str(c).lower().strip())
            if mapped:
                ebay_conditions.append(mapped)
        if ebay_conditions:
            ebay_filters.append("conditions:{" + "|".join(ebay_conditions) + "}")

    params = {
        "q": query,
        "limit": page_limit,
        "offset": offset,
        "fieldgroups": "MATCHING_ITEMS,EXTENDED",
    }
    if ebay_filters:
        params["filter"] = ",".join(ebay_filters)

    resp = requests.get(
        EBAY_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            "Content-Type": "application/json",
        },
        params=params,
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


def _extract_message_text(message):
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
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


def _extract_user_text(messages):
    if not messages:
        return ""
    last = messages[-1] or {}
    return _extract_message_text(last)


def _is_search_now_command(text):
    return bool(re.match(r"^\s*search\s+now\s*[.!?]*\s*$", text or "", flags=re.IGNORECASE))


def _is_more_results_command(text):
    return bool(re.match(
        r"^\s*(?:(?:show|load|get|give|find)\s+)?(?:more|next|additional)\s*(?:results?|listings?|deals?|options?)?\s*[.!?]*\s*$",
        text or "", flags=re.IGNORECASE,
    ))


def _count_previous_ebay_batches(messages):
    """Count how many eBay result batches have been sent in the conversation."""
    count = 0
    for msg in (messages or []):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        text = _extract_message_text(msg)
        if "Top eBay results" in text or "ebay-results" in text:
            count += 1
    return count


def _extract_search_context(messages):
    if not isinstance(messages, list):
        return ""

    user_texts = []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            text = _extract_message_text(message)
            if not text:
                continue
            if _is_search_now_command(text) or _is_more_results_command(text):
                continue
            user_texts.append(text)

    if not user_texts:
        return ""
    return " ".join(user_texts[-3:]).strip()


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


def _merge_filters(base_filters, override_filters):
    merged = dict(base_filters or {})
    for key, value in (override_filters or {}).items():
        if value in (None, "", []):
            continue
        merged[key] = value
    return merged


def _infer_filters_from_text(search_context):
    text = (search_context or "").lower()
    inferred = {}

    model_match = re.search(r"\b(rtx\s?\d{4}(?:\s?(?:ti|super|ti\s?super))?)\b", text)
    if not model_match:
        model_match = re.search(r"\b(rx\s?\d{4}\s?(?:xtx|xt)?)\b", text)
    if model_match:
        inferred["model"] = re.sub(r"\s+", " ", model_match.group(1)).upper().strip()

    max_price = None
    price_patterns = [
        r"(?:max(?:imum)?|under|below|less than|up to)\s*\$?\s*(\d{2,6}(?:\.\d{1,2})?)",
        r"(\d{2,6}(?:\.\d{1,2})?)\s*dollars?",
        r"\$\s*(\d{2,6}(?:\.\d{1,2})?)",
        r"budget\s*(?:is|of|:)?\s*\$?\s*(\d{2,6}(?:\.\d{1,2})?)",
    ]
    for pattern in price_patterns:
        match = re.search(pattern, text)
        if match:
            value = _safe_float(match.group(1))
            if value is not None:
                max_price = value if max_price is None else min(max_price, value)
    if max_price is not None:
        inferred["max_price"] = max_price

    conditions = []
    if "brand new" in text or "new condition" in text or re.search(r"\bnew\b", text):
        conditions.append("new")
    if "used" in text:
        conditions.append("used")
    if "refurb" in text:
        conditions.append("used")
    if "for parts" in text or "not working" in text:
        conditions.append("parts")
    if conditions:
        inferred["condition"] = sorted(set(conditions))

    if "local pickup" in text or "pickup only" in text:
        inferred["exclude_local"] = True

    return inferred


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
    # model is appended later only if not already in the base text
    # (stored for dedup check below)

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
    base = _strip_non_product_terms(user_text)

    # Append model only if it isn't already present in the base text
    if model and model.lower() not in base.lower():
        combined = f"{combined} {model}".strip() if combined else model

    if base and combined:
        query = f"{base} {combined}".strip()
    elif combined:
        query = combined.strip()
    else:
        query = base

    return _normalize_ebay_query(query)


def _strip_non_product_terms(text):
    """Remove budget, condition, intent, and conversational filler from query text."""
    cleaned = (text or "").lower()

    # Strip price / budget phrases
    price_patterns = [
        r"(?:max(?:imum)?|under|below|less than|up to|around|about)\s*\$?\s*\d{2,6}(?:\.\d{1,2})?\s*(?:dollars?|usd|bucks?)?",
        r"\$\s*\d{2,6}(?:\.\d{1,2})?",
        r"\d{2,6}(?:\.\d{1,2})?\s*dollars?\b",
        r"budget\s*(?:is|of|:)?\s*\$?\s*\d{2,6}(?:\.\d{1,2})?(?:\s*(?:dollars?|usd))?",
    ]
    for pattern in price_patterns:
        cleaned = re.sub(pattern, " ", cleaned)

    # Strip condition phrases
    condition_phrases = [
        r"\bbrand\s*new\b", r"\bnew\s+condition\b", r"\bnew\b",
        r"\bused\b", r"\brefurbished\b", r"\brefurb\b",
        r"\bfor\s+parts\b", r"\bnot\s+working\b", r"\bas[\s-]?is\b",
        r"\blike\s+new\b", r"\bopen\s+box\b", r"\bcertified\b",
    ]
    for pattern in condition_phrases:
        cleaned = re.sub(pattern, " ", cleaned)

    # Strip intent / filler phrases
    intent_phrases = [
        r"\bprofessional\s+work\b", r"\bfor\s+gaming\b", r"\bfor\s+work\b",
        r"\bfor\s+streaming\b", r"\bfor\s+editing\b", r"\bfor\s+school\b",
        r"\bi\s+need\b", r"\bi\s+want\b", r"\blooking\s+for\b",
        r"\bsearch\s+(?:ebay|now)\b", r"\bebay\s+search\b",
        r"\bshow\s+listings\b", r"\bfind\s+deals\b",
        r"\blook\s+it\s+up\s+on\s+ebay\b",
        r"\bplease\b", r"\basap\b", r"\burgent(?:ly)?\b",
        r"\bdesktop\b", r"\blaptop\b",
    ]
    for pattern in intent_phrases:
        cleaned = re.sub(pattern, " ", cleaned)

    # Strip stray punctuation and collapse whitespace
    cleaned = re.sub(r"[,.:;!?]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned


def _normalize_ebay_query(query):
    query = _strip_non_product_terms(query)
    if not query:
        return query

    if re.fullmatch(r"\d{4}", query):
        return f"rtx {query} graphics card"

    if re.search(r"\b\d{4}\b", query) and not any(term in query for term in ["rtx", "gtx", "rx", "ryzen", "intel", "amd"]):
        query = f"{query} graphics card pc"

    if not any(term in query for term in _HARDWARE_HINT_TERMS):
        query = f"{query} computer hardware pc part"

    return query.strip()


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
                if condition == "new" and any(kw in item_condition for kw in ["new", "open box", "sealed"]):
                    condition_match = True
                elif condition == "used" and any(kw in item_condition for kw in ["used", "pre-owned", "open box", "refurbished"]):
                    condition_match = True
                elif condition == "parts" and ("parts" in item_condition or "not working" in item_condition):
                    condition_match = True
            if not condition_match:
                continue
        if exclude_local:
            title_lower = title.lower()
            if "local pickup" in title_lower or "pickup only" in title_lower:
                continue
        item_price = item.get("price")
        if min_price is not None:
            if item_price is None or item_price < min_price:
                continue
        if max_price is not None:
            if item_price is None or item_price > max_price:
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
        results = search_ebay(query, limit=limit, offset=offset, filters=filters)
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

    if _is_search_now_command(user_text) or _is_more_results_command(user_text):
        search_context = _extract_search_context(messages)
        inferred_filters = _infer_filters_from_text(search_context)
        effective_filters = _merge_filters(inferred_filters, filters)
        query = _build_search_query(search_context, effective_filters)
        if not query:
            return jsonify({
                "reply": "Tell me what hardware you want first, then type: Search now.",
            })

        # For "more results", calculate offset from previous batches
        offset = 0
        if _is_more_results_command(user_text):
            previous_batches = _count_previous_ebay_batches(messages)
            offset = max(previous_batches, 1) * 5

        try:
            results = search_ebay(query, limit=5, offset=offset, filters=effective_filters)
            filtered = _apply_post_filters(results, effective_filters)
            formatted = _format_ebay_results(filtered, query=query, offset=offset, limit=5, filters=effective_filters)
            thinking = _build_ebay_thinking_payload(query=query, limit=5, offset=offset, filters=effective_filters)
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
