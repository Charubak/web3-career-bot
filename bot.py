#!/usr/bin/env python3
"""
Web3 Career Bot — Telegram bot with interactive setup flow.

On /start the bot walks the user through:
  1. Role selection  (inline buttons, multi-select)
  2. Location mode   (Remote / Specific cities / Anywhere)
  3. City input      (if Specific chosen)

Preferences are saved to user_prefs.json and used for all future scrapes.
The bot also runs a built-in 6-hour scheduler.
"""

import sys
import time
import threading
import schedule
import httpx
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

API              = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
POLL_TIMEOUT     = 30
STALE_CMD_SECS   = 60

_sent_msg_ids: list = []
_fetch_lock          = threading.Lock()

# ---------------------------------------------------------------------------
# All available role options shown in the setup keyboard
# ---------------------------------------------------------------------------

ALL_ROLES = [
    ("marketing",   "📣 Marketing"),
    ("engineering", "⚙️ Engineering"),
    ("legal",       "⚖️ Legal"),
    ("design",      "🎨 Design"),
    ("product",     "📦 Product"),
    ("operations",  "🔧 Operations"),
    ("bd",          "🤝 BD / Sales"),
    ("research",    "🔬 Research"),
    ("data",        "📊 Data"),
]

# Per-user setup state (in-memory; resets on restart — final prefs are persisted)
_setup: dict = {}   # user_id -> {"step": str, "roles": list, "setup_msg_id": int|None}

# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def _api(method: str, payload: dict) -> dict:
    try:
        r = httpx.post(f"{API}/{method}", json=payload, timeout=15)
        return r.json()
    except Exception as e:
        print(f"[bot] API error ({method}): {e}")
        return {}


def get_updates(offset: int) -> list:
    try:
        r = httpx.get(
            f"{API}/getUpdates",
            params={"timeout": POLL_TIMEOUT, "offset": offset},
            timeout=POLL_TIMEOUT + 10,
        )
        return r.json().get("result", [])
    except Exception as e:
        print(f"[bot] getUpdates error: {e}")
        time.sleep(5)
        return []


def send(text: str, keyboard: dict = None) -> dict:
    payload = {
        "chat_id":                  TELEGRAM_CHAT_ID,
        "text":                     text,
        "parse_mode":              "Markdown",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    data = _api("sendMessage", payload)
    if data.get("ok"):
        _sent_msg_ids.append(data["result"]["message_id"])
    return data


def edit_text(msg_id: int, text: str, keyboard: dict = None) -> None:
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "message_id": msg_id,
        "text":       text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    payload["reply_markup"] = keyboard if keyboard else {"inline_keyboard": []}
    _api("editMessageText", payload)


def edit_keyboard(msg_id: int, keyboard: dict) -> None:
    _api("editMessageReplyMarkup", {
        "chat_id":      TELEGRAM_CHAT_ID,
        "message_id":  msg_id,
        "reply_markup": keyboard,
    })


def answer_cb(query_id: str, text: str = "") -> None:
    _api("answerCallbackQuery", {"callback_query_id": query_id, "text": text})

# ---------------------------------------------------------------------------
# Setup flow — keyboards
# ---------------------------------------------------------------------------

def _role_keyboard(selected: list) -> dict:
    rows, row = [], []
    for role_id, label in ALL_ROLES:
        mark = "✅ " if role_id in selected else ""
        row.append({"text": f"{mark}{label}", "callback_data": f"role_{role_id}"})
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    btn = "➡️  Continue" if selected else "⚠️  Select at least one"
    rows.append([{"text": btn, "callback_data": "roles_done"}])
    return {"inline_keyboard": rows}


def _location_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "🌍  Remote only (recommended)", "callback_data": "loc_remote"}],
        [{"text": "📍  Specific cities",            "callback_data": "loc_specific"}],
        [{"text": "🔓  Anywhere",                   "callback_data": "loc_any"}],
    ]}

# ---------------------------------------------------------------------------
# Setup flow — entry & state transitions
# ---------------------------------------------------------------------------

def start_setup(user_id: str) -> None:
    _setup[user_id] = {"step": "roles", "roles": [], "setup_msg_id": None}
    data = send(
        "*Welcome to Web3 Career Bot!* 🤖\n\n"
        "Let's get you set up. *What roles are you looking for?*\n"
        "_Tap to select one or more, then press Continue:_",
        _role_keyboard([]),
    )
    if data.get("ok"):
        _setup[user_id]["setup_msg_id"] = data["result"]["message_id"]


def _finish_setup(user_id: str, roles: list, location_type: str, cities: list) -> None:
    import prefs
    prefs.save(roles, location_type, cities)

    role_labels = ", ".join(
        next((lbl for rid, lbl in ALL_ROLES if rid == r), r.title())
        for r in roles
    )
    loc_label = {
        "remote":   "🌍 Remote only",
        "any":      "🔓 Anywhere",
        "specific": f"📍 {', '.join(c.title() for c in cities)} (+ remote)",
    }.get(location_type, location_type)

    setup_msg_id = _setup.pop(user_id, {}).get("setup_msg_id")
    msg = (
        f"✅ *All set!*\n\n"
        f"📋 Roles: *{role_labels}*\n"
        f"📍 Location: *{loc_label}*\n\n"
        f"I'll scrape jobs every 6 hours automatically.\n"
        f"Send /jobs to fetch right now, or /settings to change your preferences."
    )
    if setup_msg_id:
        edit_text(setup_msg_id, msg)
    else:
        send(msg)
    print(f"[bot] Setup saved — roles={roles} location={location_type} cities={cities}")

# ---------------------------------------------------------------------------
# Callback query handler (button presses)
# ---------------------------------------------------------------------------

def handle_callback(cq: dict) -> None:
    query_id = cq["id"]
    data     = cq.get("data", "")
    user_id  = str(cq.get("from", {}).get("id", ""))
    msg_id   = cq.get("message", {}).get("message_id")

    answer_cb(query_id)   # dismiss loading spinner

    state = _setup.get(user_id)
    if not state:
        return

    step = state["step"]

    # ── Role selection ──────────────────────────────────────────────────────
    if step == "roles":
        if data.startswith("role_"):
            role = data[5:]
            if role in state["roles"]:
                state["roles"].remove(role)
            else:
                state["roles"].append(role)
            edit_keyboard(msg_id, _role_keyboard(state["roles"]))

        elif data == "roles_done":
            if not state["roles"]:
                answer_cb(query_id, "Please select at least one role first!")
                return
            state["step"] = "location"
            edit_text(
                msg_id,
                "*Great choice!* Now, where do you want to work?",
                _location_keyboard(),
            )

    # ── Location selection ──────────────────────────────────────────────────
    elif step == "location":
        if data == "loc_remote":
            _finish_setup(user_id, state["roles"], "remote", [])
        elif data == "loc_any":
            _finish_setup(user_id, state["roles"], "any", [])
        elif data == "loc_specific":
            state["step"] = "cities"
            edit_text(
                msg_id,
                "📍 *Which cities?*\n\nType them below, separated by commas:\n"
                "_e.g.  Dubai, Singapore, London_",
            )

# ---------------------------------------------------------------------------
# City text input handler
# ---------------------------------------------------------------------------

def handle_city_input(user_id: str, text: str) -> None:
    state = _setup.get(user_id, {})
    if state.get("step") != "cities":
        return
    cities = [c.strip().lower() for c in text.split(",") if c.strip()]
    if not cities:
        send("Couldn't read those cities. Try again, e.g.  `Dubai, Singapore`")
        return
    _finish_setup(user_id, state["roles"], "specific", cities)

# ---------------------------------------------------------------------------
# /jobs and /new handlers
# ---------------------------------------------------------------------------

def _do_fetch_jobs(new_only: bool = False) -> None:
    if not _fetch_lock.acquire(blocking=False):
        send("⏳ Already fetching — please wait.")
        return
    try:
        send("🔍 Fetching jobs... give me a minute.")
        from boards import fetch_all
        from filters import apply_filters
        from notifier import send_jobs, _role_label

        raw  = fetch_all()
        jobs = apply_filters(raw)

        if new_only:
            from storage import filter_unseen, mark_seen
            jobs = filter_unseen(jobs)
            if jobs:
                mark_seen(jobs)

        if not jobs:
            send("✅ No new jobs found right now. Check back later!")
            return

        kind = "new (unseen)" if new_only else "latest"
        send(f"*🚀 {len(jobs)} {kind} Web3 {_role_label()} job{'s' if len(jobs) != 1 else ''}:*")
        send_jobs(jobs)
    except Exception as e:
        send(f"❌ Error fetching jobs: {e}")
        print(f"[bot] fetch error: {e}")
    finally:
        _fetch_lock.release()


def handle_jobs(new_only: bool = False) -> None:
    threading.Thread(target=_do_fetch_jobs, args=(new_only,), daemon=True).start()

# ---------------------------------------------------------------------------
# /clear
# ---------------------------------------------------------------------------

def handle_clear(user_msg_id: int) -> None:
    _sent_msg_ids.clear()
    deleted = 0
    for msg_id in range(1, user_msg_id + 1):
        try:
            r = httpx.post(f"{API}/deleteMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "message_id": msg_id}, timeout=5)
            if r.json().get("ok"):
                deleted += 1
        except Exception:
            pass
    print(f"[bot] /clear — deleted {deleted} messages")

# ---------------------------------------------------------------------------
# /twitter
# ---------------------------------------------------------------------------

def handle_twitter() -> None:
    import json, os
    from company_handles import HANDLES
    cache = os.path.join(os.path.dirname(__file__), "current_companies.json")
    if not os.path.exists(cache):
        d = os.environ.get("DATA_DIR", "")
        if d:
            cache = os.path.join(d, "current_companies.json")
    try:
        with open(cache) as f:
            companies = json.load(f)
    except Exception:
        companies = []

    links, seen = [], set()
    for company in companies:
        handle = HANDLES.get(company.lower().strip())
        if handle and handle not in seen:
            links.append(f"[{company}](https://x.com/{handle})")
            seen.add(handle)

    from notifier import _role_label
    if links:
        send(
            f"*Companies hiring for {_role_label()} on X:*\n"
            "_Tap any to view their profile_\n\n" + "\n".join(links)
        )
    else:
        send("No company data cached yet.\nSend /jobs to fetch listings first.")

# ---------------------------------------------------------------------------
# Command router
# ---------------------------------------------------------------------------

def handle_command(text: str, msg_id: int, user_id: str) -> None:
    cmd = text.strip().lower().split()[0]

    if cmd in ("/start", "/settings"):
        start_setup(user_id)

    elif cmd == "/jobs":
        handle_jobs(new_only=False)

    elif cmd == "/new":
        handle_jobs(new_only=True)

    elif cmd == "/clear":
        handle_clear(msg_id)

    elif cmd in ("/twitter", "/x"):
        handle_twitter()

    elif cmd == "/help":
        import prefs
        p = prefs.load()
        roles_str = ", ".join(
            next((lbl for rid, lbl in ALL_ROLES if rid == r), r.title())
            for r in p.get("roles", [])
        )
        loc = p.get("location_type", "remote")
        cities = p.get("preferred_locations", [])
        loc_str = {
            "remote":   "Remote only",
            "any":      "Anywhere",
            "specific": f"Specific — {', '.join(c.title() for c in cities)}",
        }.get(loc, loc)
        send(
            f"*Web3 Career Bot* 🤖\n\n"
            f"*Current settings:*\n"
            f"📋 Roles: {roles_str or '_(not set)_'}\n"
            f"📍 Location: {loc_str}\n\n"
            f"/jobs — fetch latest matching jobs\n"
            f"/new — show only jobs you haven't seen\n"
            f"/twitter — X profiles of companies hiring\n"
            f"/settings — change your role or location\n"
            f"/clear — delete all bot messages\n"
            f"/help — this message"
        )

    else:
        send("Unknown command. Try /jobs, /new, /settings, or /help.")

# ---------------------------------------------------------------------------
# Built-in 6-hour scheduler
# ---------------------------------------------------------------------------

def _scheduled_scrape() -> None:
    print("[scheduler] Starting scheduled scrape...")
    try:
        import scraper
        scraper.main()
        print("[scheduler] Done.")
    except Exception as e:
        print(f"[scheduler] ERROR: {e}")
        send(f"⚠️ Scheduled scrape failed: {e}")


def _run_scheduler() -> None:
    _scheduled_scrape()
    schedule.every(6).hours.do(_scheduled_scrape)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    print("[bot] Starting — listening for commands...")
    threading.Thread(target=_run_scheduler, daemon=True).start()

    offset = 0
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1

            # ── Button press ────────────────────────────────────────────────
            if "callback_query" in update:
                cq      = update["callback_query"]
                chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
                if chat_id == TELEGRAM_CHAT_ID:
                    handle_callback(cq)
                continue

            # ── Text message ────────────────────────────────────────────────
            message = update.get("message", {})
            text    = message.get("text", "")
            chat_id = str(message.get("chat", {}).get("id", ""))
            user_id = str(message.get("from", {}).get("id", ""))

            if chat_id != TELEGRAM_CHAT_ID:
                continue

            msg_id   = message.get("message_id", 0)
            msg_date = message.get("date", 0)

            if text.startswith("/"):
                if time.time() - msg_date > STALE_CMD_SECS:
                    print(f"[bot] Ignoring stale command: {text}")
                    continue
                print(f"[bot] Command: {text}")
                handle_command(text, msg_id=msg_id, user_id=user_id)
            else:
                # Plain text → check if user is entering cities
                handle_city_input(user_id, text)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[bot] Stopped.")
        sys.exit(0)
