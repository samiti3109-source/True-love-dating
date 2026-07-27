import os
import json
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, session, jsonify,
    g, send_from_directory, abort
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db, init_db
from telegram_auth import validate_init_data, dev_login, DEV_MODE

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

UPLOAD_DIR = os.path.join(app.root_path, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_PHOTOS = 6

TYPING_TTL_SECONDS = 6

REPORT_REASONS = ["Fake Profile", "Spam", "Harassment", "Inappropriate Photos", "Other"]
ZODIAC_SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]
INTEREST_OPTIONS = [
    "Music", "Travel", "Movies", "Fitness", "Coffee", "Reading", "Gaming",
    "Cooking", "Art", "Photography", "Hiking", "Dancing", "Sports", "Fashion",
]


def get_db_conn():
    if "db" not in g:
        g.db = get_db()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


# ---------------------- Auth helpers ----------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    row = get_db_conn().execute("SELECT * FROM user WHERE id = ?", (uid,)).fetchone()
    if row and row["is_banned"]:
        return None
    return row


def profile_is_complete(user_row):
    if not user_row:
        return False
    required = [user_row["name"], user_row["age"], user_row["gender"], user_row["looking_for"]]
    if any(not v for v in required):
        return False
    photo_count = get_db_conn().execute(
        "SELECT COUNT(*) c FROM photo WHERE user_id = ?", (user_row["id"],)
    ).fetchone()["c"]
    return photo_count > 0


def current_admin():
    aid = session.get("admin_id")
    if not aid:
        return None
    return get_db_conn().execute("SELECT * FROM admin WHERE id = ?", (aid,)).fetchone()


@app.context_processor
def inject_globals():
    return {
        "logged_in_user": current_user(),
        "logged_in_admin": current_admin(),
        "dev_mode": DEV_MODE,
    }


PUBLIC_ENDPOINTS = {
    "index", "api_auth", "static", "uploaded_file",
    "admin_login",
}


@app.before_request
def guard():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if request.endpoint and request.endpoint.startswith("admin"):
        if not current_admin():
            return redirect(url_for("admin_login"))
        return
    if not session.get("user_id"):
        return redirect(url_for("index"))


# ---------------------- Entry / Telegram auth ----------------------
@app.route("/")
def index():
    if session.get("user_id"):
        user = current_user()
        if user:
            return redirect(
                url_for("discover") if profile_is_complete(user) else url_for("profile_setup")
            )
    return render_template("index.html")


@app.route("/api/auth", methods=["POST"])
def api_auth():
    data = request.get_json(silent=True) or {}
    db = get_db_conn()

    tg = None
    if DEV_MODE and data.get("dev_telegram_id"):
        tg = dev_login(data["dev_telegram_id"], data.get("dev_name", ""))
    else:
        tg = validate_init_data(data.get("initData", ""))

    if not tg:
        return jsonify({"status": "error", "message": "Invalid Telegram auth data"}), 401

    row = db.execute(
        "SELECT * FROM user WHERE telegram_id = ?", (tg["telegram_id"],)
    ).fetchone()

    if row:
        if row["is_banned"]:
            return jsonify({"status": "error", "message": "This account has been banned."}), 403
        db.execute(
            "UPDATE user SET last_seen = CURRENT_TIMESTAMP, telegram_username = ? WHERE id = ?",
            (tg["telegram_username"], row["id"]),
        )
        db.commit()
        user_id = row["id"]
    else:
        db.execute(
            "INSERT INTO user (telegram_id, telegram_username, name) VALUES (?, ?, ?)",
            (tg["telegram_id"], tg["telegram_username"], tg["first_name"]),
        )
        db.commit()
        user_id = db.execute(
            "SELECT id FROM user WHERE telegram_id = ?", (tg["telegram_id"],)
        ).fetchone()["id"]

    session["user_id"] = user_id
    user = db.execute("SELECT * FROM user WHERE id = ?", (user_id,)).fetchone()
    return jsonify({
        "status": "ok",
        "profile_complete": profile_is_complete(user),
        "redirect": url_for("discover") if profile_is_complete(user) else url_for("profile_setup"),
    })


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ---------------------- Profile ----------------------
@app.route("/profile/setup")
def profile_setup():
    return render_template(
        "profile_setup.html", zodiac_signs=ZODIAC_SIGNS, interest_options=INTEREST_OPTIONS
    )


@app.route("/profile")
def profile_view():
    user = current_user()
    photos = get_db_conn().execute(
        "SELECT * FROM photo WHERE user_id = ? ORDER BY position ASC", (user["id"],)
    ).fetchall()
    return render_template("profile_view.html", photos=photos)


@app.route("/profile/edit")
def profile_edit():
    user = current_user()
    photos = get_db_conn().execute(
        "SELECT * FROM photo WHERE user_id = ? ORDER BY position ASC", (user["id"],)
    ).fetchall()
    return render_template(
        "profile_setup.html", edit_mode=True, photos=photos,
        zodiac_signs=ZODIAC_SIGNS, interest_options=INTEREST_OPTIONS,
    )


@app.route("/api/profile", methods=["POST"])
def api_save_profile():
    user = current_user()
    db = get_db_conn()

    name = request.form.get("name", "").strip()
    bio = request.form.get("bio", "").strip()[:250]
    age = request.form.get("age", "").strip()
    gender = request.form.get("gender", "").strip()
    looking_for = request.form.get("looking_for", "").strip()
    location = request.form.get("location", "").strip()
    interests = request.form.get("interests", "").strip()
    zodiac = request.form.get("zodiac", "").strip()

    errors = []
    if not name:
        errors.append("Name is required.")
    if not age or not age.isdigit() or not (18 <= int(age) <= 99):
        errors.append("Age must be between 18 and 99.")
    if gender not in ("male", "female"):
        errors.append("Gender is required.")
    if looking_for not in ("male", "female", "everyone"):
        errors.append("Looking for is required.")

    existing_photo_count = db.execute(
        "SELECT COUNT(*) c FROM photo WHERE user_id = ?", (user["id"],)
    ).fetchone()["c"]
    new_files = [f for f in request.files.getlist("photos") if f and f.filename]
    if existing_photo_count + len(new_files) == 0:
        errors.append("At least one photo is required.")
    if existing_photo_count + len(new_files) > MAX_PHOTOS:
        errors.append(f"Maximum {MAX_PHOTOS} photos allowed.")

    if errors:
        return jsonify({"status": "error", "errors": errors}), 400

    db.execute(
        """UPDATE user SET name=?, bio=?, age=?, gender=?, looking_for=?,
           location=?, interests=?, zodiac=? WHERE id=?""",
        (name, bio, int(age), gender, looking_for, location, interests, zodiac, user["id"]),
    )

    next_pos = existing_photo_count
    for f in new_files:
        if not allowed_file(f.filename):
            continue
        ext = f.filename.rsplit(".", 1)[1].lower()
        fname = secure_filename(f"user{user['id']}_{next_pos}_{int(datetime.utcnow().timestamp())}.{ext}")
        f.save(os.path.join(UPLOAD_DIR, fname))
        db.execute(
            "INSERT INTO photo (user_id, filename, position) VALUES (?, ?, ?)",
            (user["id"], fname, next_pos),
        )
        next_pos += 1

    db.commit()
    updated_user = db.execute("SELECT * FROM user WHERE id = ?", (user["id"],)).fetchone()
    return jsonify({
        "status": "ok",
        "redirect": url_for("discover") if profile_is_complete(updated_user) else url_for("profile_setup"),
    })


@app.route("/api/profile/photo/<int:photo_id>", methods=["DELETE"])
def api_delete_photo(photo_id):
    user = current_user()
    db = get_db_conn()
    photo = db.execute("SELECT * FROM photo WHERE id = ? AND user_id = ?", (photo_id, user["id"])).fetchone()
    if not photo:
        return jsonify({"status": "error"}), 404
    try:
        os.remove(os.path.join(UPLOAD_DIR, photo["filename"]))
    except OSError:
        pass
    db.execute("DELETE FROM photo WHERE id = ?", (photo_id,))
    db.commit()
    return jsonify({"status": "ok"})


# ---------------------- Discover / Swipe ----------------------
@app.route("/discover")
def discover():
    return render_template("discover.html", report_reasons=REPORT_REASONS)


@app.route("/api/discover/next")
def api_discover_next():
    user = current_user()
    db = get_db_conn()

    swiped_rows = db.execute(
        "SELECT to_user_id FROM swipe WHERE from_user_id = ?", (user["id"],)
    ).fetchall()
    exclude_ids = [r["to_user_id"] for r in swiped_rows] + [user["id"]]
    placeholders = ",".join("?" * len(exclude_ids))

    gender_clause = ""
    params = list(exclude_ids)
    if user["looking_for"] in ("male", "female"):
        gender_clause = "AND gender = ?"
        params.append(user["looking_for"])

    candidate = db.execute(
        f"""SELECT * FROM user
            WHERE id NOT IN ({placeholders}) AND is_banned = 0 {gender_clause}
            ORDER BY id ASC LIMIT 1""",
        params,
    ).fetchone()

    if not candidate:
        return jsonify({"status": "empty"})

    photos = db.execute(
        "SELECT filename FROM photo WHERE user_id = ? ORDER BY position ASC", (candidate["id"],)
    ).fetchall()

    return jsonify({
        "status": "ok",
        "profile": {
            "id": candidate["id"],
            "name": candidate["name"],
            "age": candidate["age"],
            "bio": candidate["bio"],
            "location": candidate["location"],
            "interests": [i for i in candidate["interests"].split(",") if i],
            "zodiac": candidate["zodiac"],
            "photos": [url_for("uploaded_file", filename=p["filename"]) for p in photos],
        },
    })


@app.route("/api/swipe", methods=["POST"])
def api_swipe():
    user = current_user()
    db = get_db_conn()
    data = request.get_json(silent=True) or {}
    target_id = data.get("target_id")
    action = data.get("action")

    if action not in ("like", "pass") or not target_id:
        return jsonify({"status": "error"}), 400

    existing = db.execute(
        "SELECT id FROM swipe WHERE from_user_id = ? AND to_user_id = ?",
        (user["id"], target_id),
    ).fetchone()
    if not existing:
        db.execute(
            "INSERT INTO swipe (from_user_id, to_user_id, action) VALUES (?, ?, ?)",
            (user["id"], target_id, action),
        )
        db.commit()

    matched = False
    match_id = None
    if action == "like":
        reciprocal = db.execute(
            "SELECT id FROM swipe WHERE from_user_id = ? AND to_user_id = ? AND action = 'like'",
            (target_id, user["id"]),
        ).fetchone()
        if reciprocal:
            already = db.execute(
                """SELECT id FROM match WHERE (user_a_id=? AND user_b_id=?) OR (user_a_id=? AND user_b_id=?)""",
                (user["id"], target_id, target_id, user["id"]),
            ).fetchone()
            if already:
                match_id = already["id"]
            else:
                db.execute(
                    "INSERT INTO match (user_a_id, user_b_id) VALUES (?, ?)",
                    (user["id"], target_id),
                )
                db.commit()
                match_id = db.execute(
                    "SELECT id FROM match WHERE user_a_id=? AND user_b_id=?",
                    (user["id"], target_id),
                ).fetchone()["id"]
            matched = True

    other = None
    if matched:
        o = db.execute("SELECT * FROM user WHERE id = ?", (target_id,)).fetchone()
        photo = db.execute(
            "SELECT filename FROM photo WHERE user_id = ? ORDER BY position ASC LIMIT 1", (target_id,)
        ).fetchone()
        other = {
            "id": o["id"], "name": o["name"],
            "photo": url_for("uploaded_file", filename=photo["filename"]) if photo else None,
        }

    return jsonify({"status": "ok", "matched": matched, "match_id": match_id, "other": other})


# ---------------------- Matches ----------------------
@app.route("/matches")
def matches_page():
    return render_template("matches.html")


@app.route("/api/matches")
def api_matches():
    user = current_user()
    db = get_db_conn()
    rows = db.execute(
        "SELECT * FROM match WHERE user_a_id=? OR user_b_id=? ORDER BY last_message_at DESC",
        (user["id"], user["id"]),
    ).fetchall()

    out = []
    for m in rows:
        other_id = m["user_b_id"] if m["user_a_id"] == user["id"] else m["user_a_id"]
        other = db.execute("SELECT * FROM user WHERE id = ?", (other_id,)).fetchone()
        photo = db.execute(
            "SELECT filename FROM photo WHERE user_id=? ORDER BY position ASC LIMIT 1", (other_id,)
        ).fetchone()
        last_msg = db.execute(
            "SELECT * FROM message WHERE match_id=? ORDER BY created_at DESC LIMIT 1", (m["id"],)
        ).fetchone()
        unread = db.execute(
            "SELECT COUNT(*) c FROM message WHERE match_id=? AND sender_id=? AND read_at IS NULL",
            (m["id"], other_id),
        ).fetchone()["c"]
        out.append({
            "match_id": m["id"],
            "name": other["name"],
            "photo": url_for("uploaded_file", filename=photo["filename"]) if photo else None,
            "last_message": last_msg["text"] if last_msg else None,
            "unread": unread,
        })
    return jsonify({"status": "ok", "matches": out})


def _get_match_or_404(match_id, user_id):
    db = get_db_conn()
    m = db.execute("SELECT * FROM match WHERE id = ?", (match_id,)).fetchone()
    if not m or user_id not in (m["user_a_id"], m["user_b_id"]):
        return None
    return m


# ---------------------- Chat ----------------------
@app.route("/chat/<int:match_id>")
def chat_page(match_id):
    user = current_user()
    m = _get_match_or_404(match_id, user["id"])
    if not m:
        abort(404)
    other_id = m["user_b_id"] if m["user_a_id"] == user["id"] else m["user_a_id"]
    other = get_db_conn().execute("SELECT * FROM user WHERE id = ?", (other_id,)).fetchone()
    return render_template("chat.html", match_id=match_id, other=other)


@app.route("/api/chat/<int:match_id>/messages")
def api_chat_messages(match_id):
    user = current_user()
    m = _get_match_or_404(match_id, user["id"])
    if not m:
        return jsonify({"status": "error"}), 404
    db = get_db_conn()

    since = request.args.get("since", 0, type=int)
    msgs = db.execute(
        "SELECT * FROM message WHERE match_id=? AND id > ? ORDER BY id ASC",
        (match_id, since),
    ).fetchall()

    other_id = m["user_b_id"] if m["user_a_id"] == user["id"] else m["user_a_id"]
    typing_row = db.execute(
        "SELECT updated_at FROM typing_status WHERE match_id=? AND user_id=?",
        (match_id, other_id),
    ).fetchone()
    other_typing = False
    if typing_row:
        updated = datetime.fromisoformat(typing_row["updated_at"])
        other_typing = (datetime.utcnow() - updated) < timedelta(seconds=TYPING_TTL_SECONDS)

    return jsonify({
        "status": "ok",
        "messages": [
            {
                "id": r["id"], "sender_id": r["sender_id"], "text": r["text"],
                "created_at": r["created_at"], "read": r["read_at"] is not None,
                "mine": r["sender_id"] == user["id"],
            }
            for r in msgs
        ],
        "other_typing": other_typing,
    })


@app.route("/api/chat/<int:match_id>/send", methods=["POST"])
def api_chat_send(match_id):
    user = current_user()
    m = _get_match_or_404(match_id, user["id"])
    if not m:
        return jsonify({"status": "error"}), 404
    text = (request.get_json(silent=True) or {}).get("text", "").strip()
    if not text:
        return jsonify({"status": "error", "message": "Empty message"}), 400

    db = get_db_conn()
    db.execute(
        "INSERT INTO message (match_id, sender_id, text) VALUES (?, ?, ?)",
        (match_id, user["id"], text),
    )
    db.execute("UPDATE match SET last_message_at = CURRENT_TIMESTAMP WHERE id = ?", (match_id,))
    db.execute(
        "DELETE FROM typing_status WHERE match_id=? AND user_id=?", (match_id, user["id"])
    )
    db.commit()
    return jsonify({"status": "ok"})


@app.route("/api/chat/<int:match_id>/typing", methods=["POST"])
def api_chat_typing(match_id):
    user = current_user()
    m = _get_match_or_404(match_id, user["id"])
    if not m:
        return jsonify({"status": "error"}), 404
    db = get_db_conn()
    db.execute(
        """INSERT INTO typing_status (match_id, user_id, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(match_id, user_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP""",
        (match_id, user["id"]),
    )
    db.commit()
    return jsonify({"status": "ok"})


@app.route("/api/chat/<int:match_id>/read", methods=["POST"])
def api_chat_read(match_id):
    user = current_user()
    m = _get_match_or_404(match_id, user["id"])
    if not m:
        return jsonify({"status": "error"}), 404
    db = get_db_conn()
    db.execute(
        "UPDATE message SET read_at = CURRENT_TIMESTAMP WHERE match_id=? AND sender_id != ? AND read_at IS NULL",
        (match_id, user["id"]),
    )
    db.commit()
    return jsonify({"status": "ok"})


# ---------------------- Report ----------------------
@app.route("/api/report", methods=["POST"])
def api_report():
    user = current_user()
    data = request.get_json(silent=True) or {}
    reported_id = data.get("reported_id")
    reason = data.get("reason")
    details = (data.get("details") or "").strip()[:500]

    if not reported_id or reason not in REPORT_REASONS:
        return jsonify({"status": "error"}), 400

    db = get_db_conn()
    db.execute(
        "INSERT INTO report (reporter_id, reported_id, reason, details) VALUES (?, ?, ?, ?)",
        (user["id"], reported_id, reason, details),
    )
    db.commit()
    return jsonify({"status": "ok"})


# ======================================================================
# Admin panel (separate staff login, not tied to Telegram identity)
# ======================================================================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db_conn()
        row = db.execute("SELECT * FROM admin WHERE username = ?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session["admin_id"] = row["id"]
            return redirect(url_for("admin_dashboard"))
        return render_template("admin/login.html", error="Invalid credentials")
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
def admin_dashboard():
    db = get_db_conn()
    stats = {
        "total_users": db.execute("SELECT COUNT(*) c FROM user").fetchone()["c"],
        "banned_users": db.execute("SELECT COUNT(*) c FROM user WHERE is_banned=1").fetchone()["c"],
        "total_matches": db.execute("SELECT COUNT(*) c FROM match").fetchone()["c"],
        "total_messages": db.execute("SELECT COUNT(*) c FROM message").fetchone()["c"],
        "pending_reports": db.execute("SELECT COUNT(*) c FROM report WHERE status='pending'").fetchone()["c"],
    }
    return render_template("admin/dashboard.html", stats=stats)


@app.route("/admin/users")
def admin_users():
    db = get_db_conn()
    q = request.args.get("q", "").strip()
    if q:
        rows = db.execute(
            "SELECT * FROM user WHERE name LIKE ? OR telegram_username LIKE ? ORDER BY id DESC",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM user ORDER BY id DESC").fetchall()
    return render_template("admin/users.html", users=rows, q=q)


@app.route("/admin/users/<int:user_id>/ban", methods=["POST"])
def admin_ban_user(user_id):
    db = get_db_conn()
    db.execute("UPDATE user SET is_banned=1 WHERE id=?", (user_id,))
    db.commit()
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/unban", methods=["POST"])
def admin_unban_user(user_id):
    db = get_db_conn()
    db.execute("UPDATE user SET is_banned=0 WHERE id=?", (user_id,))
    db.commit()
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
def admin_delete_user(user_id):
    db = get_db_conn()
    photos = db.execute("SELECT * FROM photo WHERE user_id=?", (user_id,)).fetchall()
    for p in photos:
        try:
            os.remove(os.path.join(UPLOAD_DIR, p["filename"]))
        except OSError:
            pass
    match_rows = db.execute(
        "SELECT id FROM match WHERE user_a_id=? OR user_b_id=?", (user_id, user_id)
    ).fetchall()
    match_ids = [m["id"] for m in match_rows]
    for mid in match_ids:
        db.execute("DELETE FROM message WHERE match_id=?", (mid,))
        db.execute("DELETE FROM typing_status WHERE match_id=?", (mid,))
    db.execute("DELETE FROM match WHERE user_a_id=? OR user_b_id=?", (user_id, user_id))
    db.execute("DELETE FROM swipe WHERE from_user_id=? OR to_user_id=?", (user_id, user_id))
    db.execute("DELETE FROM report WHERE reporter_id=? OR reported_id=?", (user_id, user_id))
    db.execute("DELETE FROM photo WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM user WHERE id=?", (user_id,))
    db.commit()
    return redirect(url_for("admin_users"))


@app.route("/admin/reports")
def admin_reports():
    db = get_db_conn()
    rows = db.execute(
        """SELECT report.*, reporter.name as reporter_name, reported.name as reported_name,
                  reported.id as reported_user_id
           FROM report
           JOIN user reporter ON reporter.id = report.reporter_id
           JOIN user reported ON reported.id = report.reported_id
           ORDER BY report.created_at DESC"""
    ).fetchall()
    return render_template("admin/reports.html", reports=rows)


@app.route("/admin/reports/<int:report_id>/resolve", methods=["POST"])
def admin_resolve_report(report_id):
    db = get_db_conn()
    db.execute("UPDATE report SET status='resolved' WHERE id=?", (report_id,))
    db.commit()
    return redirect(url_for("admin_reports"))


@app.route("/admin/reports/<int:report_id>/ban", methods=["POST"])
def admin_ban_from_report(report_id):
    db = get_db_conn()
    row = db.execute("SELECT reported_id FROM report WHERE id=?", (report_id,)).fetchone()
    if row:
        db.execute("UPDATE user SET is_banned=1 WHERE id=?", (row["reported_id"],))
    db.execute("UPDATE report SET status='resolved' WHERE id=?", (report_id,))
    db.commit()
    return redirect(url_for("admin_reports"))


def create_admin(username, password):
    """CLI helper: python -c "from app import create_admin; create_admin('admin','pass')" """
    with app.app_context():
        init_db()
        db = get_db()
        existing = db.execute("SELECT id FROM admin WHERE username=?", (username,)).fetchone()
        if existing:
            print("Admin already exists.")
            return
        db.execute(
            "INSERT INTO admin (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        db.commit()
        print(f"Admin '{username}' created.")


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
