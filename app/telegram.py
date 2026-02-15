"""
Telegram notification service for the Livestream Schedule app.
Sends reminders to group chat for upcoming events.
"""
import os
import datetime
import uuid
import requests
from .models import Event, Assignment, PickupToken
from .extensions import db
from .utils import vancouver_today

# Configuration from environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Status emoji mapping
STATUS_EMOJI = {
    "confirmed": "✅",
    "pending": "⏳",
    "swap_needed": "🔴",
}

ROLE_EMOJI = {
    "Computer": "🖥️",
    "Camera 1": "📹",
    "Camera 2": "📹",
    "Leader": "📖",
    "Helper": "🤝",
}


def send_telegram_message(message: str, chat_id: str = None, parse_mode: str = "HTML"):
    """
    Send a message to a Telegram chat.

    Returns:
        Integer message_id on success, False on failure
    """
    if not TELEGRAM_BOT_TOKEN:
        print("Telegram: No bot token configured")
        return False

    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not target_chat:
        print("Telegram: No chat ID configured")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": target_chat,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        return result.get("result", {}).get("message_id", True)
    except requests.RequestException as e:
        print(f"Telegram send error: {e}")
        return False


def edit_telegram_message(message_id: int, text: str) -> bool:
    """Edit an existing Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not message_id:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"[Telegram] Message {message_id} edited successfully.")
            return True
        print(f"[Telegram] Failed to edit message: {resp.status_code} {resp.text}")
        return False
    except requests.RequestException as e:
        print(f"[Telegram] Error editing message: {e}")
        return False


def delete_telegram_message(message_id: int) -> bool:
    """Delete a Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not message_id:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"[Telegram] Message {message_id} deleted successfully.")
            return True
        print(f"[Telegram] Failed to delete message: {resp.status_code} {resp.text}")
        return False
    except requests.RequestException as e:
        print(f"[Telegram] Error deleting message: {e}")
        return False


def format_event_message(event: Event, header: str = "📅 Upcoming Event") -> str:
    """
    Format an event into a nice Telegram message.
    
    Args:
        event: The Event object to format
        header: Header text for the message
    
    Returns:
        Formatted HTML message string
    """
    # Determine event title
    title = event.custom_title
    if not title:
        if event.day_type == "Friday":
            title = "Bible Study"
        elif event.day_type == "Sunday":
            title = "Sunday Service"
        else:
            title = "Event"
    
    # Format date nicely
    date_str = event.date.strftime("%A, %B %d")
    
    # Build clean message
    lines = [
        f"<b>Reminder!</b>",
        f"🗓️ {date_str}",
        "",
    ]
    
    # Add assignments - clean format
    for a in event.assignments:
        role_emoji = ROLE_EMOJI.get(a.role, "👤")
        
        # Determine who's actually doing it
        if a.cover:
            person = f"{a.cover} <i>(covering for {a.person})</i>"
        else:
            person = a.person
        
        # Add status indicator only if not confirmed
        if a.status == "swap_needed":
            person = f"<b>NEEDED</b> ❌"
        elif a.status == "pending":
            person = f"{person} ⏳"
        
        lines.append(f"{role_emoji} {a.role}: {person}")
    
    # Add warning footer if needed
    swap_count = sum(1 for a in event.assignments if a.status == "swap_needed")
    if swap_count > 0:
        lines.append("")
        lines.append(f"⚠️ {swap_count} position(s) still need coverage!")
    
    return "\n".join(lines)


def send_event_reminder(event: Event) -> bool:
    """
    Send a reminder for an upcoming event.
    
    Args:
        event: The Event to send reminder for
    
    Returns:
        True if sent successfully
    """
    message = format_event_message(event, "📅 REMINDER - Tomorrow's Event")
    return send_telegram_message(message)


def send_morning_reminder(event: Event) -> bool:
    """
    Send a morning-of reminder for today's event.
    
    Args:
        event: The Event happening today
    
    Returns:
        True if sent successfully
    """
    message = format_event_message(event, "🌅 TODAY's Event")
    return send_telegram_message(message)


def generate_pickup_token(assignment: Assignment) -> str:
    """
    Generate a single pickup token for an assignment.

    Args:
        assignment: The Assignment that needs coverage

    Returns:
        The token string
    """
    token_str = str(uuid.uuid4())
    pickup_token = PickupToken(
        token=token_str,
        assignment_id=assignment.id,
        person=""  # Not needed for single-link approach
    )
    db.session.add(pickup_token)
    db.session.commit()
    return token_str


def send_swap_needed_alert(event: Event, assignment: Assignment, original_person: str,
                           pickup_url: str = None):
    """
    Send an alert when someone marks they can't make it.

    Returns:
        Integer message_id on success, False on failure
    """
    title = event.custom_title or event.day_type
    date_str = event.date.strftime("%B %d")
    role_emoji = ROLE_EMOJI.get(assignment.role, "👤")

    message = f"""🔴 <b>Coverage Needed!</b>

{original_person} can't make it to:
📆 <b>{title}</b> on {date_str}
{role_emoji} <b>{assignment.role}</b>
"""

    if pickup_url:
        message += f'\n🔄 <a href="{pickup_url}">Swap Shifts</a>'
    else:
        message += "\nCan someone cover this shift? 🙏"

    return send_telegram_message(message)


def send_shift_covered_alert(event: Event, assignment: Assignment, helper_name: str,
                              original_message_id: int = None):
    """
    Send a notification when someone covers a shift.
    Tries to edit the original swap-needed message first, falls back to delete+send.

    Returns:
        Integer message_id on success, False on failure
    """
    title = event.custom_title or event.day_type
    date_str = event.date.strftime("%B %d")
    role_emoji = ROLE_EMOJI.get(assignment.role, "👤")

    message = f"""✅ <b>Shift Covered — Resolved!</b>

{helper_name} will cover:
📆 <b>{title}</b> on {date_str}
{role_emoji} <b>{assignment.role}</b>

Thank you {helper_name}! 🎉"""

    if original_message_id:
        # Try to edit the original "Coverage Needed" message
        if edit_telegram_message(original_message_id, message):
            return original_message_id
        # Edit failed (e.g. message older than 48h) — delete and send new
        print(f"[Telegram] Edit failed, deleting old message and sending new one")
        delete_telegram_message(original_message_id)

    return send_telegram_message(message)


def get_upcoming_events(days_ahead: int = 1) -> list:
    """
    Get events happening in the next N days.
    
    Args:
        days_ahead: Number of days to look ahead
    
    Returns:
        List of Event objects
    """
    today = vancouver_today()
    target_date = today + datetime.timedelta(days=days_ahead)
    
    return Event.query.filter_by(date=target_date).all()


def get_todays_events() -> list:
    """
    Get events happening today.
    
    Returns:
        List of Event objects
    """
    today = vancouver_today()
    return Event.query.filter_by(date=today).all()


def send_daily_reminders():
    """
    Send morning reminders for today's events.
    This function is intended to be called by a scheduled task.
    
    Returns:
        Number of reminders sent
    """
    events = get_todays_events()
    sent = 0
    
    for event in events:
        if send_morning_reminder(event):
            sent += 1
    
    return sent


def test_telegram_connection() -> dict:
    """
    Test the Telegram bot connection and return bot info.
    
    Returns:
        Dict with bot info or error message
    """
    if not TELEGRAM_BOT_TOKEN:
        return {"error": "No bot token configured"}
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            return {"success": True, "bot": data.get("result", {})}
        return {"error": data.get("description", "Unknown error")}
    except requests.RequestException as e:
        return {"error": str(e)}
