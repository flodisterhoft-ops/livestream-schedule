import datetime
from zoneinfo import ZoneInfo
from .models import Event

# ============================================================
# Timezone helpers — server runs in UTC, we need Vancouver time
# ============================================================
VANCOUVER_TZ = ZoneInfo("America/Vancouver")

def vancouver_now():
    """Current datetime in Vancouver timezone."""
    return datetime.datetime.now(VANCOUVER_TZ)

def vancouver_today():
    """Current date in Vancouver timezone."""
    return vancouver_now().date()

# ============================================================
# Constants
# ============================================================
ROLES_CONFIG = {
    "Florian": {"sunday_roles": ["Computer"], "friday": True, "friday_roles": ["Computer"]},
    "Andy": {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday": True, "friday_roles": ["Computer", "Camera"]},
    "Marvin": {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday": True, "friday_roles": ["Computer", "Camera"]},
    "Patric": {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday": True, "friday_roles": ["Computer", "Camera"]},
    "Rene": {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday": True, "friday_roles": ["Computer", "Camera"]},
    "Stefan": {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday": True, "friday_roles": ["Computer", "Camera"]},
    "Viktor": {"sunday_roles": ["Camera 2"], "friday": True, "friday_roles": ["Camera"]},
}
ALL_NAMES = sorted(list(ROLES_CONFIG.keys()) + ["TBD"])
PC_ROTATION_ORDER = ["Florian", "Marvin", "Rene", "Stefan", "Andy", "Patric"]

BLACKOUTS = {
    "Florian": [(datetime.date(2025, 12, 12), datetime.date(2025, 12, 27))]
}

# ============================================================
# Helpers
# ============================================================
def is_available(person, date_obj):
    """Check if a person is available on a given date.
    Checks both hardcoded BLACKOUTS and the Availability database.
    """
    # Import here to avoid circular imports
    from .models import Availability
    
    # Check hardcoded blackouts first
    if person in BLACKOUTS:
        for start, end in BLACKOUTS[person]:
            if start <= date_obj <= end:
                return False
    
    # Check database availability entries
    avails = Availability.query.filter_by(person=person).all()
    for avail in avails:
        # Check date range
        if avail.start_date <= date_obj <= avail.end_date:
            # Check if recurring pattern matches
            if avail.recurring and avail.pattern:
                if matches_pattern(date_obj, avail.pattern):
                    return False
            elif not avail.recurring:
                return False
    
    return True

def matches_pattern(date_obj, pattern):
    """Check if a date matches a recurring pattern like '1st_sunday'."""
    day_of_week = date_obj.weekday()  # 0=Monday, 6=Sunday
    day_of_month = date_obj.day
    
    # Calculate which week of month (1st, 2nd, 3rd, 4th)
    week_num = (day_of_month - 1) // 7 + 1
    
    if pattern == "every_friday" and day_of_week == 4:
        return True
    if pattern == "every_sunday" and day_of_week == 6:
        return True
    if pattern == "1st_sunday" and day_of_week == 6 and week_num == 1:
        return True
    if pattern == "2nd_sunday" and day_of_week == 6 and week_num == 2:
        return True
    if pattern == "3rd_sunday" and day_of_week == 6 and week_num == 3:
        return True
    if pattern == "4th_sunday" and day_of_week == 6 and week_num == 4:
        return True
    
    return False

def is_real_person(p: str) -> bool:
    return bool(p) and p in ROLES_CONFIG and p != "TBD" and p != "Select Helper"

def get_history_stats():
    """
    Calculate stats for the leaderboard.
    Returns: (stats_dict, last_worked_dict)
    
    stats_dict is organized as:
    {
        "All Time": {"Florian": {"total": 5, "sunday": 3, "friday": 2}, ...},
        "January 2026": {"Florian": {"total": 2, "sunday": 1, "friday": 1}, ...},
        ...
    }
    """
    # Initialize all-time stats
    all_time = {n: {"total": 0, "sunday": 0, "friday": 0} for n in ALL_NAMES if n != "TBD"}
    last_worked = {n: [] for n in ALL_NAMES}
    
    # Per-month stats
    monthly_stats = {}
    
    events = Event.query.order_by(Event.date).all()
    
    for event in events:
        d_obj = event.date
        month_key = d_obj.strftime("%B %Y")  # e.g., "January 2026"
        
        # Initialize month if not exists
        if month_key not in monthly_stats:
            monthly_stats[month_key] = {n: {"total": 0, "sunday": 0, "friday": 0} for n in ALL_NAMES if n != "TBD"}
        
        title = (event.custom_title or "").lower()
        is_sun = (event.day_type == "Sunday") or ("new year" in title) or (d_obj.weekday() == 6)
        is_fri = event.day_type == "Friday" or d_obj.weekday() == 4
        
        for a in event.assignments:
            p = a.person
            # Count the actual worker (cover or person)
            worker = a.cover if a.cover else p
            
            if worker in all_time and worker not in ("Select Helper", "TBD"):
                # All-time stats
                all_time[worker]["total"] += 1
                if is_sun:
                    all_time[worker]["sunday"] += 1
                if is_fri:
                    all_time[worker]["friday"] += 1
                
                # Monthly stats
                monthly_stats[month_key][worker]["total"] += 1
                if is_sun:
                    monthly_stats[month_key][worker]["sunday"] += 1
                if is_fri:
                    monthly_stats[month_key][worker]["friday"] += 1
                
                last_worked[worker].append(d_obj)
    
    # Combine into final stats dict
    stats = {"All Time": all_time}
    stats.update(monthly_stats)
    
    return stats, last_worked

