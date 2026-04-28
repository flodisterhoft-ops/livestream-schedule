"""
REST API v2 blueprint for the Livestream Scheduler.

Provides JSON endpoints for the React frontend.
All endpoints are prefixed with /api/v2/.
"""
import datetime
import calendar
import os
import hashlib
import hmac
import time
from itsdangerous import BadSignature, URLSafeSerializer
from flask import Blueprint, request, jsonify, session, current_app
from .models import Event, Assignment, TeamMember, Availability, SwapRequest, TempChat, EventSuggestion, SchedulingSnapshot, SchedulingPreset
from .extensions import db
from .utils import (
    ALL_NAMES, ROLES_CONFIG, is_available, get_history_stats,
    vancouver_today, vancouver_now, is_real_person
)
from . import telegram_v2 as tg

api_v2 = Blueprint('api_v2', __name__, url_prefix='/api/v2')


def _optional_iso_date(value, field_name):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be YYYY-MM-DD")


def _optional_bool(value, field_name):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"{field_name} must be a boolean")


def _auth_serializer():
    return URLSafeSerializer(current_app.secret_key, salt="v2-auth")


def _auth_response(name):
    session["user_name"] = name
    session["manager"] = False
    session.permanent = True
    token = _auth_serializer().dumps({"name": name, "manager": False})
    return jsonify({
        "name": name,
        "is_admin": name == "Florian",
        "is_manager": bool(session.get("manager")),
        "auth_token": token,
    })


def _validate_telegram_login(data):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    received_hash = data.get("hash", "")
    auth_date = data.get("auth_date", "")
    if not token or not received_hash or not auth_date:
        return False
    try:
        if time.time() - int(auth_date) > 86400:
            return False
    except (TypeError, ValueError):
        return False
    pairs = []
    for key, value in data.items():
        if key != "hash" and value not in (None, ""):
            pairs.append(f"{key}={value}")
    check_string = "\n".join(sorted(pairs))
    secret_key = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_hash)


def _team_member_from_telegram(data):
    tg_id = str(data.get("id", "")).strip()
    first_name = (data.get("first_name") or "").strip()
    if tg_id:
        member = TeamMember.query.filter_by(telegram_user_id=tg_id).first()
        if member:
            return member
    if first_name:
        member = TeamMember.query.filter(db.func.lower(TeamMember.name) == first_name.lower()).first()
        if member and tg_id and not member.telegram_user_id:
            member.telegram_user_id = tg_id
            db.session.commit()
        return member
    return None


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

    return _auth_response(name)


@api_v2.route("/auth/token-login", methods=["POST"])
def token_login():
    data = request.json or {}
    token = data.get("token", "")
    if not token:
        return jsonify({"error": "Missing token"}), 400
    try:
        payload = _auth_serializer().loads(token)
    except BadSignature:
        return jsonify({"error": "Invalid token"}), 401

    name = payload.get("name")
    if not name:
        return jsonify({"error": "Invalid token"}), 401

    team = _get_team_names()
    if name not in team and name not in ALL_NAMES:
        return jsonify({"error": "Unknown team member"}), 400

    return _auth_response(name)


@api_v2.route("/auth/telegram-login", methods=["POST"])
def telegram_login():
    data = request.json or {}
    if not _validate_telegram_login(data):
        return jsonify({"error": "Invalid Telegram login"}), 401
    member = _team_member_from_telegram(data)
    if not member:
        return jsonify({"error": "Telegram user is not linked to the team"}), 403
    return _auth_response(member.name)


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
                "locked": bool(getattr(a, "locked", False)),
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
        if not (is_mgr or assignment.person == curr or assignment.cover == curr):
            return jsonify({"error": "Unauthorized"}), 403
        assignment.status = "confirmed"
        push_history()
        db.session.commit()

    elif action == "decline":
        if not (is_mgr or assignment.person == curr or assignment.cover == curr):
            return jsonify({"error": "Unauthorized"}), 403
        assignment.status = "swap_needed"
        push_history()

        swap = None
        if event.date >= vancouver_today():
            deadline_local = tg._swap_deadline(event)
            deadline_utc = deadline_local.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            swap = SwapRequest.query.filter_by(
                assignment_id=assignment.id, status="active"
            ).first()
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

        db.session.commit()

        if event.date >= vancouver_today():
            try:
                tg._notify_admin_text(f"❌ {assignment.person} can't make it\n{tg._event_title(event)} · {assignment.role}")
                tg.refresh_event_telegram(event)
                if swap:
                    tg.send_swap_request_temp_groups(assignment, swap)
            except Exception as e:
                print(f"Telegram error: {e}")

    elif action == "volunteer":
        if not curr:
            return jsonify({"error": "Unauthorized"}), 403
        if assignment.person != "Select Helper":
            return jsonify({"error": "Slot already filled"}), 400
        assignment.person = curr
        assignment.status = "confirmed"
        push_history()
        db.session.commit()

    elif action == "pickup":
        if not curr or assignment.person == curr or assignment.cover == curr:
            return jsonify({"error": "Unauthorized"}), 403
        if assignment.status != "swap_needed":
            return jsonify({"error": "Shift is not available for pickup"}), 400
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
        if not (is_mgr or assignment.person == curr or assignment.cover == curr):
            return jsonify({"error": "Unauthorized"}), 403
        if assignment.cover:
            assignment.cover = None
            assignment.status = "swap_needed"
        elif assignment.status == "swap_needed":
            assignment.status = "confirmed"
            active_swap = SwapRequest.query.filter_by(
                assignment_id=assignment.id, status="active"
            ).first()
            if active_swap:
                active_swap.status = "cancelled"
                for temp_chat in TempChat.query.filter_by(
                    swap_request_id=active_swap.id, status="active"
                ).all():
                    try:
                        tg._delete_temp_chat(temp_chat)
                    except Exception as e:
                        print(f"Temp chat cleanup error: {e}")
            if assignment.telegram_message_id:
                try:
                    tg.delete_message(None, assignment.telegram_message_id)
                    assignment.telegram_message_id = None
                except Exception:
                    pass
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


@api_v2.route("/generate/range", methods=["POST"])
def generate_range():
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    start_year = int(data.get("start_year", vancouver_today().year))
    start_month = int(data.get("start_month", vancouver_today().month))
    years = int(data.get("years", 10))

    if years < 1 or years > 20:
        return jsonify({"error": "years must be between 1 and 20"}), 400
    if start_month < 1 or start_month > 12:
        return jsonify({"error": "start_month must be between 1 and 12"}), 400

    from .scheduler_v2 import generate_month_v2

    results = {}
    year = start_year
    month = start_month
    for _ in range(years * 12):
        count = generate_month_v2(year, month)
        results[f"{year}-{month:02d}"] = count
        month += 1
        if month > 12:
            month = 1
            year += 1

    return jsonify({"months": results, "created": sum(results.values())})


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
    suggestion_id = data.get("suggestion_id")

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
        db.session.add(Assignment(event_id=event.id, role="Computer", person="Select Helper", status="pending"))
        db.session.add(Assignment(event_id=event.id, role="Camera", person="Select Helper", status="pending"))

    db.session.commit()

    if suggestion_id:
        try:
            suggestion = EventSuggestion.query.get(int(suggestion_id))
            if suggestion and suggestion.status == "pending":
                suggestion.status = "accepted"
                suggestion.accepted_event_date = event.date
                db.session.commit()
        except Exception as e:
            print(f"[suggestions] mark-accepted failed: {e}")

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
        existing = Event.query.filter_by(date=new_d).first()
        if existing and existing.id != event.id:
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
            assignment.status = "pending"
            db.session.commit()
        else:
            return jsonify({"error": "Unauthorized"}), 403

    return jsonify(_assignment_to_dict(assignment))


@api_v2.route("/assignment/<int:assignment_id>/lock", methods=["POST"])
def toggle_assignment_lock(assignment_id):
    """Manager pin: lock or unlock a single assignment from automated rebalancing."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    assignment = Assignment.query.get(assignment_id)
    if not assignment:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    try:
        new_value = _optional_bool(data.get("locked"), "locked")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if new_value is None:
        new_value = not bool(getattr(assignment, "locked", False))
    assignment.locked = bool(new_value)
    db.session.commit()
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
            "friday_roles": ["Computer", "Camera"] if config.get("friday") else [],
            "role_preferences": {},
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
    member.friday_roles = data.get("friday_roles", ["Computer", "Camera"])
    member.role_preferences = data.get("role_preferences", {})
    member.telegram_user_id = data.get("telegram_user_id")
    member.active = data.get("active", True)

    active_from = data.get("active_from")
    member.active_from = datetime.date.fromisoformat(active_from) if active_from else vancouver_today()

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
    if "role_preferences" in data:
        member.role_preferences = data["role_preferences"]
    if "telegram_user_id" in data:
        member.telegram_user_id = data["telegram_user_id"]
    if "active" in data:
        member.active = data["active"]
    if "active_from" in data:
        member.active_from = datetime.date.fromisoformat(data["active_from"]) if data["active_from"] else None

    db.session.commit()
    return jsonify(member.to_dict())


@api_v2.route("/team/apply-role-settings", methods=["POST"])
def apply_team_role_settings():
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    members = data.get("members", [])
    removed_ids = set(data.get("removed_ids", []))
    if not isinstance(members, list):
        return jsonify({"error": "members must be a list"}), 400

    created = []
    updated = 0
    removed = []
    preference_or_cap_changed = False

    try:
        existing = {
            member.id: member
            for member in TeamMember.query.all()
        }
        existing_by_name = {
            member.name.lower(): member
            for member in TeamMember.query.all()
        }

        for member_id in removed_ids:
            member = existing.get(int(member_id))
            if member:
                removed.append(member.name)
                db.session.delete(member)

        for item in members:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            item_id = item.get("id")
            sunday_roles = item.get("sunday_roles", [])
            friday_roles = item.get("friday_roles", [])
            role_preferences = item.get("role_preferences", {})
            caps = item.get("caps")
            if isinstance(caps, dict):
                role_preferences = {**role_preferences, "_caps": caps}

            member = None
            if item_id and not str(item_id).startswith("new-"):
                member = existing.get(int(item_id))
            if not member:
                member = existing_by_name.get(name.lower())
            if member and member.id in removed_ids:
                continue

            if member:
                if member.role_preferences != role_preferences:
                    preference_or_cap_changed = True
                member.name = name
                member.sunday_roles = sunday_roles
                member.friday_roles = friday_roles
                member.role_preferences = role_preferences
                member.active = item.get("active", True)
                if "telegram_user_id" in item:
                    member.telegram_user_id = item.get("telegram_user_id")
                if "active_from" in item:
                    member.active_from = datetime.date.fromisoformat(item["active_from"]) if item.get("active_from") else None
                updated += 1
            else:
                member = TeamMember(name=name)
                member.sunday_roles = sunday_roles
                member.friday_roles = friday_roles
                member.role_preferences = role_preferences
                member.telegram_user_id = item.get("telegram_user_id")
                member.active = item.get("active", True)
                active_from = item.get("active_from")
                member.active_from = datetime.date.fromisoformat(active_from) if active_from else vancouver_today()
                db.session.add(member)
                created.append(name)

        db.session.flush()

        from .scheduler_v2 import repair_future_assignments_for_roster
        repair_result = repair_future_assignments_for_roster(
            start_date=vancouver_today(),
            refill_pending=bool(created or preference_or_cap_changed),
        )
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "ok": True,
        "created": created,
        "updated": updated,
        "removed": removed,
        **repair_result,
    })


@api_v2.route("/team/<int:member_id>", methods=["DELETE"])
def delete_team_member(member_id):
    """Remove a team member."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    member = TeamMember.query.get(member_id)
    if not member:
        return jsonify({"error": "Not found"}), 404

    from .scheduler_v2 import rebalance_future_after_member_removal
    rebalance_result = rebalance_future_after_member_removal(member.name, vancouver_today())
    db.session.delete(member)
    db.session.commit()
    return jsonify({"ok": True, **rebalance_result})


SNAPSHOT_RETENTION = 50


@api_v2.route("/scheduling-controls/apply", methods=["POST"])
def apply_scheduling_controls():
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    targets = data.get("targets", {})

    try:
        end_date = _optional_iso_date(data.get("end_date"), "end_date")
        from .scheduler_v2 import rebalance_future_to_targets
        result = rebalance_future_to_targets(
            targets,
            start_date=vancouver_today(),
            end_date=end_date,
            lock_confirmed=True,
        )
        snapshot = result.pop("snapshot", [])
        snapshot_record = None
        if snapshot:
            snap = SchedulingSnapshot(
                created_by=session.get("user_name") or "manager",
                label=data.get("label") or "scheduling-controls",
            )
            snap.snapshot = snapshot
            db.session.add(snap)
            db.session.flush()
            snapshot_record = snap.to_dict()

            # Prune older snapshots to stay under retention.
            stale = (
                SchedulingSnapshot.query
                .order_by(SchedulingSnapshot.created_at.desc())
                .offset(SNAPSHOT_RETENTION)
                .all()
            )
            for old in stale:
                db.session.delete(old)
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "snapshot": snapshot_record, **result})


@api_v2.route("/scheduling-controls/preview", methods=["POST"])
def preview_scheduling_controls():
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    targets = data.get("targets", {})

    try:
        end_date = _optional_iso_date(data.get("end_date"), "end_date")
        from .scheduler_v2 import preview_future_targets
        preview = preview_future_targets(
            targets,
            start_date=vancouver_today(),
            end_date=end_date,
            lock_confirmed=True,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, **preview})


@api_v2.route("/scheduling-controls/refresh-reminders", methods=["POST"])
def refresh_scheduling_reminders():
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    try:
        sent = tg.send_daily_reminders_v2()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "sent": sent})


@api_v2.route("/scheduling-controls/presets")
def list_scheduling_presets():
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403
    presets = SchedulingPreset.query.order_by(SchedulingPreset.name).all()
    return jsonify([p.to_dict() for p in presets])


@api_v2.route("/scheduling-controls/presets", methods=["POST"])
def create_scheduling_preset():
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403
    data = request.json or {}
    name = (data.get("name") or "").strip()
    targets = data.get("targets") or {}
    if not name:
        return jsonify({"error": "Name is required"}), 400

    preset = SchedulingPreset.query.filter(db.func.lower(SchedulingPreset.name) == name.lower()).first()
    if preset is None:
        preset = SchedulingPreset(name=name)
        db.session.add(preset)
    preset.targets = targets
    preset.created_by = session.get("user_name") or "manager"
    db.session.commit()
    return jsonify(preset.to_dict()), 201


@api_v2.route("/scheduling-controls/presets/<int:preset_id>", methods=["DELETE"])
def delete_scheduling_preset(preset_id):
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403
    preset = SchedulingPreset.query.get(preset_id)
    if not preset:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(preset)
    db.session.commit()
    return jsonify({"ok": True})


@api_v2.route("/scheduling-controls/snapshots")
def list_scheduling_snapshots():
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403
    snaps = SchedulingSnapshot.query.order_by(
        SchedulingSnapshot.created_at.desc()
    ).limit(20).all()
    return jsonify([s.to_dict() for s in snaps])


@api_v2.route("/scheduling-controls/undo", methods=["POST"])
def undo_scheduling_controls():
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    snapshot_id = data.get("snapshot_id")
    if snapshot_id is not None:
        try:
            snapshot_id = int(snapshot_id)
        except (TypeError, ValueError):
            return jsonify({"error": "snapshot_id must be an integer"}), 400
    snap = (
        SchedulingSnapshot.query.get(snapshot_id)
        if snapshot_id is not None
        else SchedulingSnapshot.query.order_by(SchedulingSnapshot.created_at.desc()).first()
    )
    if not snap:
        return jsonify({"error": "No snapshot to undo"}), 404

    restored = 0
    for item in snap.snapshot:
        assignment = Assignment.query.get(item.get("id"))
        if not assignment:
            continue
        assignment.person = item.get("person")
        assignment.cover = item.get("cover")
        assignment.status = item.get("status") or "pending"
        assignment.swapped_with = item.get("swapped_with")
        if "locked" in item:
            assignment.locked = bool(item.get("locked"))
        assignment.telegram_message_id = None
        restored += 1

    db.session.delete(snap)
    db.session.commit()
    return jsonify({"ok": True, "restored": restored})


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


@api_v2.route("/telegram/refresh-event-message", methods=["POST"])
def telegram_refresh_event_message():
    """Refresh an existing Telegram event message (buttons + statuses) for an event."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403

    data = request.json or {}
    date_str = data.get("date")
    if date_str:
        try:
            d = datetime.date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"error": "Invalid date"}), 400
        event = Event.query.filter_by(date=d).first()
        if not event:
            return jsonify({"error": "Event not found"}), 404
    else:
        event = (
            Event.query
            .filter(Event.telegram_message_id.isnot(None))
            .order_by(Event.date.desc())
            .first()
        )
        if not event:
            return jsonify({"error": "No event message found to refresh"}), 404

    tg.refresh_event_telegram(event)
    return jsonify({"ok": True, "date": event.date.isoformat(), "message_id": event.telegram_message_id})


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
#  Event Suggestions (public)
# ═══════════════════════════════════════════════════════════════════

SUGGESTION_TYPES = {"Baptism", "Thanksgiving", "Samaritan Aid Mission Conference", "Other"}


@api_v2.route("/suggestions", methods=["POST"])
def create_suggestion():
    """Anyone can submit a suggestion for a new event."""
    data = request.json or {}
    name = (session.get("user_name") or data.get("suggester_name") or "").strip()
    event_type = (data.get("event_type") or "").strip()
    custom_title = (data.get("custom_title") or "").strip() or None
    date_str = (data.get("date") or "").strip()
    time_str = (data.get("time") or "").strip() or None
    notes = (data.get("notes") or "").strip() or None

    if not name:
        return jsonify({"error": "Please enter your name"}), 400
    if event_type not in SUGGESTION_TYPES:
        return jsonify({"error": "Invalid event type"}), 400
    if event_type == "Other" and not custom_title:
        return jsonify({"error": "Please enter a title"}), 400
    if not date_str:
        return jsonify({"error": "Please pick a date"}), 400
    try:
        d = datetime.date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Invalid date"}), 400
    if d < vancouver_today():
        return jsonify({"error": "Date must be in the future"}), 400

    suggestion = EventSuggestion(
        suggester_name=name[:100],
        event_type=event_type,
        custom_title=custom_title[:120] if custom_title else None,
        date=d,
        time=time_str[:8] if time_str else None,
        notes=notes,
        status="pending",
    )
    db.session.add(suggestion)
    db.session.commit()

    try:
        tg.send_suggestion_alert(suggestion)
    except Exception as e:
        print(f"[Telegram] suggestion alert error: {e}")

    return jsonify(suggestion.to_dict()), 201


@api_v2.route("/suggestions/<int:suggestion_id>")
def get_suggestion(suggestion_id):
    """Manager fetches a suggestion to prefill the create-event form."""
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403
    suggestion = EventSuggestion.query.get(suggestion_id)
    if not suggestion:
        return jsonify({"error": "Not found"}), 404
    return jsonify(suggestion.to_dict())


@api_v2.route("/suggestions/<int:suggestion_id>/dismiss", methods=["POST"])
def dismiss_suggestion(suggestion_id):
    if not session.get("manager"):
        return jsonify({"error": "Manager only"}), 403
    suggestion = EventSuggestion.query.get(suggestion_id)
    if not suggestion:
        return jsonify({"error": "Not found"}), 404
    suggestion.status = "dismissed"
    db.session.commit()
    return jsonify(suggestion.to_dict())


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
        "locked": bool(getattr(assignment, "locked", False)),
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
