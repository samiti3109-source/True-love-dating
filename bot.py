import os
import json
import base64
import binascii
import sqlite3
import threading
from flask import Flask, send_from_directory, request, jsonify, abort
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# --- Configuration -----------------------------------------------------
# The bot token must come from an environment variable. Never hardcode a
# real token in source control - anyone who sees this file (or the repo
# history) can take over the bot with it.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Set it in your hosting provider's dashboard (e.g. Render -> Environment)."
    )

WEB_APP_URL = os.environ.get(
    "WEB_APP_URL", "https://samiti3109-source.github.io/True-love-dating/"
)

# The frontend (index.html) is hosted separately on GitHub Pages, so the
# browser calls this backend cross-origin. Restrict which origin(s) are
# allowed to call the /upload_photo endpoint instead of using '*'.
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://samiti3109-source.github.io")

MAX_PHOTO_BYTES = 2 * 1024 * 1024  # 2 MB safety cap on uploaded photos
DB_PATH = os.environ.get("DB_PATH", "database.db")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__, static_folder=".")

# 1. FLASK ROUTES ---------------------------------------------------------
# Only ever serve a fixed allow-list of static assets. The previous
# catch-all `/<path:path>` route served EVERY file next to bot.py,
# including bot.py itself and database.db (which contains phone numbers
# and locations) to anyone who guessed the filename.
ALLOWED_STATIC_FILES = {"index.html", "style.css", "app.js", "favicon.ico"}


@app.route("/")
@app.route("/app")
def serve_app():
    return send_from_directory(".", "index.html")


@app.route("/<path:path>")
def serve_static(path):
    if path not in ALLOWED_STATIC_FILES:
        abort(404)
    return send_from_directory(".", path)


@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# 2. DATABASE --------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                age TEXT,
                gender TEXT,
                phone TEXT,
                location TEXT,
                looking_for TEXT,
                pref_age TEXT,
                religion TEXT,
                zodiac TEXT,
                bio TEXT,
                photo_base64 TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


init_db()

# 3. BOT /start COMMAND -----------------------------------------------------
@bot.message_handler(commands=["start"])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    web_app_btn = InlineKeyboardButton(
        text="❤️ Open True Love App", web_app=WebAppInfo(url=WEB_APP_URL)
    )
    markup.add(web_app_btn)

    bot.reply_to(
        message,
        f"ሰላም {message.from_user.first_name}! እንኳን ወደ True Love በደህና መጡ።\n\n"
        f"ታች ያለውን ቁልፍ በመጫን አፑን ይክፈቱ፡",
        reply_markup=markup,
    )


# 4. WEB APP DATA HANDLER (text profile fields, sent via tg.sendData) -------
@bot.message_handler(content_types=["web_app_data"])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)

        if data.get("action") == "save_profile":
            user_id = message.from_user.id

            conn = get_db()
            try:
                cursor = conn.cursor()
                # UPSERT that only touches the text columns. This matters
                # because the photo is uploaded separately via
                # /upload_photo (see below) - if this handler ran a blind
                # INSERT OR REPLACE it would wipe out a photo that was
                # already saved, regardless of which request lands first.
                cursor.execute(
                    """
                    INSERT INTO users
                        (user_id, name, age, gender, phone, location,
                         looking_for, pref_age, religion, zodiac, bio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        name=excluded.name,
                        age=excluded.age,
                        gender=excluded.gender,
                        phone=excluded.phone,
                        location=excluded.location,
                        looking_for=excluded.looking_for,
                        pref_age=excluded.pref_age,
                        religion=excluded.religion,
                        zodiac=excluded.zodiac,
                        bio=excluded.bio
                    """,
                    (
                        user_id,
                        data.get("name"),
                        data.get("age"),
                        data.get("gender"),
                        data.get("phone"),
                        data.get("location"),
                        data.get("lookingFor"),
                        data.get("prefAge"),
                        data.get("religion"),
                        data.get("zodiac"),
                        data.get("bio"),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            bot.send_message(
                message.chat.id,
                f"🎉 **ፕሮፋይልዎ በስኬት ተቀምጧል!**\n\n"
                f"👤 **ስም:** {data.get('name')}\n"
                f"🎂 **እድሜ:** {data.get('age')}\n"
                f"📱 **ስልክ:** {data.get('phone')}\n"
                f"📍 **ቦታ:** {data.get('location')}",
                parse_mode="Markdown",
            )

    except Exception as e:
        print(f"Error: {e}")
        bot.send_message(message.chat.id, "❌ መረጃውን ማስቀመጥ አልተቻለም።")


# 5. PHOTO UPLOAD ENDPOINT ---------------------------------------------------
# IMPORTANT: Telegram's WebApp.sendData() caps the payload at 4096 bytes.
# A base64-encoded photo is almost always far bigger than that, so it can
# never travel inside sendData() - Telegram will silently refuse to send
# it. This endpoint lets the frontend upload the photo directly to the
# Flask server over a normal HTTP POST instead, with no such size limit
# (other than the MAX_PHOTO_BYTES cap below).
@app.route("/upload_photo", methods=["POST", "OPTIONS"])
def upload_photo():
    if request.method == "OPTIONS":
        return "", 204

    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id")
    photo_b64 = payload.get("photo_base64")

    if not user_id or not photo_b64:
        return jsonify({"ok": False, "error": "user_id and photo_base64 are required"}), 400

    # Strip a data URL prefix like "data:image/jpeg;base64," if present.
    if "," in photo_b64:
        photo_b64 = photo_b64.split(",", 1)[1]

    try:
        raw = base64.b64decode(photo_b64, validate=True)
    except (binascii.Error, ValueError):
        return jsonify({"ok": False, "error": "invalid base64 data"}), 400

    if len(raw) > MAX_PHOTO_BYTES:
        return jsonify({"ok": False, "error": "photo too large (max 2MB)"}), 413

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (user_id, photo_base64)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET photo_base64=excluded.photo_base64
            """,
            (user_id, photo_b64),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print("True Love Bot is running...")
    bot.infinity_polling(skip_pending=True)
