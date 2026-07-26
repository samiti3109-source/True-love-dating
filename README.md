# True Love — Flask App

A real Flask web app version of the True Love dating app prototype: user accounts,
a SQLite database, swipe-based discovery, mutual matches, and chat. Includes the
EN / አማርኛ language toggle.

## Setup

```bash
cd true_love_flask
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000 in your browser.

The first time it runs, it creates `true_love.db` (SQLite) in this folder with
all the tables it needs — no manual database setup required.

## How it works

- **Accounts**: `/register` and `/login` create real accounts with hashed
  passwords (Werkzeug's `generate_password_hash`).
- **Discover**: `/discover` shows one profile at a time from users of the
  gender you're looking for, excluding anyone you've already swiped on.
- **Swiping**: `/swipe/<user_id>/like` or `/swipe/<user_id>/pass` records the
  swipe. If the other person already liked you back, a `Match` row is created
  and you land on the "It's a match!" screen.
- **Chat**: `/matches` lists your matches; `/chat/<match_id>` is a real
  message thread stored in the `Message` table.
- **Profile**: `/profile` and `/profile/edit` show and update your name, bio,
  age, location, gender, phone, and match preferences.

## Notes for going further

- To create test accounts to swipe between, just register a few different
  usernames with different genders — each browser session only stays logged
  into one account at a time (use an incognito window to test as a second
  user).
- The GPS button and Telegram-share button are UI placeholders — wire them up
  to `navigator.geolocation` and the Telegram Login Widget respectively if you
  want them functional.
- For production use: change `SECRET_KEY` in `app.py` to a real secret, switch
  `SQLALCHEMY_DATABASE_URI` to Postgres/MySQL, and run behind a real WSGI
  server (gunicorn, etc.) instead of `app.run(debug=True)`.
  
