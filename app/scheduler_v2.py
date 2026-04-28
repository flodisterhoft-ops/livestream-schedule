"""
Fairness-deficit scheduling algorithm for the Livestream Scheduler v2.

Inspired by the Young Couples Scheduler's fairness system, adapted for
multi-role assignments (Computer, Camera 1, Camera 2, Leader, Helper).

Key principles:
1. Per-role expected-vs-assigned tracking (fairness deficit)
2. Selection priority: deficit → days since last work → lifetime count
3. Respect availability blackouts and active-from dates
4. Enforce monthly caps and minimum gap rules
5. Overall workload balancing across all roles
"""
import calendar
import datetime
from .models import Event, Assignment, TeamMember, Availability
from .extensions import db
from .utils import vancouver_today, is_available

# ── Caps & constraints ──────────────────────────────────────────────
SUNDAY_CAP_PER_MONTH = 2          # Max Sunday assignments per person per month
FRIDAY_LEADER_CAP_PER_MONTH = 2   # Max Friday leader assignments per month
MONTH_TOTAL_CAP = 4               # Max total assignments per person per month
FLORIAN_MONTH_TOTAL_CAP = 3
SUNDAY_MIN_GAP_DAYS = 8           # Min days between consecutive Sundays
SERVICE_MIN_GAP_DAYS = 0
FRIDAY_MIN_GAP_DAYS = 0


# ── Team configuration (default, used when TeamMember table is empty) ────
DEFAULT_ROSTER = {
    "Florian": {"sunday_roles": ["Computer"], "friday_roles": ["Computer"]},
    "Andy":    {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday_roles": ["Computer", "Camera"]},
    "Marvin":  {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday_roles": ["Computer", "Camera"]},
    "Patric":  {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday_roles": ["Computer", "Camera"]},
    "Rene":    {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday_roles": ["Computer", "Camera"]},
    "Stefan":  {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday_roles": ["Computer", "Camera"]},
    "Viktor":  {"sunday_roles": ["Camera 2"], "friday_roles": ["Camera"]},
}

# Florian-specific caps
FLORIAN_SUNDAY_CAP = 1
FLORIAN_FRIDAY_CAP = 2   # Max 2 Fridays/month with a gap between assignments

ROLE_PREFERENCE_WEIGHTS = {
    "less": 1.0,
    "normal": 1.5,
    "more": 2.0,
}

DEFAULT_ROLE_PREFERENCES = {
    "Florian": {"Sunday:Computer": "more", "Friday:Computer": "more"},
    "Andy": {"Sunday:Computer": "less", "Sunday:Camera 1": "more", "Sunday:Camera 2": "normal"},
    "Marvin": {"Sunday:Computer": "more", "Sunday:Camera 1": "less", "Sunday:Camera 2": "less", "Friday:Computer": "more"},
    "Patric": {"Sunday:Computer": "less", "Sunday:Camera 1": "more", "Sunday:Camera 2": "normal", "Friday:Computer": "less", "Friday:Camera": "more"},
    "Rene": {"Sunday:Computer": "more", "Sunday:Camera 1": "less", "Sunday:Camera 2": "less", "Friday:Computer": "more"},
    "Stefan": {"Sunday:Computer": "more", "Sunday:Camera 1": "less", "Sunday:Camera 2": "less", "Friday:Computer": "more"},
    "Viktor": {"Sunday:Camera 2": "more", "Friday:Camera": "normal"},
}

EXTRA_SHIFT_PRIORITY = {
    "Rene": 0,
    "Stefan": 1,
    "Marvin": 2,
}


def _default_friday_roles(name, roles):
    if "Computer" in roles or "Camera" in roles:
        return roles
    if "Leader" not in roles and "Helper" not in roles:
        return roles
    if name == "Florian":
        return ["Computer"]
    if name == "Viktor":
        return ["Camera"]
    return ["Computer", "Camera"]


def _default_role_preferences(name, preferences):
    merged = dict(DEFAULT_ROLE_PREFERENCES.get(name, {}))
    merged.update(preferences or {})
    return merged


def _tracking_role(day_type, role):
    if day_type == "Friday":
        if role in ("Leader", "Computer"):
            return "Friday:Computer"
        if role in ("Helper", "Camera"):
            return "Friday:Camera"
    return f"{day_type}:{role}"


def _week_of_month(date_obj):
    return (date_obj.day - 1) // 7 + 1


def _role_weight(name, day_type, role, roster):
    preferences = roster.get(name, {}).get("role_preferences", {})
    level = preferences.get(_tracking_role(day_type, role), preferences.get(role, "normal"))
    return ROLE_PREFERENCE_WEIGHTS.get(level, ROLE_PREFERENCE_WEIGHTS["normal"])


def _month_slot_count(year, month):
    days = calendar.monthrange(year, month)[1]
    total = 0
    for day in range(1, days + 1):
        weekday = datetime.date(year, month, day).weekday()
        if weekday == 6:
            total += 3
        elif weekday == 4:
            total += 2
    return total


def _monthly_total_target(name, date_obj, roster):
    days = calendar.monthrange(date_obj.year, date_obj.month)[1]
    month_end = datetime.date(date_obj.year, date_obj.month, days)
    active_names = [
        person
        for person, config in roster.items()
        if (config.get("sunday_roles") or config.get("friday_roles"))
        and _person_is_active(person, month_end, roster)
    ]
    if not active_names:
        return 0.0
    total_slots = _month_slot_count(date_obj.year, date_obj.month)
    if "Florian" in active_names:
        florian_target = min(2.0, float(total_slots))
        if name == "Florian":
            return florian_target
        remaining = [person for person in active_names if person != "Florian"]
        if remaining:
            return (total_slots - florian_target) / len(remaining)
    return total_slots / len(active_names)


def _extra_shift_rank(name):
    return EXTRA_SHIFT_PRIORITY.get(name, len(EXTRA_SHIFT_PRIORITY))


def get_roster():
    """Get team roster from DB (TeamMember table) or fall back to defaults."""
    members = TeamMember.query.filter_by(active=True).all()
    if members:
        roster = {}
        for m in members:
            roster[m.name] = {
                "sunday_roles": m.sunday_roles,
                "friday_roles": _default_friday_roles(m.name, m.friday_roles),
                "role_preferences": _default_role_preferences(m.name, m.role_preferences),
                "active_from": m.active_from,
            }
        return roster
    return {
        name: {
            **config,
            "role_preferences": _default_role_preferences(name, config.get("role_preferences", {})),
        }
        for name, config in DEFAULT_ROSTER.items()
    }


def _get_role_pool(roster, role, day_type=None):
    """Get list of people eligible for a specific role."""
    pool = []
    for name, config in roster.items():
        sunday_roles = config.get("sunday_roles", [])
        friday_roles = config.get("friday_roles", [])
        if day_type == "Sunday" and role in sunday_roles:
            pool.append(name)
        elif day_type == "Friday" and role in friday_roles:
            pool.append(name)
        elif day_type is None and (role in sunday_roles or role in friday_roles):
            pool.append(name)
    return pool


def _person_is_active(name, date_obj, roster):
    """Check if person is active on a given date (active_from check)."""
    config = roster.get(name, {})
    active_from = config.get("active_from")
    if active_from and isinstance(active_from, datetime.date) and date_obj < active_from:
        return False
    return True


def _build_history(roster, end_before=None):
    """
    Scan existing events to build per-role and overall assignment tracking.

    Returns:
        role_tracking: {role: {person: {'assigned': int, 'expected': float}}}
        overall: {person: {'total': int, 'last_date': date|None, 'lifetime': int,
                           'month_counts': {(year,month): {'sun': int, 'fri': int, 'total': int}},
                           'last_sun_date': date|None}}
    """
    all_names = list(roster.keys())

    # Per-role tracking
    role_tracking = {}
    for day_type, role in [
        ("Sunday", "Computer"),
        ("Sunday", "Camera 1"),
        ("Sunday", "Camera 2"),
        ("Friday", "Computer"),
        ("Friday", "Camera"),
    ]:
        tracking_key = _tracking_role(day_type, role)
        pool = _get_role_pool(roster, role, day_type=day_type)
        role_tracking[tracking_key] = {
            name: {"assigned": 0, "expected": 0.0} for name in pool
        }

    # Overall tracking
    overall = {
        name: {
            "total": 0,
            "last_date": None,
            "lifetime": 0,
            "month_counts": {},
            "last_sun_date": None,
            "last_fri_date": None,
            "last_service_date": None,
        }
        for name in all_names
    }

    query = Event.query
    if end_before is not None:
        query = query.filter(Event.date < end_before)
    events = query.order_by(Event.date).all()

    for event in events:
        d = event.date
        is_sunday = event.day_type == "Sunday" or d.weekday() == 6
        is_friday = event.day_type == "Friday" or d.weekday() == 4
        month_key = (d.year, d.month)

        # Determine which roles were needed for this event
        roles_in_event = set()
        for a in event.assignments:
            roles_in_event.add(a.role)

        # Update per-role expected for all eligible available people
        for role in roles_in_event:
            event_day_type = "Friday" if is_friday else "Sunday" if is_sunday else event.day_type
            tracking_key = _tracking_role(event_day_type, role)
            if tracking_key not in role_tracking:
                continue
            pool = [n for n in role_tracking[tracking_key] if is_available(n, d) and _person_is_active(n, d, roster)]
            if pool:
                fair_share = 1.0 / len(pool)
                for n in pool:
                    role_tracking[tracking_key][n]["expected"] += fair_share

        # Update per-role assigned and overall for actual assignments
        for a in event.assignments:
            worker = a.cover if a.cover else a.person
            if not worker or worker in ("TBD", "Select Helper"):
                continue

            # Per-role assigned
            event_day_type = "Friday" if is_friday else "Sunday" if is_sunday else event.day_type
            tracking_key = _tracking_role(event_day_type, a.role)
            if tracking_key in role_tracking and worker in role_tracking[tracking_key]:
                role_tracking[tracking_key][worker]["assigned"] += 1

            # Overall
            if worker in overall:
                overall[worker]["total"] += 1
                overall[worker]["lifetime"] += 1
                overall[worker]["last_date"] = d
                overall[worker]["last_service_date"] = d

                if is_sunday:
                    overall[worker]["last_sun_date"] = d

                if month_key not in overall[worker]["month_counts"]:
                    overall[worker]["month_counts"][month_key] = {"sun": 0, "fri": 0, "total": 0}
                overall[worker]["month_counts"][month_key]["total"] += 1
                if is_sunday:
                    overall[worker]["month_counts"][month_key]["sun"] += 1
                if is_friday:
                    overall[worker]["month_counts"][month_key]["fri"] += 1
                    overall[worker]["last_fri_date"] = d

    return role_tracking, overall


def _schedule_priority(name, role, day_type, role_tracking, overall, roster, date_obj):
    """
    Calculate scheduling priority for a person.
    LOWER score = HIGHER priority (gets assigned first).

    Tier 1: Fairness deficit (assigned - expected) — most important
    Tier 2: Days since last worked (more days = lower score = higher priority)
    Tier 3: Lifetime count (fewer = higher priority)
    Tier 4: Overall total this scheduling period (fewer = higher priority)
    """
    tracking_key = _tracking_role(day_type, role)
    rt = role_tracking.get(tracking_key, {}).get(name, {"assigned": 0, "expected": 0.0})
    ov = overall.get(name, {"total": 0, "last_date": None, "lifetime": 0})

    weight = _role_weight(name, day_type, role, roster)
    deficit = round(rt["assigned"] - (rt["expected"] * weight), 8)

    if name == "Florian" and day_type in ("Sunday", "Friday") and role == "Computer":
        deficit -= 8.0

    last_date = ov.get("last_date")
    if last_date:
        days_since = (date_obj - last_date).days
    else:
        days_since = 9999  # Never worked = highest priority

    return (
        round(_month_counts(name, date_obj.year, date_obj.month, overall)["total"] - _monthly_total_target(name, date_obj, roster), 8),
        _extra_shift_rank(name),
        deficit,                    # Primary: fairness deficit
        -days_since,                # Secondary: prefer longer gap (negative = lower)
        _week_of_month(date_obj),   # Tertiary: rotate season/week placement over years
        ov.get("lifetime", 0),      # Tertiary: fewer lifetime assignments
        ov.get("total", 0),         # Quaternary: fewer total assignments
    )


def _month_counts(name, year, month, overall):
    """Get month-specific counts for a person."""
    ov = overall.get(name, {})
    mc = ov.get("month_counts", {}).get((year, month), {"sun": 0, "fri": 0, "total": 0})
    return mc


def _cap_value(caps, key, fallback):
    try:
        value = int(caps.get(key, fallback))
    except (TypeError, ValueError):
        value = fallback
    return max(0, value)


def _person_month_caps(name, roster):
    preferences = roster.get(name, {}).get("role_preferences", {})
    caps = preferences.get("_caps") if isinstance(preferences, dict) else None
    if not isinstance(caps, dict):
        caps = {}
    return {
        "custom": bool(caps),
        "total": _cap_value(caps, "total_per_month", FLORIAN_MONTH_TOTAL_CAP if name == "Florian" else MONTH_TOTAL_CAP),
        "sun": _cap_value(caps, "sunday_per_month", FLORIAN_SUNDAY_CAP if name == "Florian" else SUNDAY_CAP_PER_MONTH),
        "fri": _cap_value(caps, "friday_per_month", FLORIAN_FRIDAY_CAP if name == "Florian" else FRIDAY_LEADER_CAP_PER_MONTH),
    }

def _check_caps(name, date_obj, day_type, overall, roster):
    """Check if person is within monthly caps."""
    mc = _month_counts(name, date_obj.year, date_obj.month, overall)
    caps = _person_month_caps(name, roster)

    if mc["total"] >= caps["total"]:
        return False

    if day_type == "Sunday" and mc["sun"] >= caps["sun"]:
        return False

    if day_type == "Friday" and mc["fri"] >= caps["fri"]:
        return False

    return True


def _check_sunday_gap(name, date_obj, overall):
    """Check minimum gap between consecutive Sundays."""
    ov = overall.get(name, {})
    last_sun = ov.get("last_sun_date")
    if last_sun and (date_obj - last_sun).days < SUNDAY_MIN_GAP_DAYS:
        return False
    return True


def _check_friday_gap(name, date_obj, overall):
    if FRIDAY_MIN_GAP_DAYS <= 0:
        return True
    ov = overall.get(name, {})
    last_fri = ov.get("last_fri_date")
    if last_fri and (date_obj - last_fri).days < FRIDAY_MIN_GAP_DAYS:
        return False
    return True


def _check_service_gap(name, date_obj, overall):
    if SERVICE_MIN_GAP_DAYS <= 0:
        return True
    ov = overall.get(name, {})
    last_service = ov.get("last_service_date") or ov.get("last_date")
    if last_service and (date_obj - last_service).days < SERVICE_MIN_GAP_DAYS:
        return False
    return True


def _check_strict_person_caps(name, date_obj, day_type, overall, roster):
    caps = _person_month_caps(name, roster)
    if name != "Florian" and not caps["custom"]:
        return True
    mc = _month_counts(name, date_obj.year, date_obj.month, overall)
    if mc["total"] >= caps["total"]:
        return False
    if day_type == "Sunday" and mc["sun"] >= caps["sun"]:
        return False
    if day_type == "Friday" and mc["fri"] >= caps["fri"]:
        return False
    return True


def _select_best(pool, role, date_obj, day_type, role_tracking, overall, roster, exclude):
    """
    Select the best candidate from a pool using the fairness-deficit algorithm.

    1. Filter by availability, active status, not excluded
    2. Filter by caps and gap rules
    3. Sort by priority (deficit → days since → lifetime → total)
    4. Return the best candidate
    """
    # Step 1: Basic filtering
    valid = [
        p for p in pool
        if p not in exclude
        and is_available(p, date_obj)
        and _person_is_active(p, date_obj, roster)
        and _check_service_gap(p, date_obj, overall)
        and (day_type != "Sunday" or _check_sunday_gap(p, date_obj, overall))
        and (day_type != "Friday" or _check_friday_gap(p, date_obj, overall))
    ]

    if not valid:
        return "TBD"

    # Step 2: Apply caps and gap rules
    constrained = [
        p for p in valid
        if _check_caps(p, date_obj, day_type, overall, roster)
    ]

    # Step 3: Relax gap rule if nobody passes
    if not constrained and day_type == "Sunday":
        constrained = [
            p for p in valid
            if _check_strict_person_caps(p, date_obj, day_type, overall, roster)
        ]

    # Step 4: If still nobody, relax all caps
    if not constrained:
        constrained = [
            p for p in pool
            if p not in exclude
            and is_available(p, date_obj)
            and _person_is_active(p, date_obj, roster)
            and _check_service_gap(p, date_obj, overall)
            and (day_type != "Sunday" or _check_sunday_gap(p, date_obj, overall))
            and (day_type != "Friday" or _check_friday_gap(p, date_obj, overall))
            and _check_strict_person_caps(p, date_obj, day_type, overall, roster)
        ]
    if not constrained:
        return "TBD"

    # Step 5: Sort by priority and pick best
    constrained.sort(key=lambda p: _schedule_priority(p, role, day_type, role_tracking, overall, roster, date_obj))

    return constrained[0]


def _select_relaxed(pool, role, date_obj, day_type, role_tracking, overall, roster, exclude):
    candidates = [
        p for p in pool
        if p not in exclude
        and is_available(p, date_obj)
        and _person_is_active(p, date_obj, roster)
        and _check_service_gap(p, date_obj, overall)
        and (day_type != "Sunday" or _check_sunday_gap(p, date_obj, overall))
        and (day_type != "Friday" or _check_friday_gap(p, date_obj, overall))
    ]
    if not candidates:
        return "TBD"
    candidates.sort(key=lambda p: _schedule_priority(p, role, day_type, role_tracking, overall, roster, date_obj))
    return candidates[0]


def _record_assignment(name, role, date_obj, day_type, role_tracking, overall):
    """Record an assignment in the tracking data structures."""
    # Per-role assigned
    tracking_key = _tracking_role(day_type, role)
    if tracking_key in role_tracking and name in role_tracking[tracking_key]:
        role_tracking[tracking_key][name]["assigned"] += 1

    # Overall
    if name in overall:
        overall[name]["total"] += 1
        overall[name]["lifetime"] += 1
        overall[name]["last_date"] = date_obj
        overall[name]["last_service_date"] = date_obj

        month_key = (date_obj.year, date_obj.month)
        if month_key not in overall[name]["month_counts"]:
            overall[name]["month_counts"][month_key] = {"sun": 0, "fri": 0, "total": 0}
        overall[name]["month_counts"][month_key]["total"] += 1

        if day_type == "Sunday":
            overall[name]["month_counts"][month_key]["sun"] += 1
            overall[name]["last_sun_date"] = date_obj
        if day_type == "Friday":
            overall[name]["month_counts"][month_key]["fri"] += 1
            overall[name]["last_fri_date"] = date_obj


def _increment_expected(pool, role, date_obj, role_tracking, roster, exclude, day_type="Sunday"):
    """Increment the expected count for all eligible available people in a pool.

    Each eligible person accrues an equal share (1/N) of the slot.
    """
    available = [
        p for p in pool
        if p not in exclude
        and is_available(p, date_obj)
        and _person_is_active(p, date_obj, roster)
    ]
    if not available:
        return
    fair_share = 1.0 / len(available)
    tracking_key = _tracking_role(day_type, role)
    for p in available:
        if tracking_key in role_tracking and p in role_tracking[tracking_key]:
            role_tracking[tracking_key][p]["expected"] += fair_share


def generate_month_v2(year, month):
    """
    Generate a fair schedule for a given month using the fairness-deficit algorithm.

    - Skips dates that already have events
    - Creates Event + Assignment records in the database
    - Tracks fairness across all existing + new events
    """
    roster = get_roster()
    role_tracking, overall = _build_history(roster)

    # Build role pools
    pc_pool = _get_role_pool(roster, "Computer", day_type="Sunday")
    cam1_pool = _get_role_pool(roster, "Camera 1", day_type="Sunday")
    cam2_pool = _get_role_pool(roster, "Camera 2", day_type="Sunday")
    friday_pc_pool = _get_role_pool(roster, "Computer", day_type="Friday")
    friday_camera_pool = _get_role_pool(roster, "Camera", day_type="Friday")

    # Determine dates for the month
    num_days = calendar.monthrange(year, month)[1]
    dates = []
    for day in range(1, num_days + 1):
        d = datetime.date(year, month, day)
        if d.weekday() == 4:  # Friday
            dates.append((d, "Friday"))
        elif d.weekday() == 6:  # Sunday
            dates.append((d, "Sunday"))
    dates.sort(key=lambda item: (item[0] + datetime.timedelta(days=2), 1) if item[1] == "Friday" else (item[0], 0))

    created_events = 0

    for date_obj, day_type in dates:
        # Skip existing events
        if Event.query.filter_by(date=date_obj).first():
            continue

        assigned_today = []

        new_event = Event(date=date_obj, day_type=day_type)
        db.session.add(new_event)
        db.session.flush()  # Get ID without full commit

        if day_type == "Sunday":
            # ── PC (Computer) ────────────────────────────────
            _increment_expected(pc_pool, "Computer", date_obj, role_tracking, roster, exclude=[])
            pc = _select_best(pc_pool, "Computer", date_obj, "Sunday",
                              role_tracking, overall, roster, exclude=[])
            assigned_today.append(pc)
            if pc != "TBD":
                _record_assignment(pc, "Computer", date_obj, "Sunday", role_tracking, overall)

            # ── Camera 1 ─────────────────────────────────────
            _increment_expected(cam1_pool, "Camera 1", date_obj, role_tracking, roster, exclude=assigned_today)
            c1 = _select_best(cam1_pool, "Camera 1", date_obj, "Sunday",
                              role_tracking, overall, roster, exclude=assigned_today)
            assigned_today.append(c1)
            if c1 != "TBD":
                _record_assignment(c1, "Camera 1", date_obj, "Sunday", role_tracking, overall)

            # ── Camera 2 ─────────────────────────────────────
            _increment_expected(cam2_pool, "Camera 2", date_obj, role_tracking, roster, exclude=assigned_today)
            c2 = _select_best(cam2_pool, "Camera 2", date_obj, "Sunday",
                              role_tracking, overall, roster, exclude=assigned_today)
            assigned_today.append(c2)
            if c2 != "TBD":
                _record_assignment(c2, "Camera 2", date_obj, "Sunday", role_tracking, overall)

            # Create assignments
            db.session.add_all([
                Assignment(event_id=new_event.id, role="Computer", person=pc, status="pending"),
                Assignment(event_id=new_event.id, role="Camera 1", person=c1, status="pending"),
                Assignment(event_id=new_event.id, role="Camera 2", person=c2, status="pending"),
            ])

        elif day_type == "Friday":
            # ── Computer ─────────────────────────────────────
            _increment_expected(friday_pc_pool, "Computer", date_obj, role_tracking, roster, exclude=[], day_type="Friday")
            computer = _select_best(friday_pc_pool, "Computer", date_obj, "Friday",
                                    role_tracking, overall, roster, exclude=[])
            assigned_today.append(computer)
            if computer != "TBD":
                _record_assignment(computer, "Computer", date_obj, "Friday", role_tracking, overall)

            # ── Camera ───────────────────────────────────────
            _increment_expected(friday_camera_pool, "Camera", date_obj, role_tracking, roster, exclude=assigned_today, day_type="Friday")
            camera = _select_best(friday_camera_pool, "Camera", date_obj, "Friday",
                                  role_tracking, overall, roster, exclude=assigned_today)
            if camera != "TBD":
                _record_assignment(camera, "Camera", date_obj, "Friday", role_tracking, overall)

            db.session.add_all([
                Assignment(event_id=new_event.id, role="Computer", person=computer, status="pending"),
                Assignment(event_id=new_event.id, role="Camera", person=camera, status="pending"),
            ])

        db.session.commit()
        created_events += 1

    return created_events


def _assignment_schedule_type(event, role):
    if event.day_type == "Friday" or role in ("Camera", "Helper"):
        return "Friday"
    return "Sunday"


def _assignment_pool_role(day_type, role):
    if day_type == "Friday":
        return "Camera" if role in ("Camera", "Helper") else "Computer"
    if role in ("Camera 1", "Camera 2"):
        return role
    return "Computer"


def rebalance_future_after_member_removal(removed_name, start_date=None):
    start_date = start_date or vancouver_today()
    roster = get_roster()
    roster.pop(removed_name, None)
    role_tracking, overall = _build_history(roster, end_before=start_date)
    events = Event.query.filter(Event.date >= start_date).order_by(Event.date).all()
    replaced = 0
    tbd = 0
    locked = 0
    touched_events = set()

    for event in events:
        assigned_today = []
        for assignment in event.assignments:
            day_type = _assignment_schedule_type(event, assignment.role)
            pool_role = _assignment_pool_role(day_type, assignment.role)
            pool = _get_role_pool(roster, pool_role, day_type=day_type)
            worker = assignment.cover or assignment.person
            references_removed = (
                assignment.person == removed_name
                or assignment.cover == removed_name
            )
            is_worker_assigned = worker and worker not in ("TBD", "Select Helper")

            _increment_expected(pool, pool_role, event.date, role_tracking, roster, exclude=assigned_today, day_type=day_type)

            # Lock: keep confirmed or pinned future assignments unless they belong to the removed person
            if (assignment.status == "confirmed" or getattr(assignment, "locked", False)) and not references_removed and is_worker_assigned:
                locked += 1
                if worker in roster:
                    assigned_today.append(worker)
                    _record_assignment(worker, pool_role, event.date, day_type, role_tracking, overall)
                continue

            if references_removed:
                replacement = _select_best(pool, pool_role, event.date, day_type, role_tracking, overall, roster, exclude=assigned_today)
                if replacement == "TBD":
                    replacement = _select_relaxed(pool, pool_role, event.date, day_type, role_tracking, overall, roster, exclude=assigned_today)
                assignment.person = replacement
                assignment.cover = None
                assignment.swapped_with = None
                assignment.status = "pending"
                assignment.telegram_message_id = None
                replaced += 1
                touched_events.add(event.id)

                if replacement == "TBD":
                    tbd += 1
                else:
                    assigned_today.append(replacement)
                    _record_assignment(replacement, pool_role, event.date, day_type, role_tracking, overall)
            elif is_worker_assigned and worker in roster:
                assigned_today.append(worker)
                _record_assignment(worker, pool_role, event.date, day_type, role_tracking, overall)

    db.session.flush()
    return {
        "removed_name": removed_name,
        "future_assignments_replaced": replaced,
        "future_events_touched": len(touched_events),
        "tbd_assignments": tbd,
        "confirmed_locked": locked,
    }


def repair_future_assignments_for_roster(start_date=None, refill_pending=False):
    start_date = start_date or vancouver_today()
    roster = get_roster()
    role_tracking, overall = _build_history(roster, end_before=start_date)
    events = Event.query.filter(Event.date >= start_date).order_by(Event.date).all()
    replaced = 0
    tbd = 0
    kept = 0
    touched_events = set()

    def needs_replacement(assignment, event, assigned_today):
        day_type = _assignment_schedule_type(event, assignment.role)
        pool_role = _assignment_pool_role(day_type, assignment.role)
        worker = assignment.cover or assignment.person
        if not worker or worker in ("TBD", "Select Helper"):
            return True
        if worker not in roster:
            return True
        if worker in assigned_today:
            return True
        if worker not in _get_role_pool(roster, pool_role, day_type=day_type):
            return True
        if not _person_is_active(worker, event.date, roster):
            return True
        if not is_available(worker, event.date):
            return True
        if refill_pending and assignment.status == "pending" and not getattr(assignment, "locked", False):
            return True
        return False

    for event in events:
        assigned_today = []
        for assignment in event.assignments:
            day_type = _assignment_schedule_type(event, assignment.role)
            pool_role = _assignment_pool_role(day_type, assignment.role)
            pool = _get_role_pool(roster, pool_role, day_type=day_type)

            _increment_expected(pool, pool_role, event.date, role_tracking, roster, exclude=assigned_today, day_type=day_type)

            if not needs_replacement(assignment, event, assigned_today):
                worker = assignment.cover or assignment.person
                assigned_today.append(worker)
                _record_assignment(worker, pool_role, event.date, day_type, role_tracking, overall)
                kept += 1
                continue

            replacement = _select_best(pool, pool_role, event.date, day_type, role_tracking, overall, roster, exclude=assigned_today)
            if replacement == "TBD":
                replacement = _select_relaxed(pool, pool_role, event.date, day_type, role_tracking, overall, roster, exclude=assigned_today)

            assignment.person = replacement
            assignment.cover = None
            assignment.swapped_with = None
            assignment.status = "pending"
            assignment.telegram_message_id = None
            replaced += 1
            touched_events.add(event.id)

            if replacement == "TBD":
                tbd += 1
            else:
                assigned_today.append(replacement)
                _record_assignment(replacement, pool_role, event.date, day_type, role_tracking, overall)

    db.session.flush()
    return {
        "future_assignments_replaced": replaced,
        "future_events_touched": len(touched_events),
        "tbd_assignments": tbd,
        "kept_assignments": kept,
        "refill_pending": bool(refill_pending),
    }


def rebalance_future_to_targets(targets, start_date=None, end_date=None, lock_confirmed=True):
    start_date = start_date or vancouver_today()
    roster = get_roster()
    roster_names = set(roster.keys())
    query = Event.query.filter(
        Event.date >= start_date,
        Event.day_type.in_(["Sunday", "Friday"]),
    )
    if end_date is not None:
        query = query.filter(Event.date <= end_date)
    events = query.order_by(Event.date).all()

    slot_totals = {}
    for event in events:
        for assignment in event.assignments:
            day_type = _assignment_schedule_type(event, assignment.role)
            pool_role = _assignment_pool_role(day_type, assignment.role)
            tracking_key = _tracking_role(day_type, pool_role)
            slot_totals[tracking_key] = slot_totals.get(tracking_key, 0) + 1

    remaining = {}
    for tracking_key, people in (targets or {}).items():
        remaining[tracking_key] = {}
        for name, value in (people or {}).items():
            if name not in roster_names:
                continue
            remaining[tracking_key][name] = max(0, int(value or 0))

    for tracking_key, total in slot_totals.items():
        target_total = sum(remaining.get(tracking_key, {}).values())
        if target_total != total:
            raise ValueError(f"{tracking_key} target total must equal {total}")

    # Pre-decrement remaining for confirmed/locked future assignments so they
    # don't double-spend the user's targeted counts.
    locked_count = 0

    def _is_locked(a):
        worker = a.cover or a.person
        if not worker or worker in ("TBD", "Select Helper"):
            return False
        if getattr(a, "locked", False):
            return True
        if lock_confirmed and a.status == "confirmed":
            return True
        return False

    for event in events:
        for assignment in event.assignments:
            if not _is_locked(assignment):
                continue
            day_type = _assignment_schedule_type(event, assignment.role)
            pool_role = _assignment_pool_role(day_type, assignment.role)
            tracking_key = _tracking_role(day_type, pool_role)
            worker = assignment.cover or assignment.person
            role_remaining = remaining.get(tracking_key, {})
            if worker not in role_remaining:
                raise ValueError(f"{worker} has a locked {tracking_key} assignment but no target count")
            if role_remaining[worker] <= 0:
                raise ValueError(f"{worker} has more locked {tracking_key} assignments than the target allows")
            role_remaining[worker] -= 1
            locked_count += 1

    role_tracking, overall = _build_history(roster, end_before=start_date)
    updated = 0
    tbd = 0
    snapshot = []

    for event in events:
        assigned_today = []
        for assignment in event.assignments:
            day_type = _assignment_schedule_type(event, assignment.role)
            pool_role = _assignment_pool_role(day_type, assignment.role)
            tracking_key = _tracking_role(day_type, pool_role)

            if _is_locked(assignment):
                worker = assignment.cover or assignment.person
                if worker and worker not in ("TBD", "Select Helper"):
                    assigned_today.append(worker)
                    _record_assignment(worker, pool_role, event.date, day_type, role_tracking, overall)
                continue

            pool = [
                name for name in _get_role_pool(roster, pool_role, day_type=day_type)
                if remaining.get(tracking_key, {}).get(name, 0) > 0
            ]

            _increment_expected(pool, pool_role, event.date, role_tracking, roster, exclude=assigned_today, day_type=day_type)
            replacement = _select_best(pool, pool_role, event.date, day_type, role_tracking, overall, roster, exclude=assigned_today)
            if replacement == "TBD":
                replacement = _select_relaxed(pool, pool_role, event.date, day_type, role_tracking, overall, roster, exclude=assigned_today)

            snapshot.append({
                "id": assignment.id,
                "person": assignment.person,
                "cover": assignment.cover,
                "status": assignment.status,
                "swapped_with": assignment.swapped_with,
                "locked": bool(getattr(assignment, "locked", False)),
            })

            assignment.person = replacement
            assignment.cover = None
            assignment.swapped_with = None
            assignment.status = "pending"
            assignment.telegram_message_id = None
            updated += 1

            if replacement == "TBD":
                tbd += 1
            else:
                if remaining.get(tracking_key, {}).get(replacement, 0) > 0:
                    remaining[tracking_key][replacement] -= 1
                assigned_today.append(replacement)
                _record_assignment(replacement, pool_role, event.date, day_type, role_tracking, overall)

    db.session.flush()
    return {
        "future_assignments_updated": updated,
        "tbd_assignments": tbd,
        "confirmed_locked": locked_count,
        "remaining_targets": remaining,
        "snapshot": snapshot,
    }


def preview_future_targets(targets, start_date=None, end_date=None, lock_confirmed=True):
    """Run rebalance_future_to_targets without committing and return per-event diffs.

    Captures before-state, runs the same algorithm in-memory, then rolls back
    so no database changes persist.
    """
    start_date = start_date or vancouver_today()
    query = Event.query.filter(
        Event.date >= start_date,
        Event.day_type.in_(["Sunday", "Friday"]),
    )
    if end_date is not None:
        query = query.filter(Event.date <= end_date)
    events = query.order_by(Event.date).all()

    before = {}
    for event in events:
        for assignment in event.assignments:
            before[assignment.id] = {
                "date": event.date.isoformat(),
                "day_type": event.day_type,
                "role": assignment.role,
                "person": assignment.person,
                "cover": assignment.cover,
                "status": assignment.status,
            }

    try:
        result = rebalance_future_to_targets(
            targets,
            start_date=start_date,
            end_date=end_date,
            lock_confirmed=lock_confirmed,
        )
        result.pop("snapshot", None)

        changes = []
        for event in events:
            for assignment in event.assignments:
                prev = before.get(assignment.id)
                if not prev:
                    continue
                from_worker = prev.get("cover") or prev.get("person")
                to_worker = assignment.cover or assignment.person
                if from_worker != to_worker:
                    changes.append({
                        "assignment_id": assignment.id,
                        "date": prev["date"],
                        "day_type": prev["day_type"],
                        "role": prev["role"],
                        "from": from_worker,
                        "to": to_worker,
                    })
        return {"changes": changes, **result}
    finally:
        db.session.rollback()


def reschedule_declined(requestor, original_event_date, role, max_lookahead_months=6):
    """Perform a two-way swap for someone who declined and wasn't picked up.

    Matches the Young Couples pattern:
      1. Find the next event of the same weekday/day_type where the requestor is
         eligible, does not already appear, and the same-role slot is held by
         someone else. Skip months where the requestor can't fit.
      2. Displace that person: put the requestor into that role in that event.
      3. Push the displaced person into the requestor's *next* future assignment
         (if any) so fairness is preserved by a true swap.
      4. If no swap partner or no displaceable slot is found within
         max_lookahead_months, return a dict with status='failed' so the caller
         can alert the admin.

    Returns:
      {
        'status': 'ok' | 'failed',
        'new_event_date': date | None,
        'displaced': str | None,         # who got pushed
        'displaced_moved_to': date | None,
        'notes': str,
      }
    """
    roster = get_roster()
    same_role_pool = _get_role_pool(roster, role)
    if requestor not in same_role_pool:
        return {"status": "failed", "new_event_date": None, "displaced": None,
                "displaced_moved_to": None,
                "notes": f"{requestor} is not eligible for role {role}"}

    # Find the original event's day_type to match (Sunday vs Friday)
    original_event = Event.query.filter_by(date=original_event_date).first()
    day_type = original_event.day_type if original_event else "Sunday"

    # Walk forward month by month looking for a suitable next-month same-role slot
    cursor = original_event_date
    for month_offset in range(1, max_lookahead_months + 1):
        # First day of (original_date + month_offset months)
        target_month = cursor.month + month_offset
        target_year = cursor.year + (target_month - 1) // 12
        target_month = ((target_month - 1) % 12) + 1
        month_start = datetime.date(target_year, target_month, 1)
        month_end_day = calendar.monthrange(target_year, target_month)[1]
        month_end = datetime.date(target_year, target_month, month_end_day)

        # Candidate events in this month of matching day_type
        candidates = Event.query.filter(
            Event.date >= month_start,
            Event.date <= month_end,
            Event.day_type == day_type,
        ).order_by(Event.date).all()

        for candidate_event in candidates:
            # The requestor must not already be in this event
            people_here = {a.cover or a.person for a in candidate_event.assignments}
            if requestor in people_here:
                continue

            # Find the same-role slot in this event
            target_slot = next(
                (a for a in candidate_event.assignments if a.role == role),
                None,
            )
            if not target_slot:
                continue

            displaced_person = target_slot.cover or target_slot.person
            if displaced_person == requestor or displaced_person in ("TBD", "Select Helper", None, ""):
                # Empty slot — just drop requestor in, no swap needed
                target_slot.person = requestor
                target_slot.cover = None
                target_slot.status = "pending"
                db.session.commit()
                return {
                    "status": "ok",
                    "new_event_date": candidate_event.date,
                    "displaced": None,
                    "displaced_moved_to": None,
                    "notes": f"{requestor} placed into empty {role} slot on {candidate_event.date}",
                }

            # Two-way swap: find the displaced person's NEXT future assignment
            # (after the candidate event's date) to push them into requestor's slot.
            requestors_next = (
                Assignment.query.join(Event)
                .filter(
                    Event.date > candidate_event.date,
                    Assignment.person == requestor,
                )
                .order_by(Event.date)
                .first()
            )
            if not requestors_next:
                # No future slot to swap into — just overwrite without a reciprocal move
                target_slot.person = requestor
                target_slot.cover = None
                target_slot.status = "pending"
                db.session.commit()
                return {
                    "status": "ok",
                    "new_event_date": candidate_event.date,
                    "displaced": displaced_person,
                    "displaced_moved_to": None,
                    "notes": f"Displaced {displaced_person} without reciprocal swap (no future slot found for {requestor})",
                }

            # Perform the swap
            displaced_moved_to_date = requestors_next.event.date
            target_slot.person = requestor
            target_slot.cover = None
            target_slot.status = "pending"
            requestors_next.person = displaced_person
            requestors_next.cover = None
            requestors_next.status = "pending"
            db.session.commit()
            return {
                "status": "ok",
                "new_event_date": candidate_event.date,
                "displaced": displaced_person,
                "displaced_moved_to": displaced_moved_to_date,
                "notes": (
                    f"Swapped {requestor} into {role} on {candidate_event.date} "
                    f"(displaced {displaced_person}, who moved to {displaced_moved_to_date})"
                ),
            }

    return {
        "status": "failed",
        "new_event_date": None,
        "displaced": None,
        "displaced_moved_to": None,
        "notes": f"No eligible swap partner found within {max_lookahead_months} months",
    }


def get_fairness_report(roster=None):
    """
    Generate a fairness report showing each person's expected vs assigned.
    Useful for debugging and verifying the algorithm.
    """
    if roster is None:
        roster = get_roster()
    role_tracking, overall = _build_history(roster)

    report = {}
    for name in roster:
        ov = overall.get(name, {})
        roles = {}
        for role, data in role_tracking.items():
            if name in data:
                rd = data[name]
                roles[role] = {
                    "assigned": rd["assigned"],
                    "expected": round(rd["expected"], 2),
                    "deficit": round(rd["assigned"] - rd["expected"], 2),
                }
        report[name] = {
            "total_assigned": ov.get("total", 0),
            "lifetime": ov.get("lifetime", 0),
            "last_worked": ov.get("last_date").isoformat() if ov.get("last_date") else None,
            "roles": roles,
        }

    return report
