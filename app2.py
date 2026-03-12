import json
import os
import time
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from openai import OpenAI
from lambda_function import lambda_handler

load_dotenv()

app = Flask(__name__, static_folder=".")
CORS(app)

client = OpenAI(
    base_url=os.getenv("NOVA_BASE_URL", "https://api.nova.amazon.com/v1"),
    api_key=os.getenv("NOVA_API_KEY", ""),
)

MODEL = os.getenv("NOVA_MODEL", "AGENT-0145989dba254b77afd38709902f002c")


# ── eBay search (rate-limited to 5 RPM) ────────────────────
def search_ebay(query):
    # your Browse API call
    time.sleep(12)  # stay within 5 RPM


@app.route("/")
def index():
    return send_from_directory(".", "chat.html")


@app.route("/api/ebay", methods=["POST"])
def ebay_search():
    data = request.get_json()
    query = data.get("query", "")
    if not query:
        return jsonify({"error": "No query provided"}), 400

    try:
        results = search_ebay(query)
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    messages = data.get("messages", [])

    try:
        if not os.getenv("NOVA_API_KEY"):
            return jsonify({"error": "NOVA_API_KEY is not configured"}), 500

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
        )
        reply = response.choices[0].message.content
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyze", methods=["POST", "OPTIONS"])
def analyze_listing():
    event = {
        "httpMethod": request.method,
        "body": json.dumps(request.get_json(silent=True) or {}),
    }
    lambda_response = lambda_handler(event, None)

    status_code = lambda_response.get("statusCode", 500)
    response_body = lambda_response.get("body", "{}")
    headers = lambda_response.get("headers", {})

    try:
        response_json = json.loads(response_body)
    except json.JSONDecodeError:
        response_json = {"error": response_body}

    return jsonify(response_json), status_code, headers


if __name__ == "__main__":
    app.run(port=5000, debug=True)
