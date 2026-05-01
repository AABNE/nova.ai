from flask import Flask, request, jsonify, send_file, redirect, session
from flask_cors import CORS
import urllib.request
import urllib.parse
import json
import os
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-to-something-random")
CORS(app, supports_credentials=True)

OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.environ.get("REDIRECT_URI", "http://localhost:5000/callback")

def supabase_request(method, path, data=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    payload = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body else []
    except Exception as e:
        print(f"[SUPABASE ERROR] {e}")
        return []

def upsert_user(user_id, username, avatar):
    supabase_request("POST", "users?on_conflict=id", {
        "id": str(user_id),
        "username": username,
        "avatar": avatar,
        "last_active": "now()"
    })

def get_messages(user_id):
    return supabase_request("GET", f"messages?user_id=eq.{user_id}&order=created_at.asc")

def save_message(user_id, role, content):
    supabase_request("POST", "messages", {
        "user_id": str(user_id),
        "role": role,
        "content": content
    })
    supabase_request("PATCH", f"users?id=eq.{user_id}", {
        "last_active": "now()"
    })

@app.route("/")
def index():
    return open("nova.html", encoding="utf-8").read()

@app.route("/login")
def login():
    params = urllib.parse.urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify"
    })
    return redirect(f"https://discord.com/oauth2/authorize?{params}")

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect("/")

    token_res = requests.post("https://discord.com/api/oauth2/token", data={
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    })
    token_data = token_res.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return redirect("/")

    user_res = requests.get("https://discord.com/api/users/@me", headers={
        "Authorization": f"Bearer {access_token}"
    })
    user_data = user_res.json()

    user_id = user_data["id"]
    username = user_data["username"]
    avatar = f"https://cdn.discordapp.com/avatars/{user_id}/{user_data.get('avatar')}.png" if user_data.get("avatar") else ""

    upsert_user(user_id, username, avatar)

    session["user_id"] = user_id
    session["username"] = username
    session["avatar"] = avatar

    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/me")
def me():
    if "user_id" not in session:
        return jsonify({"logged_in": False})
    return jsonify({
        "logged_in": True,
        "user_id": session["user_id"],
        "username": session["username"],
        "avatar": session["avatar"]
    })

@app.route("/history")
def history():
    if "user_id" not in session:
        return jsonify([])
    messages = get_messages(session["user_id"])
    return jsonify(messages)

@app.route("/chat", methods=["POST"])
def chat():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    try:
        data = request.json
        messages = data["messages"]
        user_message = next((m["content"] for m in reversed(messages) if m["role"] == "user"), None)

        if user_message:
            save_message(session["user_id"], "user", user_message)

        payload = json.dumps({
            "model": data.get("model", OLLAMA_MODEL),
            "messages": messages,
            "stream": False
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://ollama.com/api/chat",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OLLAMA_API_KEY}"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=60) as r:
            result = json.loads(r.read().decode("utf-8"))

        reply = result["message"]["content"]
        save_message(session["user_id"], "assistant", reply)

        return jsonify(result)

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"[HTTP ERROR] {e.code}: {body}")
        return jsonify({"error": f"HTTP {e.code}: {body}"}), 500

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Nova server running at http://localhost:{port}")
    app.run(port=port, host="0.0.0.0", debug=True)
