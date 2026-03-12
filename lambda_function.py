import base64
import json
import os
import re
import time
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

import boto3
import requests


EBAY_AUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id"
EBAY_FINDING_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
NOVA_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
MAX_IMAGES = 4
MAX_COMPLETED_ITEMS = 30

EBAY_SCOPE = " ".join(
    [
        "https://api.ebay.com/oauth/api_scope",
        "https://api.ebay.com/oauth/api_scope/buy.item.readonly",
    ]
)

SYSTEM_PROMPT = (
    "You are an expert PC hardware analyst specializing in used component markets. "
    "You evaluate listings for deal quality, condition, and risk. "
    "You are direct, specific, and honest."
)

STRICT_JSON_SUFFIX = (
    "\n\nCRITICAL OUTPUT INSTRUCTION: Return ONLY valid JSON and no markdown, "
    "no prose before or after JSON."
)

_TOKEN_CACHE: Dict[str, Any] = {
    "access_token": None,
    "expires_at": 0,
}


class PipelineError(Exception):
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


def _response(status_code: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(payload),
    }


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_event_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body", event)
    if isinstance(body, str):
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise PipelineError("Malformed request: body must be valid JSON.", 400) from exc
    if isinstance(body, dict):
        return body
    return {}


def _validate_input(payload: Dict[str, Any]) -> Tuple[str, float, str]:
    item_id = str(payload.get("item_id", "")).strip()
    asking_price_raw = payload.get("asking_price")
    title = str(payload.get("title", "")).strip()

    if not item_id or asking_price_raw is None or not title:
        raise PipelineError(
            "Malformed request: item_id, asking_price, and title are required.",
            status_code=400,
        )

    asking_price = _safe_float(asking_price_raw)
    if asking_price is None or asking_price < 0:
        raise PipelineError("Malformed request: asking_price must be a valid number.", 400)

    return item_id, asking_price, title


def _get_env(name: str, required: bool = True, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise PipelineError(f"Missing required environment variable: {name}", 500)
    return value or ""


def _get_ebay_access_token() -> str:
    now = int(time.time())
    cached_token = _TOKEN_CACHE.get("access_token")
    expires_at = int(_TOKEN_CACHE.get("expires_at", 0))
    if cached_token and now < (expires_at - 60):
        return cached_token

    client_id = _get_env("EBAY_APP_ID")
    client_secret = _get_env("EBAY_CLIENT_SECRET")

    response = requests.post(
        EBAY_AUTH_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": EBAY_SCOPE},
        auth=(client_id, client_secret),
        timeout=20,
    )

    if not response.ok:
        raise PipelineError(f"Failed to get eBay access token: {response.text}", 502)

    data = response.json()
    access_token = data.get("access_token")
    expires_in = int(data.get("expires_in", 7200))

    if not access_token:
        raise PipelineError("eBay auth response missing access_token", 502)

    _TOKEN_CACHE["access_token"] = access_token
    _TOKEN_CACHE["expires_at"] = now + expires_in
    return access_token


def _fetch_listing(item_id: str) -> Dict[str, Any]:
    token = _get_ebay_access_token()
    response = requests.get(
        EBAY_BROWSE_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={"legacy_item_id": item_id},
        timeout=20,
    )

    if not response.ok:
        raise PipelineError(f"Browse API getItem failed: {response.text}", 502)

    data = response.json()

    image_urls: List[str] = []
    primary = data.get("image", {}).get("imageUrl")
    if primary:
        image_urls.append(primary)

    for img in data.get("additionalImages", []) or []:
        image_url = img.get("imageUrl")
        if image_url:
            image_urls.append(image_url)

    deduped_images = []
    seen = set()
    for url in image_urls:
        if url not in seen:
            seen.add(url)
            deduped_images.append(url)

    seller = data.get("seller", {}) or {}

    return {
        "title": data.get("title") or "",
        "description": data.get("description") or data.get("shortDescription") or "",
        "condition": data.get("condition") or "unknown",
        "seller_username": seller.get("username") or "unknown",
        "seller_feedback_percentage": seller.get("feedbackPercentage"),
        "seller_feedback_score": seller.get("feedbackScore"),
        "price": _safe_float((data.get("price") or {}).get("value")),
        "shipping": _safe_float(
            (((data.get("shippingOptions") or [{}])[0] or {}).get("shippingCost") or {}).get("value")
        ),
        "image_urls": deduped_images[:MAX_IMAGES],
    }


def _load_msrp_table() -> Dict[str, Any]:
    table_path = Path(__file__).resolve().parent / "msrp_reference_table.json"
    if not table_path.exists():
        return {"parts": []}

    with table_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _extract_model_from_title(title: str, msrp_models: List[str]) -> Tuple[str, str]:
    normalized = _normalize_text(title)

    component_patterns = {
        "GPU": r"\b(rtx\s?\d{3,4}(?:\s?ti|\s?super|\s?ti\s?super)?|rx\s?\d{4}\s?(?:xtx|xt)?)\b",
        "CPU": r"\b(ryzen\s?[3579]\s?\d{4,5}x?3?d?|core\s?i[3579]-?\d{4,5}k?)\b",
        "RAM": r"\b(ddr[45].*?\b(?:16|32|64)\s?gb\b|\b(?:16|32|64)\s?gb\s?ddr[45])",
        "Motherboard": r"\b(b\d{3}|x\d{3}|z\d{3}|h\d{3}|a\d{3})\b",
        "PSU": r"\b(\d{3,4}\s?w(?:att)?\b|80\+\s?(?:gold|platinum|bronze|titanium))",
        "Storage": r"\b((?:\d(?:\.\d)?)\s?tb|\d{3,4}\s?gb|nvme|ssd|hdd)\b",
    }

    for model in msrp_models:
        model_norm = _normalize_text(model)
        if model_norm and model_norm in normalized:
            category = next(
                (cat for cat, pat in component_patterns.items() if re.search(pat, model_norm)),
                "Unknown",
            )
            return category, model

    for category, pattern in component_patterns.items():
        match = re.search(pattern, normalized)
        if match:
            return category, match.group(1).upper().replace("  ", " ").strip()

    return "Unknown", "unknown"


def _find_completed_sales(model_name: str) -> List[float]:
    if model_name == "unknown":
        return []

    app_id = _get_env("EBAY_APP_ID")

    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": app_id,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "true",
        "keywords": model_name,
        "paginationInput.entriesPerPage": str(MAX_COMPLETED_ITEMS),
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "itemFilter(1).name": "Condition",
        "itemFilter(1).value": "3000",
        "itemFilter(2).name": "ListingType",
        "itemFilter(2).value": "FixedPrice",
    }

    response = requests.get(EBAY_FINDING_URL, params=params, timeout=20)
    if not response.ok:
        raise PipelineError(f"Finding API findCompletedItems failed: {response.text}", 502)

    payload = response.json()
    root = (payload.get("findCompletedItemsResponse") or [{}])[0]
    items = ((root.get("searchResult") or [{}])[0].get("item") or [])

    prices = []
    for item in items:
        selling_status = (item.get("sellingStatus") or [{}])[0]
        current_price = (selling_status.get("currentPrice") or [{}])[0].get("__value__")
        value = _safe_float(current_price)
        if value is not None:
            prices.append(value)

    return prices


def _calculate_market_stats(prices: List[float]) -> Dict[str, Any]:
    if not prices:
        return {
            "median": None,
            "low": None,
            "high": None,
            "sample_size": 0,
        }

    return {
        "median": round(median(prices), 2),
        "low": round(min(prices), 2),
        "high": round(max(prices), 2),
        "sample_size": len(prices),
    }


def _lookup_msrp(model_name: str, msrp_table: Dict[str, Any]) -> Optional[float]:
    model_norm = _normalize_text(model_name)
    for part in msrp_table.get("parts", []):
        if _normalize_text(part.get("model", "")) == model_norm:
            return _safe_float(part.get("retail_price_ceiling_usd"))
    return None


def _download_and_encode_images(image_urls: List[str]) -> List[Dict[str, Any]]:
    encoded_images = []

    for url in image_urls[:MAX_IMAGES]:
        try:
            image_response = requests.get(url, timeout=20)
            if not image_response.ok:
                continue

            content_type = image_response.headers.get("Content-Type", "image/jpeg").lower()
            if "png" in content_type:
                image_format = "png"
            elif "webp" in content_type:
                image_format = "webp"
            else:
                image_format = "jpeg"

            image_b64 = base64.b64encode(image_response.content).decode("utf-8")
            encoded_images.append(
                {
                    "image": {
                        "format": image_format,
                        "source": {"bytes": image_b64},
                    }
                }
            )
        except requests.RequestException:
            continue

    return encoded_images


def _build_user_prompt(
    component_type: str,
    model_name: str,
    asking_price: float,
    listing: Dict[str, Any],
    market: Dict[str, Any],
    msrp: Optional[float],
) -> str:
    seller_pct = listing.get("seller_feedback_percentage")
    seller_score = listing.get("seller_feedback_score")
    seller_string = (
        f"{seller_pct}% positive ({seller_score} reviews)"
        if seller_pct is not None and seller_score is not None
        else "unknown"
    )

    median_text = f"${market['median']}" if market["median"] is not None else "unknown"
    range_text = (
        f"${market['low']} - ${market['high']}"
        if market["low"] is not None and market["high"] is not None
        else "unknown"
    )
    msrp_text = f"${msrp}" if msrp is not None else "unknown"

    return f"""LISTING DETAILS
Component Type: {component_type}
Model: {model_name}
Asking Price: ${asking_price}
Condition (eBay stated): {listing.get('condition', 'unknown')}
Seller Rating: {seller_string}
Description: \"{listing.get('description', '')}\"

MARKET DATA
eBay Median Sold Price (used, last 30 days): {median_text}
eBay Price Range: {range_text}
New Retail MSRP: {msrp_text}

PHOTOS: [image1] [image2] [image3] [image4]

ANALYSIS TASK: Analyze across four dimensions:
1. PRICE ANALYSIS — compare to eBay median, is it fair/overpriced/deal?
2. PHOTO INSPECTION — fan damage, mining wear, thermal damage, bent pins
3. DESCRIPTION FLAGS — red flags (vague, as-is) and green flags (specific, tested)
4. SELLER SIGNALS — use seller rating and review count in risk assessment

Return JSON: {{
  \"score\": number,
  \"verdict\": \"Great Deal\" | \"Good Deal\" | \"Fair\" | \"Overpriced\" | \"Avoid\",
  \"price_delta_percent\": number,
  \"price_summary\": string,
  \"photo_findings\": string,
  \"red_flags\": string[],
  \"green_flags\": string[],
  \"seller_assessment\": string,
  \"recommendation\": string
}}
"""


def _extract_text_from_nova_response(response_json: Dict[str, Any]) -> str:
    if "output" in response_json:
        content = ((response_json.get("output") or {}).get("message") or {}).get("content", [])
        texts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("text")]
        if texts:
            return "\n".join(texts)

    if "content" in response_json and isinstance(response_json["content"], list):
        texts = [part.get("text", "") for part in response_json["content"] if isinstance(part, dict)]
        texts = [text for text in texts if text]
        if texts:
            return "\n".join(texts)

    for key in ["outputText", "text", "completion"]:
        if key in response_json and isinstance(response_json[key], str):
            return response_json[key]

    return json.dumps(response_json)


def _parse_json_from_text(text: str) -> Dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("Empty model response")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return json.loads(match.group(0))

    raise ValueError("Could not parse JSON object from model response")


def _invoke_nova(user_prompt: str, encoded_images: List[Dict[str, Any]], force_json: bool = False) -> Dict[str, Any]:
    region = _get_env("AWS_REGION", required=False, default="us-east-1")
    bedrock_runtime = boto3.client("bedrock-runtime", region_name=region)

    content = [{"text": user_prompt + (STRICT_JSON_SUFFIX if force_json else "")}] + encoded_images

    request_body = {
        "schemaVersion": "messages-v1",
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": {
            "max_new_tokens": 1000,
            "temperature": 0.2,
        },
    }

    response = bedrock_runtime.invoke_model(
        modelId=NOVA_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(request_body),
    )

    raw = response["body"].read()
    payload = json.loads(raw)
    text = _extract_text_from_nova_response(payload)
    return _parse_json_from_text(text)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if event.get("httpMethod") == "OPTIONS":
        return _response(200, {"ok": True})

    try:
        # Step 1 — Receive and Validate
        payload = _parse_event_body(event)
        item_id, asking_price, fallback_title = _validate_input(payload)

        # Step 2 — Fetch Full Listing via eBay Browse API
        listing = _fetch_listing(item_id)

        # Step 3 — Identify Component + Pull Sold Comps
        msrp_table = _load_msrp_table()
        msrp_models = [part.get("model", "") for part in msrp_table.get("parts", [])]

        best_title = listing.get("title") or fallback_title
        component_type, model_name = _extract_model_from_title(best_title, msrp_models)
        completed_prices = _find_completed_sales(model_name)
        market_stats = _calculate_market_stats(completed_prices)

        # Step 4 — Pull MSRP from Static Table
        msrp = _lookup_msrp(model_name, msrp_table)

        # Step 5 — Download and Encode Images
        encoded_images = _download_and_encode_images(listing.get("image_urls", []))

        # Step 6 — Build Nova Prompt
        user_prompt = _build_user_prompt(
            component_type=component_type,
            model_name=model_name,
            asking_price=asking_price,
            listing=listing,
            market=market_stats,
            msrp=msrp,
        )

        # Step 7 + Step 8 — Call Bedrock, Parse and Return
        try:
            analysis = _invoke_nova(user_prompt, encoded_images, force_json=False)
        except Exception:
            analysis = _invoke_nova(user_prompt, encoded_images, force_json=True)

        if "price_delta_percent" not in analysis and market_stats["median"]:
            analysis["price_delta_percent"] = round(
                ((asking_price - market_stats["median"]) / market_stats["median"]) * 100,
                2,
            )

        return _response(
            200,
            {
                "analysis": analysis,
                "market_data": market_stats,
                "component_type": component_type,
                "model": model_name,
                "msrp": msrp if msrp is not None else "unknown",
                "listing_data": {
                    "item_id": item_id,
                    "title": listing.get("title") or fallback_title,
                    "condition": listing.get("condition"),
                    "seller_feedback_percentage": listing.get("seller_feedback_percentage"),
                    "seller_feedback_score": listing.get("seller_feedback_score"),
                    "image_count": len(encoded_images),
                },
            },
        )

    except PipelineError as exc:
        return _response(exc.status_code, {"error": str(exc)})
    except requests.RequestException as exc:
        return _response(502, {"error": f"Network error: {str(exc)}"})
    except Exception as exc:
        return _response(500, {"error": f"Unhandled error: {str(exc)}"})
