# flask_app.py
from flask import (
    Flask,
    render_template_string,
    request,
    redirect,
    url_for,
    flash,
    session,
    make_response,
)
import json
import datetime
import os
import smtplib
import uuid
import calendar
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ============================================================
# App
# ============================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SCHEDULE_SECRET_KEY", "sona_media_team_secret_key")
app.permanent_session_lifetime = datetime.timedelta(days=90)

# ============================================================
# Email / Admin Secrets (prefer env vars on PythonAnywhere)
# ============================================================
EMAIL_ADDRESS = os.environ.get("SCHEDULE_EMAIL", "flodisterhoft@gmail.com")
EMAIL_PASSWORD = os.environ.get("SCHEDULE_EMAIL_APP_PASSWORD", "REPLACE_ME")
MANAGER_PASSWORD = os.environ.get("SCHEDULE_MANAGER_PASSWORD", "steroids")

# ============================================================
# Storage
# ============================================================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_FILE = os.path.join(BASE_DIR, "schedule_db.json")

# ============================================================
# Team
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

# ============================================================
# Fairness Policy (MONTH CAPS / PRIORITIES)
# ============================================================
# Your requirements:
# - "New Year's Day Service" counts like a Sunday service (toward Sunday caps).
# - Everyone: max 2 Sunday services/month (across ANY Sunday role).
# - Avoid back-to-back Sundays for the same person (hard rule, relaxed only if needed).
# - Friday Leader: max 1/month, and prioritize people who have been scheduled least for Sundays (within the month).
# - Monthly total event cap:
#     - Preferred core team: can be scheduled up to 3 total events/month
#     - Everyone else: cap at 2 total events/month
# - Prefer Florian/Marvin/Stefan to reach 3 total services rather than giving someone else a 3rd.
SUNDAY_CAP_PER_MONTH = 2
FRIDAY_LEADER_CAP_PER_MONTH = 1

PREFERRED_3_TOTAL = ["Florian", "Marvin", "Stefan"]
MONTH_TOTAL_CAP_DEFAULT = 2
MONTH_TOTAL_CAP_PREFERRED = 3

# Back-to-back Sunday guard (7 days = consecutive Sundays)
SUNDAY_MIN_GAP_DAYS = 8  # must be >= 8 days since last Sunday assignment to avoid "two in a row"


# ============================================================
# DB helpers
# ============================================================
def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_db(data):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass


def check_and_init():
    db = load_db()
    roster = db.get("roster", {})
    dirty = False

    # Force Create Jan 1 (New Year's) if missing
    if "January 01, 2026" not in roster:
        roster["January 01, 2026"] = {
            "day_type": "Custom",
            "custom_title": "New Year's Day Service",
            "assignments": [
                {"role": "Computer", "person": "Florian", "status": "confirmed", "cover": None},
                {"role": "Camera 1", "person": "Marvin", "status": "confirmed", "cover": None},
                {"role": "Camera 2", "person": "Viktor", "status": "confirmed", "cover": None},
            ],
        }
        dirty = True

    # Force Create Jan 4 (Sunday Service) if missing
    if "January 04, 2026" not in roster:
        roster["January 04, 2026"] = {
            "day_type": "Sunday",
            "custom_title": "Sunday Service",
            "assignments": [
                {"role": "Computer", "person": "Stefan", "status": "confirmed", "cover": None},
                {"role": "Camera 1", "person": "Andy", "status": "confirmed", "cover": None},
                {"role": "Camera 2", "person": "Viktor", "status": "confirmed", "cover": None},
            ],
        }
        dirty = True

    if dirty:
        db["roster"] = roster

    if "tokens" not in db:
        db["tokens"] = {}

    if dirty or ("tokens" not in load_db()):
        save_db(db)


def is_available(person, date_obj):
    if person not in BLACKOUTS:
        return True
    for start, end in BLACKOUTS[person]:
        if start <= date_obj <= end:
            return False
    return True


def _is_real_person(p: str) -> bool:
    return bool(p) and p in ROLES_CONFIG and p != "TBD" and p != "Select Helper"


# ============================================================
# Robust UNDO
# ============================================================
def _snapshot(a: dict) -> dict:
    return {
        "role": a.get("role"),
        "person": a.get("person"),
        "status": a.get("status"),
        "cover": a.get("cover"),
        "swapped_with": a.get("swapped_with"),
    }


def push_history(a: dict):
    hist = a.get("_hist")
    if not isinstance(hist, list):
        hist = []
    hist.append(_snapshot(a))
    if len(hist) > 10:
        hist = hist[-10:]
    a["_hist"] = hist


def pop_history(a: dict) -> bool:
    hist = a.get("_hist")
    if not isinstance(hist, list) or not hist:
        return False
    prev = hist.pop()
    a["role"] = prev.get("role")
    a["person"] = prev.get("person")
    a["status"] = prev.get("status")
    a["cover"] = prev.get("cover")
    if prev.get("swapped_with") is None:
        a.pop("swapped_with", None)
    else:
        a["swapped_with"] = prev.get("swapped_with")
    a["_hist"] = hist
    return True


# ============================================================
# Swap helper
# ============================================================
def get_user_swaps(roster, user_name, target_type, target_date_str):
    swaps = []
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%B %d, %Y")
    except Exception:
        return []

    sorted_dates = sorted(roster.keys(), key=lambda x: datetime.datetime.strptime(x, "%B %d, %Y"))

    for d_key in sorted_dates:
        d_obj = datetime.datetime.strptime(d_key, "%B %d, %Y")
        if d_obj <= target_date:
            continue

        data = roster[d_key]
        d_type = data.get("day_type", "Custom")
        is_sun = d_type == "Sunday" or d_obj.weekday() == 6
        is_fri = d_type == "Friday" or d_obj.weekday() == 4

        target_is_sun = target_type == "Sunday"
        target_is_fri = target_type == "Friday"

        if (target_is_sun and not is_sun) or (target_is_fri and not is_fri):
            continue

        event_name = data.get("custom_title")
        if not event_name:
            if is_fri:
                event_name = "Bible Study"
            elif is_sun:
                event_name = "Sunday Service"
            else:
                event_name = "Event"

        for a in data.get("assignments", []):
            if a.get("person") == user_name:
                swaps.append(
                    {
                        "date": d_key,
                        "event_name": event_name,
                        "readable_date": d_obj.strftime("%B %d"),
                    }
                )
    return swaps


# ============================================================
# HTMX helpers
# ============================================================
def mark_oob(html: str) -> str:
    if not html:
        return html
    if 'hx-swap-oob=' in html:
        return html
    return html.replace("<div ", '<div hx-swap-oob="true" ', 1)


# ============================================================
# Row renderer (HTMX)
# ============================================================
def render_row(assign, full_date, current_user, is_manager, roster_data=None):
    swap_options = []
    can_pickup = False

    if (
        assign.get("status") == "swap_needed"
        and current_user
        and current_user != assign.get("person")
        and roster_data
    ):
        day_type = roster_data.get(full_date, {}).get("day_type", "Custom")
        swap_options = get_user_swaps(roster_data, current_user, day_type, full_date)
        can_pickup = True

    return render_template_string(
        ROW_TEMPLATE,
        assign=assign,
        full_date=full_date,
        current_user=current_user,
        is_manager=is_manager,
        all_names=ALL_NAMES,
        swap_options=swap_options,
        can_pickup=can_pickup,
    )


# ============================================================
# Email
# ============================================================
def send_access_email(magic_link, requester_name):
    subject = f"Access Request: {requester_name}"
    body = f\"""
    <h3>Manager Access Request</h3>
    <p><b>{requester_name}</b> is requesting manager access.</p>
    <p>Forward this link to them (valid until you delete tokens):</p>
    <p>{magic_link}</p>
    <hr>
    <p><small><a href="{magic_link}">Login Link</a></small></p>
    \"""
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


# ============================================================
# Scheduling helpers (FAIRNESS)
# ============================================================
def get_history_stats(roster):
    stats = {n: {"total": 0, "sunday": 0, "friday": 0} for n in ALL_NAMES}
    last_worked = {n: [] for n in ALL_NAMES}
    sorted_dates = sorted(roster.keys(), key=lambda x: datetime.datetime.strptime(x, "%B %d, %Y"))
    for d_key in sorted_dates:
        data = roster[d_key]
        d_obj = datetime.datetime.strptime(d_key, "%B %d, %Y").date()

        # Treat Sunday-like services:
        # - day_type == Sunday
        # - OR custom title contains New Year's Day Service (counts as Sunday)
        # - OR actual weekday Sunday AND not Friday
        title = (data.get("custom_title") or "").lower()
        is_sun = (data.get("day_type") == "Sunday") or ("new year's day service" in title) or (d_obj.weekday() == 6)
        is_fri = data.get("day_type") == "Friday" or d_obj.weekday() == 4

        for a in data.get("assignments", []):
            p = a.get("person")
            if p in stats and p not in ("Select Helper", "TBD"):
                stats[p]["total"] += 1
                if is_sun:
                    stats[p]["sunday"] += 1
                if is_fri:
                    stats[p]["friday"] += 1
                last_worked[p].append(d_obj)
    return stats, last_worked


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


def get_next_pc(roster):
    sorted_dates = sorted(roster.keys(), key=lambda x: datetime.datetime.strptime(x, "%B %d, %Y"))
    for d_key in reversed(sorted_dates):
        data = roster[d_key]
        if data.get("day_type") == "Sunday":
            for a in data.get("assignments", []):
                if a.get("role") == "Computer" and a.get("person") in PC_ROTATION_ORDER:
                    return PC_ROTATION_ORDER.index(a["person"])
    return -1


def month_counts_from_existing(roster, year, month):
    \"""
    Seed counts from whatever already exists in the DB for that month.

    Tracks:
      - sun: counts toward "Sunday service cap" (includes New Year's Day Service)
      - fri_leader: only counts Friday Leader slot
      - total: total assignments in the month (any role)
      - last_sun_date: the last date this person served on a Sunday-like service in the month
    \"""
    counts = {n: {"sun": 0, "fri_leader": 0, "total": 0, "last_sun_date": None} for n in ALL_NAMES}

    for d_key, data in roster.items():
        try:
            d_obj_dt = datetime.datetime.strptime(d_key, "%B %d, %Y")
            d_obj = d_obj_dt.date()
        except Exception:
            continue

        if d_obj.year != year or d_obj.month != month:
            continue

        title = (data.get("custom_title") or "").lower()
        is_sun_like = (data.get("day_type") == "Sunday") or ("new year's day service" in title) or (d_obj.weekday() == 6)
        is_fri = (data.get("day_type") == "Friday") or (d_obj.weekday() == 4)

        for a in data.get("assignments", []):
            p = a.get("person")
            if not _is_real_person(p):
                continue

            counts[p]["total"] += 1

            if is_sun_like:
                counts[p]["sun"] += 1
                prev = counts[p]["last_sun_date"]
                if prev is None or d_obj > prev:
                    counts[p]["last_sun_date"] = d_obj

            if is_fri and a.get("role") == "Leader":
                counts[p]["fri_leader"] += 1

    return counts


def _month_total_cap(person: str) -> int:
    return MONTH_TOTAL_CAP_PREFERRED if person in PREFERRED_3_TOTAL else MONTH_TOTAL_CAP_DEFAULT


def _too_close_to_last_sunday(person: str, date_obj: datetime.date, local_month_stats: dict) -> bool:
    last_d = local_month_stats.get(person, {}).get("last_sun_date")
    if not last_d:
        return False
    return (date_obj - last_d).days < SUNDAY_MIN_GAP_DAYS


def generate_month(year, month):
    \"""
    Updated scheduler per your rules:
      - New Year's Day Service counts as a Sunday service for caps.
      - Everyone max 2 Sunday services/month.
      - No back-to-back Sundays for the same person (relax only if no valid candidates).
      - Friday Leader max 1/month and prioritize people with least Sunday count (in the month).
      - Monthly total cap: Preferred (Florian/Marvin/Stefan) can reach 3 total; others capped at 2.
      - Keep fatigue and all-time fairness as tie-breakers.
    \"""
    check_and_init()
    db = load_db()
    roster = db.get("roster", {})

    stats, last_worked = get_history_stats(roster)
    last_pc_idx = get_next_pc(roster)
    current_pc_idx = last_pc_idx + 1

    # Seed month counts from existing DB entries (including fixed Jan 1, Jan 4)
    local_month_stats = month_counts_from_existing(roster, year, month)

    num_days = calendar.monthrange(year, month)[1]
    dates = []
    for day in range(1, num_days + 1):
        d = datetime.date(year, month, day)
        if d.weekday() == 4:
            dates.append((d, "Friday"))
        elif d.weekday() == 6:
            dates.append((d, "Sunday"))

    def _preferred_bonus(person: str) -> int:
        # Prefer Florian/Marvin/Stefan to reach their 3 total assignments,
        # without violating Sunday caps or other hard rules.
        if person in PREFERRED_3_TOTAL:
            cap = _month_total_cap(person)
            if local_month_stats[person]["total"] < cap:
                return -400
        return 0

    def _month_total_cap_penalty(person: str) -> int:
        cap = _month_total_cap(person)
        over = max(0, local_month_stats[person]["total"] - (cap - 1))
        return over * 60000  # huge pressure against giving someone their (cap+1)th assignment

    def _month_sunday_cap_penalty(person: str) -> int:
        over = max(0, local_month_stats[person]["sun"] - (SUNDAY_CAP_PER_MONTH - 1))
        return over * 80000

    def _month_friday_cap_penalty(person: str) -> int:
        over = max(0, local_month_stats[person]["fri_leader"] - (FRIDAY_LEADER_CAP_PER_MONTH - 1))
        return over * 50000

    def get_score(person, role_type, date_obj, day_type):
        fatigue = get_fatigue_penalty(person, date_obj, last_worked)

        # All-time fairness (kept, but less dominant than violating your caps)
        fairness = stats[person]["total"] * 120

        # Month balancing: discourage piling on the same person in the same month
        month_total_pen = _month_total_cap_penalty(person)
        month_sun_pen = _month_sunday_cap_penalty(person) if day_type == "Sunday" else 0
        month_fri_pen = _month_friday_cap_penalty(person) if day_type == "Friday" else 0

        # Friday Leader priority: choose those with least Sunday count this month
        # (so Friday "spreads" work toward those not getting Sundays)
        friday_sunday_bias = 0
        if day_type == "Friday":
            friday_sunday_bias = local_month_stats[person]["sun"] * 1200

        # Avoid consecutive Sundays (hard) by heavy penalty (and we also filter it out in pick_best pass 1)
        consecutive_sun_pen = 0
        if day_type == "Sunday" and _too_close_to_last_sunday(person, date_obj, local_month_stats):
            consecutive_sun_pen = 200000

        # Specialist handling: Viktor can do Camera 2, but he must still respect caps
        specialist_bonus = 0
        if day_type == "Sunday" and role_type == "Camera 2":
            if len(ROLES_CONFIG[person]["sunday_roles"]) == 1:
                specialist_bonus = -300  # small preference, not overpowering caps

        pref_bonus = _preferred_bonus(person)

        return (
            fatigue
            + fairness
            + month_total_pen
            + month_sun_pen
            + month_fri_pen
            + friday_sunday_bias
            + consecutive_sun_pen
            + specialist_bonus
            + pref_bonus
        )

    def pick_best(cands, role, date_obj, day_type, exclude):
        valid = [p for p in cands if is_available(p, date_obj) and p not in exclude]

        def under_caps(p: str) -> bool:
            # Total cap
            if local_month_stats[p]["total"] >= _month_total_cap(p):
                return False

            # Sunday cap + no consecutive Sundays
            if day_type == "Sunday":
                if local_month_stats[p]["sun"] >= SUNDAY_CAP_PER_MONTH:
                    return False
                if _too_close_to_last_sunday(p, date_obj, local_month_stats):
                    return False

            # Friday leader cap
            if day_type == "Friday":
                if local_month_stats[p]["fri_leader"] >= FRIDAY_LEADER_CAP_PER_MONTH:
                    return False

            return True

        # Pass 1: strictly under caps (and no consecutive Sundays)
        pool = [p for p in valid if under_caps(p)]

        # Pass 2: relax consecutive Sundays only (still respect caps) if needed
        if not pool and day_type == "Sunday":
            def under_caps_relax_gap(p: str) -> bool:
                if local_month_stats[p]["total"] >= _month_total_cap(p):
                    return False
                if local_month_stats[p]["sun"] >= SUNDAY_CAP_PER_MONTH:
                    return False
                return True
            pool = [p for p in valid if under_caps_relax_gap(p)]

        # Pass 3: if still none, allow going over caps (rare) to avoid TBD
        if not pool:
            pool = valid

        pool.sort(key=lambda p: get_score(p, role, date_obj, day_type))
        return pool[0] if pool else "TBD"

    for date_obj, day_type in dates:
        date_key = date_obj.strftime("%B %d, %Y")
        if date_key in roster:
            continue

        assigned_today = []

        if day_type == "Friday":
            # Friday: only Leader is auto-assigned; Helper stays "Select Helper"
            pool = [p for p in ROLES_CONFIG.keys() if ROLES_CONFIG[p].get("friday")]

            leader = pick_best(pool, "Leader", date_obj, "Friday", exclude=[])

            roster[date_key] = {
                "day_type": "Friday",
                "assignments": [
                    {"role": "Leader", "person": leader, "status": "pending", "cover": None},
                    {"role": "Helper", "person": "Select Helper", "status": "pending", "cover": None},
                ],
            }

            if _is_real_person(leader):
                stats[leader]["total"] += 1
                stats[leader]["friday"] += 1

                local_month_stats[leader]["fri_leader"] += 1
                local_month_stats[leader]["total"] += 1

                last_worked[leader].append(date_obj)

        elif day_type == "Sunday":
            # PC: keep rotation, but enforce:
            # - Sunday cap (incl New Year's)
            # - total cap
            # - avoid consecutive Sundays
            pc_person = "TBD"
            attempts = 0
            tries = 0

            while tries < (len(PC_ROTATION_ORDER) * 2):
                cand = PC_ROTATION_ORDER[current_pc_idx % len(PC_ROTATION_ORDER)]
                current_pc_idx += 1
                tries += 1

                if not is_available(cand, date_obj):
                    continue

                # Under caps + no consecutive Sundays
                if local_month_stats[cand]["total"] >= _month_total_cap(cand):
                    continue
                if local_month_stats[cand]["sun"] >= SUNDAY_CAP_PER_MONTH:
                    continue
                if _too_close_to_last_sunday(cand, date_obj, local_month_stats):
                    continue

                fatigue = get_fatigue_penalty(cand, date_obj, last_worked)
                if fatigue >= 1500:
                    continue

                pc_person = cand
                break

            # Relax gap rule if we must, but still respect Sunday + total caps
            if pc_person == "TBD":
                tries = 0
                while tries < (len(PC_ROTATION_ORDER) * 2):
                    cand = PC_ROTATION_ORDER[current_pc_idx % len(PC_ROTATION_ORDER)]
                    current_pc_idx += 1
                    tries += 1

                    if not is_available(cand, date_obj):
                        continue
                    if local_month_stats[cand]["total"] >= _month_total_cap(cand):
                        continue
                    if local_month_stats[cand]["sun"] >= SUNDAY_CAP_PER_MONTH:
                        continue

                    pc_person = cand
                    break

            assigned_today.append(pc_person)
            if _is_real_person(pc_person):
                stats[pc_person]["total"] += 1
                stats[pc_person]["sunday"] += 1

                local_month_stats[pc_person]["sun"] += 1
                local_month_stats[pc_person]["total"] += 1
                local_month_stats[pc_person]["last_sun_date"] = date_obj

                last_worked[pc_person].append(date_obj)

            # Camera roles
            cam1_pool = [p for p in ROLES_CONFIG.keys() if "Camera 1" in ROLES_CONFIG[p]["sunday_roles"]]
            cam2_pool = [p for p in ROLES_CONFIG.keys() if "Camera 2" in ROLES_CONFIG[p]["sunday_roles"]]

            c1 = pick_best(cam1_pool, "Camera 1", date_obj, "Sunday", exclude=assigned_today)
            assigned_today.append(c1)
            if _is_real_person(c1):
                stats[c1]["total"] += 1
                stats[c1]["sunday"] += 1

                local_month_stats[c1]["sun"] += 1
                local_month_stats[c1]["total"] += 1
                local_month_stats[c1]["last_sun_date"] = date_obj

                last_worked[c1].append(date_obj)

            c2 = pick_best(cam2_pool, "Camera 2", date_obj, "Sunday", exclude=assigned_today)
            assigned_today.append(c2)
            if _is_real_person(c2):
                stats[c2]["total"] += 1
                stats[c2]["sunday"] += 1

                local_month_stats[c2]["sun"] += 1
                local_month_stats[c2]["total"] += 1
                local_month_stats[c2]["last_sun_date"] = date_obj

                last_worked[c2].append(date_obj)

            roster[date_key] = {
                "day_type": "Sunday",
                "assignments": [
                    {"role": "Computer", "person": pc_person, "status": "pending", "cover": None},
                    {"role": "Camera 1", "person": c1, "status": "pending", "cover": None},
                    {"role": "Camera 2", "person": c2, "status": "pending", "cover": None},
                ],
            }

    db["roster"] = roster
    save_db(db)
    return True


# ============================================================
# Routes
# ============================================================
@app.route("/")
def home():
    check_and_init()
    db = load_db()
    current_user = session.get("user_name")
    return render_page(db.get("roster", {}), is_preview=False, current_user=current_user)


@app.route("/set_identity/<name>")
def set_identity(name):
    session.permanent = True
    session["user_name"] = name
    session["manager"] = False
    flash(f"Welcome, {name}")
    return redirect(url_for("home"))


@app.route("/switch_user")
def switch_user():
    session.clear()
    return redirect(url_for("home"))


@app.route("/toggle_manager")
def toggle_manager():
    if session.get("user_name") == "Florian":
        session["manager"] = not session.get("manager")
        status = "enabled" if session["manager"] else "disabled"
        flash(f"Manager Mode {status}")
    return redirect(url_for("home"))


@app.route("/login", methods=["POST"])
def login():
    if request.form.get("password") == MANAGER_PASSWORD:
        session.permanent = True
        session["manager"] = True
        flash("Manager Unlocked")
    else:
        flash("Wrong Password")
    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session.pop("manager", None)
    flash("Manager Locked")
    return redirect(url_for("home"))


@app.route("/request_access", methods=["POST"])
def request_access():
    requester = session.get("user_name", "Unknown")
    token = str(uuid.uuid4())
    db = load_db()
    db.setdefault("tokens", {})
    db["tokens"][token] = datetime.datetime.now().strftime("%Y-%m-%d")
    save_db(db)

    link = url_for("magic_login", token=token, _external=True)

    if send_access_email(link, requester):
        flash("Admin notified!")
    else:
        flash("Email Error (check env vars + Gmail App Password)")

    return redirect(url_for("home"))


@app.route("/magic_login/<token>")
def magic_login(token):
    db = load_db()
    if token in db.get("tokens", {}):
        session.permanent = True
        session["manager"] = True
        session["user_name"] = "Florian"
        flash("Magic Login!")
    else:
        flash("Invalid Link")
    return redirect(url_for("home"))


@app.route("/generate_specific", methods=["POST"])
def generate_specific():
    if not session.get("manager"):
        return redirect(url_for("home"))
    try:
        month_str = request.form.get("gen_month")
        y, m = map(int, month_str.split("-"))
        generate_month(y, m)
        flash(f"Generated {month_str} CONFETTI")
    except Exception as e:
        flash(f"Error: {e}")
    return redirect(url_for("home"))


@app.route("/wipe_month", methods=["POST"])
def wipe_month():
    if not session.get("manager"):
        return redirect(url_for("home"))
    try:
        month_str = request.form.get("gen_month")
        if not month_str:
            return redirect(url_for("home"))
        y, m = map(int, month_str.split("-"))
        db = load_db()
        roster = db.get("roster", {})
        keys_to_delete = []
        for d_key in roster:
            try:
                d_obj = datetime.datetime.strptime(d_key, "%B %d, %Y")
                if d_obj.year == y and d_obj.month == m:
                    keys_to_delete.append(d_key)
            except Exception:
                continue
        for key in keys_to_delete:
            del roster[key]
        save_db(db)
        flash(f"Wiped all events for {month_str}")
    except Exception as e:
        flash(f"Error wiping month: {e}")
    return redirect(url_for("home"))


@app.route("/add_event", methods=["POST"])
def add_event():
    if not session.get("manager"):
        return redirect(url_for("home"))
    d_str = request.form.get("event_date")
    e_type = request.form.get("event_type")
    title = (request.form.get("custom_title") or "").strip()
    if not d_str:
        return redirect(url_for("home"))

    d_obj = datetime.datetime.strptime(d_str, "%Y-%m-%d")
    d_key = d_obj.strftime("%B %d, %Y")

    def mk(r, p="TBD"):
        return {"role": r, "person": p, "status": "pending", "cover": None}

    assigns = []
    d_type = "Custom"

    if e_type == "Friday":
        d_type = "Friday"
        assigns = [mk("Leader"), mk("Helper", "Select Helper")]
    elif e_type == "Sunday":
        d_type = "Sunday"
        assigns = [mk("Computer"), mk("Camera 1"), mk("Camera 2")]
    elif e_type == "Custom":
        if request.form.get("role_pc"):
            assigns.append(mk("Computer"))
        if request.form.get("role_cam1"):
            assigns.append(mk("Camera 1"))
        if request.form.get("role_cam2"):
            assigns.append(mk("Camera 2"))
        if not assigns:
            assigns = [mk("Team")]

    db = load_db()
    db.setdefault("roster", {})
    db["roster"][d_key] = {"day_type": d_type, "assignments": assigns}
    if title:
        db["roster"][d_key]["custom_title"] = title

    save_db(db)
    flash(f"Added {d_key}")
    return redirect(url_for("home"))


# ============================================================
# HTMX / Action Handling
# ============================================================
@app.route("/action", methods=["POST"])
def handle_action():
    a_type = request.form.get("type")
    d_key = request.form.get("date")
    idx = request.form.get("idx")
    curr = session.get("user_name")
    is_mgr = bool(session.get("manager"))

    if not curr:
        return redirect(url_for("home"))

    db = load_db()
    roster = db.get("roster", {})

    if d_key not in roster:
        return redirect(url_for("home"))

    assignments = roster[d_key].get("assignments", [])

    try:
        target_idx = int(idx)
    except Exception:
        target_idx = -1

    if not (0 <= target_idx < len(assignments)):
        return redirect(url_for("home"))

    target_a = assignments[target_idx]

    if not is_mgr:
        if a_type in ("confirm", "decline", "undo"):
            if target_a.get("person") != curr:
                return redirect(url_for("home"))
        elif a_type == "volunteer":
            if target_a.get("person") != "Select Helper":
                return redirect(url_for("home"))
        elif a_type in ("pickup", "swap_shift"):
            if target_a.get("status") != "swap_needed":
                return redirect(url_for("home"))
            if target_a.get("person") == curr:
                return redirect(url_for("home"))
        else:
            return redirect(url_for("home"))

    other_row_html = ""

    if a_type == "undo":
        pop_history(target_a)
    else:
        push_history(target_a)

        if a_type == "confirm":
            target_a["status"] = "confirmed"
            target_a["cover"] = None
            target_a.pop("swapped_with", None)

        elif a_type == "decline":
            target_a["status"] = "swap_needed"
            target_a["cover"] = None

        elif a_type == "volunteer":
            target_a["person"] = curr
            target_a["status"] = "confirmed"
            target_a["cover"] = None
            target_a.pop("swapped_with", None)

        elif a_type == "pickup":
            original_owner = target_a.get("person")
            target_a["person"] = curr
            target_a["status"] = "confirmed"
            target_a["cover"] = None
            target_a["swapped_with"] = original_owner

        elif a_type == "swap_shift":
            offer_date = request.form.get("swap_offer_date")
            if offer_date and offer_date in roster:
                offer_assignments = roster[offer_date].get("assignments", [])
                offer_a = None
                offer_idx = -1
                for i, oa in enumerate(offer_assignments):
                    if oa.get("person") == curr:
                        offer_a = oa
                        offer_idx = i
                        break

                if offer_a:
                    push_history(offer_a)
                    original_owner = target_a.get("person")
                    swapper = curr

                    target_a["person"] = swapper
                    target_a["status"] = "confirmed"
                    target_a["cover"] = None
                    target_a["swapped_with"] = original_owner

                    offer_a["person"] = original_owner
                    offer_a["status"] = "confirmed"
                    offer_a["cover"] = None
                    offer_a["swapped_with"] = swapper

                    offer_a_with_idx = offer_a.copy()
                    offer_a_with_idx["idx"] = offer_idx
                    other_row_html = render_row(offer_a_with_idx, offer_date, curr, is_mgr, roster)

    save_db(db)

    if request.headers.get("HX-Request"):
        target_for_render = target_a.copy()
        target_for_render["idx"] = target_idx
        target_row_html = render_row(target_for_render, d_key, curr, is_mgr, roster)

        resp = make_response(target_row_html)

        trigger_data = {}
        if a_type == "confirm":
            trigger_data["confetti"] = "simple"
        elif a_type in ("volunteer", "pickup"):
            trigger_data["confetti"] = "thankyou"

        if trigger_data:
            resp.headers["HX-Trigger"] = json.dumps(trigger_data)

        if other_row_html:
            resp.data += ("\n" + mark_oob(other_row_html)).encode("utf-8")

        return resp

    return redirect(url_for("home"))


@app.route("/update_person", methods=["POST"])
def update_person():
    d_key = request.form.get("date")
    idx = int(request.form.get("role_idx"))
    new_p = request.form.get("new_person")
    curr = session.get("user_name")
    is_mgr = bool(session.get("manager"))

    db = load_db()
    roster = db.get("roster", {})

    if d_key in roster:
        assignments = roster[d_key].get("assignments", [])
        if 0 <= idx < len(assignments):
            target_a = assignments[idx]
            current_p = target_a.get("person")

            if is_mgr or (current_p == "Select Helper" and new_p == curr):
                push_history(target_a)
                target_a["person"] = new_p
                target_a["cover"] = None
                target_a.pop("swapped_with", None)
                target_a["status"] = "confirmed" if current_p == "Select Helper" else "pending"
                save_db(db)

                if request.headers.get("HX-Request"):
                    target_a_with_idx = target_a.copy()
                    target_a_with_idx["idx"] = idx
                    return render_row(target_a_with_idx, d_key, curr, is_mgr, roster)

    return redirect(url_for("home"))


@app.route("/edit_title", methods=["POST"])
def edit_title():
    if not session.get("manager"):
        return redirect(url_for("home"))
    db = load_db()
    d_key = request.form.get("date")
    if d_key in db.get("roster", {}):
        db["roster"][d_key]["custom_title"] = request.form.get("new_title")
        save_db(db)
    return redirect(url_for("home"))


@app.route("/delete/<d_key>")
def delete_event(d_key):
    if not session.get("manager"):
        return redirect(url_for("home"))
    db = load_db()
    if d_key in db.get("roster", {}):
        del db["roster"][d_key]
        save_db(db)
    return redirect(url_for("home"))


@app.route("/delete_schedule")
def delete_schedule():
    if not session.get("manager"):
        return redirect(url_for("home"))
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    return redirect(url_for("home"))


# ============================================================
# Page renderer
# ============================================================
def render_page(roster, is_preview, current_user):
    today = datetime.date.today()

    stats = {}
    stats["All Time"] = {n: {"total": 0, "sunday": 0, "friday": 0} for n in ALL_NAMES if n != "TBD"}

    view_data = []

    if roster:
        sorted_items = sorted(roster.items(), key=lambda x: datetime.datetime.strptime(x[0], "%B %d, %Y"))

        for d_key, data in sorted_items:
            d_obj = datetime.datetime.strptime(d_key, "%B %d, %Y")
            month_key = d_obj.strftime("%B %Y")

            if month_key not in stats:
                stats[month_key] = {n: {"total": 0, "sunday": 0, "friday": 0} for n in ALL_NAMES if n != "TBD"}

            is_past = d_obj.date() < today

            title = data.get("custom_title") or ("Bible Study" if (data.get("day_type") == "Friday" or d_obj.weekday() == 4) else "Sunday Service")
            title_l = (title or "").lower()
            is_sunday = (data.get("day_type") == "Sunday") or ("new year's day service" in title_l) or (d_obj.weekday() == 6)
            is_friday = data.get("day_type") == "Friday" or d_obj.weekday() == 4

            assigns_idx = []
            for i, a in enumerate(data.get("assignments", [])):
                tmp = a.copy()
                tmp["idx"] = i
                assigns_idx.append(tmp)

                p = a.get("person")
                cv = a.get("cover")
                worker = cv if (cv and cv != "Unknown") else p

                if worker in ALL_NAMES and worker not in ("TBD", "Select Helper"):
                    stats["All Time"][worker]["total"] += 1
                    if is_sunday:
                        stats["All Time"][worker]["sunday"] += 1
                    if is_friday:
                        stats["All Time"][worker]["friday"] += 1

                    stats[month_key][worker]["total"] += 1
                    if is_sunday:
                        stats[month_key][worker]["sunday"] += 1
                    if is_friday:
                        stats[month_key][worker]["friday"] += 1

            view_data.append(
                {
                    "full_date": d_key,
                    "day_num": d_obj.strftime("%d"),
                    "month": d_obj.strftime("%b"),
                    "month_year": d_obj.strftime("%b %Y"),
                    "title": title,
                    "assignments": assigns_idx,
                    "raw_date": d_obj.strftime("%Y-%m-%d"),
                    "is_past": is_past,
                }
            )

    month_list = [k for k in stats.keys() if k != "All Time"]
    month_list.sort(key=lambda m: datetime.datetime.strptime(m, "%B %Y"))
    month_list.insert(0, "All Time")

    return render_template_string(
        HTML_TEMPLATE,
        schedule=view_data,
        is_preview=is_preview,
        all_names=ALL_NAMES,
        is_manager=session.get("manager"),
        stats=stats,
        month_list=month_list,
        current_user=current_user,
        roster_data=roster,
        render_row_fn=render_row,
    )


# ============================================================
# Templates
# ============================================================
ROW_TEMPLATE = """
<div class="role-row {% if assign.status == 'swap_needed' %}row-swap-needed{% endif %}"
     id="row-{{ full_date | replace(',','') | replace(' ','') }}-{{ assign.idx }}"
     data-name="{{ assign.person }}">

    <div style="flex-grow:1; display:flex; flex-direction:column;">
        <div class="role-left">
            {% set r = assign.role %}
            <span class="role-icon">
                {% if r == 'Computer' %}<i class="fas fa-desktop"></i>
                {% elif r == 'Camera 1' %}<i class="fas fa-video"></i>
                {% elif r == 'Camera 2' %}<i class="fas fa-video"></i>
                {% else %}<i class="fas fa-users"></i>
                {% endif %}
            </span>

            {% if is_manager %}
                <form hx-post="/update_person" hx-swap="outerHTML"
                      hx-target="#row-{{ full_date | replace(',','') | replace(' ','') }}-{{ assign.idx }}"
                      style="margin:0; flex-grow:1;" onclick="event.stopPropagation()">
                    <input type="hidden" name="date" value="{{ full_date }}">
                    <input type="hidden" name="role_idx" value="{{ assign.idx }}">
                    <select name="new_person" onchange="this.form.requestSubmit()" class="modern-select"
                            style="padding:0; margin:0; width:100%; background:transparent; border:none; font-weight:500; color:{% if assign.person == 'Select Helper' %}var(--open-text){% else %}#e2e8f0{% endif %};">
                        {% if assign.person == 'Select Helper' %}
                            <option value="Select Helper" disabled selected>Helper (Unassigned)</option>
                        {% endif %}
                        {% for name in all_names %}
                            <option value="{{ name }}" {% if name == assign.person %}selected{% endif %}>{{ name }}</option>
                        {% endfor %}
                    </select>
                </form>
            {% else %}
                {% if assign.person == 'Select Helper' %}
                    <span style="color:var(--open-text); font-style:italic;">Volunteer Needed</span>
                {% else %}
                    {% if assign.swapped_with %}
                        <span class="person-name">
                            <s style="opacity:.7;">{{ assign.swapped_with }}</s>
                            <span style="color:var(--muted); font-size:0.78rem; margin-left:6px; font-weight:600;">(⇄ {{ assign.person }})</span>
                        </span>
                    {% else %}
                        <span class="person-name">{{ assign.person }}</span>
                    {% endif %}
                {% endif %}
            {% endif %}

            {% if assign.cover %}
                <span style="color:var(--highlight); font-size:0.75rem; margin-left:5px; font-weight:bold;">(🔄 {{ assign.cover }})</span>
            {% endif %}
        </div>

        {# Swap-needed UI for OTHER users #}
        {% if assign.status == 'swap_needed' and current_user and current_user != assign.person %}
            <div class="swap-ui" style="margin-left:26px; margin-top:6px;">
                {% if swap_options %}
                    <form hx-post="/action"
                          hx-target="#row-{{ full_date | replace(',','') | replace(' ','') }}-{{ assign.idx }}"
                          hx-swap="outerHTML"
                          style="display:flex; gap:6px; align-items:center;">
                        <input type="hidden" name="type" value="swap_shift">
                        <input type="hidden" name="date" value="{{ full_date }}">
                        <input type="hidden" name="idx" value="{{ assign.idx }}">

                        <select name="swap_offer_date" class="modern-select"
                                style="padding:4px; font-size:0.8rem; width:auto; background:rgba(0,0,0,0.3);">
                            {% for s in swap_options %}
                                <option value="{{ s.date }}">Swap with my {{ s.readable_date }} {{ s.event_name }}</option>
                            {% endfor %}
                        </select>
                        <button class="action-btn btn-take" style="font-size:0.7rem; padding:0 10px;">Swap</button>
                    </form>
                {% else %}
                    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
                        <span style="color:var(--muted); font-size:0.8rem;">Pick up {{ assign.person }}'s shift</span>
                        <button class="action-btn btn-take"
                                hx-post="/action"
                                hx-vals='{"type":"pickup","date":"{{ full_date }}","idx":"{{ assign.idx }}"}'
                                hx-target="#row-{{ full_date | replace(",","") | replace(" ","") }}-{{ assign.idx }}"
                                hx-swap="outerHTML"
                                style="font-size:0.7rem; padding:0 10px;">Pick up</button>
                    </div>
                {% endif %}
            </div>
        {% endif %}
    </div>

    <div class="action-group">
        {% if assign.person == 'Select Helper' %}
            {% if not is_manager %}
                <button class="action-btn btn-help"
                        hx-post="/action"
                        hx-vals='{"type":"volunteer","date":"{{ full_date }}","idx":"{{ assign.idx }}"}'
                        hx-target="#row-{{ full_date | replace(",","") | replace(" ","") }}-{{ assign.idx }}"
                        hx-swap="outerHTML"
                        title="I can help">
                    <i class="fas fa-hand-paper"></i>
                </button>
            {% endif %}

        {% elif assign.status == 'confirmed' %}
            {% if is_manager or assign.person == current_user %}
                <button class="action-btn btn-undo"
                        hx-post="/action"
                        hx-vals='{"type":"undo","date":"{{ full_date }}","idx":"{{ assign.idx }}"}'
                        hx-target="#row-{{ full_date | replace(",","") | replace(" ","") }}-{{ assign.idx }}"
                        hx-swap="outerHTML"
                        title="Undo"><i class="fas fa-undo"></i></button>
            {% else %}
                <div class="status-badge st-ok"><i class="fas fa-check"></i></div>
            {% endif %}

        {% elif assign.status == 'swap_needed' %}
            {% if assign.person == current_user %}
                <button class="action-btn btn-confirm"
                        hx-post="/action"
                        hx-vals='{"type":"undo","date":"{{ full_date }}","idx":"{{ assign.idx }}"}'
                        hx-target="#row-{{ full_date | replace(",","") | replace(" ","") }}-{{ assign.idx }}"
                        hx-swap="outerHTML"
                        title="Undo Can't Make It"><i class="fas fa-undo"></i></button>
            {% else %}
                <div class="status-badge st-swap" title="Needs coverage"><i class="fas fa-exclamation"></i></div>
            {% endif %}

        {% else %}
            {% if is_manager or assign.person == current_user %}
                <button class="action-btn btn-confirm"
                        hx-post="/action"
                        hx-vals='{"type":"confirm","date":"{{ full_date }}","idx":"{{ assign.idx }}"}'
                        hx-target="#row-{{ full_date | replace(",","") | replace(" ","") }}-{{ assign.idx }}"
                        hx-swap="outerHTML"><i class="fas fa-check"></i></button>

                <button class="action-btn btn-decline"
                        hx-post="/action"
                        hx-vals='{"type":"decline","date":"{{ full_date }}","idx":"{{ assign.idx }}"}'
                        hx-target="#row-{{ full_date | replace(",","") | replace(" ","") }}-{{ assign.idx }}"
                        hx-swap="outerHTML"
                        title="I can't make it"><i class="fas fa-times"></i></button>
            {% else %}
                <span style="font-size:0.7rem; color:var(--muted); opacity:0.5;">Pending</span>
            {% endif %}
        {% endif %}
    </div>
</div>
"""

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">

    <meta property="og:title" content="Media Team Schedule">
    <meta property="og:description" content="Check your dates, swap shifts, and view the roster for upcoming services.">
    <meta property="og:type" content="website">
    <title>Livestream Schedule</title>
    ...>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Livestream Schedule</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/canvas-confetti@1.6.0/dist/confetti.browser.min.js"></script>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <style>
        :root {
            --bg: #0f172a;
            --card-bg: rgba(30, 41, 59, 0.6);
            --accent: #38bdf8;
            --text: #f1f5f9;
            --muted: #94a3b8;
            --highlight: #facc15;
            --glass: blur(12px) saturate(180%);
            --open-text: #fb923c;
        }
        body { margin:0; font-family: 'Inter', 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding-bottom: 80px; -webkit-tap-highlight-color: transparent; }
        .container { max-width: 550px; margin: 0 auto; padding: 15px; }

        header { display: flex; justify-content: space-between; align-items: center; padding: 15px 0; margin-bottom: 5px; }
        .app-title { font-size: 1.4rem; font-weight: 800; background: linear-gradient(135deg, #38bdf8, #a855f7); -webkit-background-clip: text; color: transparent; margin: 0; letter-spacing: -0.5px; }
        .app-title a { text-decoration: none; color: inherit; -webkit-background-clip: text; background-clip: text; }

        .tool-bar { display: flex; gap: 8px; align-items: center; }
        .tool-btn { background: rgba(51, 65, 85, 0.5); border: 1px solid rgba(255,255,255,0.1); color: var(--text); width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; font-size: 1rem; transition: 0.2s; }
        .tool-btn:active { transform: scale(0.95); background: var(--accent); color: #000; }

        .user-btn { width: 36px; height: 36px; border-radius: 50%; background: var(--accent); color: #0f172a; display: flex; align-items: center; justify-content: center; cursor: pointer; font-size: 1.1rem; border: none; transition: 0.2s; }
        .user-btn:active { transform: scale(0.95); opacity: 0.8; }

        .filter-container { margin-bottom: 15px; display: flex; flex-direction: column; gap: 10px; }
        .modern-select { background: rgba(30, 41, 59, 0.8); color: white; border: 1px solid rgba(255,255,255,0.1); padding: 10px 14px; border-radius: 12px; font-size: 0.9rem; outline: none; width: 100%; backdrop-filter: var(--glass); -webkit-appearance: none; }

        .month-nav { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 5px; scrollbar-width: none; margin-bottom: 10px; }
        .month-pill { background: rgba(30, 41, 59, 0.5); border: 1px solid rgba(255,255,255,0.1); padding: 6px 14px; border-radius: 20px; font-size: 0.85rem; color: var(--muted); cursor: pointer; white-space: nowrap; transition: 0.3s; font-weight: 600; }
        .month-pill.active { background: var(--accent); color: #0f172a; border-color: var(--accent); box-shadow: 0 0 10px rgba(56, 189, 248, 0.4); }

        .admin-panel { background: rgba(30, 41, 59, 0.4); padding: 15px; border-radius: 16px; margin-bottom: 20px; border: 1px solid rgba(56, 189, 248, 0.2); }
        .admin-panel h4 { margin: 0 0 10px 0; color: var(--accent); font-size: 0.9rem; text-transform: uppercase; letter-spacing: 1px; }

        .timeline { display: flex; flex-direction: column; gap: 10px; }
        .card { background: var(--card-bg); border: 1px solid rgba(255,255,255,0.05); border-radius: 16px; padding: 12px; display: flex; gap: 12px; align-items: flex-start; backdrop-filter: var(--glass); box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
        .card.hidden-month { display: none !important; }

        .card.past { opacity: 0.5; filter: grayscale(100%); }
        .card.past .action-btn { display:none; }
        {% if is_manager %} .card.past { pointer-events: auto; opacity: 0.7; } {% endif %}

        .date-col { text-align: center; min-width: 42px; display: flex; flex-direction: column; justify-content: center; align-items: center; background: rgba(0,0,0,0.2); border-radius: 10px; padding: 8px 4px; height: fit-content; }
        .day-num { font-size: 1.3rem; font-weight: 800; display: block; line-height: 1; color: var(--text); }
        .day-month { font-size: 0.7rem; color: var(--accent); text-transform: uppercase; font-weight: 700; margin-top: 2px; }

        .info-col { flex-grow: 1; min-width: 0; }
        .card-header { display: flex; justify-content: space-between; margin-bottom: 6px; align-items: center; }
        .event-title { font-weight: 700; font-size: 1rem; color: white; letter-spacing: -0.2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

        .role-row { display: flex; justify-content: space-between; align-items: flex-start; padding: 8px 0; gap: 8px; border-bottom: 1px solid rgba(255,255,255,0.05); transition: background 0.2s; }
        .role-row:last-child { border-bottom: none; }
        .role-row.highlight-yellow { background: rgba(250, 204, 21, 0.12); border-radius: 6px; padding-left: 5px; padding-right: 5px; margin: 0 -5px; }

        @keyframes pulse-red {
            0%   { background: rgba(239, 68, 68, 0.10); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.35); }
            70%  { background: rgba(239, 68, 68, 0.22); box-shadow: 0 0 0 6px rgba(239, 68, 68, 0); }
            100% { background: rgba(239, 68, 68, 0.10); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
        }
        .row-swap-needed { animation: pulse-red 2s infinite; border-radius: 8px; margin: 0 -5px; padding: 4px 5px; border: 1px solid rgba(239, 68, 68, 0.25); }
        .row-swap-needed:hover { background: rgba(239, 68, 68, 0.18); }

        .person-name { font-weight: 500; font-size: 0.95rem; color: #e2e8f0; }

        .action-group { display: flex; gap: 5px; flex-shrink: 0; }
        .action-btn { width: 28px; height: 28px; border-radius: 8px; border: none; cursor: pointer; color: white; display: flex; align-items: center; justify-content: center; font-size: 0.8rem; transition: 0.15s; }
        .action-btn:active { transform: scale(0.96); }
        .btn-confirm { background: linear-gradient(135deg, #10b981, #059669); }
        .btn-decline { background: rgba(51, 65, 85, 0.8); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }
        .btn-take { background: var(--accent); color: #0f172a; width: auto; padding: 0 10px; font-weight: 800; font-size: 0.75rem; text-transform: uppercase; }
        .btn-help { background: var(--open-text); color: #0f172a; font-size: 1rem; }
        .btn-undo { background: transparent; color: #f8fafc; border: 1px solid rgba(255,255,255,0.22); }

        .status-badge { font-size: 0.75rem; font-weight: bold; display: inline-flex; align-items: center; gap: 4px; padding: 2px 6px; border-radius: 6px; }
        .st-ok { background: rgba(16, 185, 129, 0.15); color: #10b981; }
        .st-swap { background: rgba(239, 68, 68, 0.15); color: #ef4444; }

        .role-icon { width: 20px; text-align: center; color: var(--muted); font-size: 0.8rem; margin-right: 6px; }
        .role-left { display: flex; align-items: center; overflow: hidden; white-space: nowrap; flex-wrap: wrap; }

        .mgr-only { display: none; }
        {% if is_manager %} .mgr-only { display: inline-block; } {% endif %}

        .modal { display: none; position: fixed; top:0; left:0; width:100%; height:100%; background: rgba(0,0,0,0.8); z-index: 1000; justify-content: center; align-items: center; backdrop-filter: blur(8px); }
        .modal-content { background: #1e293b; padding: 25px; border-radius: 20px; width: 85%; max-width: 340px; text-align: center; border: 1px solid rgba(255,255,255,0.1); box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5); }
        input, select { width: 100%; padding: 12px; margin: 8px 0; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 12px; box-sizing: border-box; font-size: 1rem; outline: none; }
        .modal-btn { width: 100%; padding: 12px; border-radius: 12px; border: none; cursor: pointer; font-weight: bold; margin-top: 5px; font-size: 1rem; transition: 0.2s; box-sizing: border-box; }
        .save-btn { background: var(--accent); color: #0f172a; }

        .lb-row { display: flex; justify-content: space-between; padding: 10px 5px; border-bottom: 1px solid rgba(255,255,255,0.05); align-items: center; }
        .lb-name { font-weight: 600; color: white; flex: 1; text-align: left; }
        .lb-val-group { display: flex; gap: 8px; font-size: 0.8rem; color: var(--muted); align-items: center; }
        .lb-total { font-weight: 800; color: var(--accent); font-size: 1.1rem; min-width: 20px; text-align: right; }
        .hidden { display: none !important; }

        .identity-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: #0f172a; z-index: 9999; display: flex; flex-direction: column; align-items: center; padding-top: 40px; }
        .id-card { background: rgba(30, 41, 59, 0.8); border: 1px solid rgba(255,255,255,0.1); padding: 15px 25px; border-radius: 16px; width: 80%; max-width: 300px; margin-bottom: 12px; text-align: center; color: white; font-weight: 600; font-size: 1.1rem; text-decoration: none; transition: 0.2s; backdrop-filter: blur(10px); }
        .id-card:active { transform: scale(0.96); background: var(--accent); color: #000; }
        .id-admin { border-color: var(--accent); color: white; }

        #flashMsg { transition: opacity 0.5s ease-out, max-height 0.5s ease-out, margin 0.5s ease-out, padding 0.5s ease-out; opacity: 1; max-height: 100px; overflow: hidden; }
        .fade-out { opacity: 0 !important; max-height: 0 !important; margin-top: 0 !important; margin-bottom: 0 !important; padding-top: 0 !important; padding-bottom: 0 !important; }
    </style>
</head>
<body>

{% if not current_user %}
<div class="identity-overlay">
    <h2 style="color:white; margin-bottom: 30px;">Who are you?</h2>
    <a href="/set_identity/Florian" class="id-card id-admin">Florian <span style="font-size:0.8rem; opacity:0.7; font-weight:400;">(Admin)</span></a>
    {% for name in all_names %}
        {% if name != 'Florian' and name != 'TBD' %}
            <a href="/set_identity/{{ name }}" class="id-card">{{ name }}</a>
        {% endif %}
    {% endfor %}
</div>
{% endif %}

<div class="container" id="mainContainer">
    <header>
        <div class="app-title"><a href="/">Livestream Schedule</a></div>
        <div class="tool-bar">
            <div class="tool-btn" onclick="toggleLeaderboard()"><i class="fas fa-trophy"></i></div>
            {% if current_user %}
                <div class="user-btn" onclick="openUserMenu()">
                    <i class="fas fa-user-circle"></i>
                </div>
            {% endif %}
        </div>
    </header>

    {% if is_manager %}
    <div class="admin-panel">
        <h4><i class="fas fa-tools"></i> Manager</h4>
        <div style="display:flex; gap:10px; margin-bottom:10px;">
            <form action="/generate_specific" method="POST" style="flex-grow:1; display:flex; gap:5px;">
                <input type="month" name="gen_month" id="adminMonthInput" value="2026-02" style="margin:0;">
                <button class="modal-btn save-btn" style="width:auto; margin:0; padding:0 15px;"><i class="fas fa-bolt"></i></button>
            </form>
        </div>
        <div style="display:flex; gap:10px; overflow-x:auto;">
            <button onclick="openAddModal()" class="month-pill" style="background:#334155; color:white;"><i class="fas fa-plus"></i> Add Event</button>
            <button class="month-pill" style="background:rgba(239, 68, 68, 0.2); color:#ef4444; border-color:rgba(239, 68, 68, 0.3);" onclick="wipeSelectedMonth()"><i class="fas fa-trash"></i> Wipe</button>
        </div>
    </div>
    {% endif %}

    <div class="filter-container">
        <select class="modern-select" id="focusFilter" onchange="applyFocus()">
            <option value="all">👁️ Show Everyone</option>
            {% for name in all_names %}{% if name != 'TBD' %}<option value="{{ name }}">{{ name }}</option>{% endif %}{% endfor %}
        </select>

        <div class="month-nav" id="monthNav"></div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% set msg_text = messages[0] | replace("CONFETTI", "") | trim %}
        {% if msg_text %}
        <div id="flashMsg" style="background: rgba(0,0,0,0.65); color:white; padding:30px; border-radius:16px; margin-bottom:15px; text-align:center; font-weight:800; font-size:1.5rem; backdrop-filter:blur(4px); border:1px solid rgba(255,255,255,0.1); position:fixed; top:50%; left:50%; transform:translate(-50%, -50%); z-index:9999; min-width:300px;">
            {{ msg_text }}
        </div>
        {% endif %}
        {% if "CONFETTI" in messages[0] %}<script>confetti({particleCount: 150, spread: 70, origin: { y: 0.6 }});</script>{% endif %}
      {% endif %}
    {% endwith %}

    <div class="timeline">
        {% if not schedule %}
            <div style="text-align:center; margin-top: 40px; padding: 30px; border-radius:15px; background:rgba(255,255,255,0.03);">
                <i class="fas fa-calendar-times fa-3x" style="color:var(--muted); margin-bottom:15px; opacity:0.3;"></i>
                <p style="color:var(--muted);">No events found.</p>
            </div>
        {% endif %}

        {% for item in schedule %}
        <div class="card {% if item.is_past %}past{% endif %}"
             data-people="{{ item.assignments | map(attribute='person') | join(',') }}"
             data-month="{{ item.month_year }}">
            <div class="date-col">
                <span class="day-num">{{ item.day_num }}</span>
                <span class="day-month">{{ item.month }}</span>
            </div>
            <div class="info-col">
                <div class="card-header">
                    <div class="event-title">{{ item.title }}
                        <span class="mgr-only">
                            <i class="fas fa-pen" style="font-size:0.7rem; color:var(--muted); cursor:pointer; margin-left:5px;" onclick="openTitleModal('{{ item.full_date }}', '{{ item.title }}')"></i>
                        </span>
                    </div>
                    <div class="mgr-only">
                        <a href="/delete/{{ item.full_date }}" style="color:#ef4444; opacity:0.6;" onclick="return confirm('Delete?')"><i class="fas fa-times"></i></a>
                    </div>
                </div>

                {% for assign in item.assignments %}
                    {{ render_row_fn(assign, item.full_date, current_user, is_manager, roster_data) | safe }}
                {% endfor %}
            </div>
        </div>
        {% endfor %}
    </div>
</div>

<div class="modal" id="lbModal"><div class="modal-content">
    <h3 style="color:var(--accent);">Team Stats</h3>
    <select id="lbPeriodSelect" onchange="updateLbView()" style="margin-bottom:15px; background:#334155;">
        {% for m in month_list %}
            <option value="{{ m }}">{{ m }}</option>
        {% endfor %}
    </select>
    <div id="lb-list"></div>
    <button class="modal-btn" style="background:transparent; color:var(--muted);" onclick="closeModal('lbModal')">Close</button>
</div></div>

<div class="modal" id="userMenuModal"><div class="modal-content">
    <h3><i class="fas fa-user-circle"></i> {{ current_user }}</h3>
    {% if current_user == 'Florian' %}
        <a href="/toggle_manager" class="modal-btn save-btn" style="text-decoration:none; display:block; margin-bottom:10px;">
            {% if is_manager %}Disable Manager Mode{% else %}Enable Manager Mode{% endif %}
        </a>
    {% else %}
        <form action="/request_access" method="POST" onsubmit="closeModal('userMenuModal')">
            <button class="modal-btn" style="border:1px solid rgba(255,255,255,0.2); background:transparent; color:white; margin-bottom:10px;">Request Manager Access</button>
        </form>
    {% endif %}
    <a href="/switch_user" class="modal-btn" style="background:rgba(239, 68, 68, 0.2); color:#ef4444; text-decoration:none; display:flex; justify-content:center; align-items:center; text-align:center;">Logout</a>
    <button class="modal-btn" style="background:transparent; color:var(--muted); margin-top:10px;" onclick="closeModal('userMenuModal')">Close</button>
</div></div>

<div class="modal" id="addModal"><div class="modal-content">
    <h3>Add Event</h3>
    <form action="/add_event" method="POST" onsubmit="saveScrollPosition()">
      <input type="date" name="event_date" required>
      <select name="event_type" id="eventTypeSelect" onchange="syncAddOptions()">
        <option value="Sunday">Sunday Service</option>
        <option value="Friday">Friday Bible Study</option>
        <option value="Custom">Custom</option>
      </select>
      <input type="text" name="custom_title" placeholder="Title (optional)">
      <div id="customRoleBox" class="hidden" style="text-align:left; margin-top:10px;">
        <div style="display:flex; gap:8px; margin-bottom:8px; flex-wrap:wrap; justify-content:center;">
          <label><input type="checkbox" name="role_pc" value="1" checked> PC</label>
          <label><input type="checkbox" name="role_cam1" value="1" checked> Cam 1</label>
          <label><input type="checkbox" name="role_cam2" value="1" checked> Cam 2</label>
        </div>
      </div>
      <button type="submit" class="modal-btn save-btn">Create</button>
    </form>
    <button class="modal-btn" style="background:transparent; color:var(--muted);" onclick="closeModal('addModal')">Cancel</button>
</div></div>

<div class="modal" id="titleModal"><div class="modal-content">
  <h3>Rename</h3>
  <form action="/edit_title" method="POST">
    <input type="hidden" name="date" id="titleDateInput">
    <input type="text" name="new_title" id="titleTextInput">
    <button class="modal-btn save-btn">Save</button>
  </form>
  <button class="modal-btn" style="background:transparent; color:var(--muted);" onclick="closeModal('titleModal')">Cancel</button>
</div></div>

<script>
    const STATS_DATA = {{ stats | tojson }};

    document.addEventListener("DOMContentLoaded", function() {
        let scrollPos = sessionStorage.getItem('scrollPos');
        if (scrollPos) { window.scrollTo(0, scrollPos); sessionStorage.removeItem('scrollPos'); }

        let flash = document.getElementById('flashMsg');
        if(flash) {
            setTimeout(() => { flash.classList.add('fade-out'); }, 500);
            setTimeout(() => { flash.style.display='none'; }, 1000);
        }

        syncAddOptions();
        initMonthNav();
    });

    document.body.addEventListener("confetti", function(evt){
        var type = evt.detail.value;
        if(type === "simple") {
            confetti({ particleCount: 80, spread: 60, origin: { y: 0.6 } });
        } else if(type === "thankyou") {
            confetti({ particleCount: 150, spread: 100, origin: { y: 0.6 }, startVelocity: 30 });
            showFlash("Thank you! 🙌");
        }
    });

    function showFlash(msg) {
        let f = document.getElementById('flashMsg');
        if(!f) {
            f = document.createElement('div');
            f.id = 'flashMsg';
            f.style.cssText = "background: rgba(0,0,0,0.65); color:white; padding:30px; border-radius:16px; margin-bottom:15px; text-align:center; font-weight:800; font-size:1.5rem; backdrop-filter:blur(4px); border:1px solid rgba(255,255,255,0.1); position:fixed; top:50%; left:50%; transform:translate(-50%, -50%); z-index:9999; min-width:300px;";
            document.body.appendChild(f);
        }
        f.innerText = msg;
        f.style.display = 'block';
        f.classList.remove('fade-out');
        f.style.opacity = '1';
        f.style.maxHeight = '1000px';

        setTimeout(() => { f.classList.add('fade-out'); }, 500);
        setTimeout(() => { f.style.display='none'; }, 1000);
    }

    function initMonthNav() {
        const nav = document.getElementById('monthNav');
        const cards = document.querySelectorAll('.card');
        const months = new Set();
        cards.forEach(c => months.add(c.getAttribute('data-month')));
        const sortedMonths = Array.from(months).sort((a,b) => new Date(a) - new Date(b));
        if(sortedMonths.length === 0) { nav.style.display = 'none'; return; }

        sortedMonths.forEach(m => {
            const btn = document.createElement('div');
            btn.className = 'month-pill';
            btn.innerText = m;
            btn.onclick = () => selectMonth(m);
            nav.appendChild(btn);
        });

        const nowStr = new Date().toLocaleString('default', { month: 'short', year: 'numeric' });
        if(months.has(nowStr)) selectMonth(nowStr);
        else selectMonth(sortedMonths[sortedMonths.length - 1]);
    }

    function selectMonth(mStr) {
        document.querySelectorAll('.month-pill').forEach(b => {
            if(b.innerText === mStr) b.classList.add('active');
            else b.classList.remove('active');
        });
        document.querySelectorAll('.card').forEach(c => {
            if(c.getAttribute('data-month') === mStr) c.classList.remove('hidden-month');
            else c.classList.add('hidden-month');
        });
        applyFocus();
    }

    function wipeSelectedMonth() {
        const monthInput = document.getElementById('adminMonthInput');
        if (!monthInput) return;

        const val = monthInput.value;
        if (confirm('Are you sure you want to WIPE all events for ' + val + '?')) {
            const f = document.createElement('form');
            f.method = 'POST';
            f.action = '/wipe_month';

            const i = document.createElement('input');
            i.type = 'hidden';
            i.name = 'gen_month';
            i.value = val;

            f.appendChild(i);
            document.body.appendChild(f);
            f.submit();
        }
    }

    function saveScrollPosition() { sessionStorage.setItem('scrollPos', window.scrollY); }
    function closeModal(id) { document.getElementById(id).style.display = 'none'; }
    function syncAddOptions() {
        const sel = document.getElementById('eventTypeSelect');
        const box = document.getElementById('customRoleBox');
        if (!sel || !box) return;
        box.classList.toggle('hidden', sel.value !== 'Custom');
    }
    function openAddModal() { document.getElementById('addModal').style.display = 'flex'; syncAddOptions(); }
    function openTitleModal(d, t) { document.getElementById('titleModal').style.display='flex'; document.getElementById('titleDateInput').value=d; document.getElementById('titleTextInput').value=t; }
    function openUserMenu() { document.getElementById('userMenuModal').style.display = 'flex'; }

    function applyFocus() {
        let name = document.getElementById('focusFilter').value;
        document.querySelectorAll('.card:not(.hidden-month)').forEach(card => {
            const hasPerson = card.getAttribute('data-people').includes(name);
            card.style.display = (name === 'all' || hasPerson) ? 'flex' : 'none';
        });
        document.querySelectorAll('.role-row').forEach(row => {
            if (name !== 'all' && row.getAttribute('data-name') === name) row.classList.add('highlight-yellow');
            else row.classList.remove('highlight-yellow');
        });
    }

    function toggleLeaderboard() { document.getElementById('lbModal').style.display = 'flex'; updateLbView(); }
    function updateLbView() {
        const period = document.getElementById('lbPeriodSelect').value;
        const list = document.getElementById('lb-list');
        list.innerHTML = "";
        let arr = [];

        if (STATS_DATA[period]) {
            for (const [name, data] of Object.entries(STATS_DATA[period])) {
                arr.push({ name: name, count: data.total, sun: data.sunday, fri: data.friday });
            }
        }

        arr.sort((a,b) => b.count - a.count);
        arr.forEach(p => {
            if(p.count > 0) {
                list.innerHTML += `
                <div class="lb-row">
                    <div class="lb-name">${p.name}</div>
                    <div class="lb-val-group">
                        <span><i class="fas fa-church"></i> ${p.sun}</span>
                        <span><i class="fas fa-book"></i> ${p.fri}</span>
                        <div class="lb-total">${p.count}</div>
                    </div>
                </div>`;
            }
        });
        if(arr.length === 0 || arr.every(p => p.count === 0)) list.innerHTML = `<p style="color:var(--muted); margin-top:15px;">No stats for ${period}</p>`;
    }

    document.body.addEventListener('htmx:beforeRequest', function(evt) {
        document.body.style.cursor = 'progress';
    });
    document.body.addEventListener('htmx:afterRequest', function(evt) {
        document.body.style.cursor = 'default';
    });
</script>
</body>
</html>
if __name__ == "__main__":
    check_and_init()
    app.run(debug=True)

