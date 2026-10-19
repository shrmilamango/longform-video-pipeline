"""Phone alerts through a free Telegram bot (optional). Always also writes to logs/alerts.log."""
import requests

from . import config
from .util import LOGS, log, now_iso


def notify(msg):
    log("ALERT: " + msg)
    with open(LOGS / "alerts.log", "a", encoding="utf-8") as f:
        f.write(f"{now_iso()}  {msg}\n")
    token, chat = config.key("telegram_bot_token"), config.key("telegram_chat_id")
    if not (token and chat):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": f"🌌 Sleep On Galaxy\n{msg}"}, timeout=20)
    except requests.RequestException as e:
        log(f"Telegram alert failed: {e}")


def print_chat_id():
    token = config.key("telegram_bot_token")
    if not token:
        print("Add telegram_bot_token to config.yaml first.")
        return
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    ids = {u["message"]["chat"]["id"] for u in r.get("result", []) if "message" in u}
    if not ids:
        print("No messages found. Send any message to your bot in Telegram, then run this again.")
    for i in ids:
        print(f"Your chat id: {i}   -> put it in config.yaml as telegram_chat_id")
