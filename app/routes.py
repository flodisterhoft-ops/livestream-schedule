import datetime
import hashlib
from collections import defaultdict

from flask import Blueprint, current_app, make_response, request
from .models import Event, Assignment
from .utils import VANCOUVER_TZ, vancouver_today

bp = Blueprint('main', __name__)

CALENDAR_TZID = "America/Vancouver"
WEEKLY_OVERVIEW_WEEKDAY = 1  # Tuesday
WEEKLY_OVERVIEW_MINUTES = 15


def _event_title(event):
    if event.custom_title:
        return event.custom_title
    if event.day_type == "Friday":
        return "Bible Study"
    if event.day_type == "Sunday":
        return "Sunday Service"
    return event.day_type or "Event"


def _event_start_time(event):
    if event.start_time:
        return event.start_time
    if event.day_type == "Friday" or event.date.weekday() == 4:
        return datetime.time(19, 0)
    return datetime.time(14, 30)


def _event_start_dt(event):
    return datetime.datetime.combine(event.date, _event_start_time(event), tzinfo=VANCOUVER_TZ)


def _event_end_dt(event):
    return _event_start_dt(event) + datetime.timedelta(hours=2)


def _calendar_reminder_hour():
    try:
        return int(current_app.config.get("WEEKLY_SCHEDULE_HOUR", 8))
    except RuntimeError:
        return 8


def _site_url():
    try:
        return current_app.config.get("BASE_URL", "https://livestream.disterhoft.com")
    except RuntimeError:
        return "https://livestream.disterhoft.com"


def _escape_ical(value):
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _format_local_dt(dt):
    return dt.strftime("%Y%m%dT%H%M%S")


def _format_utc_dt(dt):
    return dt.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _coerce_utc_dt(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _event_modified_dt(event):
    updated_at = _coerce_utc_dt(getattr(event, "updated_at", None))
    if updated_at:
        return updated_at
    return _event_start_dt(event).astimezone(datetime.timezone.utc)


def _sequence_from_dt(dt):
    if not dt:
        return 0
    return max(0, int(dt.timestamp()))


def _format_time_label(dt):
    return dt.strftime("%a, %b %d at %I:%M %p").replace(" 0", " ").replace(" at 0", " at ")


def _uid_token(value):
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in (value or "full"))
    token = "-".join(part for part in token.split("-") if part)
    return token or "full"


def _vtimezone_lines():
    return [
        "BEGIN:VTIMEZONE",
        f"TZID:{CALENDAR_TZID}",
        f"X-LIC-LOCATION:{CALENDAR_TZID}",
        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:-0800",
        "TZOFFSETTO:-0700",
        "TZNAME:PDT",
        "DTSTART:19700308T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
        "END:DAYLIGHT",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:-0700",
        "TZOFFSETTO:-0800",
        "TZNAME:PST",
        "DTSTART:19701101T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]


def _worker(assignment):
    return assignment.cover or assignment.person


def _visible_assignments(event, person=None):
    rows = []
    for assignment in event.assignments:
        worker = _worker(assignment)
        if person and worker != person:
            continue
        rows.append(assignment)
    return rows


def _assignment_description(assignment):
    worker = _worker(assignment)
    if assignment.cover:
        worker = f"{assignment.cover} covering for {assignment.person}"
    if assignment.status == "swap_needed":
        worker = f"{worker} - needs cover"
    return f"{assignment.role}: {worker}"


def _event_description(event, person=None):
    lines = []
    if getattr(event, "cancelled", False):
        lines.append("No livestream needed.")
    if event.location:
        lines.append(f"Location: {event.location}")
    for assignment in _visible_assignments(event, person):
        lines.append(_assignment_description(assignment))
    if not lines:
        lines.append("Livestream schedule event.")
    lines.append("")
    lines.append(_site_url().rstrip("/"))
    return "\n".join(lines)


def _event_summary(event, person=None):
    title = _event_title(event)
    if getattr(event, "cancelled", False):
        return f"No livestream needed: {title}"
    assignments = _visible_assignments(event, person)
    if person:
        roles = ", ".join(a.role for a in assignments)
        return f"Livestream: {title} - {roles}" if roles else f"Livestream: {title}"
    if any(a.status == "swap_needed" for a in assignments):
        return f"Livestream: {title} - needs cover"
    return f"Livestream: {title}"


def _event_day_reminder_dt(event):
    return datetime.datetime.combine(
        event.date,
        datetime.time(_calendar_reminder_hour(), 0),
        tzinfo=VANCOUVER_TZ,
    )


def _weekly_overview_dt(monday):
    return datetime.datetime.combine(
        monday + datetime.timedelta(days=WEEKLY_OVERVIEW_WEEKDAY),
        datetime.time(_calendar_reminder_hour(), 0),
        tzinfo=VANCOUVER_TZ,
    )


def _duration_trigger(delta):
    total_seconds = int(delta.total_seconds())
    if total_seconds == 0:
        return "PT0S"

    sign = "-" if total_seconds < 0 else ""
    total_seconds = abs(total_seconds)
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = [sign, "P"]
    if days:
        parts.append(f"{days}D")
    if hours or minutes or seconds or not days:
        parts.append("T")
        if hours:
            parts.append(f"{hours}H")
        if minutes:
            parts.append(f"{minutes}M")
        if seconds:
            parts.append(f"{seconds}S")
    return "".join(parts)


def _alarm_lines(description, trigger_value):
    return [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_escape_ical(description)}",
        f"TRIGGER:{trigger_value}",
        "END:VALARM",
    ]


def _event_lines(event, person=None, dtstamp=None):
    start_dt = _event_start_dt(event)
    end_dt = _event_end_dt(event)
    modified_dt = _event_modified_dt(event)
    stamp = dtstamp or _format_utc_dt(modified_dt)
    lines = [
        "BEGIN:VEVENT",
        f"UID:event-{event.id}-{_uid_token(person)}@livestream-schedule",
        f"DTSTAMP:{stamp}",
        f"LAST-MODIFIED:{_format_utc_dt(modified_dt)}",
        f"SEQUENCE:{_sequence_from_dt(modified_dt)}",
        f"DTSTART;TZID={CALENDAR_TZID}:{_format_local_dt(start_dt)}",
        f"DTEND;TZID={CALENDAR_TZID}:{_format_local_dt(end_dt)}",
        f"SUMMARY:{_escape_ical(_event_summary(event, person))}",
        f"DESCRIPTION:{_escape_ical(_event_description(event, person))}",
        f"URL:{_escape_ical(_site_url().rstrip('/'))}",
    ]
    if event.location:
        lines.append(f"LOCATION:{_escape_ical(event.location)}")
    if getattr(event, "cancelled", False):
        lines.append("STATUS:CANCELLED")
    else:
        reminder_dt = _event_day_reminder_dt(event)
        if reminder_dt < start_dt:
            lines.extend(_alarm_lines(
                f"Livestream today: {_event_title(event)}",
                _duration_trigger(reminder_dt - start_dt),
            ))
    lines.append("END:VEVENT")
    return lines


def _weekly_overview_description(events, person=None):
    lines = ["This week's livestream schedule:"]
    for event in events:
        start_dt = _event_start_dt(event)
        status = " - no livestream needed" if getattr(event, "cancelled", False) else ""
        location = f" @ {event.location}" if event.location else ""
        assignments = _visible_assignments(event, person)
        if person:
            role_text = ", ".join(a.role for a in assignments)
            suffix = f" ({role_text})" if role_text else ""
        else:
            suffix = ""
        lines.append(f"{_format_time_label(start_dt)} - {_event_title(event)}{suffix}{location}{status}")
    lines.append("")
    lines.append(_site_url().rstrip("/"))
    return "\n".join(lines)


def _weekly_overview_lines(monday, events, person=None, dtstamp=None):
    start_dt = _weekly_overview_dt(monday)
    end_dt = start_dt + datetime.timedelta(minutes=WEEKLY_OVERVIEW_MINUTES)
    suffix = _uid_token(person)
    modified_dt = max((_event_modified_dt(event) for event in events), default=start_dt.astimezone(datetime.timezone.utc))
    stamp = dtstamp or _format_utc_dt(modified_dt)
    lines = [
        "BEGIN:VEVENT",
        f"UID:week-{monday.strftime('%Y%m%d')}-{suffix}@livestream-schedule",
        f"DTSTAMP:{stamp}",
        f"LAST-MODIFIED:{_format_utc_dt(modified_dt)}",
        f"SEQUENCE:{_sequence_from_dt(modified_dt)}",
        f"DTSTART;TZID={CALENDAR_TZID}:{_format_local_dt(start_dt)}",
        f"DTEND;TZID={CALENDAR_TZID}:{_format_local_dt(end_dt)}",
        "TRANSP:TRANSPARENT",
        "SUMMARY:Livestream schedule this week",
        f"DESCRIPTION:{_escape_ical(_weekly_overview_description(events, person))}",
        f"URL:{_escape_ical(_site_url().rstrip('/'))}",
    ]
    lines.extend(_alarm_lines("Livestream schedule this week", "PT0S"))
    lines.append("END:VEVENT")
    return lines


def _events_by_week(events):
    grouped = defaultdict(list)
    for event in events:
        monday = event.date - datetime.timedelta(days=event.date.weekday())
        grouped[monday].append(event)
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def generate_ical(events, person=None):
    events = sorted(events, key=lambda event: event.date)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Livestream Scheduler//Calendar Feed//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Livestream Schedule" if not person else f"X-WR-CALNAME:{_escape_ical(person)} Livestream Schedule",
        f"X-WR-TIMEZONE:{CALENDAR_TZID}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    lines.extend(_vtimezone_lines())

    for monday, week_events in _events_by_week(events).items():
        lines.extend(_weekly_overview_lines(monday, week_events, person))

    for event in events:
        lines.extend(_event_lines(event, person))

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def _calendar_response(body, filename):
    response = make_response(body)
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.set_etag(hashlib.sha256(body.encode("utf-8")).hexdigest())
    response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    disposition = "attachment" if request.args.get("download") in ("1", "true", "yes") else "inline"
    response.headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


def _calendar_start_date():
    if request.args.get("archive") in ("1", "true", "yes"):
        return None
    return vancouver_today() - datetime.timedelta(days=14)


@bp.route("/calendar.ics")
def calendar_full():
    query = Event.query
    start_date = _calendar_start_date()
    if start_date:
        query = query.filter(Event.date >= start_date)
    events = query.order_by(Event.date).all()
    return _calendar_response(generate_ical(events), "livestream_schedule.ics")


@bp.route("/calendar/<person>.ics")
def calendar_person(person):
    assignment_query = Assignment.query.filter(
        (Assignment.person == person) | (Assignment.cover == person)
    )
    start_date = _calendar_start_date()
    if start_date:
        assignment_query = assignment_query.join(Event).filter(Event.date >= start_date)
    assignments = assignment_query.all()
    event_ids = {assignment.event_id for assignment in assignments}
    events = Event.query.filter(Event.id.in_(event_ids)).order_by(Event.date).all() if event_ids else []
    filename = f"{_uid_token(person)}_schedule.ics"
    return _calendar_response(generate_ical(events, person), filename)


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
