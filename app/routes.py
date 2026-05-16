import datetime

from flask import Blueprint, make_response, request
from .models import Event, Assignment

bp = Blueprint('main', __name__)


def _event_title(event):
    if event.custom_title:
        return event.custom_title
    if event.day_type == "Friday":
        return "Bible Study"
    if event.day_type == "Sunday":
        return "Sunday Service"
    return event.day_type or "Event"


def _event_times(event):
    if event.day_type == "Friday":
        return "190000", "210000"
    if event.start_time:
        start = event.start_time
    else:
        start = datetime.time(14, 30)
    end_hour = start.hour + 2
    end = start.replace(hour=end_hour) if end_hour < 24 else start
    return start.strftime("%H%M%S"), end.strftime("%H%M%S")


def _escape_ical(value):
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def generate_ical(events, person=None):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Livestream Schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Livestream Schedule" if not person else f"X-WR-CALNAME:{_escape_ical(person)} Livestream Schedule",
    ]

    for event in events:
        desc_parts = []
        for assignment in event.assignments:
            worker = assignment.cover or assignment.person
            if person and worker != person:
                continue
            desc_parts.append(f"{assignment.role}: {worker}")

        if person and not desc_parts:
            continue

        start_time, end_time = _event_times(event)
        date_str = event.date.strftime("%Y%m%d")
        suffix = person or "full"
        uid = f"{date_str}-{suffix}@livestream-schedule"

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{_escape_ical(uid)}",
            f"DTSTART:{date_str}T{start_time}",
            f"DTEND:{date_str}T{end_time}",
            f"SUMMARY:{_escape_ical(_event_title(event))}",
            f"DESCRIPTION:{_escape_ical(chr(10).join(desc_parts))}",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


@bp.route("/calendar.ics")
def calendar_full():
    events = Event.query.order_by(Event.date).all()
    response = make_response(generate_ical(events))
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=livestream_schedule.ics"
    return response


@bp.route("/calendar/<person>.ics")
def calendar_person(person):
    assignments = Assignment.query.filter(
        (Assignment.person == person) | (Assignment.cover == person)
    ).all()
    event_ids = {assignment.event_id for assignment in assignments}
    events = Event.query.filter(Event.id.in_(event_ids)).order_by(Event.date).all() if event_ids else []
    response = make_response(generate_ical(events, person))
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename={person}_schedule.ics"
    return response


@bp.route("/cron/daily-reminder", methods=["GET", "POST"])
def cron_daily_reminder():
    cron_secret = __import__("os").environ.get("CRON_SECRET", "")
    provided_secret = request.args.get("secret", "") or request.headers.get("X-Cron-Secret", "")
    if cron_secret and provided_secret != cron_secret:
        return {"error": "Unauthorized"}, 401

    try:
        from .telegram_v2 import send_daily_reminders_v2
        sent = send_daily_reminders_v2()
    except Exception as e:
        print(f"[cron] v2 reminder failed: {e}")
        return {"success": False, "error": str(e)}, 500

    return {
        "success": True,
        "reminders_sent": sent,
        "reminders_sent_v2": sent,
        "message": f"Sent {sent} v2 reminder(s)",
    }