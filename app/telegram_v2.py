"""
Telegram v2 integration for the Livestream Scheduler.

Features:
- Inline keyboard buttons (Confirm / Can't make it / Swap)
- Rich message formatting with role icons and person names
- Callback query handling for button presses
- Message editing on status changes
- Weekly reminders with per-person confirmation buttons
- Monthly schedule overview
- Swap request/accept workflow
- Test messages to personal chat ID
"""
import os
import uuid
import datetime
import hashlib
import html
import threading
import time
import requests
from itsdangerous import URLSafeSerializer
from flask import current_app
from .models import Event, Assignment, TeamMember, InteractionLog, SwapRequest, TempChat
from .extensions import db
from .utils import is_available, vancouver_today, vancouver_now, VANCOUVER_TZ
from . import telegram_temp_groups

# ── Configuration ────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
PERSONAL_CHAT_ID = os.environ.get("TELEGRAM_PERSONAL_CHAT_ID", "27859948")
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

BASE_API = "https://api.telegram.org/bot"
REMINDER_CUSTOM_EMOJI_ID = "5314354612357055779"
REMINDER_WIDTH_PAD_CHAR = "\u2800"
REMINDER_WIDTH_TARGET = 28
CONFIRM_CUSTOM_EMOJI_ID = "5447642621671386392"
DECLINE_CUSTOM_EMOJI_ID = "5474188341354180347"

TELEGRAM_PERSON_OVERRIDE = {}

# ── Emoji maps ───────────────────────────────────────────────────────
ROLE_EMOJI = {
    "Computer": "\U0001F5A5\uFE0F",
    "Camera 1": "\U0001F4F9",
    "Camera 2": "\U0001F4F9",
    "Camera": "\U0001F4F9",
    "Leader": "🎤",
    "Helper": "🙌",
}

# Friday Bible Study: first person gets computer icon, second gets hands icon
FRIDAY_ICONS = ["\U0001F5A5\uFE0F", "\U0001F4F9"]

# Short display names for roles
ROLE_SHORT = {
    "Computer": "PC",
    "Camera 1": "Cam 1",
    "Camera 2": "Cam 2",
    "Camera": "Cam",
    "Leader": "Leader",
    "Helper": "Helper",
}

STATUS_EMOJI = {
    "confirmed": "✅",
    "pending": "",
    "swap_needed": "🔴",
}

# ── Friendly action labels for notifications ─────────────────────
ACTION_LABELS = {
    "confirm": "✅ Confirmed",
    "decline": "🔴 Can't make it",
    "ack": "👍 Reminder acknowledged",
    "swap_accept": "🔄 Swap accepted",
    "swap_cover": "✅ Voluntary cover",
    "swap_decline": "👍 Swap declined",
    "undo": "↩️ Undo",
    "pickup": "👀 Picking up…",
    "pickup_as": "✅ Picked up shift",
    "cancel_pickup": "❌ Cancelled pickup",
    "expand": "👆 Tapped name",
    "collapse": "👆 Collapsed",
    "noop": "ℹ️ Info tap",
}


def _log_interaction(telegram_user_id, first_name, action, person_name,
                     assignment=None, event=None, details=None):
    """Log a Telegram interaction to the database."""
    log = InteractionLog(
        telegram_user_id=telegram_user_id,
        first_name=first_name,
        action=action,
        person_name=person_name,
        assignment_id=assignment.id if assignment else None,
        event_date=event.date if event else None,
        role=assignment.role if assignment else None,
        details=details,
    )
    db.session.add(log)
    # Don't commit here — let the caller's commit include this


def _notify_admin(action, person_name, assignment=None, event=None):
    """Send a short DM to the admin when someone interacts."""
    if not PERSONAL_CHAT_ID:
        return
    label = ACTION_LABELS.get(action, action)
    parts = [f"🔔 <b>{label}</b> - {person_name}"]
    if event:
        title = event.custom_title or event.day_type
        parts.append(f"📆 {title} · {event.date.strftime('%b %d')}")
    if assignment:
        icon = ROLE_EMOJI.get(assignment.role, "")
        parts.append(f"{icon} {assignment.role}")
    text = "\n".join(parts)
    # Fire-and-forget to personal chat
    try:
        _api_call("sendMessage", {
            "chat_id": PERSONAL_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": True,
        })
    except Exception as e:
        print(f"[Telegram] Admin notification failed: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Low-level Telegram API
# ═══════════════════════════════════════════════════════════════════

def _api_call(method, payload, timeout=10):
    """Make a Telegram Bot API call. Returns the result dict or None."""
    if not TELEGRAM_BOT_TOKEN:
        print("[Telegram v2] No bot token configured")
        return None
    url = f"{BASE_API}{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        data = resp.json()
        if data.get("ok"):
            return data.get("result")
        print(f"[Telegram v2] API error: {data.get('description', 'Unknown')}")
        return None
    except requests.RequestException as e:
        print(f"[Telegram v2] Request error: {e}")
        return None


def send_message(text, chat_id=None, reply_markup=None, parse_mode="HTML"):
    """
    Send a message. Returns message_id on success, None on failure.
    """
    payload = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    result = _api_call("sendMessage", payload)
    return result.get("message_id") if result else None


def edit_message(chat_id, message_id, text, reply_markup=None):
    """Edit an existing message. Returns True on success."""
    payload = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _api_call("editMessageText", payload) is not None


def edit_message_markup(chat_id, message_id, reply_markup):
    """Edit only the inline keyboard of a message."""
    payload = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "reply_markup": reply_markup,
    }
    return _api_call("editMessageReplyMarkup", payload) is not None


def answer_callback(callback_query_id, text="", show_alert=False):
    """Answer a callback query (button press acknowledgment)."""
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert,
    }
    return _api_call("answerCallbackQuery", payload) is not None


def delete_message(chat_id, message_id):
    """Delete a message."""
    payload = {"chat_id": chat_id or TELEGRAM_CHAT_ID, "message_id": message_id}
    return _api_call("deleteMessage", payload) is not None


def _site_url():
    try:
        return current_app.config.get("BASE_URL", "https://livestream.disterhoft.com")
    except RuntimeError:
        return "https://livestream.disterhoft.com"


def _schedule_url(person=None):
    url = _site_url().rstrip("/") + "/"
    if person:
        try:
            token = URLSafeSerializer(current_app.secret_key, salt="v2-auth").dumps({
                "name": person,
                "manager": person == "Florian",
            })
            url += f"?auth={token}"
        except RuntimeError:
            pass
    return url


def _schedule_button(label="\U0001F4C5 View Schedule", person=None):
    if person:
        return {"text": label, "url": _schedule_url(person)}
    if os.environ.get("TELEGRAM_LOGIN_URL_ENABLED", "").lower() not in ("1", "true", "yes", "on"):
        return {"text": label, "url": _schedule_url()}
    return {
        "text": label,
        "login_url": {
            "url": _schedule_url(),
            "request_write_access": False,
        },
    }


def _button(text, callback_data, **extra):
    button = {"text": text, "callback_data": callback_data}
    button.update({key: value for key, value in extra.items() if value})
    return button


def _weekly_schedule_buttons():
    return _make_inline_keyboard([
        [
            {"text": "✅ Confirm", "callback_data": "weekly_confirm"},
            {"text": "❌ Can't make it", "callback_data": "weekly_decline"},
        ],
        [_schedule_button("\U0001F4C5 View Schedule")],
    ])


# Map of preset custom_title -> emoji, mirroring the frontend EVENT_TYPES list.
EVENT_TYPE_EMOJI = {
    "sunday service":        "\u26EA",
    "bible study":           "\U0001F4D6",
    "communion service":     "\U0001F377",
    "members meeting":       "\U0001F465",
    "good friday":           "\u271D\uFE0F",
    "easter sunday":         "\U0001F305",
    "baptism":               "\U0001F4A6",
    "thanksgiving":          "\U0001F342",
    "baby dedication":       "\U0001F476",
    "christmas eve service": "\U0001F56F\uFE0F",
    "christmas service":     "\U0001F384",
}


def _event_emoji(event):
    title = (event.custom_title or "").strip().lower()
    if title and title in EVENT_TYPE_EMOJI:
        return EVENT_TYPE_EMOJI[title]
    if not title:
        if event.day_type == "Sunday":
            return EVENT_TYPE_EMOJI["sunday service"]
        if event.day_type == "Friday":
            return EVENT_TYPE_EMOJI["bible study"]
    # Unknown custom title -> no decoration
    return ""


def _event_title(event):
    if event.custom_title:
        title = event.custom_title
    elif event.day_type == "Friday":
        title = "Bible Study"
    elif event.day_type == "Sunday":
        title = "Sunday Service"
    else:
        title = event.day_type or "Event"
    emoji = _event_emoji(event)
    if emoji and emoji not in title:
        title = f"{title} {emoji}"
    return title


def _event_label_with_emoji(event, label):
    emoji = _event_emoji(event)
    if emoji and label and emoji not in label:
        return f"{label} {emoji}"
    return label


def _event_time(event):
    start_time = event.start_time
    if not start_time:
        start_time = datetime.time(14, 30) if _is_sunday_event(event) else datetime.time(19, 0)
    return start_time.strftime("%I:%M %p").lstrip("0")


def _date_line(date_obj):
    return date_obj.strftime("%A, %B %d, %Y").replace(" 0", " ")


def _short_date(date_obj):
    return date_obj.strftime("%B %d").replace(" 0", " ")


def _is_sunday_event(event):
    return event.day_type == "Sunday" or event.date.weekday() == 6


def _event_start_dt(event):
    start_time = event.start_time or (datetime.time(14, 30) if _is_sunday_event(event) else datetime.time(19, 0))
    return datetime.datetime.combine(event.date, start_time, tzinfo=VANCOUVER_TZ)


def _swap_deadline(event):
    if _is_sunday_event(event):
        return datetime.datetime.combine(event.date, datetime.time(hour=17), tzinfo=VANCOUVER_TZ)
    return _event_start_dt(event) + datetime.timedelta(hours=2)


def _role_icon(assignment, index=0):
    if assignment.event and assignment.event.day_type == "Friday":
        return FRIDAY_ICONS[index] if index < len(FRIDAY_ICONS) else ROLE_EMOJI.get(assignment.role, "👤")
    return ROLE_EMOJI.get(assignment.role, "👤")


def _worker_name(assignment):
    return assignment.cover or assignment.person


def _notify_admin_text(text):
    if not PERSONAL_CHAT_ID:
        return
    try:
        send_message(text, chat_id=PERSONAL_CHAT_ID)
    except Exception as e:
        print(f"[Telegram] Admin notification failed: {e}")


def _notify_name_tap(person_name, assignment, event):
    if not PERSONAL_CHAT_ID:
        return
    icon = ROLE_EMOJI.get(assignment.role, "👤")
    text = (
        f"👆 <b>Tapped name</b> - {html.escape(person_name or 'Unknown')}\n"
        f"📆 {html.escape(_event_title(event))} · {event.date.strftime('%b %d')}\n"
        f"{icon} {html.escape(assignment.person or '')} · {html.escape(assignment.role or '')}"
    )
    _notify_admin_text(text)


def _suggestion_url(suggestion_id):
    base = _site_url().rstrip("/") + "/"
    return f"{base}?suggest={suggestion_id}"


def send_suggestion_alert(suggestion):
    """DM the admin about a new event suggestion with an Open Request button."""
    if not PERSONAL_CHAT_ID:
        return None
    title = suggestion.custom_title or suggestion.event_type
    date_str = suggestion.date.strftime("%a, %b %d, %Y") if suggestion.date else "—"
    parts = [
        "💡 <b>New event suggestion</b>",
        f"From: <b>{suggestion.suggester_name}</b>",
        f"Type: {suggestion.event_type}",
    ]
    if suggestion.custom_title and suggestion.event_type == "Other":
        parts.append(f"Title: {suggestion.custom_title}")
    parts.append(f"📆 {date_str}")
    if suggestion.time:
        parts.append(f"🕒 {suggestion.time}")
    if suggestion.notes:
        parts.append(f"📝 {suggestion.notes}")

    text = "\n".join(parts)
    buttons = _make_inline_keyboard([
        [{"text": "🛠 Open Request", "url": _suggestion_url(suggestion.id)}],
    ])
    msg_id = send_message(text, chat_id=PERSONAL_CHAT_ID, reply_markup=buttons)
    if msg_id:
        suggestion.telegram_message_id = msg_id
        suggestion.telegram_chat_id = str(PERSONAL_CHAT_ID)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return msg_id


def _delete_temp_group_later(temp_chat_id, chat_id, delay=5, app=None):
    def _delete():
        time.sleep(delay)
        ok = False
        for attempt in range(3):
            ok = telegram_temp_groups.delete_group(chat_id)
            if ok:
                break
            time.sleep(5 * (attempt + 1))
        if app:
            with app.app_context():
                temp_chat = TempChat.query.get(temp_chat_id)
                if temp_chat:
                    temp_chat.status = "deleted" if ok else "delete_failed"
                    db.session.commit()
        if not ok:
            print(f"[TempGroup] Scheduled delete failed for {chat_id}")
    threading.Thread(target=_delete, daemon=True).start()


def _delete_temp_message_later(temp_chat_id, chat_id, message_id, delay=5, app=None):
    """Schedule deletion of a single message (not the whole group) after `delay` seconds.

    Used when a TempChat row's group is shared with sibling TempChats that should
    keep living (for example, two concurrent swap requests broadcast into the same
    helper chat).
    """
    def _delete():
        time.sleep(delay)
        try:
            delete_message(chat_id, message_id)
        except Exception as e:
            print(f"[TempChat] delayed delete message failed: {e}")
        if app:
            with app.app_context():
                temp_chat = TempChat.query.get(temp_chat_id)
                if temp_chat:
                    temp_chat.status = "deleted"
                    db.session.commit()
    threading.Thread(target=_delete, daemon=True).start()


def _has_active_siblings(temp_chat):
    """True if other active TempChat rows share this TempChat's chat_id."""
    if not temp_chat or not temp_chat.chat_id:
        return False
    return TempChat.query.filter(
        TempChat.chat_id == temp_chat.chat_id,
        TempChat.id != temp_chat.id,
        TempChat.status == "active",
    ).first() is not None


def _delete_temp_chat(temp_chat, delay=0):
    """Tear down (or merely deactivate) a TempChat.

    If the underlying Telegram group is shared with other active TempChats, only
    this row's message is removed (immediately or after `delay`); the group is
    preserved so siblings stay reachable.  Otherwise the whole group is deleted
    as before.
    """
    if not temp_chat or not temp_chat.chat_id:
        return False

    if _has_active_siblings(temp_chat):
        # Preserve the group for the other active swap/notice; only retire this
        # message + row.
        if delay and temp_chat.message_id:
            temp_chat.status = "deleting"
            db.session.commit()
            try:
                app = current_app._get_current_object()
            except RuntimeError:
                app = None
            _delete_temp_message_later(
                temp_chat.id, temp_chat.chat_id, temp_chat.message_id,
                delay=delay, app=app,
            )
            return True
        if temp_chat.message_id:
            try:
                delete_message(temp_chat.chat_id, temp_chat.message_id)
            except Exception as e:
                print(f"[TempChat] delete message failed: {e}")
        temp_chat.status = "deleted"
        db.session.commit()
        return True

    # No siblings: tear down the whole group as before.
    temp_chat.status = "deleting" if delay else temp_chat.status
    db.session.commit()
    if delay:
        try:
            app = current_app._get_current_object()
        except RuntimeError:
            app = None
        _delete_temp_group_later(temp_chat.id, temp_chat.chat_id, delay=delay, app=app)
        return True
    ok = telegram_temp_groups.delete_group(temp_chat.chat_id)
    temp_chat.status = "deleted" if ok else "delete_failed"
    db.session.commit()
    return ok


def _send_temp_group(kind, person, text, buttons, assignment=None, swap_request=None,
                     future_assignment=None, expires_at=None, title=None,
                     reuse_chat_id=None):
    """Send a message into a per-person temp group.

    If `reuse_chat_id` is provided, the message is sent into that existing chat
    and a new TempChat row is created pointing at the same chat_id.  Otherwise a
    fresh group is created via `telegram_temp_groups`.
    """
    print(f"[TempGroup] Suppressed temp chat for {person} ({kind}); using main chat flows only")
    return None

    if expires_at and expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(datetime.timezone.utc).replace(tzinfo=None)

    if reuse_chat_id:
        chat_id = str(reuse_chat_id)
        message_id = send_message(text, chat_id=chat_id, reply_markup=_make_inline_keyboard(buttons))
        if not message_id:
            return None
        temp_chat = TempChat(
            chat_id=chat_id,
            message_id=message_id,
            kind=kind,
            assignment_id=assignment.id if assignment else None,
            swap_request_id=swap_request.id if swap_request else None,
            future_assignment_id=future_assignment.id if future_assignment else None,
            person=assignment.person if assignment else None,
            recipient=person,
            expires_at=expires_at,
        )
        db.session.add(temp_chat)
        db.session.commit()
        return temp_chat

    member = TeamMember.query.filter_by(name=person).first()
    if not member or not member.telegram_user_id:
        _notify_admin_text(f"⚠️ No Telegram ID for {person}")
        return None
    if not telegram_temp_groups.is_available():
        _notify_admin_text(f"⚠️ Temp groups unavailable for {person}")
        return None
    group_title = title or f"🎬 Livestream {person}"
    chat_id = telegram_temp_groups.create_temp_group(
        group_title,
        [member.telegram_user_id],
        bot_token=TELEGRAM_BOT_TOKEN,
        bot_username=os.environ.get("TELEGRAM_BOT_USERNAME", ""),
    )
    if not chat_id:
        _notify_admin_text(f"⚠️ Could not create temp chat for {person}")
        return None
    message_id = send_message(text, chat_id=str(chat_id), reply_markup=_make_inline_keyboard(buttons))
    temp_chat = TempChat(
        chat_id=str(chat_id),
        message_id=message_id,
        kind=kind,
        assignment_id=assignment.id if assignment else None,
        swap_request_id=swap_request.id if swap_request else None,
        future_assignment_id=future_assignment.id if future_assignment else None,
        person=assignment.person if assignment else None,
        recipient=person,
        expires_at=expires_at,
    )
    db.session.add(temp_chat)
    db.session.commit()
    return temp_chat


# ═══════════════════════════════════════════════════════════════════
#  Inline Keyboard Builders
# ═══════════════════════════════════════════════════════════════════

def _make_inline_keyboard(buttons_rows):
    """
    Build an inline keyboard reply_markup.
    buttons_rows: list of rows, each row is a list of {text, callback_data} dicts.
    """
    return {"inline_keyboard": buttons_rows}


def _build_event_buttons(
    event,
    assignments=None,
    expanded_id=None,
    include_schedule_button=True,
    inline_assignments=False,
    compact_callbacks=False,
):
    """
    Build the collapsed/expand-on-click keyboard for an event.

    Default (collapsed) — one button per assignment, row = [🖥️ Andy]
      pending    → label: "🖥️ Andy"            (tap → expand to confirm/decline)
      confirmed  → label: "✅ Andy — Confirmed" (tap → expand to undo)
      swap_need  → label: "🔴 Andy NEEDS COVER" (tap → expand to undo/pickup)

    Expanded — the tapped row becomes an action menu:
      pending   : [✅ Confirm]  [❌ Can't make it]  [⬅️ Back]
      confirmed : [↩️ Undo]                          [⬅️ Back]
      swap_need : [🙋 I can cover] [↩️ Undo]        [⬅️ Back]

    By default, appends a schedule URL button at the bottom. Compact weekly
    schedule previews can opt into one-row assignment buttons and omit that URL
    because the header message carries it instead.
    """
    if assignments is None:
        assignments = event.assignments

    rows = []
    inline_row = []
    is_friday = event.day_type == "Friday"
    action_suffix = "_compact" if compact_callbacks else ""

    def callback(action, assignment_id):
        return f"{action}{action_suffix}:{assignment_id}"

    for i, a in enumerate(assignments):
        if a.person in ("TBD", "Select Helper"):
            continue

        worker = a.cover if a.cover else a.person

        if is_friday:
            role_icon = FRIDAY_ICONS[i] if i < len(FRIDAY_ICONS) else "🙌"
        else:
            role_icon = ROLE_EMOJI.get(a.role, "👤")

        # ── Expanded: show action buttons for the tapped row ──
        if expanded_id == a.id:
            if a.status == "confirmed":
                rows.append([
                    {"text": "↩️ Undo",  "callback_data": callback("undo", a.id)},
                    {"text": "⬅️ Back",  "callback_data": callback("collapse", a.id)},
                ])
            elif a.status == "swap_needed":
                rows.append([
                    {"text": "🙋 I can cover", "callback_data": callback("pickup", a.id)},
                ])
                rows.append([
                    {"text": "↩️ Undo",  "callback_data": callback("undo", a.id)},
                    {"text": "⬅️ Back",  "callback_data": callback("collapse", a.id)},
                ])
            else:  # pending
                rows.append([
                    {"text": "✅ Yes, I'll be there",  "callback_data": callback("confirm", a.id)},
                ])
                rows.append([
                    {"text": "❌ Can't make it",       "callback_data": callback("decline", a.id)},
                    {"text": "⬅️ Back",                "callback_data": callback("collapse", a.id)},
                ])
            continue

        # ── Collapsed: single-button row showing status + name ──
        if a.status == "confirmed":
            label = f"✅ {worker} - Confirmed"
        elif a.status == "swap_needed":
            label = f"🔴 {worker} NEEDS COVERAGE"
        else:
            label = f"{role_icon} {worker}"

        button = {"text": label, "callback_data": callback("expand", a.id)}
        if inline_assignments:
            inline_row.append(button)
        else:
            rows.append([button])

    if inline_row:
        rows.append(inline_row)

    if include_schedule_button:
        rows.append([
            _schedule_button("\U0001F4C5 Show Schedule")
        ])

    return _make_inline_keyboard(rows) if rows else None


def _build_pickup_buttons(assignment):
    """Build buttons for shift pickup — list all team members who could cover."""
    from .utils import ROLES_CONFIG, ALL_NAMES
    rows = []
    assigned_here = {
        a.cover or a.person
        for a in assignment.event.assignments
        if a.id != assignment.id
    }
    for name in ALL_NAMES:
        if name in ("TBD", "Select Helper") or name == assignment.person or name in assigned_here:
            continue
        rows.append([{
            "text": f"🙋 {name}",
            "callback_data": f"pickup_as:{assignment.id}:{name}",
        }])
    rows.append([{"text": "❌ Cancel", "callback_data": f"cancel_pickup:{assignment.id}"}])
    return _make_inline_keyboard(rows)


# ═══════════════════════════════════════════════════════════════════
#  Message Formatting
# ═══════════════════════════════════════════════════════════════════

def format_event_message(event, header=None):
    """Format a short header for the reminder.

    Example:
        🙏 Today is your turn on the livestream team!
        📅 Sunday Service — April 19, 2026

        Tap your name to confirm or let us know if you can't make it.
    """
    title = event.custom_title
    if not title:
        if event.day_type == "Friday":
            title = "Bible Study"
        elif event.day_type == "Sunday":
            title = "Sunday Service"
        else:
            title = "Event"

    date_str = event.date.strftime("%A, %B %d, %Y")
    lines = [
        "🙏 <b>Today is your turn on the livestream team!</b>",
        f"📅 {title} - {date_str}",
        "",
        "<i>Tap your name to confirm or let us know if you can't make it.</i>",
    ]
    return "\n".join(lines)


def format_today_group_post(event):
    title = _event_title_without_emoji(event)
    event_line = _reminder_event_line(title, _event_time(event))
    if getattr(event, "cancelled", False):
        return "\n".join([
            f"{_reminder_icon()} <b>Reminder</b>",
            event_line,
            "✅ <i>No livestream needed</i>",
        ])

    lines = [
        f"{_reminder_icon()} <b>Reminder</b>",
        event_line,
        "",
    ]
    for i, assignment in enumerate(event.assignments):
        lines.append(_assignment_line(assignment, i))
    return "\n".join(lines)


def _reminder_event_line(title, event_time):
    line = f"{title} @ {event_time}"
    pad = max(0, REMINDER_WIDTH_TARGET - len(line))
    return f"{line}{REMINDER_WIDTH_PAD_CHAR * pad}"


def _event_reminder_buttons(event):
    return _make_inline_keyboard([
        [
            {"text": "✅ Confirm", "callback_data": f"event_confirm:{event.id}"},
            {"text": "❌ Can't make it", "callback_data": f"event_decline:{event.id}"},
        ],
        [_schedule_button("\U0001F4C5 View Schedule")],
    ])


def format_interactive_event_post(event):
    lines = [
        f"<b>{_event_title(event)}</b>",
        f"{_date_line(event.date)} @ {_event_time(event)}",
    ]
    if getattr(event, "cancelled", False):
        lines.append("✅ <i>No livestream needed</i>")
    return "\n".join(lines)


def format_personal_question(assignment):
    event = assignment.event
    title = _event_title(event)
    icon = ROLE_EMOJI.get(assignment.role, "👤")
    return (
        f"👋 Hi {assignment.person},\n\n"
        f"You are scheduled for livestream today.\n\n"
        f"📅 <b>{title}</b>\n"
        f"🗓 {_date_line(event.date)}\n"
        f"🕖 {_event_time(event)}\n\n"
        f"{icon} <b>Role:</b> {assignment.role}\n\n"
        f"Can you make it?"
    )


def format_weekday_ack_reminder(event, assignment):
    pending = assignment.status == "pending"
    lines = ["🔔 <b>Just a quick reminder</b>", ""]
    if pending:
        lines.extend(["We didn't get a response from you yet.", "", "You are scheduled tonight:", ""])
    for i, a in enumerate(event.assignments):
        lines.append(f"{_role_icon(a, i)} {a.role} - {_worker_name(a)}")
    lines.extend(["", "See you tonight!"])
    return "\n".join(lines)


def _personal_question_buttons(assignment):
    return [
        [{"text": "✅ Yes, I'll be there", "callback_data": f"personal_confirm:{assignment.id}"}],
        [{"text": "❌ I can't make it", "callback_data": f"personal_decline:{assignment.id}"}],
        [_schedule_button("\U0001F4C5 Open Schedule", person=assignment.person)],
    ]


def _ack_buttons(assignment):
    return [[{"text": "👍 Sounds good", "callback_data": f"weekday_ack:{assignment.id}"}]]


# ═══════════════════════════════════════════════════════════════════
#  Deadline helpers
# ═══════════════════════════════════════════════════════════════════

def compute_pickup_deadline(event_date, day_type):
    """Return the Vancouver datetime by which an open shift must be picked up.

    - Sunday events    → 16:00 Vancouver
    - All other events → 21:00 Vancouver
    """
    if day_type == "Sunday" or event_date.weekday() == 6:
        hour = 16
    else:
        hour = 21
    return datetime.datetime.combine(
        event_date, datetime.time(hour=hour, minute=0), tzinfo=VANCOUVER_TZ
    )


def format_monthly_schedule(year, month):
    """Format a monthly schedule overview message."""
    import calendar as cal
    month_name = cal.month_name[month]
    start = datetime.date(year, month, 1)
    _, num_days = cal.monthrange(year, month)
    end = datetime.date(year, month, num_days)

    events = Event.query.filter(
        Event.date >= start, Event.date <= end
    ).order_by(Event.date).all()

    lines = [f"📅 <b>{month_name} {year} - Livestream Schedule</b>", ""]

    if not events:
        lines.append("<i>No events scheduled yet.</i>")
        return "\n".join(lines)

    for event in events:
        title = event.custom_title or ("Bible Study" if event.day_type == "Friday" else "Sunday Service")
        day = event.date.strftime("%a %d")
        lines.append(f"<b>{day}</b> - {title}")

        if getattr(event, "cancelled", False):
            lines.append("  ✅ <i>No livestream needed</i>")
            lines.append("")
            continue

        is_friday = event.day_type == "Friday"
        for i, a in enumerate(event.assignments):
            worker = a.cover if a.cover else a.person

            if is_friday:
                icon = FRIDAY_ICONS[i] if i < len(FRIDAY_ICONS) else "🙌"
                if a.status == "swap_needed":
                    lines.append(f"  {icon} <b>NEEDED</b>")
                elif worker in ("TBD", "Select Helper"):
                    lines.append(f"  {icon} <i>TBD</i>")
                else:
                    confirmed = "✅ " if a.status == "confirmed" else ""
                    lines.append(f"  {icon} {confirmed}{worker}")
            else:
                role_icon = ROLE_EMOJI.get(a.role, "👤")
                if a.status == "swap_needed":
                    lines.append(f"  {role_icon} <b>NEEDED</b>")
                elif worker in ("TBD", "Select Helper"):
                    lines.append(f"  {role_icon} <i>TBD</i>")
                else:
                    confirmed = "✅ " if a.status == "confirmed" else ""
                    lines.append(f"  {role_icon} {confirmed}{worker}")
        lines.append("")

    return "\n".join(lines)


def _weekly_schedule_anchor(today=None):
    today = today or vancouver_today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    monday_events = Event.query.filter(Event.date == monday).all()
    has_monday_event = bool(monday_events)
    send_date = monday if has_monday_event else monday + datetime.timedelta(days=1)
    return monday, sunday, send_date


def _weekly_schedule_events(today=None):
    monday, sunday, _send_date = _weekly_schedule_anchor(today)
    events = Event.query.filter(
        Event.date >= monday,
        Event.date <= sunday,
    ).order_by(Event.date).all()
    # Slot by weekday so a custom-typed event (e.g. Communion on Friday) still
    # lands in the Friday slot instead of falling through to "extras".
    friday = next((e for e in events if e.date.weekday() == 4), None)
    sunday_event = next((e for e in events if e.date.weekday() == 6), None)
    extras = [e for e in events if e is not friday and e is not sunday_event]
    return monday, sunday, friday, sunday_event, extras


def _custom_emoji_html(emoji_id, fallback):
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def _weekly_confirm_status_icon():
    return _custom_emoji_html(CONFIRM_CUSTOM_EMOJI_ID, "✅")


def _weekly_decline_status_icon():
    return _custom_emoji_html(DECLINE_CUSTOM_EMOJI_ID, "🔴")


def _reminder_icon():
    return _custom_emoji_html(REMINDER_CUSTOM_EMOJI_ID, "🔔")


def _assignment_line(assignment, index=0):
    worker = _worker_name(assignment)
    if not worker or worker in ("TBD", "Select Helper"):
        worker = "TBD"
    icon = _role_icon(assignment, index)
    if assignment.status == "swap_needed":
        return f"{icon} {worker} {_weekly_decline_status_icon()}"
    if assignment.cover:
        status = f" {_weekly_confirm_status_icon()}" if assignment.status == "confirmed" else ""
        return f"{icon} <s>{assignment.person}</s> → {assignment.cover}{status}"
    if assignment.status == "confirmed":
        return f"{icon} {worker} {_weekly_confirm_status_icon()}"
    return f"{icon} {worker}"


def _weekly_event_block(event, default_header=None, default_time=None, missing_label=None, default_day_type=None):
    """Render one event's block in the weekly schedule.

    Header rule: when an event has a custom title (e.g. 'Communion'), use it
    on its own. When the title is the default ('Bible Study' / 'Sunday Service'),
    fall back to the caller-provided default_header (e.g. 'Friday - Bible Study').
    """
    if not event:
        if default_header is None:
            return []
        return [
            f"<b>{default_header}</b>",
            f"<i>{missing_label or 'Not scheduled.'}</i>",
        ]

    title = _event_title(event)
    if event.custom_title or (default_day_type and event.day_type != default_day_type):
        header = f"<b>{title}</b>"
    elif default_header:
        header = f"<b>{_event_label_with_emoji(event, default_header)}</b>"
    else:
        header = f"<b>{title}</b>"

    lines = [
        header,
        f"{_short_date(event.date)} @ {_event_time(event)}",
    ]
    if getattr(event, "cancelled", False):
        lines.append("✅ <i>No livestream needed</i>")
    else:
        for index, assignment in enumerate(event.assignments):
            lines.append(_assignment_line(assignment, index))
    return lines


def format_weekly_schedule(today=None):
    monday, _sunday, friday, sunday_event, extras = _weekly_schedule_events(today)
    lines = ["\U0001F4C5 <b>Livestream schedule this week</b>", ""]

    for event in extras:
        lines.extend(_weekly_event_block(event))
        lines.append("")

    lines.extend(_weekly_event_block(
        friday,
        default_header="Bible Study",
        default_time="7:00 PM",
        missing_label="No Bible Study scheduled.",
        default_day_type="Friday",
    ))
    lines.append("")

    lines.extend(_weekly_event_block(
        sunday_event,
        default_header="Sunday Service",
        default_time="2:30 PM",
        missing_label="No Sunday Service scheduled.",
        default_day_type="Sunday",
    ))

    return "\n".join(lines).strip()


def _weekly_assignments_for_person(person_name, today=None):
    if not person_name:
        return []
    monday, sunday, _send_date = _weekly_schedule_anchor(today)
    rows = []
    events = Event.query.filter(
        Event.date >= monday,
        Event.date <= sunday,
    ).order_by(Event.date).all()
    for event in events:
        if getattr(event, "cancelled", False):
            continue
        for index, assignment in enumerate(event.assignments):
            worker = _worker_name(assignment)
            if worker == person_name and worker not in ("TBD", "Select Helper"):
                rows.append((event, assignment, index))
    return rows


def _weekly_assignment_label(event, assignment, index, prefix=""):
    icon = _role_icon(assignment, index)
    role = ROLE_SHORT.get(assignment.role, assignment.role)
    return f"{prefix}{icon} {_short_date(event.date)} · {role}"


def _weekly_need_cover_label(event, assignment, index):
    icon = _role_icon(assignment, index)
    title = _event_title_without_emoji(event)
    return f"{title} · {icon} {assignment.person}"


def _event_title_without_emoji(event):
    title = _event_title(event)
    event_emoji = _event_emoji(event)
    if event_emoji:
        title = title.replace(f" {event_emoji}", "").strip()
    return title


def _restore_weekly_message(chat_id, message_id, today=None):
    text = format_weekly_schedule(today=today or vancouver_today())
    return edit_message(chat_id, message_id, text, reply_markup=_weekly_schedule_buttons())


def _weekly_select_shift(callback_id, chat_id, message_id, person_name, mode,
                         telegram_user_id=None, first_name=None, today=None):
    rows = _weekly_assignments_for_person(person_name, today=today)
    if not rows:
        answer_callback(callback_id, "I don't see you scheduled this week.", show_alert=True)
        return True
    if len(rows) == 1:
        event, assignment, _index = rows[0]
        if mode == "confirm":
            return _weekly_confirm_assignment(
                callback_id, chat_id, message_id, assignment, person_name,
                telegram_user_id=telegram_user_id, first_name=first_name,
            )
        return _weekly_show_decline_confirmation(callback_id, chat_id, message_id, assignment)

    button_rows = []
    for event, assignment, index in rows:
        action = "weekly_confirm_shift" if mode == "confirm" else "weekly_decline_shift"
        if mode == "confirm":
            button_rows.append([_button(
                _weekly_assignment_label(event, assignment, index, prefix="✅ "),
                f"{action}:{assignment.id}",
            )])
        else:
            button_rows.append([_button(
                _weekly_assignment_label(event, assignment, index, prefix="❌ "),
                f"{action}:{assignment.id}",
            )])
    button_rows.append([{"text": "Never mind", "callback_data": "weekly_back"}])
    edit_message_markup(chat_id, message_id, _make_inline_keyboard(button_rows))
    answer_callback(callback_id)
    return True


def _weekly_show_decline_confirmation(callback_id, chat_id, message_id, assignment):
    event = assignment.event
    try:
        index = list(event.assignments).index(assignment)
    except ValueError:
        index = 0
    label = _weekly_need_cover_label(event, assignment, index)
    buttons = _make_inline_keyboard([
        [{"text": f"❌ {label}", "callback_data": f"weekly_decline_yes:{assignment.id}"}],
        [{"text": "Never mind", "callback_data": "weekly_back"}],
    ])
    edit_message_markup(chat_id, message_id, buttons)
    answer_callback(callback_id)
    return True


def _weekly_confirm_assignment(callback_id, chat_id, message_id, assignment, person_name,
                               telegram_user_id=None, first_name=None):
    event = assignment.event
    if assignment.status == "confirmed":
        assignment.status = "pending"
        h = assignment.history
        h.append({"action": "undo", "by": person_name, "via": "weekly_telegram", "ts": str(vancouver_now())})
        assignment.history = h
        _log_interaction(telegram_user_id, first_name, "undo", person_name, assignment, event, details="weekly_button")
        db.session.commit()
        _notify_admin("undo", person_name, assignment, event)
        answer_callback(callback_id)
        _restore_weekly_message(chat_id, message_id, today=event.date)
        refresh_event_telegram(event)
        return True
    assignment.status = "confirmed"
    h = assignment.history
    h.append({"action": "confirm", "by": person_name, "via": "weekly_telegram", "ts": str(vancouver_now())})
    assignment.history = h
    _log_interaction(telegram_user_id, first_name, "confirm", person_name, assignment, event, details="weekly_button")
    db.session.commit()
    _notify_admin("confirm", person_name, assignment, event)
    answer_callback(callback_id)
    _restore_weekly_message(chat_id, message_id, today=event.date)
    refresh_event_telegram(event)
    return True


def _weekly_decline_assignment(callback_id, chat_id, message_id, assignment, person_name,
                               telegram_user_id=None, first_name=None):
    event = assignment.event
    assignment.status = "swap_needed"
    h = assignment.history
    h.append({"action": "decline", "by": person_name, "via": "weekly_telegram", "ts": str(vancouver_now())})
    assignment.history = h
    deadline_local = compute_pickup_deadline(event.date, event.day_type)
    deadline_utc = deadline_local.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    swap = SwapRequest.query.filter_by(assignment_id=assignment.id, status="active").first()
    if not swap:
        swap = SwapRequest(
            assignment_id=assignment.id,
            requestor=person_name,
            event_date=event.date,
            role=assignment.role,
            expires_at=deadline_utc,
            status="active",
        )
        db.session.add(swap)
    _log_interaction(telegram_user_id, first_name, "decline", person_name, assignment, event, details="weekly_button")
    db.session.commit()
    answer_callback(callback_id)
    _restore_weekly_message(chat_id, message_id, today=event.date)
    refresh_event_telegram(event)
    send_swap_needed(event, assignment, chat_id=chat_id, source_message_id=message_id)
    return True


def _restore_event_reminder_message(chat_id, message_id, event):
    return edit_message(chat_id, message_id, format_today_group_post(event), reply_markup=_event_reminder_buttons(event))


def _event_assignments_for_person(event, person_name):
    if not event or not person_name:
        return []
    rows = []
    for index, assignment in enumerate(event.assignments):
        worker = _worker_name(assignment)
        if worker == person_name and worker not in ("TBD", "Select Helper"):
            rows.append((assignment, index))
    return rows


def _event_confirm_assignment(callback_id, chat_id, message_id, assignment, person_name,
                              telegram_user_id=None, first_name=None):
    event = assignment.event
    if assignment.status == "confirmed":
        assignment.status = "pending"
        action = "undo"
    else:
        assignment.status = "confirmed"
        action = "confirm"
    h = assignment.history
    h.append({"action": action, "by": person_name, "via": "event_reminder", "ts": str(vancouver_now())})
    assignment.history = h
    _log_interaction(telegram_user_id, first_name, action, person_name, assignment, event, details="event_reminder")
    db.session.commit()
    answer_callback(callback_id)
    _restore_event_reminder_message(chat_id, message_id, event)
    update_weekly_schedule_for_event(event)
    return True


def _event_show_decline_confirmation(callback_id, chat_id, message_id, assignment):
    event = assignment.event
    try:
        index = list(event.assignments).index(assignment)
    except ValueError:
        index = 0
    label = _weekly_need_cover_label(event, assignment, index)
    buttons = _make_inline_keyboard([
        [{"text": f"❌ {label}", "callback_data": f"event_decline_yes:{assignment.id}:{message_id}"}],
        [{"text": "Never mind", "callback_data": f"event_back:{event.id}"}],
    ])
    edit_message_markup(chat_id, message_id, buttons)
    answer_callback(callback_id)
    return True


def _event_decline_assignment(callback_id, chat_id, message_id, assignment, person_name,
                              telegram_user_id=None, first_name=None, source_message_id=None):
    event = assignment.event
    assignment.status = "swap_needed"
    h = assignment.history
    h.append({"action": "decline", "by": person_name, "via": "event_reminder", "ts": str(vancouver_now())})
    assignment.history = h
    deadline_local = compute_pickup_deadline(event.date, event.day_type)
    deadline_utc = deadline_local.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    swap = SwapRequest.query.filter_by(assignment_id=assignment.id, status="active").first()
    if not swap:
        swap = SwapRequest(
            assignment_id=assignment.id,
            requestor=person_name,
            event_date=event.date,
            role=assignment.role,
            expires_at=deadline_utc,
            status="active",
        )
        db.session.add(swap)
    _log_interaction(telegram_user_id, first_name, "decline", person_name, assignment, event, details="event_reminder")
    db.session.commit()
    answer_callback(callback_id)
    _restore_event_reminder_message(chat_id, source_message_id or message_id, event)
    update_weekly_schedule_for_event(event)
    send_swap_needed(event, assignment, chat_id=chat_id, source_message_id=source_message_id or message_id)
    return True


def _event_undo_decline_assignment(callback_id, chat_id, message_id, assignment, person_name,
                                   telegram_user_id=None, first_name=None):
    event = assignment.event
    active_swap = SwapRequest.query.filter_by(
        assignment_id=assignment.id, status="active"
    ).first()
    cover_chat_id = None
    cover_message_id = assignment.telegram_message_id
    if active_swap:
        active_swap.status = "cancelled"
        cover_chat_id = active_swap.telegram_chat_id
        cover_message_id = active_swap.telegram_message_id or cover_message_id

    assignment.status = "pending"
    assignment.cover = None
    h = assignment.history
    h.append({"action": "undo_decline", "by": person_name, "via": "event_reminder", "ts": str(vancouver_now())})
    assignment.history = h
    assignment.telegram_message_id = None
    _log_interaction(telegram_user_id, first_name, "undo_decline", person_name, assignment, event,
                     details="event_reminder")
    db.session.commit()

    answer_callback(callback_id)
    if cover_message_id:
        delete_message(cover_chat_id or chat_id, cover_message_id)
    _restore_event_reminder_message(chat_id, message_id, event)
    update_weekly_schedule_for_event(event)
    return True


def _event_select_shift(callback_id, chat_id, message_id, event, person_name, mode,
                        telegram_user_id=None, first_name=None):
    rows = _event_assignments_for_person(event, person_name)
    if not rows:
        answer_callback(callback_id, "I don't see you scheduled for this event.", show_alert=True)
        return True
    if len(rows) == 1:
        assignment, _index = rows[0]
        if mode == "confirm":
            return _event_confirm_assignment(
                callback_id, chat_id, message_id, assignment, person_name,
                telegram_user_id=telegram_user_id, first_name=first_name,
            )
        if _can_undo_event_decline(assignment, person_name):
            return _event_undo_decline_assignment(
                callback_id, chat_id, message_id, assignment, person_name,
                telegram_user_id=telegram_user_id, first_name=first_name,
            )
        return _event_show_decline_confirmation(callback_id, chat_id, message_id, assignment)

    button_rows = []
    for assignment, index in rows:
        action = "event_confirm_shift" if mode == "confirm" else "event_decline_shift"
        prefix = "✅ " if mode == "confirm" else "❌ "
        button_rows.append([{
            "text": _weekly_assignment_label(event, assignment, index, prefix=prefix),
            "callback_data": f"{action}:{assignment.id}:{message_id}",
        }])
    button_rows.append([{"text": "Never mind", "callback_data": f"event_back:{event.id}"}])
    edit_message_markup(chat_id, message_id, _make_inline_keyboard(button_rows))
    answer_callback(callback_id)
    return True


def _can_undo_event_decline(assignment, person_name):
    if not assignment or assignment.status != "swap_needed":
        return False
    active_swap = SwapRequest.query.filter_by(
        assignment_id=assignment.id, status="active"
    ).first()
    if active_swap:
        return active_swap.requestor == person_name
    return assignment.person == person_name


def _weekly_schedule_already_sent(monday):
    key = f"weekly_schedule:{monday.isoformat()}"
    return InteractionLog.query.filter(
        InteractionLog.action == "weekly_schedule_sent",
        InteractionLog.event_date == monday,
        InteractionLog.details.like(f"{key}%"),
    ).first() is not None


def send_weekly_schedule(chat_id=None, force=False, today=None):
    today = today or vancouver_today()
    monday, sunday, send_date = _weekly_schedule_anchor(today)
    if not force and today != send_date:
        return 0
    if _weekly_schedule_already_sent(monday):
        return 0
    events_exist = Event.query.filter(Event.date >= monday, Event.date <= sunday).first() is not None
    if not events_exist:
        return 0

    text = format_weekly_schedule(today)
    buttons = _weekly_schedule_buttons()
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    msg_id = send_message(text, chat_id=target_chat_id, reply_markup=buttons)
    if not msg_id:
        return 0

    db.session.add(InteractionLog(
        action="weekly_schedule_sent",
        person_name="group",
        event_date=monday,
        details=f"weekly_schedule:{monday.isoformat()}|chat_id={target_chat_id}|message_id={msg_id}",
    ))
    db.session.commit()
    return 1


# ═══════════════════════════════════════════════════════════════════
#  High-level Actions
# ═══════════════════════════════════════════════════════════════════

def send_event_reminder(event, chat_id=None):
    """Send the public 9 AM event post to the group chat."""
    text = format_today_group_post(event)
    buttons = _event_reminder_buttons(event)
    target = chat_id or TELEGRAM_CHAT_ID
    msg_id = send_message(text, chat_id=target, reply_markup=buttons)

    if msg_id:
        event.telegram_message_id = msg_id
        event.telegram_chat_id = str(target)
        db.session.commit()

    return msg_id


def update_event_reminder(event):
    """Re-render and edit the previously sent group reminder for this event.

    No-op if the event has no recorded message id (none was ever sent).
    Bots can edit their own messages without a time limit, so this works
    regardless of how old the original post is. Returns True on success.
    """
    if not event or not event.telegram_message_id or not event.telegram_chat_id:
        return False
    text = format_today_group_post(event)
    buttons = _event_reminder_buttons(event)
    return bool(edit_message(event.telegram_chat_id, event.telegram_message_id, text, reply_markup=buttons))


def delete_past_event_reminders(today=None):
    """Delete stored event reminder messages for events before today."""
    today = today or vancouver_today()
    events = Event.query.filter(
        Event.date < today,
        Event.telegram_message_id.isnot(None),
        Event.telegram_chat_id.isnot(None),
    ).all()

    deleted = 0
    for event in events:
        if delete_message(event.telegram_chat_id, event.telegram_message_id):
            deleted += 1
        event.telegram_message_id = None
        event.telegram_chat_id = None
    if events:
        db.session.commit()
        print(f"[cleanup] Cleared {len(events)} past event reminder(s); deleted {deleted} Telegram message(s)")
    return deleted


def _parse_weekly_schedule_log(log):
    """Pull (chat_id, message_id) out of an InteractionLog details string."""
    if not log or not log.details:
        return None, None
    parts = {}
    for chunk in log.details.split("|"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            parts[k.strip()] = v.strip()
    chat_id = parts.get("chat_id")
    try:
        msg_id = int(parts["message_id"]) if parts.get("message_id") else None
    except (TypeError, ValueError):
        msg_id = None
    return chat_id, msg_id


def update_weekly_schedule_for_date(date_obj):
    """Re-render and edit the weekly schedule message that contains this date.

    No-op if no weekly schedule was sent for this date's week. Logged for
    debug.
    """
    if not date_obj:
        return False
    monday = date_obj - datetime.timedelta(days=date_obj.weekday())
    key = f"weekly_schedule:{monday.isoformat()}"
    log = (
        InteractionLog.query
        .filter(
            InteractionLog.action == "weekly_schedule_sent",
            InteractionLog.event_date == monday,
            InteractionLog.details.like(f"{key}%"),
        )
        .order_by(InteractionLog.id.desc())
        .first()
    )
    chat_id, msg_id = _parse_weekly_schedule_log(log)
    if not chat_id or not msg_id:
        return False
    text = format_weekly_schedule(today=monday)
    buttons = _weekly_schedule_buttons()
    return bool(edit_message(chat_id, msg_id, text, reply_markup=buttons))


def update_weekly_schedule_for_event(event):
    """Re-render and edit the weekly schedule message that contains this event."""
    if not event:
        return False
    return update_weekly_schedule_for_date(event.date)


def _send_or_refresh_group_event_reminder(event, chat_id=None):
    """Keep reminders in the main chat instead of creating per-person chats."""
    if not event or getattr(event, "cancelled", False):
        return False
    if chat_id:
        return bool(send_event_reminder(event, chat_id=chat_id))
    if event.telegram_chat_id and event.telegram_message_id:
        return update_event_reminder(event)
    return bool(send_event_reminder(event))


def send_personal_question_temp_group(assignment):
    return _send_or_refresh_group_event_reminder(assignment.event)


def send_weekday_ack_temp_group(assignment):
    return _send_or_refresh_group_event_reminder(assignment.event)


def send_monthly_schedule(year=None, month=None, chat_id=None):
    """Send the monthly schedule overview."""
    today = vancouver_today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    text = format_monthly_schedule(year, month)
    return send_message(text, chat_id=chat_id)


def send_swap_needed(event, assignment, chat_id=None, pickup_url=None, source_message_id=None):
    """
    Send alert when someone marks they can't make it.
    Includes inline buttons for other team members to pick up the shift.
    Returns message_id on success.
    """
    role_icon = ROLE_EMOJI.get(assignment.role, "👤")
    title = _event_title_without_emoji(event)

    text = (
        f"{_weekly_decline_status_icon()} {assignment.person} can't make it to his shift:\n"
        f"<b>{title} - {_short_date(event.date)} ({role_icon} {assignment.role})</b>\n\n"
        "Could someone please jump in for him?"
    )

    if pickup_url:
        text += f'\n🔗 <a href="{pickup_url}">Pick up via web</a>'

    callback_data = f"pickup:{assignment.id}"
    if source_message_id:
        callback_data = f"{callback_data}:{source_message_id}"
    buttons = _make_inline_keyboard([[
        {"text": "👋 I can", "callback_data": callback_data},
    ]])
    msg_id = send_message(text, chat_id=chat_id, reply_markup=buttons)

    if msg_id:
        assignment.telegram_message_id = msg_id
        active_swap = SwapRequest.query.filter_by(
            assignment_id=assignment.id, status="active"
        ).first()
        if active_swap:
            active_swap.telegram_message_id = msg_id
            active_swap.telegram_chat_id = str(chat_id or TELEGRAM_CHAT_ID)
        db.session.commit()

    return msg_id


def send_shift_covered(event, assignment, helper_name, original_msg_id=None, chat_id=None):
    """Send confirmation when someone covers a shift."""
    title = event.custom_title or event.day_type
    date_str = event.date.strftime("%B %d")
    role_icon = ROLE_EMOJI.get(assignment.role, "👤")

    text = (
        f"✅ <b>Shift Covered!</b>\n\n"
        f"{helper_name} will cover:\n"
        f"📆 <b>{title}</b> - {date_str}\n"
        f"{role_icon} <b>{assignment.role}</b>\n\n"
        f"Thank you {helper_name}! 🎉"
    )

    target = chat_id or TELEGRAM_CHAT_ID
    if original_msg_id:
        if edit_message(target, original_msg_id, text):
            return original_msg_id
        delete_message(target, original_msg_id)

    return send_message(text, chat_id=target)


def _compact_date(date_obj):
    return f"{date_obj.strftime('%a %b')} {date_obj.day}"


def _find_future_swap_assignment(person, role, day_type, after_date, original_person=None):
    assignments = (
        Assignment.query.join(Event)
        .filter(
            Event.date > after_date,
            Event.day_type == day_type,
            Assignment.person == person,
            Assignment.status.in_(["pending", "confirmed"]),
            Assignment.cover.is_(None),
        )
        .order_by(Event.date)
        .all()
    )
    safe = []
    for assignment in assignments:
        if original_person and any((a.cover or a.person) == original_person for a in assignment.event.assignments):
            continue
        safe.append(assignment)
    safe.sort(key=lambda a: (0 if a.role == role else 1, a.event.date))
    return safe[0] if safe else None


def _eligible_swap_members(assignment):
    event = assignment.event
    day_type = event.day_type
    members = TeamMember.query.filter_by(active=True).all()
    eligible = []
    for member in members:
        if member.name == assignment.person or not member.telegram_user_id:
            continue
        if not is_available(member.name, event.date):
            continue
        if any((a.cover or a.person) == member.name for a in event.assignments):
            continue
        future = _find_future_swap_assignment(member.name, assignment.role, day_type, event.date, assignment.person)
        eligible.append((member.name, future))
    return eligible


def _format_swap_request(assignment, recipient, future_assignment):
    event = assignment.event
    title = _event_title(event)
    icon = ROLE_EMOJI.get(assignment.role, "👤")
    return (
        f"🔄 <b>Coverage needed</b>\n\n"
        f"{assignment.person} can’t make his livestream shift.\n\n"
        f"{title} - {_compact_date(event.date)}\n"
        f"{icon} Role: {assignment.role}"
    )


def _swap_buttons(swap_request, recipient, future_assignment):
    buttons = [
        [{"text": "✅ I can cover it voluntarily", "callback_data": f"swap_cover:{swap_request.id}"}],
    ]
    if future_assignment:
        buttons.append([{
            "text": f"🔄 Swap with my ({future_assignment.role} - {_short_date(future_assignment.event.date)}) shift",
            "callback_data": f"swap_accept:{swap_request.id}:{future_assignment.id}",
        }])
    buttons.extend([
        [{"text": "❌ No, I'm good", "callback_data": f"swap_decline:{swap_request.id}"}],
        [_schedule_button("\U0001F4C5 Open Schedule", person=recipient)],
    ])
    return buttons


def send_swap_request_temp_groups(assignment, swap_request):
    msg_id = send_swap_needed(assignment.event, assignment)
    return 1 if msg_id else 0


# ═══════════════════════════════════════════════════════════════════
#  Callback Query Handler
# ═══════════════════════════════════════════════════════════════════

def handle_callback_query(data):
    """
    Process a Telegram callback query from an inline button press.

    Callback data format: "action:assignment_id[:extra]"

    Actions:
      confirm:{id}         — Confirm assignment
      decline:{id}         — Mark as can't make it
      undo:{id}            — Undo confirmation
      pickup:{id}          — Pick up a shift as the Telegram user who tapped
      pickup_as:{id}:{name} — Pick up shift as specific person
      cancel_pickup:{id}   — Cancel pickup selection
      noop:{id}            — No action (info-only button)
      expand:{id}          — Expand options for a specific assignment
      collapse:{id}        — Collapse options back to names only
    """
    callback_id = data.get("id")
    callback_data = data.get("data", "")
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    from_user = data.get("from", {})
    telegram_user_id = str(from_user.get("id", ""))
    first_name = from_user.get("first_name", "Unknown")

    parts = callback_data.split(":")
    raw_action = parts[0] if parts else ""
    compact_callbacks = raw_action.endswith("_compact")
    action = raw_action[:-8] if compact_callbacks else raw_action

    if action == "noop":
        _log_interaction(telegram_user_id, first_name, "noop", first_name)
        db.session.commit()
        answer_callback(callback_id, "ℹ️ Tap the buttons below to confirm or decline.")
        return

    if action in (
        "weekly_confirm",
        "weekly_decline",
        "weekly_confirm_shift",
        "weekly_decline_shift",
        "weekly_decline_yes",
        "weekly_back",
    ):
        person_name = _resolve_person(telegram_user_id, first_name)
        if action == "weekly_back":
            _log_interaction(telegram_user_id, first_name, "weekly_back", person_name)
            db.session.commit()
            _restore_weekly_message(chat_id, message_id)
            answer_callback(callback_id, "Cancelled")
            return

        if action == "weekly_confirm":
            _log_interaction(telegram_user_id, first_name, "weekly_confirm_tap", person_name)
            db.session.commit()
            _weekly_select_shift(
                callback_id, chat_id, message_id, person_name, "confirm",
                telegram_user_id=telegram_user_id, first_name=first_name,
            )
            return

        if action == "weekly_decline":
            _log_interaction(telegram_user_id, first_name, "weekly_decline_tap", person_name)
            db.session.commit()
            _weekly_select_shift(
                callback_id, chat_id, message_id, person_name, "decline",
                telegram_user_id=telegram_user_id, first_name=first_name,
            )
            return

        assignment_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        assignment = Assignment.query.get(assignment_id) if assignment_id else None
        if not assignment:
            answer_callback(callback_id, "❌ Assignment not found", show_alert=True)
            return

        if action == "weekly_confirm_shift":
            _weekly_confirm_assignment(
                callback_id, chat_id, message_id, assignment, person_name,
                telegram_user_id=telegram_user_id, first_name=first_name,
            )
            return

        if action == "weekly_decline_shift":
            _weekly_show_decline_confirmation(callback_id, chat_id, message_id, assignment)
            return

        if action == "weekly_decline_yes":
            _weekly_decline_assignment(
                callback_id, chat_id, message_id, assignment, person_name,
                telegram_user_id=telegram_user_id, first_name=first_name,
            )
            return

    if action in (
        "event_confirm",
        "event_decline",
        "event_confirm_shift",
        "event_decline_shift",
        "event_decline_yes",
        "event_back",
    ):
        person_name = _resolve_person(telegram_user_id, first_name)
        if action == "event_back":
            event_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            event = Event.query.get(event_id) if event_id else None
            if not event:
                answer_callback(callback_id, "❌ Event not found", show_alert=True)
                return
            _restore_event_reminder_message(chat_id, message_id, event)
            answer_callback(callback_id)
            return

        if action in ("event_confirm", "event_decline"):
            event_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            event = Event.query.get(event_id) if event_id else None
            if not event:
                answer_callback(callback_id, "❌ Event not found", show_alert=True)
                return
            _event_select_shift(
                callback_id, chat_id, message_id, event, person_name,
                "confirm" if action == "event_confirm" else "decline",
                telegram_user_id=telegram_user_id, first_name=first_name,
            )
            return

        assignment_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        assignment = Assignment.query.get(assignment_id) if assignment_id else None
        source_message_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else message_id
        if not assignment:
            answer_callback(callback_id, "❌ Assignment not found", show_alert=True)
            return

        if action == "event_confirm_shift":
            _event_confirm_assignment(
                callback_id, chat_id, source_message_id, assignment, person_name,
                telegram_user_id=telegram_user_id, first_name=first_name,
            )
            return

        if action == "event_decline_shift":
            if _can_undo_event_decline(assignment, person_name):
                _event_undo_decline_assignment(
                    callback_id, chat_id, source_message_id, assignment, person_name,
                    telegram_user_id=telegram_user_id, first_name=first_name,
                )
                return
            _event_show_decline_confirmation(callback_id, chat_id, source_message_id, assignment)
            return

        if action == "event_decline_yes":
            _event_decline_assignment(
                callback_id, chat_id, message_id, assignment, person_name,
                telegram_user_id=telegram_user_id, first_name=first_name,
                source_message_id=source_message_id,
            )
            return

    if action in ("personal_confirm", "personal_decline", "weekday_ack"):
        assignment_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        assignment = Assignment.query.get(assignment_id) if assignment_id else None
        if not assignment:
            answer_callback(callback_id, "❌ Assignment not found", show_alert=True)
            return
        event = assignment.event
        person_name = _resolve_person(telegram_user_id, first_name)
        temp_chat = TempChat.query.filter_by(chat_id=str(chat_id), assignment_id=assignment.id, status="active").first()
        if action == "personal_confirm":
            assignment.status = "confirmed"
            h = assignment.history
            h.append({"action": "confirm", "by": person_name, "via": "temp_group", "ts": str(vancouver_now())})
            assignment.history = h
            _log_interaction(telegram_user_id, first_name, "confirm", person_name, assignment, event)
            db.session.commit()
            _notify_admin_text(f"✅ {assignment.person} confirmed\n{_event_title(event)} · {assignment.role}")
            answer_callback(callback_id, "Confirmed")
            edit_message(chat_id, message_id, (
                f"✅ <b>Confirmed</b>\n\n"
                f"Thanks {assignment.person} - you're marked as coming for {_event_title(event)} today.\n\n"
                f"<i>This chat will auto-destruct in 5 seconds.</i>"
            ))
            refresh_event_telegram(event)
            _delete_temp_chat(temp_chat, delay=5)
            return
        if action == "weekday_ack":
            if assignment.status == "pending":
                assignment.status = "confirmed"
            h = assignment.history
            h.append({"action": "ack", "by": person_name, "via": "temp_group", "ts": str(vancouver_now())})
            assignment.history = h
            _log_interaction(telegram_user_id, first_name, "ack", person_name, assignment, event)
            db.session.commit()
            _notify_admin_text(f"👍 {assignment.person} acknowledged\n{_event_title(event)} · {assignment.role}")
            answer_callback(callback_id, "Confirmed")
            edit_message(chat_id, message_id, "👍 <b>Sounds good</b>\n\nSee you tonight!\n\n<i>This chat will auto-destruct in 5 seconds.</i>")
            refresh_event_telegram(event)
            _delete_temp_chat(temp_chat, delay=5)
            return
        assignment.status = "swap_needed"
        h = assignment.history
        h.append({"action": "decline", "by": person_name, "via": "temp_group", "ts": str(vancouver_now())})
        assignment.history = h
        deadline_local = _swap_deadline(event)
        deadline_utc = deadline_local.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        swap = SwapRequest.query.filter_by(assignment_id=assignment.id, status="active").first()
        if not swap:
            swap = SwapRequest(
                assignment_id=assignment.id,
                requestor=assignment.person,
                event_date=event.date,
                role=assignment.role,
                expires_at=deadline_utc,
                status="active",
            )
            db.session.add(swap)
        _log_interaction(telegram_user_id, first_name, "decline", person_name, assignment, event)
        db.session.commit()
        _notify_admin_text(f"❌ {assignment.person} can't make it\n{_event_title(event)} · {assignment.role}")
        answer_callback(callback_id)
        edit_message(chat_id, message_id, (
            "Thanks for letting us know.\n\n"
            "We'll ask the team if someone can cover your shift.\n\n"
            "<i>This chat will auto-destruct in 5 seconds.</i>"
        ))
        _delete_temp_chat(temp_chat, delay=5)
        refresh_event_telegram(event)
        send_swap_needed(event, assignment)
        return

    if action in ("swap_cover", "swap_accept", "swap_decline"):
        swap_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        swap = SwapRequest.query.get(swap_id) if swap_id else None
        if not swap or swap.status != "active":
            answer_callback(callback_id, "This shift has already been covered. Thanks!", show_alert=True)
            temp_chat = TempChat.query.filter_by(chat_id=str(chat_id)).order_by(TempChat.id.desc()).first()
            if temp_chat:
                edit_message(chat_id, message_id, "✅ <b>Already covered</b>\n\n<i>This chat will auto-destruct in 5 seconds.</i>")
                _delete_temp_chat(temp_chat, delay=5)
            return
        assignment = Assignment.query.get(swap.assignment_id)
        if not assignment:
            answer_callback(callback_id, "❌ Assignment not found", show_alert=True)
            return
        event = assignment.event
        temp_chat = TempChat.query.filter_by(chat_id=str(chat_id), swap_request_id=swap.id, status="active").first()
        person_name = temp_chat.recipient if temp_chat and temp_chat.recipient else _resolve_person(telegram_user_id, first_name)
        if action == "swap_decline":
            _log_interaction(telegram_user_id, first_name, "swap_decline", person_name, assignment, event)
            db.session.commit()
            _notify_admin_text(f"👍 {person_name} declined swap\n{_event_title(event)} · {assignment.role}")
            answer_callback(callback_id, "No problem - thanks for responding.", show_alert=True)
            edit_message(chat_id, message_id, "👍 <b>No problem</b>\n\nThanks for letting us know.\n\n<i>This chat will auto-destruct in 5 seconds.</i>")
            _delete_temp_chat(temp_chat, delay=5)
            return
        original_person = assignment.person
        if action == "swap_cover":
            if any(a.id != assignment.id and (a.cover or a.person) == person_name for a in event.assignments):
                answer_callback(callback_id, "You are already scheduled for this event.", show_alert=True)
                return
            assignment.cover = person_name
            assignment.status = "confirmed"
            swap.status = "accepted"
            swap.accepted_by = person_name
            swap.accepted_at = datetime.datetime.utcnow()
            swap.reschedule_event_date = None
            swap.reschedule_notes = f"{person_name} voluntarily covered {event.date}; no future shift changed"
            _log_interaction(telegram_user_id, first_name, "swap_cover", person_name, assignment, event)
            db.session.commit()
            _notify_admin_text(f"✅ {person_name} volunteered to cover {original_person}\n{_event_title(event)} · {assignment.role}")
            answer_callback(callback_id, f"Thanks {person_name} - you're covering it. ✅", show_alert=True)
            edit_message(chat_id, message_id, (
                f"✅ <b>Covered voluntarily</b>\n\n"
                f"Thanks {person_name}!\n\n"
                f"You are now covering:\n\n"
                f"📅 <b>{_event_title(event)}</b>\n"
                f"🗓 {_date_line(event.date)}\n"
                f"{ROLE_EMOJI.get(assignment.role, '👤')} Role: {assignment.role}\n\n"
                f"Your future shifts stay unchanged.\n\n"
                f"<i>This chat will auto-destruct in 5 seconds.</i>"
            ))
            refresh_event_telegram(event)
            for other in TempChat.query.filter(
                TempChat.swap_request_id == swap.id,
                TempChat.status == "active",
                TempChat.chat_id != str(chat_id),
            ).all():
                if other.message_id:
                    edit_message(other.chat_id, other.message_id, (
                        f"✅ <b>Covered</b>\n\n"
                        f"{person_name} volunteered to cover {original_person}, so this shift is taken care of.\n\n"
                        f"Thanks for being available!\n\n"
                        f"<i>This chat will auto-destruct in 5 seconds.</i>"
                    ))
                _delete_temp_chat(other, delay=5)
            _delete_temp_chat(temp_chat, delay=5)
            return
        future_assignment_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        future_assignment = Assignment.query.get(future_assignment_id) if future_assignment_id else None
        if not future_assignment:
            answer_callback(callback_id, "❌ Future shift not found", show_alert=True)
            return
        future_event = future_assignment.event
        assignment.cover = person_name
        assignment.status = "confirmed"
        future_assignment.person = original_person
        future_assignment.cover = None
        future_assignment.status = "pending"
        swap.status = "accepted"
        swap.accepted_by = person_name
        swap.accepted_at = datetime.datetime.utcnow()
        swap.reschedule_event_date = future_event.date
        swap.reschedule_notes = f"{person_name} covered {event.date}; {original_person} moved to {future_event.date}"
        _log_interaction(telegram_user_id, first_name, "swap_accept", person_name, assignment, event)
        db.session.commit()
        _notify_admin_text(f"🔄 {person_name} swapped with {original_person}\n{_event_title(event)} · {assignment.role}")
        answer_callback(callback_id, f"Thanks {person_name} - swap completed. ✅", show_alert=True)
        edit_message(chat_id, message_id, (
            f"✅ <b>Swap confirmed</b>\n\n"
            f"Thanks {person_name}!\n\n"
            f"You are now covering:\n\n"
            f"📅 <b>{_event_title(event)}</b>\n"
            f"🗓 {_date_line(event.date)}\n"
            f"{ROLE_EMOJI.get(assignment.role, '👤')} Role: {assignment.role}\n\n"
            f"{original_person} will take your future shift:\n\n"
            f"📅 <b>{_event_title(future_event)}</b>\n"
            f"🗓 {_date_line(future_event.date)}\n"
            f"{ROLE_EMOJI.get(future_assignment.role, '👤')} Role: {future_assignment.role}\n\n"
            f"<i>This chat will auto-destruct in 5 seconds.</i>"
        ))
        refresh_event_telegram(event)
        refresh_event_telegram(future_event)
        for other in TempChat.query.filter(
            TempChat.swap_request_id == swap.id,
            TempChat.status == "active",
            TempChat.chat_id != str(chat_id),
        ).all():
            if other.message_id:
                edit_message(other.chat_id, other.message_id, (
                    f"✅ <b>Covered</b>\n\n"
                    f"{person_name} already swapped with {original_person}, so this shift is taken care of.\n\n"
                    f"Thanks for being available!\n\n"
                    f"<i>This chat will auto-destruct in 5 seconds.</i>"
                ))
            _delete_temp_chat(other, delay=5)
        notice = _send_temp_group(
            "swap_notice",
            original_person,
            (
                f"👍 <b>Sounds good</b>\n\n"
                f"Thanks {original_person}.\n\n"
                f"{person_name} swapped with you and will cover your shift today.\n\n"
                f"<i>This message will auto-delete in 5 seconds.</i>"
            ),
            [[{"text": "👍 Sounds good", "callback_data": "noop"}]],
            assignment=assignment,
            expires_at=_swap_deadline(event),
            title=f"🎬 Swap Covered {original_person}",
        )
        if notice:
            _delete_temp_chat(notice, delay=5)
        _delete_temp_chat(temp_chat, delay=5)
        return

    # ── Resolve the assignment ──────────────────────────────────
    assignment_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    if not assignment_id:
        answer_callback(callback_id, "❌ Invalid action", show_alert=True)
        return

    assignment = Assignment.query.get(assignment_id)
    if not assignment:
        answer_callback(callback_id, "❌ Assignment not found", show_alert=True)
        return

    event = assignment.event

    # ── Resolve who pressed the button ──────────────────────────
    # Try to match telegram_user_id to a TeamMember
    person_name = _resolve_person(telegram_user_id, first_name)

    # ── Process the action ──────────────────────────────────────
    if action == "confirm":
        if assignment.status == "confirmed":
            answer_callback(callback_id, "Already confirmed!")
            return
        assignment.status = "confirmed"
        h = assignment.history
        h.append({"action": "confirm", "by": person_name, "via": "telegram", "ts": str(vancouver_now())})
        assignment.history = h
        _log_interaction(telegram_user_id, first_name, "confirm", person_name, assignment, event)
        db.session.commit()
        _notify_admin("confirm", person_name, assignment, event)
        answer_callback(callback_id, "Confirmed")

    elif action == "decline":
        assignment.status = "swap_needed"
        h = assignment.history
        h.append({"action": "decline", "by": person_name, "via": "telegram", "ts": str(vancouver_now())})
        assignment.history = h

        # Create a SwapRequest with the appropriate deadline
        deadline_local = compute_pickup_deadline(event.date, event.day_type)
        deadline_utc = deadline_local.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        # Don't duplicate if one is already active
        existing = SwapRequest.query.filter_by(
            assignment_id=assignment.id, status="active"
        ).first()
        if existing:
            swap = existing
        else:
            swap = SwapRequest(
                assignment_id=assignment.id,
                requestor=person_name,
                event_date=event.date,
                role=assignment.role,
                expires_at=deadline_utc,
                status="active",
            )
            db.session.add(swap)

        _log_interaction(telegram_user_id, first_name, "decline", person_name, assignment, event)
        db.session.commit()
        _notify_admin("decline", person_name, assignment, event)

        if hasattr(deadline_local, "strftime"):
            deadline_str = deadline_local.strftime("%a %b %d at %I:%M %p")
            # Strip leading zero from hour (cross-platform; %-I is Linux-only)
            deadline_str = deadline_str.replace(" at 0", " at ")
        else:
            deadline_str = str(deadline_local)
        answer_callback(
            callback_id,
            f"Got it - {person_name} can't make it. The shift is now open for someone "
            f"else to pick up by {deadline_str}. You can undo anytime before the deadline.",
            show_alert=True,
        )

        # Broadcast a separate swap-needed alert so everyone can pick it up
        send_swap_needed(event, assignment, chat_id=chat_id)

    elif action == "undo":
        if assignment.cover:
            assignment.cover = None
            assignment.status = "swap_needed"
        elif assignment.status == "swap_needed":
            assignment.status = "confirmed"
            # Undo of a previous decline — cancel the active swap request and
            # tear down the broadcast chats so re-declines start clean.
            active_swap = SwapRequest.query.filter_by(
                assignment_id=assignment.id, status="active"
            ).first()
            if active_swap:
                active_swap.status = "cancelled"
                for tc in TempChat.query.filter_by(
                    swap_request_id=active_swap.id, status="active"
                ).all():
                    try:
                        _delete_temp_chat(tc)
                    except Exception as e:
                        print(f"Temp chat cleanup error: {e}")
        elif assignment.status == "confirmed":
            assignment.status = "pending"
        h = assignment.history
        h.append({"action": "undo", "by": person_name, "via": "telegram", "ts": str(vancouver_now())})
        assignment.history = h
        _log_interaction(telegram_user_id, first_name, "undo", person_name, assignment, event)
        db.session.commit()
        _notify_admin("undo", person_name, assignment, event)
        answer_callback(callback_id, f"↩️ Undone by {person_name}")

    elif action == "pickup":
        weekly_message_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        cover_name = _resolve_person(telegram_user_id, first_name, use_override=False)
        if not cover_name or cover_name == "Unknown":
            answer_callback(callback_id, "I couldn't tell who tapped this.", show_alert=True)
            return
        if cover_name == assignment.person:
            answer_callback(callback_id, "You are already assigned to this shift.", show_alert=True)
            return
        if any(a.id != assignment.id and (a.cover or a.person) == cover_name for a in event.assignments):
            answer_callback(callback_id, f"{cover_name} is already scheduled for this event.", show_alert=True)
            return
        assignment.cover = cover_name
        assignment.status = "confirmed"
        h = assignment.history
        h.append({"action": "pickup", "by": cover_name, "via": "telegram", "ts": str(vancouver_now())})
        assignment.history = h

        active_swap = SwapRequest.query.filter_by(
            assignment_id=assignment.id, status="active"
        ).first()
        if active_swap:
            active_swap.status = "accepted"
            active_swap.accepted_by = cover_name
            active_swap.accepted_at = datetime.datetime.utcnow()

        _log_interaction(telegram_user_id, first_name, "pickup", cover_name, assignment, event,
                         details=f"Covered by {cover_name}")
        assignment.telegram_message_id = None
        db.session.commit()
        answer_callback(callback_id)
        delete_message(chat_id, message_id)
        if weekly_message_id:
            _restore_weekly_message(chat_id, weekly_message_id, today=event.date)
        refresh_event_telegram(event)
        return

    elif action == "pickup_as":
        cover_name = parts[2] if len(parts) > 2 else person_name
        if any(a.id != assignment.id and (a.cover or a.person) == cover_name for a in event.assignments):
            answer_callback(callback_id, f"{cover_name} is already scheduled for this event.", show_alert=True)
            return
        assignment.cover = cover_name
        assignment.status = "confirmed"
        h = assignment.history
        h.append({"action": "pickup", "by": cover_name, "via": "telegram", "ts": str(vancouver_now())})
        assignment.history = h

        # Close out any active swap request for this assignment
        active_swap = SwapRequest.query.filter_by(
            assignment_id=assignment.id, status="active"
        ).first()
        if active_swap:
            active_swap.status = "accepted"
            active_swap.accepted_by = cover_name
            active_swap.accepted_at = datetime.datetime.utcnow()

        _log_interaction(telegram_user_id, first_name, "pickup_as", cover_name, assignment, event,
                         details=f"Covered by {cover_name}")
        db.session.commit()
        _notify_admin("pickup_as", cover_name, assignment, event)
        answer_callback(callback_id, f"✅ {cover_name} is covering! Thank you! 🎉")

        # Send confirmation
        send_shift_covered(event, assignment, cover_name, chat_id=chat_id)

    elif action == "cancel_pickup":
        _log_interaction(telegram_user_id, first_name, "cancel_pickup", person_name, assignment, event)
        db.session.commit()
        answer_callback(callback_id, "Cancelled.")

    elif action == "expand":
        _log_interaction(telegram_user_id, first_name, "expand", person_name, assignment, event)
        db.session.commit()
        _notify_name_tap(person_name, assignment, event)
        buttons = _build_event_buttons(
            event,
            expanded_id=assignment.id,
            include_schedule_button=not compact_callbacks,
            inline_assignments=compact_callbacks,
            compact_callbacks=compact_callbacks,
        )
        edit_message_markup(chat_id, message_id, buttons)
        answer_callback(callback_id)
        return

    elif action == "collapse":
        _log_interaction(telegram_user_id, first_name, "collapse", person_name, assignment, event)
        db.session.commit()
        buttons = _build_event_buttons(
            event,
            expanded_id=None,
            include_schedule_button=not compact_callbacks,
            inline_assignments=compact_callbacks,
            compact_callbacks=compact_callbacks,
        )
        edit_message_markup(chat_id, message_id, buttons)
        answer_callback(callback_id)
        return

    else:
        answer_callback(callback_id, "❓ Unknown action")
        return

    # ── Rebuild the event message with updated statuses ─────────
    # Find the original reminder message and update it
    if compact_callbacks:
        _refresh_interactive_event_message(event, chat_id, message_id)
    else:
        _refresh_event_message(event, chat_id, message_id)
    try:
        update_weekly_schedule_for_event(event)
    except Exception as e:
        print(f"[Telegram] Failed to refresh weekly schedule message: {e}")


def _resolve_person(telegram_user_id, fallback_name, use_override=True):
    """Try to match a Telegram user ID to a team member name.

    If the user ID isn't linked yet, attempt to auto-link by matching
    the Telegram first_name to a TeamMember.name.
    """
    if telegram_user_id:
        override = TELEGRAM_PERSON_OVERRIDE.get(str(telegram_user_id))
        if use_override and override:
            return override

        member = TeamMember.query.filter_by(telegram_user_id=telegram_user_id).first()
        if member:
            return member.name

        # Auto-link: try matching fallback_name to an unlinked team member
        if fallback_name and fallback_name != "Unknown":
            candidate = TeamMember.query.filter_by(name=fallback_name, telegram_user_id=None).first()
            if not candidate:
                # Try case-insensitive / partial match
                candidate = TeamMember.query.filter(
                    TeamMember.telegram_user_id.is_(None),
                    db.func.lower(TeamMember.name) == fallback_name.lower()
                ).first()
            if candidate:
                candidate.telegram_user_id = telegram_user_id
                db.session.commit()
                print(f"[Telegram] Auto-linked user {fallback_name} (ID {telegram_user_id}) to TeamMember '{candidate.name}'")
                return candidate.name

    # Fallback: use first_name from Telegram
    return fallback_name


def _refresh_event_message(event, chat_id, message_id):
    """Re-render the event message with current statuses and buttons."""
    text = format_today_group_post(event)
    buttons = _make_inline_keyboard([[_schedule_button()]])
    edit_message(chat_id, message_id, text, reply_markup=buttons)


def _refresh_interactive_event_message(event, chat_id, message_id):
    """Re-render a compact event post that keeps name/status buttons attached."""
    text = format_interactive_event_post(event)
    buttons = _build_event_buttons(
        event,
        include_schedule_button=False,
        inline_assignments=True,
        compact_callbacks=True,
    )
    edit_message(chat_id, message_id, text, reply_markup=buttons)


def refresh_event_telegram(event):
    """Public helper: refresh the Telegram reminder message for an event.

    Called from v1 routes so status changes made through the web UI
    are reflected in the Telegram inline message.
    """
    if not event:
        return
    chat_id = event.telegram_chat_id
    message_id = event.telegram_message_id
    if chat_id and message_id:
        try:
            _refresh_event_message(event, chat_id, message_id)
        except Exception as e:
            print(f"[Telegram] Failed to refresh event message: {e}")
    try:
        update_weekly_schedule_for_event(event)
    except Exception as e:
        print(f"[Telegram] Failed to refresh weekly schedule message: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Scheduled Notifications
# ═══════════════════════════════════════════════════════════════════

def _inside_daily_reminder_window(now=None):
    now = now or vancouver_now()
    return now.hour == 8


def send_daily_reminders_v2(chat_id=None):
    """Send or refresh 8 AM event reminders in the main chat."""
    now = vancouver_now()
    if not _inside_daily_reminder_window(now):
        print(f"[Scheduler] Skipping daily reminders outside 8AM window ({now.isoformat()})")
        return 0
    today = vancouver_today()
    events = Event.query.filter_by(date=today).all()
    sent = 0
    for event in events:
        if getattr(event, "cancelled", False):
            continue
        if _send_or_refresh_group_event_reminder(event, chat_id=chat_id):
            sent += 1
    return sent


def sweep_expired_swaps(chat_id=None):
    """Expire unresolved swaps after event+2h without auto-swapping."""
    now_utc = datetime.datetime.utcnow()
    expired = SwapRequest.query.filter(
        SwapRequest.status == "active",
        SwapRequest.expires_at <= now_utc,
    ).all()

    processed = 0
    for swap in expired:
        swap.status = "expired"
        try:
            assignment = Assignment.query.get(swap.assignment_id)
            event = assignment.event if assignment else None
            for temp_chat in TempChat.query.filter_by(swap_request_id=swap.id, status="active").all():
                _delete_temp_chat(temp_chat)
            db.session.commit()
            title = _event_title(event) if event else "Livestream"
            _notify_admin_text(f"⚠️ No swap accepted\n{swap.requestor} · {title} · {swap.role}")
        except Exception as e:
            print(f"[sweep] Failed to process swap {swap.id}: {e}")
            db.session.rollback()
        processed += 1

    if processed:
        print(f"[sweep] Processed {processed} expired swap(s)")
    return processed


def sweep_expired_temp_chats():
    now_utc = datetime.datetime.utcnow()
    expired = TempChat.query.filter(
        db.or_(
            TempChat.status == "deleting",
            db.and_(
                TempChat.status == "active",
                TempChat.expires_at.isnot(None),
                TempChat.expires_at <= now_utc,
            ),
        )
    ).all()

    processed = 0
    for temp_chat in expired:
        if _delete_temp_chat(temp_chat):
            processed += 1

    if processed:
        print(f"[sweep] Deleted {processed} expired temp chat(s)")
    return processed


def send_noon_response_followups(chat_id=None):
    return 0


def send_weekday_5pm_reminders_v2(chat_id=None):
    today = vancouver_today()
    events = Event.query.filter(Event.date == today, Event.day_type != "Sunday").all()
    sent = 0
    for event in events:
        if _is_sunday_event(event) or event.date.weekday() >= 5:
            continue
        if getattr(event, "cancelled", False):
            continue
        if _send_or_refresh_group_event_reminder(event, chat_id=chat_id):
            sent += 1
    return sent


def send_day_before_reminders_v2(chat_id=None):
    """Send reminders for tomorrow's events with inline buttons."""
    tomorrow = vancouver_today() + datetime.timedelta(days=1)
    events = Event.query.filter_by(date=tomorrow).all()
    sent = 0
    for event in events:
        msg_id = send_event_reminder(event, chat_id=chat_id)
        if msg_id:
            sent += 1
    return sent


# ═══════════════════════════════════════════════════════════════════
#  Testing
# ═══════════════════════════════════════════════════════════════════

def send_test_reminder(chat_id=None):
    """
    Send a test reminder to the personal chat ID.
    Uses the next upcoming event or creates a mock one.
    """
    target = chat_id or PERSONAL_CHAT_ID

    # Find next upcoming event
    today = vancouver_today()
    event = Event.query.filter(Event.date >= today).order_by(Event.date).first()

    if event:
        return send_event_reminder(event, chat_id=target)
    else:
        # Send a simple test message
        return send_message(
            "🔔 <b>Test Message</b>\n\nLivestream Schedule bot (v2) is connected! ✅\n"
            "No upcoming events found to preview.",
            chat_id=target
        )


def send_test_monthly(chat_id=None):
    """Send a test monthly schedule to the personal chat ID."""
    target = chat_id or PERSONAL_CHAT_ID
    today = vancouver_today()
    return send_monthly_schedule(today.year, today.month, chat_id=target)


def test_connection():
    """Test the bot connection."""
    if not TELEGRAM_BOT_TOKEN:
        return {"error": "No bot token configured"}
    result = _api_call("getMe", {})
    if result:
        return {"success": True, "bot": result}
    return {"error": "Connection failed"}


# ═══════════════════════════════════════════════════════════════════
#  Webhook Setup
# ═══════════════════════════════════════════════════════════════════

def set_webhook(url):
    """Set the Telegram webhook URL."""
    payload = {"url": url}
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET
    return _api_call("setWebhook", payload)


def delete_webhook():
    """Remove the webhook."""
    return _api_call("deleteWebhook", {})
