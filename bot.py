import random

from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

from translations import get_translator
from db import get_db, init_db

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key-change-me"

AVATAR_COLORS = [
    "linear-gradient(135deg,#3aa0ff,#1d6fd1)",
    "linear-gradient(135deg,#ff6b7a,#c23c56)",
    "linear-gradient(135deg,#f2b537,#c98a12)",
    "linear-gradient(135deg,#7c6bff,#4a3bd6)",
    "linear-gradient(135deg,#34c99b,#1c8f6c)",
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


# ---------------------- Helpers ----------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    row = get_db_conn().execute("SELECT * FROM user WHERE id = ?", (uid,)).fetchone()
    return row


def other_user_in_match(match_row, user_id):
    other_id = match_row["user_b_id"] if match_row["user_a_id"] == user_id else match_row["user_a_id"]
    return get_db_conn().execute("SELECT * FROM user WHERE id = ?", (other_id,)).fetchone()


@app.context_processor
def inject_globals():
    lang = session.get("lang", "en")
    return {
        "t": get_translator(lang),
        "current_lang": lang,
        "logged_in_user": current_user(),
    }


@app.before_request
def require_login():
    open_endpoints = {"login", "register", "set_lang", "static"}
    if request.endpoint in open_endpoints:
        return
    if not session.get("user_id"):
        return redirect(url_for("login"))


# ---------------------- Auth ----------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        db = get_db_conn()
        username = request.form["username"].strip()
        password = request.form["password"]
        name = request.form["name"].strip()
        age = int(request.form.get("age", 18) or 18)
        gender = request.form.get("gender", "male")
        looking_for = "female" if gender == "male" else "male"

        existing = db.execute("SELECT id FROM user WHERE username = ?", (username,)).fetchone()
        if existing:
            flash(get_translator(session.get("lang", "en"))("username_taken"))
            return redirect(url_for("register"))

        db.execute(
            """INSERT INTO user (username, password_hash, name, age, gender, looking_for, avatar_color)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                username,
                generate_password_hash(password),
                name,
                age,
                gender,
                looking_for,
                random.choice(AVATAR_COLORS),
            ),
        )
        db.commit()
        user_id = db.execute("SELECT id FROM user WHERE username = ?", (username,)).fetchone()["id"]

        session["user_id"] = user_id
        return redirect(url_for("discover"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = get_db_conn().execute("SELECT * FROM user WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("discover"))
        flash(get_translator(session.get("lang", "en"))("invalid_login"))
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("login"))


@app.route("/lang/<code>")
def set_lang(code):
    if code in ("en", "am"):
        session["lang"] = code
    ref = request.referrer or url_for("discover")
    return redirect(ref)


# ---------------------- Discover / Swipe ----------------------
@app.route("/")
def index():
    return redirect(url_for("discover"))


@app.route("/discover")
def discover():
    user = current_user()
    db = get_db_conn()

    swiped_rows = db.execute(
        "SELECT to_user_id FROM swipe WHERE from_user_id = ?", (user["id"],)
    ).fetchall()
    swiped_ids = [r["to_user_id"] for r in swiped_rows] + [user["id"]]

    placeholders = ",".join("?" * len(swiped_ids))
    candidate = db.execute(
        f"""SELECT * FROM user
            WHERE id NOT IN ({placeholders}) AND gender = ?
            ORDER BY id ASC LIMIT 1""",
        (*swiped_ids, user["looking_for"]),
    ).fetchone()

    return render_template("discover.html", candidate=candidate)


@app.route("/swipe/<int:target_id>/<action>")
def swipe(target_id, action):
    user = current_user()
    db = get_db_conn()
    if action not in ("like", "pass"):
        return redirect(url_for("discover"))

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
    if action == "like":
        reciprocal = db.execute(
            "SELECT id FROM swipe WHERE from_user_id = ? AND to_user_id = ? AND action = 'like'",
            (target_id, user["id"]),
        ).fetchone()
        if reciprocal:
            already = db.execute(
                """SELECT id FROM match
                   WHERE (user_a_id = ? AND user_b_id = ?)
                      OR (user_a_id = ? AND user_b_id = ?)""",
                (user["id"], target_id, target_id, user["id"]),
            ).fetchone()
            if not already:
                db.execute(
                    "INSERT INTO match (user_a_id, user_b_id) VALUES (?, ?)",
                    (user["id"], target_id),
                )
                db.commit()
            matched = True

    if matched:
        return redirect(url_for("matched", target_id=target_id))
    return redirect(url_for("discover"))


@app.route("/matched/<int:target_id>")
def matched(target_id):
    other = get_db_conn().execute("SELECT * FROM user WHERE id = ?", (target_id,)).fetchone()
    if not other:
        return redirect(url_for("discover"))
    return render_template("matched.html", other=other)


# ---------------------- Matches / Chat ----------------------
@app.route("/matches")
def matches():
    user = current_user()
    db = get_db_conn()
    user_matches = db.execute(
        """SELECT * FROM match WHERE user_a_id = ? OR user_b_id = ?
           ORDER BY created_at DESC""",
        (user["id"], user["id"]),
    ).fetchall()

    rows = []
    for m in user_matches:
        other = other_user_in_match(m, user["id"])
        last = db.execute(
            "SELECT * FROM message WHERE match_id = ? ORDER BY created_at DESC LIMIT 1",
            (m["id"],),
        ).fetchone()
        rows.append({"match": m, "other": other, "last": last})

    return render_template("matches.html", rows=rows)


@app.route("/chat/<int:match_id>", methods=["GET", "POST"])
def chat(match_id):
    user = current_user()
    db = get_db_conn()
    m = db.execute("SELECT * FROM match WHERE id = ?", (match_id,)).fetchone()
    if not m or user["id"] not in (m["user_a_id"], m["user_b_id"]):
        return redirect(url_for("matches"))

    other = other_user_in_match(m, user["id"])

    if request.method == "POST":
        text = request.form.get("text", "").strip()
        if text:
            db.execute(
                "INSERT INTO message (match_id, sender_id, text) VALUES (?, ?, ?)",
                (match_id, user["id"], text),
            )
            db.commit()
        return redirect(url_for("chat", match_id=match_id))

    msgs = db.execute(
        "SELECT * FROM message WHERE match_id = ? ORDER BY created_at ASC", (match_id,)
    ).fetchall()
    return render_template("chat.html", other=other, msgs=msgs, match_id=match_id)


# ---------------------- Profile ----------------------
@app.route("/profile")
def profile():
    return render_template("profile.html")


@app.route("/profile/edit", methods=["GET", "POST"])
def edit_profile():
    user = current_user()
    db = get_db_conn()
    if request.method == "POST":
        name = request.form["name"].strip()
        bio = request.form.get("bio", "")[:120]
        age = int(request.form.get("age", user["age"]) or user["age"])
        location = request.form.get("location", "").strip()
        gender = request.form.get("gender", user["gender"])
        phone = request.form.get("phone", "").strip()
        looking_for = request.form.get("looking_for", user["looking_for"])
        age_min = int(request.form.get("age_min", user["age_min"]) or user["age_min"])
        age_max = int(request.form.get("age_max", user["age_max"]) or user["age_max"])

        db.execute(
            """UPDATE user SET name=?, bio=?, age=?, location=?, gender=?, phone=?,
               looking_for=?, age_min=?, age_max=? WHERE id=?""",
            (name, bio, age, location, gender, phone, looking_for, age_min, age_max, user["id"]),
        )
        db.commit()
        return redirect(url_for("profile"))

    return render_template("edit_profile.html")


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
