from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__, static_folder=".")
CORS(app)

client = OpenAI(
    base_url="https://api.nova.amazon.com/v1",
    api_key="705c5381-57b8-4f02-b8a0-5d75988c32d4",
)

MODEL = "AGENT-0145989dba254b77afd38709902f002c"


@app.route("/")
def index():
    return send_from_directory(".", "chat.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    messages = data.get("messages", [])

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
