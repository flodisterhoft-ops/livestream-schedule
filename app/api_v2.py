"""
REST API v2 blueprint for the Livestream Scheduler.

Provides JSON endpoints for the React frontend.
All endpoints are prefixed with /api/v2/.
"""
import datetime
import calendar
import uuid
import os
from flask import Blueprint, request, jsonify, session, current_app
from .models import Event, Assignment, TeamMember, Availability, PickupToken
from .extensions import db
from .utils import (
    ALL_NAMES, ROLES_CONFIG, is_available, get_history_stats,
    vancouver_today, vancouver_now, is_real_person
)
from . import telegram_v2 as tg

api_v2 = Blueprint('api_v2', __name__, url_prefix='/api/v2')


# ═══════════════════════════════════════════════════════════════════
#  Auth / Identity
# ═══════════════════════════════════════════════════════════════════

@api_v2.route("/auth/login", methods=["POST"])
def login():
    """Set identity. Florian requires password."""
    data = request.json or {}
    name = data.get("name", "")
    password = data.get("password", "")

    if name == "Florian":
        if password != "steroids":
            return jsonify({"error": "Incorrect password"}), 401

    # Check if name is valid
    team = _get_team_names()
    if name not in team and name not in ALL_NAMES:
        return jsonify({"error": "Unknown team member"}), 400

    session["user_name"] = name
    session.permanent = True
    return jsonify({"name": name, "is_admin": name == "Florian"})


@api_v2.route("/auth/logout", methods=["POST"])
def logout():
    session.pop("user_name", None)
    session.pop("manager", None)
    return jsonify({"ok": True})


@api_v2.route("/auth/me")
def me():
    name = session.get("user_name")
    is_manager = bool(session.get("manager"))
    return jsonify({
        "name": name,
        "is_manager": is_manager,
        "is_admin": name == "Florian",
    })


@api_v2.route("/auth/manager", methods=["POST"])
def toggle_manager():
    if session.get("user_name") != "Florian":
        return jsonify({"error": "Unauthorized"}), 403
    data = request.json or {}
    pin = data.get("pin", "")
    if pin != "2026":
        return jsonify({"error": "Wrong PIN"}), 401
    session["manager"] = not session.get("manager", False)
    return jsonify({"is_manager": bool(session.get("manager"))})


# ═══════════════════════════════════════════════════════════════════
#  Schedule
# ═══════════════════════════════════════════════════════════════════

@api_v2.route("/schedule")
def get_schedule():
    """Get all events with assignments."""
    events = Event.query.order_by(Event.date).all()
    today = vancouver_today()

    result = []
    for event in events:
        assignments = []
        for a in event.assignments:
            assignments.append({
                "id": a.id,
                "role": a.role,
                "person": a.person,
                "status": a.status,
                "cover": a.cover,
                "swapped_with": a.swapped_with,
                "history": a.history,
            })

        title = event.custom_title
        if not title:
            if event.day_type == "Friday":
                title = "Bible Study"
            elif event.day_type == "Sunday":
                title = "Sunday Service"
            else:
                title = "Event"

        result.append({
            "date": event.date.isoformat(),
            "day_type": event.day_type,
            "title": title,
            "custom_title": event.custom_title,
            "notes": event.notes,
            "is_past": event.date < today,
            "assignments": assignments,
        })

    return jsonify(result)


@api_v2.route("/schedule/upcoming")
def get_upcoming():
    """Get upcoming events (next 12 weeks)."""
    today = vancouver_today()
    end = today + datetime.timedelta(weeks=12)
    events = Event.query.filter(
        Event.date >= today, Event.date <= end
    ).order_by(Event.date).all()

    return jsonify([_event_to_dict(e) for e in events])


@api_v2.route("/schedule/month/<int:year>/<int:month>")
def get_month(year, month):
    """Get events for a specific month."""
    start = datetime.date(year, month, 1)
    _, num_days = calendar.monthrange(year, month)
    end = datetime.date(year, month, num_days)

    events = Event.query.filter(
        Event.date >= start, Event.date <= end
    ).order_by(Event.date).all()

    return jsonify([_event_to_dict(e) for e in events])


# ═══════════════════════════════════════════════════════════════════
#  Actions (Confirm, Decline, Pickup, Swap)
# ═══════════════════════════════════════════════════════════════════

@api_v2.route("/action", methods=["POST"])
def do_action():
    """Perform an action on an assignment."""
    data = request.json or {}
    action = data.get("action")
    assignment_id = data.get("assignment_id")
    curr = session.get("user_name")
    is_mgr = bool(session.get("manager"))

    if not assignment_id:
        return jsonify({"error": "Missing assignment_id"}), 400

    assignment = Assignment.query.get(assignment_id)
    if not assignment:
        return jsonify({"error": "Assignment not found"}), 404

    event = assignment.event

    def push_history():
        h = assignment.history
        h.append({
            "action": action, "by": curr,
            "prev_status": assignment.status,
            "ts": str(vancouver_now())
        })
        assignment.history = h

    if action == "confirm":
        if not (is_mgr or assignment.person == curr):
            return jsonify({"error": "Unauthorized"}), 403
        assignment.status = "confirmed"
        push_history()
        db.session.commit()

    elif action == "decline":
        if not (is_mgr or assignment.person == curr):
            return jsonify({"error": "Unauthorized"}), 403
        assignment.status = "swap_needed"
        push_history()
        db.session.commit()

        # Send Telegram notification for future events
        if event.date >= vancouver_today():
            try:
                token_str = str(uuid.uuid4())
                pt = PickupToken(token=token_str, assignment_id=assignment.id, person="")
                db.session.add(pt)
                db.session.commit()
                base_url = current_app.config.get('BASE_URL', '').rstrip('/')
                pickup_url = f"{base_url}/pickup/{token_str}" if base_url else None
                tg.send_swap_needed(event, assignment, pickup_url=pickup_url)
            except Exception as e:
                print(f"Telegram error: {e}")

    elif action == "volunteer":
        if assignment.person != "Select Helper":
            return jsonify({"error": "Slot already filled"}), 400
        assignment.person = curr
        assignment.status = "confirmed"
        push_history()
        db.session.commit()

    elif action == "pickup":
        assignment.cover = curr
        assignment.status = "confirmed"
        push_history()
        db.session.commit()
        try:
            tg.send_shift_covered(event, assignment, curr,
                                  original_msg_id=assignment.telegram_message_id)
            assignment.telegram_message_id = None
            db.session.commit()
        except Exception as e:
            print(f"Telegram error: {e}")

    elif action == "undo":
        if assignment.cover:
            assignment.cover = None
            assignment.status = "swap_needed"
        elif assignment.status == "swap_needed":
            assignment.status = "confirmed"
            if assignment.telegram_message_id:
                try:
                    tg.delete_message(None, assignment.telegram_message_id)
                    assignment.telegram_message_id = None
                except Exception:
                    pass
                PickupToken.query.filter_by(
                    assignment_id=assignment.id, used=False
                ).update({"used": True})
        elif assignment.status == "confirmed":
            assignment.status = "pending"
        push_history()
        db.session.commit()

    elif action == "swap":
        offer_date = data.get("offer_date")
        if not offer_date:
            return jsonify({"error": "Missing offer_date"}), 400
        od = datetime.date.fromisoformat(offer_date)
        other_event = Event.query.filter_by(date=od).first()
        if not other_event:
            return jsonify({"error": "No event on offer date"}), 404

        my_assign = None
        for a in other_event.assignments:
            if a.person == curr and a.status in ("confirmed", "pending"):
                my_assign = a
                break
        if not my_assign:
            return jsonify({"error": "No matching assignment to swap"}), 400

        them = assignment.person
        assignment.person = curr
        assignment.swapped_with = them
        assignment.status = "confirmed"
        assignment.cover = None

        my_assign.person = them
        my_assign.swapped_with = curr
        my_assign.status = "confirmed"

        push_history()
        db.session.commit()

    else:
        return jsonify({"error": f"Unknown action: {action}"}), 400

    return jsonify(_assignment_to_dict(assignment))


# ═══════════════════════════════════════════════════════════════════
#  Schedule Generation
# ═══════════════════════════════════════════════════════════════════

@api_v2.route("/generate", methods=["POST"])
def generate():
    """Generate schedule for a month using the v2 fairness algorithm."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    year = data.get("year")
    month = data.get("month")

    if not year or not month:
        return jsonify({"error": "Missing year/month"}), 400

    from .scheduler_v2 import generate_month_v2
    count = generate_month_v2(int(year), int(month))
    return jsonify({"created": count})


@api_v2.route("/generate/year", methods=["POST"])
def generate_year():
    """Generate schedule for remaining months of the year."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    year = data.get("year", vancouver_today().year)
    start_month = data.get("start_month", vancouver_today().month)

    from .scheduler_v2 import generate_month_v2
    results = {}
    for month in range(start_month, 13):
        count = generate_month_v2(int(year), month)
        results[calendar.month_name[month]] = count

    return jsonify(results)


@api_v2.route("/wipe", methods=["POST"])
def wipe_month():
    """Wipe all events for a month."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    year = data.get("year")
    month = data.get("month")
    if not year or not month:
        return jsonify({"error": "Missing year/month"}), 400

    start = datetime.date(int(year), int(month), 1)
    _, num_days = calendar.monthrange(int(year), int(month))
    end = datetime.date(int(year), int(month), num_days)

    events = Event.query.filter(Event.date >= start, Event.date <= end).all()
    count = len(events)
    for e in events:
        db.session.delete(e)
    db.session.commit()

    return jsonify({"deleted": count})


# ═══════════════════════════════════════════════════════════════════
#  Event CRUD
# ═══════════════════════════════════════════════════════════════════

@api_v2.route("/event", methods=["POST"])
def add_event():
    """Add a new event."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    date_str = data.get("date")
    day_type = data.get("day_type", "Sunday")
    custom_title = data.get("custom_title")
    roles = data.get("roles")  # Optional: list of roles to create

    if not date_str:
        return jsonify({"error": "Missing date"}), 400

    d = datetime.date.fromisoformat(date_str)
    if Event.query.filter_by(date=d).first():
        return jsonify({"error": "Event already exists on this date"}), 409

    event = Event(date=d, day_type=day_type, custom_title=custom_title)
    db.session.add(event)
    db.session.flush()

    if roles:
        for role in roles:
            db.session.add(Assignment(event_id=event.id, role=role, person="Select Helper", status="pending"))
    elif day_type == "Sunday":
        for role in ["Computer", "Camera 1", "Camera 2"]:
            db.session.add(Assignment(event_id=event.id, role=role, person="Select Helper", status="pending"))
    elif day_type == "Friday":
        db.session.add(Assignment(event_id=event.id, role="Leader", person="Select Helper", status="pending"))
        db.session.add(Assignment(event_id=event.id, role="Helper", person="Select Helper", status="pending"))

    db.session.commit()
    return jsonify(_event_to_dict(event)), 201


@api_v2.route("/event/<date_str>", methods=["DELETE"])
def delete_event(date_str):
    """Delete an event."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    d = datetime.date.fromisoformat(date_str)
    event = Event.query.filter_by(date=d).first()
    if not event:
        return jsonify({"error": "Not found"}), 404

    db.session.delete(event)
    db.session.commit()
    return jsonify({"ok": True})


@api_v2.route("/event/<date_str>", methods=["PATCH"])
def update_event(date_str):
    """Update event details."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    d = datetime.date.fromisoformat(date_str)
    event = Event.query.filter_by(date=d).first()
    if not event:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    if "custom_title" in data:
        event.custom_title = data["custom_title"] or None
    if "day_type" in data:
        event.day_type = data["day_type"]
    if "notes" in data:
        event.notes = data["notes"]
    if "new_date" in data:
        new_d = datetime.date.fromisoformat(data["new_date"])
        if Event.query.filter_by(date=new_d).first():
            return jsonify({"error": "Event already exists on new date"}), 409
        event.date = new_d

    db.session.commit()
    return jsonify(_event_to_dict(event))


@api_v2.route("/assignment/<int:assignment_id>", methods=["PATCH"])
def update_assignment(assignment_id):
    """Update assignment person (manager or self-assign)."""
    curr = session.get("user_name")
    is_mgr = bool(session.get("manager"))

    assignment = Assignment.query.get(assignment_id)
    if not assignment:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    new_person = data.get("person")

    if new_person:
        if is_mgr or (assignment.person == "Select Helper" and new_person == curr):
            h = assignment.history
            h.append({"from": assignment.person, "to": new_person, "by": curr, "ts": str(vancouver_now())})
            assignment.history = h
            assignment.person = new_person
            assignment.cover = None
            assignment.swapped_with = None
            assignment.status = "confirmed" if assignment.person == "Select Helper" else "pending"
            db.session.commit()
        else:
            return jsonify({"error": "Unauthorized"}), 403

    return jsonify(_assignment_to_dict(assignment))


# ═══════════════════════════════════════════════════════════════════
#  Team Management
# ═══════════════════════════════════════════════════════════════════

@api_v2.route("/team")
def get_team():
    """Get all team members."""
    members = TeamMember.query.order_by(TeamMember.name).all()
    if members:
        return jsonify([m.to_dict() for m in members])

    # Fall back to ROLES_CONFIG if TeamMember table is empty
    result = []
    for name, config in ROLES_CONFIG.items():
        result.append({
            "name": name,
            "sunday_roles": config.get("sunday_roles", []),
            "friday_roles": ["Leader"] if config.get("friday") else [],
            "active": True,
            "active_from": None,
            "telegram_user_id": None,
        })
    return jsonify(sorted(result, key=lambda x: x["name"]))


@api_v2.route("/team", methods=["POST"])
def add_team_member():
    """Add a new team member."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    if TeamMember.query.filter_by(name=name).first():
        return jsonify({"error": f"{name} already exists"}), 409

    member = TeamMember(name=name)
    member.sunday_roles = data.get("sunday_roles", ["Computer", "Camera 1", "Camera 2"])
    member.friday_roles = data.get("friday_roles", ["Leader"])
    member.telegram_user_id = data.get("telegram_user_id")
    member.active = data.get("active", True)

    active_from = data.get("active_from")
    if active_from:
        member.active_from = datetime.date.fromisoformat(active_from)

    db.session.add(member)
    db.session.commit()

    # Also ensure they're in the seed data / roster
    _sync_roster()

    return jsonify(member.to_dict()), 201


@api_v2.route("/team/<int:member_id>", methods=["PATCH"])
def update_team_member(member_id):
    """Update a team member."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    member = TeamMember.query.get(member_id)
    if not member:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    if "name" in data:
        member.name = data["name"]
    if "sunday_roles" in data:
        member.sunday_roles = data["sunday_roles"]
    if "friday_roles" in data:
        member.friday_roles = data["friday_roles"]
    if "telegram_user_id" in data:
        member.telegram_user_id = data["telegram_user_id"]
    if "active" in data:
        member.active = data["active"]
    if "active_from" in data:
        member.active_from = datetime.date.fromisoformat(data["active_from"]) if data["active_from"] else None

    db.session.commit()
    return jsonify(member.to_dict())


@api_v2.route("/team/<int:member_id>", methods=["DELETE"])
def delete_team_member(member_id):
    """Remove a team member."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    member = TeamMember.query.get(member_id)
    if not member:
        return jsonify({"error": "Not found"}), 404

    db.session.delete(member)
    db.session.commit()
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════════
#  Leaderboard / Stats
# ═══════════════════════════════════════════════════════════════════

@api_v2.route("/leaderboard")
def leaderboard():
    """Get leaderboard stats."""
    stats, last_worked = get_history_stats()
    return jsonify({
        "stats": stats,
        "last_worked": {
            name: [d.isoformat() for d in dates]
            for name, dates in last_worked.items()
        }
    })


@api_v2.route("/fairness")
def fairness_report():
    """Get the fairness deficit report."""
    from .scheduler_v2 import get_fairness_report
    return jsonify(get_fairness_report())


# ═══════════════════════════════════════════════════════════════════
#  Availability
# ═══════════════════════════════════════════════════════════════════

@api_v2.route("/availability")
def get_availability():
    curr = session.get("user_name")
    is_mgr = bool(session.get("manager"))

    if is_mgr:
        avails = Availability.query.order_by(Availability.start_date).all()
    else:
        avails = Availability.query.filter_by(person=curr).order_by(Availability.start_date).all()

    return jsonify([a.to_dict() for a in avails])


@api_v2.route("/availability", methods=["POST"])
def add_availability():
    curr = session.get("user_name")
    is_mgr = bool(session.get("manager"))

    data = request.json or {}
    person = data.get("person", curr)
    if not is_mgr and person != curr:
        person = curr

    start = data.get("start_date")
    end = data.get("end_date", start)
    reason = data.get("reason", "")
    pattern = data.get("pattern", "")

    if not start:
        return jsonify({"error": "Missing start_date"}), 400

    avail = Availability(
        person=person,
        start_date=datetime.date.fromisoformat(start),
        end_date=datetime.date.fromisoformat(end) if end else datetime.date.fromisoformat(start),
        reason=reason,
        recurring=bool(pattern),
        pattern=pattern,
    )
    db.session.add(avail)
    db.session.commit()
    return jsonify(avail.to_dict()), 201


@api_v2.route("/availability/<int:avail_id>", methods=["DELETE"])
def delete_availability(avail_id):
    curr = session.get("user_name")
    is_mgr = bool(session.get("manager"))

    avail = Availability.query.get(avail_id)
    if not avail:
        return jsonify({"error": "Not found"}), 404
    if not is_mgr and avail.person != curr:
        return jsonify({"error": "Unauthorized"}), 403

    db.session.delete(avail)
    db.session.commit()
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════════
#  Telegram
# ═══════════════════════════════════════════════════════════════════

@api_v2.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    """Handle incoming Telegram updates (callback queries from inline buttons)."""
    # Verify webhook secret if configured
    if tg.WEBHOOK_SECRET:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if header_secret != tg.WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401

    update = request.json or {}

    # Handle callback queries (inline button presses)
    callback_query = update.get("callback_query")
    if callback_query:
        tg.handle_callback_query(callback_query)
        return jsonify({"ok": True})

    return jsonify({"ok": True})


@api_v2.route("/telegram/test", methods=["POST"])
def telegram_test():
    """Send a test message to the personal chat."""
    data = request.json or {}
    test_type = data.get("type", "reminder")

    if test_type == "monthly":
        msg_id = tg.send_test_monthly()
    else:
        msg_id = tg.send_test_reminder()

    if msg_id:
        return jsonify({"ok": True, "message_id": msg_id})
    return jsonify({"error": "Failed to send"}), 500


@api_v2.route("/telegram/notify", methods=["POST"])
def telegram_notify():
    """Send a notification for a specific event date."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    date_str = data.get("date")
    if not date_str:
        return jsonify({"error": "Missing date"}), 400

    d = datetime.date.fromisoformat(date_str)
    event = Event.query.filter_by(date=d).first()
    if not event:
        return jsonify({"error": "Event not found"}), 404

    msg_id = tg.send_event_reminder(event)
    if msg_id:
        return jsonify({"ok": True, "message_id": msg_id})
    return jsonify({"error": "Failed to send"}), 500


@api_v2.route("/telegram/setup-webhook", methods=["POST"])
def setup_webhook():
    """Set up the Telegram webhook."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    base_url = current_app.config.get('BASE_URL', '').rstrip('/')
    webhook_url = f"{base_url}/api/v2/telegram/webhook"
    result = tg.set_webhook(webhook_url)
    if result:
        return jsonify({"ok": True, "url": webhook_url})
    return jsonify({"error": "Failed to set webhook"}), 500


@api_v2.route("/telegram/connection")
def telegram_status():
    """Check Telegram bot connection status."""
    return jsonify(tg.test_connection())


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _event_to_dict(event):
    """Convert Event to dict for JSON serialization."""
    title = event.custom_title
    if not title:
        if event.day_type == "Friday":
            title = "Bible Study"
        elif event.day_type == "Sunday":
            title = "Sunday Service"
        else:
            title = "Event"

    return {
        "date": event.date.isoformat(),
        "day_type": event.day_type,
        "title": title,
        "custom_title": event.custom_title,
        "notes": event.notes,
        "is_past": event.date < vancouver_today(),
        "assignments": [_assignment_to_dict(a) for a in event.assignments],
    }


def _assignment_to_dict(assignment):
    """Convert Assignment to dict for JSON serialization."""
    return {
        "id": assignment.id,
        "role": assignment.role,
        "person": assignment.person,
        "status": assignment.status,
        "cover": assignment.cover,
        "swapped_with": assignment.swapped_with,
        "history": assignment.history,
    }


def _get_team_names():
    """Get list of team member names from DB or ROLES_CONFIG."""
    members = TeamMember.query.filter_by(active=True).all()
    if members:
        return [m.name for m in members]
    return list(ROLES_CONFIG.keys())


def _sync_roster():
    """Ensure ALL_NAMES and ROLES_CONFIG stay in sync with TeamMember table.
    This is a best-effort sync — the v2 system primarily uses TeamMember."""
    pass  # Roster sync is handled at query time by scheduler_v2.get_roster()
