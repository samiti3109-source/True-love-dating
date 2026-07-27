# True Love — Telegram Mini App

A production-style dating platform built as a Telegram Mini App: swipe-based
discovery, mutual matching, real-time-feeling chat (typing indicator + read
receipts), multi-photo profiles, a reporting system, and a full admin panel.
Dark, Tinder-style UI throughout.

There is no VIP/payment system in this build — it's a clean, ad-free core
dating experience.

## Setup

```bash
cd telegram_dating_app
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The app creates `dating_app.db` (SQLite) automatically on first run.

### Running without a real Telegram bot (dev mode)

If you don't set `TELEGRAM_BOT_TOKEN`, the app runs in **dev mode**: opening
`/` shows a small form to log in as a fake Telegram user (just an ID + name).
This lets you build and test the whole app — swiping, matching, chatting,
reporting — without needing Telegram at all. Open two different browsers (or
one normal + one incognito window) and log in as two different dev IDs to
test matching and chat between two accounts.

### Connecting it to a real Telegram bot

1. Create a bot with [@BotFather](https://t.me/BotFather) and get its token.
2. Deploy this app somewhere with HTTPS (Telegram Mini Apps require HTTPS —
   services like Render, Railway, Fly.io, or a VPS + reverse proxy all work).
3. Set the environment variable before running:
   ```bash
   export TELEGRAM_BOT_TOKEN="123456:ABC-your-bot-token"
   ```
4. In BotFather, use `/newapp` (or `/setmenubutton`) to register your
   deployed URL as the bot's Mini App. Once that's set, tapping the bot's menu
   button opens this app inside Telegram, and `window.Telegram.WebApp.initData`
   is automatically validated server-side (see `telegram_auth.py`) — real
   users are created from their Telegram identity, no separate signup needed.

## How it works

### Auth & profile flow
- Telegram identity (or the dev-mode fake login) creates/looks up a `user` row
  keyed by `telegram_id`. No passwords for regular users — Telegram is the
  identity provider.
- `POST /api/auth` tells the frontend whether the profile is complete. If not,
  it's sent to `/profile/setup`; if it is, straight to `/discover`. This means
  returning users skip profile creation entirely, every time.

### Discover / Swipe
- `GET /api/discover/next` returns one candidate at a time: excludes yourself,
  anyone already swiped on, banned users, and (if set) filters by the gender
  you're looking for.
- Cards support drag-to-swipe (mouse + touch) with rotation and LIKE/NOPE
  badge animations, plus dedicated pass/like/report buttons.
- `POST /api/swipe` records the swipe and checks for a mutual like — if found,
  a `match` row is created and the frontend shows the match celebration modal.

### Chat
- Chat uses short-interval polling (every 2.5s) rather than WebSockets, so
  there's no extra infrastructure (like Redis or a socket server) required to
  run this anywhere Flask runs. `typing_status` and `message.read_at` give you
  a live typing indicator and read receipts (✓ sent / ✓✓ read) without that
  complexity.
- If you later want true push-based real-time chat, the natural upgrade is
  `flask-socketio` + `eventlet`/`gevent` — the message/typing/read data model
  here maps directly onto socket events if you make that switch.

### Reporting
- Every profile (in Discover and in Chat) has a report button with the
  required reason list. Reports are saved with `status='pending'` and shown
  to admins in `/admin/reports`, where they can mark a report resolved or ban
  the reported user directly.

### Admin panel
- Admin accounts are **separate** from Telegram dating users — they're staff
  accounts with their own username/password login at `/admin/login`, since
  admins run the dashboard from a normal browser, not from inside Telegram.
- Create your first admin account:
  ```bash
  python3 -c "from app import create_admin; create_admin('admin', 'choose-a-strong-password')"
  ```
- Dashboard shows total/banned users, total matches, messages sent, and
  pending reports. User management supports search, ban/unban, and permanent
  delete (which also cleans up that user's photos, swipes, matches, messages,
  and reports — no orphaned rows left behind).

## Notes for going to production

- Change `SECRET_KEY` (set the `SECRET_KEY` env var) to a long random value.
- Swap SQLite for Postgres/MySQL once you outgrow single-file SQLite — only
  `db.py` needs to change, since all queries go through `get_db()`.
- Put uploaded photos behind a CDN or object storage (S3, R2, etc.) instead of
  local disk if you deploy across multiple app instances.
- Run behind gunicorn/uwsgi instead of `app.run(debug=True)`, and always
  behind HTTPS (required by Telegram Mini Apps regardless).
