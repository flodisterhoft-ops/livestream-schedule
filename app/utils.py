import datetime
import os
import smtplib
import uuid
from flask import url_for
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from .models import Event, Assignment, Token
from .extensions import db
from sqlalchemy.exc import IntegrityError

# ============================================================
# Constants
# ============================================================
ROLES_CONFIG = {
    "Florian": {"sunday_roles": ["Computer"], "friday": True},
    "Andy": {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday": True},
    "Marvin": {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday": True},
    "Patric": {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday": True},
    "Rene": {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday": True},
    "Stefan": {"sunday_roles": ["Computer", "Camera 1", "Camera 2"], "friday": True},
    "Viktor": {"sunday_roles": ["Camera 2"], "friday": True},
}
ALL_NAMES = sorted(list(ROLES_CONFIG.keys()) + ["TBD"])
PC_ROTATION_ORDER = ["Florian", "Marvin", "Rene", "Stefan", "Andy", "Patric"]

BLACKOUTS = {
    "Florian": [(datetime.date(2025, 12, 12), datetime.date(2025, 12, 27))]
}

EMAIL_ADDRESS = os.environ.get("SCHEDULE_EMAIL", "flodisterhoft@gmail.com")
EMAIL_PASSWORD = os.environ.get("SCHEDULE_EMAIL_APP_PASSWORD", "REPLACE_ME")

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

def check_and_init():
    # Force Create Jan 1 (New Year's) if missing
    d1 = datetime.date(2026, 1, 1)
    if not Event.query.filter_by(date=d1).first():
        e1 = Event(date=d1, day_type="Custom", custom_title="New Year's Day Service")
        db.session.add(e1)
        db.session.commit() # Commit to get ID
        
        assigns = [
            Assignment(event_id=e1.id, role="Computer", person="Florian", status="confirmed"),
            Assignment(event_id=e1.id, role="Camera 1", person="Marvin", status="confirmed"),
            Assignment(event_id=e1.id, role="Camera 2", person="Viktor", status="confirmed"),
        ]
        db.session.add_all(assigns)
        db.session.commit()

    # Force Create Jan 4 (Sunday Service) if missing
    d2 = datetime.date(2026, 1, 4)
    if not Event.query.filter_by(date=d2).first():
        e2 = Event(date=d2, day_type="Sunday", custom_title="Sunday Service")
        db.session.add(e2)
        db.session.commit()
        
        assigns = [
            Assignment(event_id=e2.id, role="Computer", person="Stefan", status="confirmed"),
            Assignment(event_id=e2.id, role="Camera 1", person="Andy", status="confirmed"),
            Assignment(event_id=e2.id, role="Camera 2", person="Viktor", status="confirmed"),
        ]
        db.session.add_all(assigns)
        db.session.commit()

def send_access_email(magic_link, requester_name):
    subject = f"Access Request: {requester_name}"
    body = f"""
    <h3>Manager Access Request</h3>
    <p><b>{requester_name}</b> is requesting manager access.</p>
    <p>Forward this link to them (valid until you delete tokens):</p>
    <p>{magic_link}</p>
    <hr>
    <p><small><a href="{magic_link}">Login Link</a></small></p>
    """
    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = EMAIL_ADDRESS
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html"))
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Email Error: {e}")
        return False

def start_access_grant(user_name):
    token_str = str(uuid.uuid4())
    t = Token(token=token_str)
    db.session.add(t)
    db.session.commit()
    
    link = url_for('main.manager_login', token=token_str, _external=True)
    return send_access_email(link, user_name)

def verify_token(token_str):
    t = Token.query.filter_by(token=token_str).first()
    if t:
        db.session.delete(t)
        db.session.commit()
        return True
    return False

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

