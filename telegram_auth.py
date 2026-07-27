"""
Validates Telegram Mini App `initData` per Telegram's spec:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

In production, set TELEGRAM_BOT_TOKEN as an environment variable. Without it,
the app runs in DEV_MODE, which accepts a `dev_telegram_id` / `dev_name` pair
instead of real Telegram data — useful for building/testing outside Telegram.
"""
import hashlib
import hmac
import os
import time
from urllib.parse import parse_qsl

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DEV_MODE = not BOT_TOKEN

# How long a Telegram initData payload stays valid (Telegram recommends re-checking this).
MAX_AGE_SECONDS = 24 * 60 * 60


def _build_secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def validate_init_data(init_data: str):
    """
    Returns a dict of parsed Telegram user fields on success, or None if invalid.
    """
    if not init_data:
        return None

    parsed = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(
        f"{k}={parsed[k]}" for k in sorted(parsed.keys())
    )
    secret_key = _build_secret_key(BOT_TOKEN)
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > MAX_AGE_SECONDS:
        return None

    import json
    user_json = parsed.get("user")
    if not user_json:
        return None
    user = json.loads(user_json)
    return {
        "telegram_id": str(user["id"]),
        "telegram_username": user.get("username", ""),
        "first_name": user.get("first_name", ""),
    }


def dev_login(telegram_id: str, name: str):
    return {
        "telegram_id": str(telegram_id),
        "telegram_username": "",
        "first_name": name or f"User{telegram_id}",
    }
