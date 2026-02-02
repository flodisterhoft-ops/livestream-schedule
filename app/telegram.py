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


def send_telegram_message(message: str, chat_id: str = None, parse_mode: str = "HTML") -> bool:
    """
    Send a message to a Telegram chat.
    
    Args:
        message: The message text to send (supports HTML formatting)
        chat_id: Target chat ID (defaults to configured group chat)
        parse_mode: Parsing mode for message formatting ("HTML" or "Markdown")
    
    Returns:
        True if message was sent successfully, False otherwise
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
        return True
    except requests.RequestException as e:
        print(f"Telegram send error: {e}")
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
        Pickup URL for the assignment
    """
    base_url = os.environ.get("BASE_URL", "http://localhost:5000")
    token_str = str(uuid.uuid4())
    pickup_token = PickupToken(
        token=token_str,
        assignment_id=assignment.id,
        person=""  # Not needed for single-link approach
    )
    db.session.add(pickup_token)
    db.session.commit()
    return f"{base_url}/pickup/{token_str}"


def send_swap_needed_alert(event: Event, assignment: Assignment, original_person: str, 
                           pickup_url: str = None) -> bool:
    """
    Send an alert when someone marks they can't make it.
    
    Args:
        event: The Event with the swap needed
        assignment: The Assignment that needs coverage
        original_person: Who originally had the assignment
        pickup_url: Optional URL for picking up the shift
    
    Returns:
        True if sent successfully
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
        message += f'\n👉 <a href="{pickup_url}">Click here to pick up this shift</a>'
    else:
        message += "\nCan someone cover this shift? 🙏"
    
    return send_telegram_message(message)


def send_shift_covered_alert(event: Event, assignment: Assignment, helper_name: str) -> bool:
    """
    Send a notification when someone covers a shift.
    
    Args:
        event: The Event
        assignment: The Assignment that was covered
        helper_name: Who picked up the shift
    
    Returns:
        True if sent successfully
    """
    title = event.custom_title or event.day_type
    date_str = event.date.strftime("%B %d")
    role_emoji = ROLE_EMOJI.get(assignment.role, "👤")
    
    message = f"""✅ <b>Shift Covered!</b>

{helper_name} will cover:
📆 <b>{title}</b> on {date_str}
{role_emoji} <b>{assignment.role}</b>

Thank you {helper_name}! 🎉"""
    
    return send_telegram_message(message)


def get_upcoming_events(days_ahead: int = 1) -> list:
    """
    Get events happening in the next N days.
    
    Args:
        days_ahead: Number of days to look ahead
    
    Returns:
        List of Event objects
    """
    today = datetime.date.today()
    target_date = today + datetime.timedelta(days=days_ahead)
    
    return Event.query.filter_by(date=target_date).all()


def get_todays_events() -> list:
    """
    Get events happening today.
    
    Returns:
        List of Event objects
    """
    today = datetime.date.today()
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
