import calendar
import datetime
from .utils import (
    check_and_init, get_history_stats, is_available, is_real_person,
    ROLES_CONFIG, ALL_NAMES, PC_ROTATION_ORDER
)
from .models import Event, Assignment
from .extensions import db

SUNDAY_CAP_PER_MONTH = 2
FRIDAY_LEADER_CAP_PER_MONTH = 2  # Increased to 2 for everyone

# Florian-specific caps
FLORIAN_SUNDAY_CAP = 1  # Florian: max 1 Sunday/month
FLORIAN_FRIDAY_CAP = 2  # Florian: max 2 Friday/month

PREFERRED_3_TOTAL = ["Florian", "Marvin", "Stefan"]  # Keep for backward compat
MONTH_TOTAL_CAP_DEFAULT = 3  # Everyone gets 3 assignments/month
MONTH_TOTAL_CAP_PREFERRED = 3  # Same as default now

SUNDAY_MIN_GAP_DAYS = 8

def get_fatigue_penalty(person, date_obj, last_worked_map):
    if person not in last_worked_map or not last_worked_map[person]:
        return 0
    dates = sorted(last_worked_map[person])
    last_date = dates[-1]
    days_since = (date_obj - last_date).days
    penalty = 0
    if days_since < 3:
        penalty += 2000
    elif days_since == 7:
        penalty += 1500
    elif days_since < 10:
        penalty += 200
    return penalty

def get_next_pc():
    # Find the last assigned PC from DB
    # We iterate backwards through dates
    events = Event.query.filter_by(day_type="Sunday").order_by(Event.date.desc()).all()
    for e in events:
        for a in e.assignments:
            if a.role == "Computer" and a.person in PC_ROTATION_ORDER:
                return PC_ROTATION_ORDER.index(a.person)
    return -1

def month_counts_from_existing(year, month):
    counts = {n: {"sun": 0, "fri_leader": 0, "total": 0, "last_sun_date": None} for n in ALL_NAMES}

    # Query all events in this month
    start_date = datetime.date(year, month, 1)
    # simple end date calc
    _, num_days = calendar.monthrange(year, month)
    end_date = datetime.date(year, month, num_days)
    
    events = Event.query.filter(Event.date >= start_date, Event.date <= end_date).all()

    for event in events:
        d_obj = event.date
        title = (event.custom_title or "").lower()
        is_sun_like = (event.day_type == "Sunday") or ("new year's day service" in title) or (d_obj.weekday() == 6)
        is_fri = (event.day_type == "Friday") or (d_obj.weekday() == 4)

        for a in event.assignments:
            p = a.person
            if not is_real_person(p):
                continue
            
            counts[p]["total"] += 1
            
            if is_sun_like:
                counts[p]["sun"] += 1
                prev = counts[p]["last_sun_date"]
                if prev is None or d_obj > prev:
                    counts[p]["last_sun_date"] = d_obj
            
            if is_fri and a.role == "Leader":
                counts[p]["fri_leader"] += 1
                
    return counts

def _month_total_cap(person: str) -> int:
    # Dynamic Cap: If person is lagging behind the average, allow 1 extra assignment/month
    # Calculate lagging status
    # Note: stats is global in context of generate_month but not here. 
    # Wait, in the original code stats was passed or available?
    # In my previous edit I used `stats[n]["total"]` but `stats` is not in scope here!
    # I must move this function INSIDE generate_month or pass `stats` to it.
    # The original request had it seemingly at module level in Step 95?
    return MONTH_TOTAL_CAP_PREFERRED if person in PREFERRED_3_TOTAL else MONTH_TOTAL_CAP_DEFAULT

def _too_close_to_last_sunday(person: str, date_obj: datetime.date, local_month_stats: dict) -> bool:
    last_d = local_month_stats.get(person, {}).get("last_sun_date")
    if not last_d:
        return False
    return (date_obj - last_d).days < SUNDAY_MIN_GAP_DAYS

def generate_month(year, month):
    check_and_init()
    
    stats_nested, last_worked = get_history_stats()
    stats = stats_nested["All Time"]  # Extract the flat stats dictionary we need
    last_pc_idx = get_next_pc()
    current_pc_idx = last_pc_idx + 1

    local_month_stats = month_counts_from_existing(year, month)

    num_days = calendar.monthrange(year, month)[1]
    dates = []
    for day in range(1, num_days + 1):
        d = datetime.date(year, month, day)
        if d.weekday() == 4:
            dates.append((d, "Friday"))
        elif d.weekday() == 6:
            dates.append((d, "Sunday"))

    # Capture stats in closure
    def _month_total_cap_dynamic(person: str) -> int:
        # Dynamic Cap: If person is lagging behind the average, allow 1 extra assignment/month
        all_vals = [stats[n]["total"] for n in ALL_NAMES if n in stats]
        if not all_vals: return MONTH_TOTAL_CAP_DEFAULT
        
        avg = sum(all_vals) / len(all_vals)
        base_cap = MONTH_TOTAL_CAP_PREFERRED if person in PREFERRED_3_TOTAL else MONTH_TOTAL_CAP_DEFAULT
        
        # If lagging by more than 1.5 assignments, boost cap by 1 to allow catch-up
        if stats[person]["total"] < (avg - 1.5):
            return base_cap + 1
            
        return base_cap

    def _preferred_bonus(person: str) -> int:
        if person in PREFERRED_3_TOTAL:
            cap = _month_total_cap_dynamic(person)
            if local_month_stats[person]["total"] < cap:
                return -400
        return 0

    def _month_total_cap_penalty(person: str) -> int:
        cap = _month_total_cap_dynamic(person)
        over = max(0, local_month_stats[person]["total"] - (cap - 1))
        return over * 60000 

    def _month_sunday_cap_penalty(person: str) -> int:
        # Florian: max 1 Sunday/month, others: max 2 Sunday/month
        cap = FLORIAN_SUNDAY_CAP if person == "Florian" else SUNDAY_CAP_PER_MONTH
        over = max(0, local_month_stats[person]["sun"] - (cap - 1))
        return over * 80000

    def _month_friday_cap_penalty(person: str) -> int:
        # Florian: max 2 Friday/month, others: max 2 Friday/month
        cap = FLORIAN_FRIDAY_CAP if person == "Florian" else FRIDAY_LEADER_CAP_PER_MONTH
        over = max(0, local_month_stats[person]["fri_leader"] - (cap - 1))
        return over * 50000

    def get_score(person, role_type, date_obj, day_type):
        fatigue = get_fatigue_penalty(person, date_obj, last_worked)
        fairness = stats[person]["total"] * 2000
        month_total_pen = _month_total_cap_penalty(person)
        month_sun_pen = _month_sunday_cap_penalty(person) if day_type == "Sunday" else 0
        month_fri_pen = _month_friday_cap_penalty(person) if day_type == "Friday" else 0

        friday_sunday_bias = 0
        if day_type == "Friday":
            friday_sunday_bias = local_month_stats[person]["sun"] * 1200

        consecutive_sun_pen = 0
        if day_type == "Sunday" and _too_close_to_last_sunday(person, date_obj, local_month_stats):
            consecutive_sun_pen = 200000

        specialist_bonus = 0
        if day_type == "Sunday" and role_type == "Camera 2":
            if len(ROLES_CONFIG[person]["sunday_roles"]) == 1:
                specialist_bonus = -300

        pref_bonus = _preferred_bonus(person)

        return (
            fatigue + fairness + month_total_pen + month_sun_pen +
            month_fri_pen + friday_sunday_bias + consecutive_sun_pen +
            specialist_bonus + pref_bonus
        )

    def pick_best(cands, role, date_obj, day_type, exclude):
        valid = [p for p in cands if is_available(p, date_obj) and p not in exclude]

        def under_caps(p: str) -> bool:
            if local_month_stats[p]["total"] >= _month_total_cap_dynamic(p):
                return False
            if day_type == "Sunday":
                if local_month_stats[p]["sun"] >= SUNDAY_CAP_PER_MONTH:
                    return False
                if _too_close_to_last_sunday(p, date_obj, local_month_stats):
                    return False
            if day_type == "Friday":
                if local_month_stats[p]["fri_leader"] >= FRIDAY_LEADER_CAP_PER_MONTH:
                    return False
            return True

        pool = [p for p in valid if under_caps(p)]
        if not pool and day_type == "Sunday":
            def under_caps_relax_gap(p: str) -> bool:
                if local_month_stats[p]["total"] >= _month_total_cap_dynamic(p):
                    return False
                if local_month_stats[p]["sun"] >= SUNDAY_CAP_PER_MONTH:
                    return False
                return True
            pool = [p for p in valid if under_caps_relax_gap(p)]
        if not pool:
            pool = valid

        pool.sort(key=lambda p: get_score(p, role, date_obj, day_type))
        return pool[0] if pool else "TBD"

    for date_obj, day_type in dates:
        # Check if exists in DB
        if Event.query.filter_by(date=date_obj).first():
            continue

        assigned_today = []
        new_event = Event(date=date_obj, day_type=day_type)
        db.session.add(new_event)
        db.session.commit() # get ID

        if day_type == "Friday":
            pool = [p for p in ROLES_CONFIG.keys() if ROLES_CONFIG[p].get("friday")]
            leader = pick_best(pool, "Leader", date_obj, "Friday", exclude=[])
            
            a1 = Assignment(event_id=new_event.id, role="Leader", person=leader, status="pending")
            a2 = Assignment(event_id=new_event.id, role="Helper", person="Select Helper", status="pending")
            db.session.add_all([a1, a2])
            db.session.commit()

            if is_real_person(leader):
                stats[leader]["total"] += 1
                stats[leader]["friday"] += 1
                local_month_stats[leader]["fri_leader"] += 1
                local_month_stats[leader]["total"] += 1
                last_worked[leader].append(date_obj)

        elif day_type == "Sunday":
            pc_person = "TBD"
            tries = 0
            while tries < (len(PC_ROTATION_ORDER) * 2):
                cand = PC_ROTATION_ORDER[current_pc_idx % len(PC_ROTATION_ORDER)]
                current_pc_idx += 1
                tries += 1
                
                if not is_available(cand, date_obj): continue
                if local_month_stats[cand]["total"] >= _month_total_cap_dynamic(cand): continue
                if local_month_stats[cand]["sun"] >= SUNDAY_CAP_PER_MONTH: continue
                if _too_close_to_last_sunday(cand, date_obj, local_month_stats): continue
                if get_fatigue_penalty(cand, date_obj, last_worked) >= 1500: continue
                
                pc_person = cand
                break
                
            if pc_person == "TBD":
                # Relax gap
                tries = 0
                while tries < (len(PC_ROTATION_ORDER) * 2):
                    cand = PC_ROTATION_ORDER[current_pc_idx % len(PC_ROTATION_ORDER)]
                    current_pc_idx += 1
                    tries += 1
                    if not is_available(cand, date_obj): continue
                    if local_month_stats[cand]["total"] >= _month_total_cap_dynamic(cand): continue
                    if local_month_stats[cand]["sun"] >= SUNDAY_CAP_PER_MONTH: continue
                    pc_person = cand
                    break
            
            assigned_today.append(pc_person)
            if is_real_person(pc_person):
                stats[pc_person]["total"] += 1
                stats[pc_person]["sunday"] += 1
                local_month_stats[pc_person]["sun"] += 1
                local_month_stats[pc_person]["total"] += 1
                local_month_stats[pc_person]["last_sun_date"] = date_obj
                last_worked[pc_person].append(date_obj)

            cam1_pool = [p for p in ROLES_CONFIG.keys() if "Camera 1" in ROLES_CONFIG[p]["sunday_roles"]]
            cam2_pool = [p for p in ROLES_CONFIG.keys() if "Camera 2" in ROLES_CONFIG[p]["sunday_roles"]]
            
            c1 = pick_best(cam1_pool, "Camera 1", date_obj, "Sunday", exclude=assigned_today)
            assigned_today.append(c1)
            if is_real_person(c1):
                stats[c1]["total"] += 1
                stats[c1]["sunday"] += 1
                local_month_stats[c1]["sun"] += 1
                local_month_stats[c1]["total"] += 1
                local_month_stats[c1]["last_sun_date"] = date_obj
                last_worked[c1].append(date_obj)

            c2 = pick_best(cam2_pool, "Camera 2", date_obj, "Sunday", exclude=assigned_today)
            assigned_today.append(c2)
            if is_real_person(c2):
                stats[c2]["total"] += 1
                stats[c2]["sunday"] += 1
                local_month_stats[c2]["sun"] += 1
                local_month_stats[c2]["total"] += 1
                local_month_stats[c2]["last_sun_date"] = date_obj
                last_worked[c2].append(date_obj)

            a_pc = Assignment(event_id=new_event.id, role="Computer", person=pc_person, status="pending")
            a_c1 = Assignment(event_id=new_event.id, role="Camera 1", person=c1, status="pending")
            a_c2 = Assignment(event_id=new_event.id, role="Camera 2", person=c2, status="pending")
            db.session.add_all([a_pc, a_c1, a_c2])
            db.session.commit()

    return True
