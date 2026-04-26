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
import requests
from .models import Event, Assignment, PickupToken, TeamMember, InteractionLog, SwapRequest
from .extensions import db
from .utils import vancouver_today, vancouver_now, VANCOUVER_TZ

# ── Configuration ────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
PERSONAL_CHAT_ID = os.environ.get("TELEGRAM_PERSONAL_CHAT_ID", "27859948")
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

BASE_API = "https://api.telegram.org/bot"

# ── Emoji maps ───────────────────────────────────────────────────────
ROLE_EMOJI = {
    "Computer": "🖥️",
    "Camera 1": "🎦1️⃣",
    "Camera 2": "🎦2️⃣",
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
    parts = [f"🔔 <b>{label}</b> — {person_name}"]
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
            label = f"✅ {worker} — Confirmed"
        elif a.status == "swap_needed":
            label = f"🔴 {worker} NEEDS COVERAGE"
        else:
            label = f"{role_icon} {worker}"

        rows.append([
            {"text": label, "callback_data": f"expand:{a.id}"},
        ])

    # "Show Schedule" button at the bottom (always present)
    from flask import current_app
    site_url = "https://livestream.disterhoft.com"
    try:
        if current_app:
            site_url = current_app.config.get('BASE_URL', site_url)
    except RuntimeError:
        pass
    rows.append([
        {"text": "📅 Show Schedule", "url": site_url}
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
    """Format a short header for the 8 AM reminder.

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
        f"📅 {title} — {date_str}",
        "",
        "<i>Tap your name to confirm or let us know if you can't make it.</i>",
    ]
    return "\n".join(lines)


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

    lines = [f"📅 <b>{month_name} {year} — Livestream Schedule</b>", ""]

    if not events:
        lines.append("<i>No events scheduled yet.</i>")
        return "\n".join(lines)

    for event in events:
        title = event.custom_title or ("Bible Study" if event.day_type == "Friday" else "Sunday Service")
        day = event.date.strftime("%a %d")
        lines.append(f"<b>{day}</b> — {title}")

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
    """Send an event reminder with inline confirmation buttons."""
    text = format_event_message(event, header="📋 Reminder!")
    buttons = _build_event_buttons(event)
    target = chat_id or TELEGRAM_CHAT_ID
    msg_id = send_message(text, chat_id=target, reply_markup=buttons)

    # Store message ID on event and assignments for later editing
    if msg_id:
        event.telegram_message_id = msg_id
        event.telegram_chat_id = str(target)
        for a in event.assignments:
            a.telegram_message_id = msg_id
        db.session.commit()

    return msg_id


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
        f"📆 <b>{title}</b> — {date_str}\n"
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
        f"📆 <b>{title}</b> — {date_str}\n"
        f"{role_icon} <b>{assignment.role}</b>\n\n"
        f"Thank you {helper_name}! 🎉"
    )

    target = chat_id or TELEGRAM_CHAT_ID
    if original_msg_id:
        if edit_message(target, original_msg_id, text):
            return original_msg_id
        delete_message(target, original_msg_id)

    return send_message(text, chat_id=target)


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
            f"Got it — {person_name} can't make it. The shift is now open for someone "
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
    text = format_event_message(event, header="📋 Reminder!")
    buttons = _build_event_buttons(event)
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
    """Send reminders for today's events with inline buttons."""
    today = vancouver_today()
    events = Event.query.filter_by(date=today).all()
    sent = 0
    for event in events:
        if send_event_reminder(event, chat_id=chat_id):
            sent += 1
    return sent


def sweep_expired_swaps(chat_id=None):
    """Expire any SwapRequests past their deadline and auto-reschedule.

    Called hourly by APScheduler. For each SwapRequest that:
      - is still 'active'
      - has expires_at <= now (UTC)
    we:
      1. Mark it 'expired'
      2. Run reschedule_declined() for the requestor
      3. DM the admin with the outcome
      4. Notify the requestor on their original Telegram message
    Returns the number of expired swaps processed.
    """
    from .scheduler_v2 import reschedule_declined

    now_utc = datetime.datetime.utcnow()
    expired = SwapRequest.query.filter(
        SwapRequest.status == "active",
        SwapRequest.expires_at <= now_utc,
    ).all()

    processed = 0
    for swap in expired:
        swap.status = "expired"
        try:
            result = reschedule_declined(
                requestor=swap.requestor,
                original_event_date=swap.event_date,
                role=swap.role,
            )
            swap.reschedule_event_date = result.get("new_event_date")
            swap.reschedule_notes = result.get("notes")
            db.session.commit()

            # Admin DM
            if result["status"] == "ok":
                admin_text = (
                    f"⏰ <b>Shift deadline passed — auto-rescheduled</b>\n\n"
                    f"👤 <b>{swap.requestor}</b> — {swap.role}\n"
                    f"🚫 Original: {swap.event_date.strftime('%a %b %d, %Y')}\n"
                    f"📆 Moved to: {result['new_event_date'].strftime('%a %b %d, %Y')}\n"
                )
                if result["displaced"]:
                    admin_text += (
                        f"🔄 Displaced {result['displaced']} → "
                        f"{result['displaced_moved_to'].strftime('%a %b %d, %Y') if result['displaced_moved_to'] else 'no move'}\n"
                    )
                admin_text += f"\n<i>{result['notes']}</i>"
            else:
                admin_text = (
                    f"⚠️ <b>Auto-reschedule FAILED</b>\n\n"
                    f"👤 <b>{swap.requestor}</b> — {swap.role}\n"
                    f"🚫 Original: {swap.event_date.strftime('%a %b %d, %Y')}\n\n"
                    f"<i>{result['notes']}</i>\n\n"
                    f"Please handle manually."
                )
            if PERSONAL_CHAT_ID:
                _api_call("sendMessage", {
                    "chat_id": PERSONAL_CHAT_ID,
                    "text": admin_text,
                    "parse_mode": "HTML",
                })
        except Exception as e:
            print(f"[sweep] Failed to process swap {swap.id}: {e}")
            db.session.rollback()
        processed += 1

    if processed:
        print(f"[sweep] Processed {processed} expired swap(s)")
    return processed


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
