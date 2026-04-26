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
import threading
import time
import requests
from itsdangerous import URLSafeSerializer
from flask import current_app
from .models import Event, Assignment, PickupToken, TeamMember, InteractionLog, SwapRequest, TempChat
from .extensions import db
from .utils import is_available, vancouver_today, vancouver_now, VANCOUVER_TZ
from . import telegram_temp_groups

# ── Configuration ────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
PERSONAL_CHAT_ID = os.environ.get("TELEGRAM_PERSONAL_CHAT_ID", "27859948")
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

BASE_API = "https://api.telegram.org/bot"

# ── Emoji maps ───────────────────────────────────────────────────────
ROLE_EMOJI = {
    "Computer": "🖥️",
    "Camera 1": "🎦",
    "Camera 2": "🎦",
    "Camera": "🎦",
    "Leader": "🎤",
    "Helper": "🙌",
}

# Friday Bible Study: first person gets computer icon, second gets hands icon
FRIDAY_ICONS = ["🖥️", "🙌"]

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
    url = _site_url().rstrip("/") + "/v2"
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


def _schedule_button(label="📅 View Schedule", person=None):
    return {"text": label, "url": _schedule_url(person)}


def _event_title(event):
    if event.custom_title:
        return event.custom_title
    if event.day_type == "Friday":
        return "Bible Study"
    if event.day_type == "Sunday":
        return "Sunday Service"
    return event.day_type or "Event"


def _event_time(event):
    if event.day_type == "Sunday" or event.date.weekday() >= 5:
        return "2:00 PM"
    return "7:00 PM"


def _date_line(date_obj):
    return date_obj.strftime("%A, %B %d, %Y").replace(" 0", " ")


def _short_date(date_obj):
    return date_obj.strftime("%B %d").replace(" 0", " ")


def _event_start_dt(event):
    hour = 14 if event.day_type == "Sunday" or event.date.weekday() >= 5 else 19
    return datetime.datetime.combine(event.date, datetime.time(hour=hour), tzinfo=VANCOUVER_TZ)


def _swap_deadline(event):
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


def _delete_temp_group_later(chat_id, delay=10):
    def _delete():
        time.sleep(delay)
        telegram_temp_groups.delete_group(chat_id)
    threading.Thread(target=_delete, daemon=True).start()


def _delete_temp_chat(temp_chat, delay=0):
    if not temp_chat or not temp_chat.chat_id:
        return
    temp_chat.status = "deleted"
    db.session.commit()
    if delay:
        _delete_temp_group_later(temp_chat.chat_id, delay=delay)
    else:
        telegram_temp_groups.delete_group(temp_chat.chat_id)


def _send_temp_group(kind, person, text, buttons, assignment=None, swap_request=None,
                     future_assignment=None, expires_at=None, title=None):
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


def _build_event_buttons(event, assignments=None, expanded_id=None):
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

    Always appends a "Show Schedule" URL button at the bottom.
    """
    if assignments is None:
        assignments = event.assignments

    rows = []
    is_friday = event.day_type == "Friday"

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
                    {"text": "↩️ Undo",  "callback_data": f"undo:{a.id}"},
                    {"text": "⬅️ Back",  "callback_data": f"collapse:{a.id}"},
                ])
            elif a.status == "swap_needed":
                rows.append([
                    {"text": "🙋 I can cover", "callback_data": f"pickup:{a.id}"},
                ])
                rows.append([
                    {"text": "↩️ Undo",  "callback_data": f"undo:{a.id}"},
                    {"text": "⬅️ Back",  "callback_data": f"collapse:{a.id}"},
                ])
            else:  # pending
                rows.append([
                    {"text": "✅ Yes, I'll be there",  "callback_data": f"confirm:{a.id}"},
                ])
                rows.append([
                    {"text": "❌ Can't make it",       "callback_data": f"decline:{a.id}"},
                    {"text": "⬅️ Back",                "callback_data": f"collapse:{a.id}"},
                ])
            continue

        # ── Collapsed: single-button row showing status + name ──
        if a.status == "confirmed":
            label = f"✅ {worker} - Confirmed"
        elif a.status == "swap_needed":
            label = f"🔴 {worker} NEEDS COVERAGE"
        else:
            label = f"{role_icon} {worker}"

        rows.append([
            {"text": label, "callback_data": f"expand:{a.id}"},
        ])

    rows.append([
        _schedule_button("📅 Show Schedule")
    ])

    return _make_inline_keyboard(rows) if rows else None


def _build_pickup_buttons(assignment):
    """Build buttons for shift pickup — list all team members who could cover."""
    from .utils import ROLES_CONFIG, ALL_NAMES
    rows = []
    for name in ALL_NAMES:
        if name in ("TBD", "Select Helper") or name == assignment.person:
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
    title = _event_title(event)
    lines = [
        f"📅 <b>{title}</b>",
        f"🗓 {_date_line(event.date)}",
        f"🕖 {_event_time(event)}",
        "",
    ]
    for i, assignment in enumerate(event.assignments):
        icon = _role_icon(assignment, i)
        worker = _worker_name(assignment)
        if assignment.status == "swap_needed":
            lines.append(f"{icon} {assignment.role} - 🔴 <s>{assignment.person}</s> needs coverage")
        elif assignment.cover:
            lines.append(f"{icon} {assignment.role} - <s>{assignment.person}</s> -> now swapped with {assignment.cover}")
        else:
            prefix = "✅ " if assignment.status == "confirmed" else ""
            lines.append(f"{icon} {assignment.role} - {prefix}{worker}")
    if any(a.cover for a in event.assignments):
        helper = next((a.cover for a in event.assignments if a.cover), None)
        lines.extend(["", f"Thank you {helper} for helping cover. 🙏"])
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
        [_schedule_button("📅 Open Schedule", person=assignment.person)],
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


# ═══════════════════════════════════════════════════════════════════
#  High-level Actions
# ═══════════════════════════════════════════════════════════════════

def send_event_reminder(event, chat_id=None):
    """Send the public 9 AM event post to the group chat."""
    text = format_today_group_post(event)
    buttons = _make_inline_keyboard([[_schedule_button("📅 View Schedule")]])
    target = chat_id or TELEGRAM_CHAT_ID
    msg_id = send_message(text, chat_id=target, reply_markup=buttons)

    if msg_id:
        event.telegram_message_id = msg_id
        event.telegram_chat_id = str(target)
        db.session.commit()

    return msg_id


def send_personal_question_temp_group(assignment):
    return _send_temp_group(
        "question",
        assignment.person,
        format_personal_question(assignment),
        _personal_question_buttons(assignment),
        assignment=assignment,
        expires_at=_swap_deadline(assignment.event),
        title=f"🎬 Livestream {assignment.person}",
    )


def send_weekday_ack_temp_group(assignment):
    worker = assignment.cover or assignment.person
    return _send_temp_group(
        "weekday_ack",
        worker,
        format_weekday_ack_reminder(assignment.event, assignment),
        _ack_buttons(assignment),
        assignment=assignment,
        expires_at=_swap_deadline(assignment.event),
        title=f"🎬 Reminder {worker}",
    )


def send_monthly_schedule(year=None, month=None, chat_id=None):
    """Send the monthly schedule overview."""
    today = vancouver_today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    text = format_monthly_schedule(year, month)
    return send_message(text, chat_id=chat_id)


def send_swap_needed(event, assignment, chat_id=None, pickup_url=None):
    """
    Send alert when someone marks they can't make it.
    Includes inline buttons for other team members to pick up the shift.
    Returns message_id on success.
    """
    title = event.custom_title or event.day_type
    date_str = event.date.strftime("%A, %B %d")
    role_icon = ROLE_EMOJI.get(assignment.role, "👤")

    text = (
        f"🔴 <b>Coverage Needed!</b>\n\n"
        f"{assignment.person} can't make it:\n"
        f"📆 <b>{title}</b> - {date_str}\n"
        f"{role_icon} <b>{assignment.role}</b>\n"
    )

    if pickup_url:
        text += f'\n🔗 <a href="{pickup_url}">Pick up via web</a>'

    text += "\nOr tap a name below to cover:"

    buttons = _build_pickup_buttons(assignment)
    msg_id = send_message(text, chat_id=chat_id, reply_markup=buttons)

    if msg_id:
        assignment.telegram_message_id = msg_id
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


def _find_future_swap_assignment(person, role, day_type, after_date):
    return (
        Assignment.query.join(Event)
        .filter(
            Event.date > after_date,
            Event.day_type == day_type,
            Assignment.person == person,
            Assignment.role == role,
            Assignment.status.in_(["pending", "confirmed"]),
            Assignment.cover.is_(None),
        )
        .order_by(Event.date)
        .first()
    )


def _eligible_swap_members(assignment):
    event = assignment.event
    day_type = event.day_type
    members = TeamMember.query.filter_by(active=True).all()
    eligible = []
    for member in members:
        if member.name == assignment.person or not member.telegram_user_id:
            continue
        roles = member.friday_roles if day_type == "Friday" else member.sunday_roles
        if assignment.role not in roles:
            continue
        if not is_available(member.name, event.date):
            continue
        if any((a.cover or a.person) == member.name for a in event.assignments):
            continue
        future = _find_future_swap_assignment(member.name, assignment.role, day_type, event.date)
        if future:
            eligible.append((member.name, future))
    return eligible


def _format_swap_request(assignment, recipient, future_assignment):
    event = assignment.event
    future_event = future_assignment.event
    title = _event_title(event)
    future_title = _event_title(future_event)
    icon = ROLE_EMOJI.get(assignment.role, "👤")
    return (
        f"🔄 <b>Swap request</b>\n\n"
        f"{assignment.person} can't make their livestream shift today.\n\n"
        f"📅 <b>Needs coverage:</b>\n"
        f"{title} - {_date_line(event.date)}\n"
        f"{icon} Role: {assignment.role}\n\n"
        f"Your next matching future shift is:\n\n"
        f"📅 <b>Your shift:</b>\n"
        f"{future_title} - {_date_line(future_event.date)}\n"
        f"{icon} Role: {future_assignment.role}\n\n"
        f"Would you like to swap?\n\n"
        f"If you accept:\n"
        f"- You serve today instead of {assignment.person}.\n"
        f"- {assignment.person} takes your future shift on {_short_date(future_event.date)}."
    )


def _swap_buttons(swap_request, future_assignment):
    return [
        [{"text": f"✅ Yes, swap with {swap_request.requestor}", "callback_data": f"swap_accept:{swap_request.id}:{future_assignment.id}"}],
        [{"text": "❌ No, I'm good", "callback_data": f"swap_decline:{swap_request.id}"}],
        [_schedule_button("📅 Open Schedule", person=future_assignment.person)],
    ]


def send_swap_request_temp_groups(assignment, swap_request):
    sent = 0
    for recipient, future_assignment in _eligible_swap_members(assignment):
        temp = _send_temp_group(
            "swap_request",
            recipient,
            _format_swap_request(assignment, recipient, future_assignment),
            _swap_buttons(swap_request, future_assignment),
            assignment=assignment,
            swap_request=swap_request,
            future_assignment=future_assignment,
            expires_at=swap_request.expires_at,
            title=f"🎬 Swap Request {recipient}",
        )
        if temp:
            sent += 1
    if sent == 0:
        _notify_admin_text(f"⚠️ No eligible swap recipients\n{assignment.person} · {_event_title(assignment.event)} · {assignment.role}")
    return sent


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
      pickup:{id}          — Pick up a shift (shows name selection)
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
    action = parts[0] if parts else ""

    if action == "noop":
        _log_interaction(telegram_user_id, first_name, "noop", first_name)
        db.session.commit()
        answer_callback(callback_id, "ℹ️ Tap the buttons below to confirm or decline.")
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
            answer_callback(callback_id, "Thanks for confirming! 😊", show_alert=True)
            edit_message(chat_id, message_id, (
                f"✅ <b>Confirmed</b>\n\n"
                f"Thanks {assignment.person} - you're marked as coming for {_event_title(event)} today.\n\n"
                f"<i>This chat will auto-destruct in 10 seconds.</i>"
            ))
            refresh_event_telegram(event)
            _delete_temp_chat(temp_chat, delay=10)
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
            answer_callback(callback_id, "Thanks for confirming! 😊", show_alert=True)
            edit_message(chat_id, message_id, "👍 <b>Sounds good</b>\n\nSee you tonight!\n\n<i>This chat will auto-destruct in 10 seconds.</i>")
            refresh_event_telegram(event)
            _delete_temp_chat(temp_chat, delay=10)
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
        answer_callback(callback_id, "Got it - we'll ask the team if someone can swap with you.", show_alert=True)
        edit_message(chat_id, message_id, (
            "Thanks for letting us know.\n\n"
            "We'll ask the team if someone can swap into your shift today.\n\n"
            "<i>This chat will auto-destruct in 10 seconds.</i>"
        ))
        refresh_event_telegram(event)
        send_swap_request_temp_groups(assignment, swap)
        _delete_temp_chat(temp_chat, delay=10)
        return

    if action in ("swap_accept", "swap_decline"):
        swap_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        swap = SwapRequest.query.get(swap_id) if swap_id else None
        if not swap or swap.status != "active":
            answer_callback(callback_id, "This shift has already been covered. Thanks!", show_alert=True)
            temp_chat = TempChat.query.filter_by(chat_id=str(chat_id), status="active").first()
            if temp_chat:
                edit_message(chat_id, message_id, "✅ <b>Already covered</b>\n\n<i>This chat will auto-destruct in 10 seconds.</i>")
                _delete_temp_chat(temp_chat, delay=10)
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
            edit_message(chat_id, message_id, "👍 <b>No problem</b>\n\nThanks for letting us know.\n\n<i>This chat will auto-destruct in 10 seconds.</i>")
            _delete_temp_chat(temp_chat, delay=10)
            return
        future_assignment_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        future_assignment = Assignment.query.get(future_assignment_id) if future_assignment_id else None
        if not future_assignment:
            answer_callback(callback_id, "❌ Future shift not found", show_alert=True)
            return
        original_person = assignment.person
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
            f"<i>This chat will auto-destruct in 10 seconds.</i>"
        ))
        refresh_event_telegram(event)
        for other in TempChat.query.filter(
            TempChat.swap_request_id == swap.id,
            TempChat.status == "active",
            TempChat.chat_id != str(chat_id),
        ).all():
            _delete_temp_chat(other)
        _send_temp_group(
            "swap_notice",
            original_person,
            (
                f"👍 <b>Sounds good</b>\n\n"
                f"Thanks {original_person}.\n\n"
                f"{person_name} swapped with you and will cover your shift today.\n\n"
                f"<i>This message will auto-delete in 10 seconds.</i>"
            ),
            [[{"text": "👍 Sounds good", "callback_data": "noop"}]],
            assignment=assignment,
            expires_at=_swap_deadline(event),
            title=f"🎬 Swap Covered {original_person}",
        )
        notice = TempChat.query.filter_by(kind="swap_notice", assignment_id=assignment.id, recipient=original_person, status="active").order_by(TempChat.id.desc()).first()
        if notice:
            _delete_temp_chat(notice, delay=10)
        _delete_temp_chat(temp_chat, delay=10)
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
        answer_callback(
            callback_id,
            f"🙏 Thank you for confirming, {person_name}! See you there.",
            show_alert=True,
        )

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

        deadline_str = deadline_local.strftime("%a %b %d at %-I:%M %p") \
            if hasattr(deadline_local, "strftime") else str(deadline_local)
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
            # Undo of a previous decline — cancel the active swap request
            active_swap = SwapRequest.query.filter_by(
                assignment_id=assignment.id, status="active"
            ).first()
            if active_swap:
                active_swap.status = "cancelled"
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
        # Show name selection for pickup
        _log_interaction(telegram_user_id, first_name, "pickup", person_name, assignment, event)
        db.session.commit()
        buttons = _build_pickup_buttons(assignment)
        edit_message_markup(chat_id, message_id, buttons)
        answer_callback(callback_id, "Select who's covering:")
        return  # Don't rebuild event buttons

    elif action == "pickup_as":
        cover_name = parts[2] if len(parts) > 2 else person_name
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
        buttons = _build_event_buttons(event, expanded_id=assignment.id)
        edit_message_markup(chat_id, message_id, buttons)
        answer_callback(callback_id)
        return

    elif action == "collapse":
        _log_interaction(telegram_user_id, first_name, "collapse", person_name, assignment, event)
        db.session.commit()
        buttons = _build_event_buttons(event, expanded_id=None)
        edit_message_markup(chat_id, message_id, buttons)
        answer_callback(callback_id)
        return

    else:
        answer_callback(callback_id, "❓ Unknown action")
        return

    # ── Rebuild the event message with updated statuses ─────────
    # Find the original reminder message and update it
    _refresh_event_message(event, chat_id, message_id)


def _resolve_person(telegram_user_id, fallback_name):
    """Try to match a Telegram user ID to a team member name.

    If the user ID isn't linked yet, attempt to auto-link by matching
    the Telegram first_name to a TeamMember.name.
    """
    if telegram_user_id:
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
    buttons = _make_inline_keyboard([[_schedule_button("📅 View Schedule")]])
    edit_message(chat_id, message_id, text, reply_markup=buttons)


def refresh_event_telegram(event):
    """Public helper: refresh the Telegram reminder message for an event.

    Called from v1 routes so status changes made through the web UI
    are reflected in the Telegram inline message.
    """
    chat_id = event.telegram_chat_id
    message_id = event.telegram_message_id
    if not chat_id or not message_id:
        return
    try:
        _refresh_event_message(event, chat_id, message_id)
    except Exception as e:
        print(f"[Telegram] Failed to refresh event message: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Scheduled Notifications
# ═══════════════════════════════════════════════════════════════════

def send_daily_reminders_v2(chat_id=None):
    """Send 9 AM group posts and temp-group personal questions."""
    today = vancouver_today()
    events = Event.query.filter_by(date=today).all()
    sent = 0
    for event in events:
        if send_event_reminder(event, chat_id=chat_id):
            sent += 1
        for assignment in event.assignments:
            worker = assignment.cover or assignment.person
            if worker in ("TBD", "Select Helper") or assignment.status == "swap_needed":
                continue
            if assignment.cover:
                continue
            if send_personal_question_temp_group(assignment):
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


def send_weekday_5pm_reminders_v2(chat_id=None):
    today = vancouver_today()
    events = Event.query.filter(Event.date == today, Event.day_type != "Sunday").all()
    sent = 0
    for event in events:
        for assignment in event.assignments:
            worker = assignment.cover or assignment.person
            if worker in ("TBD", "Select Helper") or assignment.status == "swap_needed":
                continue
            if send_weekday_ack_temp_group(assignment):
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
