import os
import json
import base64
import binascii
import sqlite3
import threading
from datetime import datetime, timezone
from io import BytesIO
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

# Your own Telegram numeric user id (chat with @userinfobot to get it).
# VIP payment screenshots get forwarded here for manual review.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

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
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
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
                is_vip INTEGER DEFAULT 0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Migration: CREATE TABLE IF NOT EXISTS above won't add new columns
        # to a users table that already existed before is_vip was introduced.
        cursor.execute("PRAGMA table_info(users)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        if "is_vip" not in existing_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN is_vip INTEGER DEFAULT 0")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS vip_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                package TEXT,
                photo_base64 TEXT,
                status TEXT DEFAULT 'pending',
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER,
                reported_user TEXT,
                reason TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                # UPSERT that only touches the text columns; see
                # upsert_profile_text_fields() for why photo is untouched.
                upsert_profile_text_fields(cursor, user_id, data)
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


# 4a2. VIP PAYMENT RECEIPT ENDPOINT ------------------------------------------
# The frontend previously just showed a fake "sent!" message without
# actually sending anything anywhere. This endpoint receives the receipt
# screenshot, stores a permanent record of it, and forwards the photo to
# the admin's Telegram chat so a human can review and approve it.
@app.route("/submit_vip_receipt", methods=["POST", "OPTIONS"])
def submit_vip_receipt():
    if request.method == "OPTIONS":
        return "", 204

    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id")
    package = payload.get("package", "")
    photo_b64 = payload.get("photo_base64")

    if not user_id or not photo_b64:
        return jsonify({"ok": False, "error": "user_id and photo_base64 are required"}), 400

    if "," in photo_b64:
        photo_b64 = photo_b64.split(",", 1)[1]

    try:
        raw = base64.b64decode(photo_b64, validate=True)
    except (binascii.Error, ValueError):
        return jsonify({"ok": False, "error": "invalid base64 data"}), 400

    if len(raw) > MAX_PHOTO_BYTES:
        return jsonify({"ok": False, "error": "photo too large (max 2MB)"}), 413

    # 1. Keep a permanent record in the database, regardless of whether
    # the Telegram notification below succeeds.
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO vip_requests (user_id, package, photo_base64) VALUES (?, ?, ?)",
            (user_id, package, photo_b64),
        )
        request_id = cursor.lastrowid
        conn.commit()
    except Exception as e:
        print(f"submit_vip_receipt DB error: {e}")
        return jsonify({"ok": False, "error": "database error"}), 500
    finally:
        conn.close()

    # 2. Forward the screenshot to the admin so it can actually be reviewed,
    # with inline Approve/Reject buttons wired to the request id above.
    if not ADMIN_CHAT_ID:
        print("ADMIN_CHAT_ID is not set - VIP receipt was saved but NOT forwarded to an admin.")
        # Still return ok: the record is saved, it just wasn't pushed to Telegram yet.
        return jsonify({"ok": True, "warning": "admin not configured"})

    # Best-effort lookup of the user's display name / username for the
    # admin message. Not fatal if it fails - we still have the user_id.
    display_name = str(user_id)
    username = ""
    try:
        chat = bot.get_chat(user_id)
        if chat.first_name:
            display_name = chat.first_name
            if chat.last_name:
                display_name += f" {chat.last_name}"
        username = chat.username or ""
    except Exception as e:
        print(f"submit_vip_receipt get_chat error: {e}")

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Approve VIP", callback_data=f"vip_approve:{request_id}"),
        InlineKeyboardButton("❌ Reject VIP", callback_data=f"vip_reject:{request_id}"),
    )

    caption = (
        f"👑 New VIP payment receipt\n"
        f"Name: {display_name}\n"
        f"Telegram ID: {user_id}\n"
        f"Username: {'@' + username if username else 'N/A'}\n"
        f"Package: {package}"
    )

    try:
        bot.send_photo(ADMIN_CHAT_ID, BytesIO(raw), caption=caption, reply_markup=markup)
    except Exception as e:
        print(f"submit_vip_receipt admin notify error: {e}")
        # The record is already saved in vip_requests, so this isn't fatal -
        # you can still review it from the database even if the push failed.
        return jsonify({"ok": True, "warning": "saved but admin notification failed"})

    return jsonify({"ok": True})


# 4a3. VIP APPROVE/REJECT CALLBACK -------------------------------------------
# Handles taps on the "✅ Approve VIP" / "❌ Reject VIP" buttons attached to
# the admin notification above. Flips users.is_vip and tells the user.
@bot.callback_query_handler(
    func=lambda call: call.data and call.data.startswith(("vip_approve:", "vip_reject:"))
)
def handle_vip_decision(call):
    action, _, request_id_str = call.data.partition(":")
    try:
        request_id = int(request_id_str)
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid request id")
        return

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, status FROM vip_requests WHERE id = ?", (request_id,))
        row = cursor.fetchone()
        if not row:
            bot.answer_callback_query(call.id, "Request not found")
            return

        target_user_id, status = row
        if status != "pending":
            bot.answer_callback_query(call.id, f"Already {status}")
            return

        new_status = "approved" if action == "vip_approve" else "rejected"
        cursor.execute("UPDATE vip_requests SET status = ? WHERE id = ?", (new_status, request_id))
        if new_status == "approved":
            cursor.execute(
                "INSERT INTO users (user_id, is_vip) VALUES (?, 1) "
                "ON CONFLICT(user_id) DO UPDATE SET is_vip = 1",
                (target_user_id,),
            )
        conn.commit()
    except Exception as e:
        print(f"handle_vip_decision DB error: {e}")
        bot.answer_callback_query(call.id, "Server error")
        return
    finally:
        conn.close()

    if new_status == "approved":
        user_message = "🎉 Congratulations! Your VIP membership has been approved."
        admin_note = "✅ Approved"
    else:
        user_message = "❌ Your payment could not be verified. Please upload a clear payment screenshot."
        admin_note = "❌ Rejected"

    try:
        bot.send_message(target_user_id, user_message)
    except Exception as e:
        print(f"handle_vip_decision notify error: {e}")

    try:
        bot.answer_callback_query(call.id, admin_note)
        original_caption = call.message.caption or ""
        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption=f"{original_caption}\n\n{admin_note}",
        )
    except Exception as e:
        print(f"handle_vip_decision UI update error: {e}")


# 4b. SAVE PROFILE ENDPOINT (replaces tg.sendData() for the text fields) ---
# tg.sendData() closes the Mini App the instant it's called, which makes it
# impossible for the frontend to reliably show a success message and then
# redirect within the app afterwards. Saving over a normal HTTP POST avoids
# that problem entirely and lets the frontend know for certain whether the
# save actually succeeded before it redirects.
REQUIRED_PROFILE_FIELDS = ["name", "age", "phone", "location", "bio"]


def upsert_profile_text_fields(cursor, user_id, data):
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


@app.route("/save_profile", methods=["POST", "OPTIONS"])
def save_profile():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")

    if not user_id:
        return jsonify({"ok": False, "error": "user_id is required"}), 400

    missing = [f for f in REQUIRED_PROFILE_FIELDS if not str(data.get(f, "")).strip()]
    if missing:
        return jsonify({"ok": False, "error": f"missing fields: {', '.join(missing)}"}), 400

    conn = get_db()
    try:
        cursor = conn.cursor()
        upsert_profile_text_fields(cursor, user_id, data)
        conn.commit()
    except Exception as e:
        print(f"save_profile DB error: {e}")
        return jsonify({"ok": False, "error": "database error"}), 500
    finally:
        conn.close()

    # Best-effort confirmation message in the chat. For a private chat
    # opened via the bot's own button, chat_id == user_id, but this is
    # wrapped in try/except since it's not essential to the save itself.
    try:
        bot.send_message(
            user_id,
            f"🎉 **ፕሮፋይልዎ በስኬት ተቀምጧል!**\n\n"
            f"👤 **ስም:** {data.get('name')}\n"
            f"🎂 **እድሜ:** {data.get('age')}\n"
            f"📱 **ስልክ:** {data.get('phone')}\n"
            f"📍 **ቦታ:** {data.get('location')}",
            parse_mode="Markdown",
        )
    except Exception as e:
        print(f"save_profile notify error: {e}")

    return jsonify({"ok": True})


# 4c. PROFILE LOOKUP ENDPOINT --------------------------------------------
# Lets the frontend ask "does this user already have a saved profile?" on
# load, so returning users skip straight to Discover instead of the
# profile form - and so this works from any device, not just the one
# local storage happens to be cached on.
PROFILE_COLUMNS = [
    "name", "age", "gender", "phone", "location",
    "looking_for", "pref_age", "religion", "zodiac", "bio", "is_vip",
]


@app.route("/profile/<int:user_id>", methods=["GET"])
def get_profile(user_id):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {', '.join(PROFILE_COLUMNS)}, photo_base64 IS NOT NULL "
            f"FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"ok": True, "exists": False})

    profile = dict(zip(PROFILE_COLUMNS, row[:-1]))
    profile["is_vip"] = bool(profile["is_vip"])
    profile["hasPhoto"] = bool(row[-1])
    return jsonify({"ok": True, "exists": True, "profile": profile})


# 6. REPORT ENDPOINT ---------------------------------------------------------
# Lets the frontend flag a profile as fake/spam/harassment/etc. The report
# is saved permanently and also pushed to the admin for quick review.
@app.route("/report", methods=["POST", "OPTIONS"])
def submit_report():
    if request.method == "OPTIONS":
        return "", 204

    payload = request.get_json(silent=True) or {}
    reporter_id = payload.get("reporter_id")
    reported_user = payload.get("reported_user")
    reason = payload.get("reason")

    if not reporter_id or not reported_user or not reason:
        return jsonify({"ok": False, "error": "reporter_id, reported_user and reason are required"}), 400

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reports (reporter_id, reported_user, reason) VALUES (?, ?, ?)",
            (reporter_id, str(reported_user), reason),
        )
        conn.commit()
    except Exception as e:
        print(f"submit_report DB error: {e}")
        return jsonify({"ok": False, "error": "database error"}), 500
    finally:
        conn.close()

    if ADMIN_CHAT_ID:
        try:
            bot.send_message(
                ADMIN_CHAT_ID,
                "🚩 New report\n"
                f"Reporter: {reporter_id}\n"
                f"Reported user: {reported_user}\n"
                f"Reason: {reason}\n"
                f"Time: {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC",
      )
        except Exception as e:
            print(f"submit_report admin notify error: {e}")

    return jsonify({"ok": True})


# 7. ADMIN REPORTS COMMAND ---------------------------------------------------
# A lightweight "Admin Reports page": the admin sends /reports in their
# chat with the bot and gets the most recent reports listed back.
@bot.message_handler(commands=["reports"])
def list_reports(message):
    if not ADMIN_CHAT_ID or str(message.chat.id) != str(ADMIN_CHAT_ID):
        return  # silently ignore - reports are admin-only

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, reporter_id, reported_user, reason, status, created_at "
            "FROM reports ORDER BY created_at DESC LIMIT 20"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        bot.reply_to(message, "No reports yet.")
        return

    lines = ["🚩 Recent reports (latest 20):\n"]
    for r_id, reporter_id, reported_user, reason, status, created_at in rows:
        lines.append(
            f"#{r_id} [{status}] {created_at}\n"
            f"Reporter: {reporter_id} → Reported: {reported_user}\n"
            f"Reason: {reason}\n"
        )
    bot.reply_to(message, "\n".join(lines))


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print("True Love Bot is running...")
    bot.infinity_polling(skip_pending=True)
