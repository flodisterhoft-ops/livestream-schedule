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
MONTH_TOTAL_CAP = 3               # Max total assignments per person per month
SUNDAY_MIN_GAP_DAYS = 8           # Min days between consecutive Sundays


# ── Team configuration (default, used when TeamMember table is empty) ────
DEFAULT_ROSTER = {
    "Florian": {"sunday_roles": ["Computer"], "friday_roles": ["Leader"]},
    "Andy":    {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday_roles": ["Leader"]},
    "Marvin":  {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday_roles": ["Leader"]},
    "Patric":  {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday_roles": ["Leader"]},
    "Rene":    {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday_roles": ["Leader"]},
    "Stefan":  {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday_roles": ["Leader"]},
    "Viktor":  {"sunday_roles": ["Camera 2"], "friday_roles": ["Leader"]},
}

# Florian-specific caps
FLORIAN_SUNDAY_CAP = 1
FLORIAN_FRIDAY_CAP = 2


def get_roster():
    """Get team roster from DB (TeamMember table) or fall back to defaults."""
    members = TeamMember.query.filter_by(active=True).all()
    if members:
        roster = {}
        for m in members:
            roster[m.name] = {
                "sunday_roles": m.sunday_roles,
                "friday_roles": m.friday_roles,
                "active_from": m.active_from,
            }
        return roster
    return DEFAULT_ROSTER


def _get_role_pool(roster, role):
    """Get list of people eligible for a specific role."""
    pool = []
    for name, config in roster.items():
        sunday_roles = config.get("sunday_roles", [])
        friday_roles = config.get("friday_roles", [])
        if role in sunday_roles or role in friday_roles:
            pool.append(name)
    return pool


def _person_is_active(name, date_obj, roster):
    """Check if person is active on a given date (active_from check)."""
    config = roster.get(name, {})
    active_from = config.get("active_from")
    if active_from and isinstance(active_from, datetime.date) and date_obj < active_from:
        return False
    return True


def _build_history(roster):
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
    for role in ["Computer", "Camera 1", "Camera 2", "Leader"]:
        pool = _get_role_pool(roster, role)
        role_tracking[role] = {
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
        }
        for name in all_names
    }

    events = Event.query.order_by(Event.date).all()

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
            if role == "Helper":
                continue  # Helper is manually assigned
            if role not in role_tracking:
                continue
            pool = [n for n in role_tracking[role] if is_available(n, d) and _person_is_active(n, d, roster)]
            if pool:
                fair_share = 1.0 / len(pool)
                for n in pool:
                    role_tracking[role][n]["expected"] += fair_share

        # Update per-role assigned and overall for actual assignments
        for a in event.assignments:
            worker = a.cover if a.cover else a.person
            if not worker or worker in ("TBD", "Select Helper"):
                continue

            # Per-role assigned
            if a.role in role_tracking and worker in role_tracking[a.role]:
                role_tracking[a.role][worker]["assigned"] += 1

            # Overall
            if worker in overall:
                overall[worker]["total"] += 1
                overall[worker]["lifetime"] += 1
                overall[worker]["last_date"] = d

                if is_sunday:
                    overall[worker]["last_sun_date"] = d

                if month_key not in overall[worker]["month_counts"]:
                    overall[worker]["month_counts"][month_key] = {"sun": 0, "fri": 0, "total": 0}
                overall[worker]["month_counts"][month_key]["total"] += 1
                if is_sunday:
                    overall[worker]["month_counts"][month_key]["sun"] += 1
                if is_friday:
                    overall[worker]["month_counts"][month_key]["fri"] += 1

    return role_tracking, overall


def _schedule_priority(name, role, role_tracking, overall, date_obj):
    """
    Calculate scheduling priority for a person.
    LOWER score = HIGHER priority (gets assigned first).

    Tier 1: Fairness deficit (assigned - expected) — most important
    Tier 2: Days since last worked (more days = lower score = higher priority)
    Tier 3: Lifetime count (fewer = higher priority)
    Tier 4: Overall total this scheduling period (fewer = higher priority)
    """
    rt = role_tracking.get(role, {}).get(name, {"assigned": 0, "expected": 0.0})
    ov = overall.get(name, {"total": 0, "last_date": None, "lifetime": 0})

    deficit = round(rt["assigned"] - rt["expected"], 8)

    last_date = ov.get("last_date")
    if last_date:
        days_since = (date_obj - last_date).days
    else:
        days_since = 9999  # Never worked = highest priority

    return (
        deficit,                    # Primary: fairness deficit
        -days_since,                # Secondary: prefer longer gap (negative = lower)
        ov.get("lifetime", 0),      # Tertiary: fewer lifetime assignments
        ov.get("total", 0),         # Quaternary: fewer total assignments
    )


def _month_counts(name, year, month, overall):
    """Get month-specific counts for a person."""
    ov = overall.get(name, {})
    mc = ov.get("month_counts", {}).get((year, month), {"sun": 0, "fri": 0, "total": 0})
    return mc


def _check_caps(name, date_obj, day_type, overall):
    """Check if person is within monthly caps."""
    mc = _month_counts(name, date_obj.year, date_obj.month, overall)

    # Total cap
    if mc["total"] >= MONTH_TOTAL_CAP:
        return False

    # Sunday cap
    if day_type == "Sunday":
        cap = FLORIAN_SUNDAY_CAP if name == "Florian" else SUNDAY_CAP_PER_MONTH
        if mc["sun"] >= cap:
            return False

    # Friday cap
    if day_type == "Friday":
        cap = FLORIAN_FRIDAY_CAP if name == "Florian" else FRIDAY_LEADER_CAP_PER_MONTH
        if mc["fri"] >= cap:
            return False

    return True


def _check_sunday_gap(name, date_obj, overall):
    """Check minimum gap between consecutive Sundays."""
    ov = overall.get(name, {})
    last_sun = ov.get("last_sun_date")
    if last_sun and (date_obj - last_sun).days < SUNDAY_MIN_GAP_DAYS:
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
    ]

    if not valid:
        return "TBD"

    # Step 2: Apply caps and gap rules
    constrained = [
        p for p in valid
        if _check_caps(p, date_obj, day_type, overall)
        and (day_type != "Sunday" or _check_sunday_gap(p, date_obj, overall))
    ]

    # Step 3: Relax gap rule if nobody passes
    if not constrained and day_type == "Sunday":
        constrained = [
            p for p in valid
            if _check_caps(p, date_obj, day_type, overall)
        ]

    # Step 4: If still nobody, relax all caps
    if not constrained:
        constrained = valid

    # Step 5: Sort by priority and pick best
    constrained.sort(key=lambda p: _schedule_priority(p, role, role_tracking, overall, date_obj))

    return constrained[0]


def _record_assignment(name, role, date_obj, day_type, role_tracking, overall):
    """Record an assignment in the tracking data structures."""
    # Per-role assigned
    if role in role_tracking and name in role_tracking[role]:
        role_tracking[role][name]["assigned"] += 1

    # Overall
    if name in overall:
        overall[name]["total"] += 1
        overall[name]["lifetime"] += 1
        overall[name]["last_date"] = date_obj

        month_key = (date_obj.year, date_obj.month)
        if month_key not in overall[name]["month_counts"]:
            overall[name]["month_counts"][month_key] = {"sun": 0, "fri": 0, "total": 0}
        overall[name]["month_counts"][month_key]["total"] += 1

        if day_type == "Sunday":
            overall[name]["month_counts"][month_key]["sun"] += 1
            overall[name]["last_sun_date"] = date_obj
        if day_type == "Friday":
            overall[name]["month_counts"][month_key]["fri"] += 1


def _increment_expected(pool, role, date_obj, role_tracking, roster, exclude):
    """Increment the expected count for all eligible available people in a pool."""
    available = [
        p for p in pool
        if p not in exclude
        and is_available(p, date_obj)
        and _person_is_active(p, date_obj, roster)
    ]
    if not available:
        return
    fair_share = 1.0 / len(available)
    for p in available:
        if role in role_tracking and p in role_tracking[role]:
            role_tracking[role][p]["expected"] += fair_share


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
    pc_pool = _get_role_pool(roster, "Computer")
    cam1_pool = _get_role_pool(roster, "Camera 1")
    cam2_pool = _get_role_pool(roster, "Camera 2")
    leader_pool = _get_role_pool(roster, "Leader")

    # Determine dates for the month
    num_days = calendar.monthrange(year, month)[1]
    dates = []
    for day in range(1, num_days + 1):
        d = datetime.date(year, month, day)
        if d.weekday() == 4:  # Friday
            dates.append((d, "Friday"))
        elif d.weekday() == 6:  # Sunday
            dates.append((d, "Sunday"))

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
            # ── Leader ───────────────────────────────────────
            _increment_expected(leader_pool, "Leader", date_obj, role_tracking, roster, exclude=[])
            leader = _select_best(leader_pool, "Leader", date_obj, "Friday",
                                  role_tracking, overall, roster, exclude=[])
            if leader != "TBD":
                _record_assignment(leader, "Leader", date_obj, "Friday", role_tracking, overall)

            db.session.add_all([
                Assignment(event_id=new_event.id, role="Leader", person=leader, status="pending"),
                Assignment(event_id=new_event.id, role="Helper", person="Select Helper", status="pending"),
            ])

        db.session.commit()
        created_events += 1

    return created_events


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
