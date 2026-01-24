from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app, make_response
from .models import Event, Assignment, Token, Availability
from .extensions import db
from .utils import check_and_init, send_access_email, ALL_NAMES, ROLES_CONFIG, is_available, get_history_stats
from .scheduler import generate_month
from .telegram import send_swap_needed_alert, send_shift_covered_alert, test_telegram_connection, send_telegram_message
import datetime
import uuid
import calendar

bp = Blueprint('main', __name__)

def get_swap_options(person, roster_data):
    today = datetime.date.today()
    future_assigns = Assignment.query.join(Event).filter(
        Assignment.person == person,
        Event.date >= today,
        Assignment.status != 'decline' 
    ).order_by(Event.date).all()
    
    options = []
    for a in future_assigns:
        if a.status in ('confirmed', 'pending'):
            options.append({
                "date": a.event.date.strftime("%B %d, %Y"),
                "readable_date": a.event.date.strftime("%b %d"),
                "event_name": a.event.custom_title or a.event.day_type,
                "role": a.role
            })
    return options

def render_row(assign_dict, full_date, current_user, is_manager, roster_data=None):
    swap_op = []
    if current_user and current_user != assign_dict['person'] and assign_dict['status'] == 'swap_needed':
        swap_op = get_swap_options(current_user, roster_data)

    return render_template(
        "partials/row.html",
        assign=assign_dict,
        full_date=full_date,
        current_user=current_user,
        is_manager=is_manager,
        swap_options=swap_op,
        all_names=ALL_NAMES
    )

@bp.route("/")
def home():
    if "user_name" not in session:
        session["user_name"] = None
    
    check_and_init()
    
    events = Event.query.order_by(Event.date).all()
    
    schedule_data = []
    today = datetime.date.today()
    stats, _ = get_history_stats()
    
    for event in events:
        d_obj = event.date
        d_key = d_obj.strftime("%B %d, %Y")
        
        assigns_view = []
        for i, a in enumerate(event.assignments):
            ad = a.to_dict()
            ad['idx'] = i
            ad['id'] = a.id
            ad['role'] = a.role
            assigns_view.append(ad)
            
        is_past = d_obj < today
        
        schedule_data.append({
            "full_date": d_key,
            "day_num": d_obj.strftime("%d"),
            "month": d_obj.strftime("%b"),
            "month_year": d_obj.strftime("%B %Y"),
            "title": event.custom_title or ("Bible Study" if event.day_type == "Friday" else "Sunday Service"),
            "assignments": assigns_view,
            "raw_date": d_obj.strftime("%Y-%m-%d"),
            "is_past": is_past,
            "day_type": event.day_type
        })

    month_list = sorted(list(set(item['month_year'] for item in schedule_data)), key=lambda x: datetime.datetime.strptime(x, "%B %Y"))
    month_list.insert(0, "All Time")

    return render_template(
        "index.html",
        schedule=schedule_data,
        is_preview=False,
        all_names=ALL_NAMES,
        is_manager=session.get("manager"),
        stats=stats,
        month_list=month_list,
        current_user=session.get("user_name"),
        roster_data={}, 
        render_row_fn=render_row
    )

@bp.route("/set_identity/<name>", methods=["GET", "POST"])
def set_identity(name):
    if name == "Florian":
        # Florian requires password
        if request.method == "POST":
            password = request.form.get("password", "")
            if password == "steroids":
                session["user_name"] = name
                return redirect(url_for("main.home"))
            else:
                flash("Incorrect password", "error")
                return render_template("login.html", name=name)
        else:
            return render_template("login.html", name=name)
    elif name in ALL_NAMES:
        session["user_name"] = name
    return redirect(url_for("main.home"))

@bp.route("/switch_user")
@bp.route("/logout")
def switch_user():
    """Logout - return to identity selection."""
    session.pop("user_name", None)
    session.pop("manager", None)
    return redirect(url_for("main.home"))

@bp.route("/toggle_manager")
@bp.route("/logout_manager")
def toggle_manager():
    """Toggle manager mode on/off for Florian."""
    if session.get("user_name") != "Florian":
        return redirect(url_for("main.home"))
    
    if session.get("manager"):
        session.pop("manager", None)
    else:
        session["manager"] = True
    
    return redirect(url_for("main.home"))

@bp.route("/request_access", methods=["POST"])
def request_access():
    user = session.get("user_name", "Unknown")
    # Create token
    token_str = str(uuid.uuid4())
    new_token = Token(token=token_str)
    db.session.add(new_token)
    db.session.commit()
    
    # Generate magic link
    magic_link = url_for("main.manager_login", token=token_str, _external=True)
    
    if send_access_email(magic_link, user):
        flash("Admin notified! Check email for magic link.", "success")
    else:
        flash("Error sending email.", "error")
    return redirect(url_for("main.home"))

@bp.route("/manager_login/<token>")
def manager_login(token):
    # Find and validate token
    token_obj = Token.query.filter_by(token=token).first()
    if token_obj:
        session["manager"] = True
        session["user_name"] = "Florian"
        flash("Manager mode enabled", "success")
        # Delete used token
        db.session.delete(token_obj)
        db.session.commit()
    else:
        flash("Invalid or expired token", "error")
    return redirect(url_for("main.home"))

@bp.route("/generate_specific", methods=["POST"])
def generate_specific_route():
    if not session.get("manager"): return redirect(url_for("main.home"))
    
    ym_str = request.form.get("gen_month") # YYYY-MM
    if ym_str:
        y, m = map(int, ym_str.split("-"))
        generate_month(y, m)
        flash(f"Generated events for {ym_str}", "CONFETTI")
    return redirect(url_for("main.home"))

@bp.route("/wipe_month", methods=["POST"])
def wipe_month():
    if not session.get("manager"): return redirect(url_for("main.home"))
    ym_str = request.form.get("gen_month")
    if ym_str:
        y, m = map(int, ym_str.split("-"))
        start_date = datetime.date(y, m, 1)
        _, num_days = calendar.monthrange(y, m)
        end_date = datetime.date(y, m, num_days)
        Event.query.filter(Event.date >= start_date, Event.date <= end_date).delete()
        db.session.commit()
        flash(f"Wiped events for {ym_str}", "success")
    return redirect(url_for("main.home"))

@bp.route("/test_telegram", methods=["POST"])
def test_telegram():
    """Test Telegram bot connection and send a test message."""
    if not session.get("manager"):
        return redirect(url_for("main.home"))
    
    # Test connection
    result = test_telegram_connection()
    
    if result.get("success"):
        bot_name = result.get("bot", {}).get("username", "Unknown")
        # Try to send a test message
        if send_telegram_message("🔔 <b>Test Message</b>\n\nThe Livestream Schedule bot is connected and working! ✅"):
            flash(f"Telegram connected! Bot: @{bot_name} - Test message sent!", "CONFETTI")
        else:
            flash(f"Bot connected (@{bot_name}) but couldn't send message. Check CHAT_ID.", "error")
    else:
        flash(f"Telegram error: {result.get('error', 'Unknown error')}", "error")
    
    return redirect(url_for("main.home"))

@bp.route("/add_event", methods=["POST"])
def add_event():
    if not session.get("manager"): return redirect(url_for("main.home"))
    
    d_str = request.form.get("event_date")
    e_type = request.form.get("event_type")
    c_title = request.form.get("custom_title")
    
    if d_str:
        d_obj = datetime.datetime.strptime(d_str, "%Y-%m-%d").date()
        if Event.query.filter_by(date=d_obj).first():
            flash("Event already exists!", "error")
        else:
            e = Event(date=d_obj, day_type=e_type, custom_title=c_title)
            db.session.add(e)
            db.session.commit()
            
            assigns = []
            if e_type == "Custom":
                if request.form.get("role_pc"):
                     assigns.append(Assignment(event_id=e.id, role="Computer", person="Select Helper"))
                if request.form.get("role_cam1"):
                     assigns.append(Assignment(event_id=e.id, role="Camera 1", person="Select Helper"))
                if request.form.get("role_cam2"):
                     assigns.append(Assignment(event_id=e.id, role="Camera 2", person="Select Helper"))
            elif e_type == "Sunday":
                assigns = [
                    Assignment(event_id=e.id, role="Computer", person="Select Helper"),
                    Assignment(event_id=e.id, role="Camera 1", person="Select Helper"),
                    Assignment(event_id=e.id, role="Camera 2", person="Select Helper"),
                ]
            elif e_type == "Friday":
                assigns = [
                    Assignment(event_id=e.id, role="Leader", person="Select Helper"),
                    Assignment(event_id=e.id, role="Helper", person="Select Helper"),
                ]
            
            db.session.add_all(assigns)
            db.session.commit()
            flash("Event added!", "CONFETTI")
            
    return redirect(url_for("main.home"))

@bp.route("/delete/<d_key>")
def delete_event_route(d_key):
    if not session.get("manager"): return redirect(url_for("main.home"))
    d_obj = datetime.datetime.strptime(d_key, "%B %d, %Y").date()
    e = Event.query.filter_by(date=d_obj).first()
    if e:
        db.session.delete(e)
        db.session.commit()
    return redirect(url_for("main.home"))

@bp.route("/edit_title", methods=["POST"])
def edit_title():
    if not session.get("manager"): return redirect(url_for("main.home"))
    d_key = request.form.get("date")
    new_title = request.form.get("new_title")
    d_obj = datetime.datetime.strptime(d_key, "%B %d, %Y").date()
    e = Event.query.filter_by(date=d_obj).first()
    if e:
        e.custom_title = new_title
        db.session.commit()
    return redirect(url_for("main.home"))

@bp.route("/update_person", methods=["POST"])
def update_person():
    d_key = request.form.get("date")
    idx = int(request.form.get("role_idx"))
    new_p = request.form.get("new_person")
    curr = session.get("user_name")
    is_mgr = bool(session.get("manager"))

    d_obj = datetime.datetime.strptime(d_key, "%B %d, %Y").date()
    event = Event.query.filter_by(date=d_obj).first()
    
    if event and 0 <= idx < len(event.assignments):
        target_a = event.assignments[idx]
        current_p = target_a.person
        
        if is_mgr or (current_p == "Select Helper" and new_p == curr):
            hist = target_a.history
            hist.append({
                "from": current_p, "to": new_p,
                "by": curr, "ts": str(datetime.datetime.now())
            })
            target_a.history = hist
            
            target_a.person = new_p
            target_a.cover = None
            target_a.swapped_with = None
            target_a.status = "confirmed" if current_p == "Select Helper" else "pending"
            db.session.commit()
            
            if request.headers.get("HX-Request"):
                ad = target_a.to_dict()
                ad['idx'] = idx 
                return render_row(ad, d_key, curr, is_mgr)

    return redirect(url_for("main.home"))

@bp.route("/action", methods=["POST"])
def action_route():
    atype = request.form.get("type")
    d_key = request.form.get("date")
    idx = int(request.form.get("idx"))
    curr = session.get("user_name")
    is_mgr = bool(session.get("manager"))
    
    d_obj = datetime.datetime.strptime(d_key, "%B %d, %Y").date()
    event = Event.query.filter_by(date=d_obj).first()
    
    if event and 0 <= idx < len(event.assignments):
        target_a = event.assignments[idx]
        
        def push_h():
            h = target_a.history
            h.append({"action": atype, "by": curr, "prev_status": target_a.status, "ts": str(datetime.datetime.now())})
            target_a.history = h

        changed = False
        
        if atype == "confirm":
             if is_mgr or target_a.person == curr:
                 target_a.status = "confirmed"
                 changed = True
        
        elif atype == "decline":
             if is_mgr or target_a.person == curr:
                 target_a.status = "swap_needed"
                 changed = True
                 # Send Telegram notification
                 try:
                     send_swap_needed_alert(event, target_a, target_a.person)
                 except Exception as e:
                     print(f"Telegram notification error: {e}")
                 
        elif atype == "volunteer": 
             if not is_mgr and target_a.person == "Select Helper":
                 target_a.person = curr
                 target_a.status = "confirmed"
                 changed = True
        
        elif atype == "pickup": 
             original_person = target_a.person
             target_a.cover = curr
             target_a.status = "confirmed" 
             changed = True
             # Send Telegram notification
             try:
                 send_shift_covered_alert(event, target_a, curr)
             except Exception as e:
                 print(f"Telegram notification error: {e}")
             
        elif atype == "undo":
             if target_a.cover:
                 target_a.cover = None
                 target_a.status = "swap_needed" 
             elif target_a.status == "swap_needed":
                 target_a.status = "confirmed" 
             elif target_a.status == "confirmed":
                 target_a.status = "pending"
             changed = True

        elif atype == "swap_shift":
            offer_date_str = request.form.get("swap_offer_date") 
            if offer_date_str:
                od_obj = datetime.datetime.strptime(offer_date_str, "%B %d, %Y").date()
                other_event = Event.query.filter_by(date=od_obj).first()
                if other_event:
                    my_assign = None
                    for a in other_event.assignments:
                        if a.person == curr and a.status in ('confirmed', 'pending'):
                            my_assign = a
                            break
                    
                    if my_assign:
                        them = target_a.person
                        me = curr
                        target_a.person = me
                        target_a.swapped_with = them
                        target_a.status = "confirmed"
                        target_a.cover = None
                        
                        my_assign.person = them
                        my_assign.swapped_with = me
                        my_assign.status = "confirmed"
                        changed = True
                        db.session.add(my_assign)

        if changed:
            push_h()
            db.session.commit()
            if request.headers.get("HX-Request"):
                ad = target_a.to_dict()
                ad['idx'] = idx
                return render_row(ad, d_key, curr, is_mgr)

    return redirect(url_for("main.home"))

# ============================================================
# BULK CONFIRM
# ============================================================
@bp.route("/bulk_confirm", methods=["POST"])
def bulk_confirm():
    """Confirm all pending assignments for a given month."""
    if not session.get("manager"):
        return redirect(url_for("main.home"))
    
    ym_str = request.form.get("gen_month")  # YYYY-MM
    if ym_str:
        y, m = map(int, ym_str.split("-"))
        start_date = datetime.date(y, m, 1)
        last_day = calendar.monthrange(y, m)[1]
        end_date = datetime.date(y, m, last_day)
        
        # Get all events in the month
        events = Event.query.filter(Event.date >= start_date, Event.date <= end_date).all()
        count = 0
        for event in events:
            for a in event.assignments:
                if a.status == "pending":
                    a.status = "confirmed"
                    count += 1
        db.session.commit()
        flash(f"Confirmed {count} assignments!", "CONFETTI")
    
    return redirect(url_for("main.home"))

# ============================================================
# iCAL CALENDAR EXPORT
# ============================================================
@bp.route("/calendar.ics")
def calendar_full():
    """Export full calendar as iCal format."""
    events = Event.query.order_by(Event.date).all()
    ical = generate_ical(events)
    
    response = make_response(ical)
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=livestream_schedule.ics"
    return response

@bp.route("/calendar/<person>.ics")
def calendar_person(person):
    """Export personal calendar as iCal format."""
    # Get all assignments for this person
    assignments = Assignment.query.filter(
        (Assignment.person == person) | (Assignment.cover == person)
    ).all()
    
    # Get unique events
    event_ids = set(a.event_id for a in assignments)
    events = Event.query.filter(Event.id.in_(event_ids)).order_by(Event.date).all()
    
    ical = generate_ical(events, person)
    
    response = make_response(ical)
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename={person}_schedule.ics"
    return response

def generate_ical(events, person=None):
    """Generate iCal format string from events."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Livestream Schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Livestream Schedule",
    ]
    
    for event in events:
        title = event.custom_title or ("Bible Study" if event.day_type == "Friday" else "Sunday Service")
        
        # Build description with assignments
        desc_parts = []
        for a in event.assignments:
            worker = a.cover if a.cover else a.person
            desc_parts.append(f"{a.role}: {worker}")
        
        description = "\\n".join(desc_parts)
        
        # Event times (assume 10am-12pm for Sunday, 7pm-9pm for Friday)
        if event.day_type == "Friday":
            start_time = "190000"
            end_time = "210000"
        else:
            start_time = "100000"
            end_time = "120000"
        
        date_str = event.date.strftime("%Y%m%d")
        uid = f"{date_str}@livestream-schedule"
        
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART:{date_str}T{start_time}",
            f"DTEND:{date_str}T{end_time}",
            f"SUMMARY:{title}",
            f"DESCRIPTION:{description}",
            "END:VEVENT",
        ])
    
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)

# ============================================================
# AVAILABILITY MANAGEMENT
# ============================================================
@bp.route("/availability")
def availability_page():
    """Show availability management page."""
    curr = session.get("user_name")
    is_mgr = session.get("manager")
    
    if is_mgr:
        # Managers see all availability
        avails = Availability.query.order_by(Availability.start_date).all()
    else:
        # Users see only their own
        avails = Availability.query.filter_by(person=curr).order_by(Availability.start_date).all()
    
    return render_template("availability.html", 
                          availabilities=avails, 
                          all_names=ALL_NAMES,
                          current_user=curr,
                          is_manager=is_mgr)

@bp.route("/availability/add", methods=["POST"])
def add_availability():
    """Add a new unavailable period."""
    curr = session.get("user_name")
    is_mgr = session.get("manager")
    
    person = request.form.get("person", curr)
    # Only managers can set availability for others
    if not is_mgr and person != curr:
        person = curr
    
    start = request.form.get("start_date")
    end = request.form.get("end_date") or start  # Default to start for single day
    reason = request.form.get("reason", "")
    pattern = request.form.get("pattern", "")
    recurring = pattern != ""
    
    if start:
        start_obj = datetime.datetime.strptime(start, "%Y-%m-%d").date()
        end_obj = datetime.datetime.strptime(end, "%Y-%m-%d").date() if end else start_obj
        
        avail = Availability(
            person=person,
            start_date=start_obj,
            end_date=end_obj,
            reason=reason,
            recurring=recurring,
            pattern=pattern
        )
        db.session.add(avail)
        db.session.commit()
        flash(f"Added unavailability for {person}", "success")
    
    return redirect(url_for("main.availability_page"))

@bp.route("/availability/delete/<int:avail_id>", methods=["POST"])
def delete_availability(avail_id):
    """Delete an availability entry."""
    curr = session.get("user_name")
    is_mgr = session.get("manager")
    
    avail = Availability.query.get(avail_id)
    if avail and (is_mgr or avail.person == curr):
        db.session.delete(avail)
        db.session.commit()
        flash("Deleted unavailability", "success")
    
    return redirect(url_for("main.availability_page"))

# ============================================================
# EVENT NOTES
# ============================================================
@bp.route("/update_notes", methods=["POST"])
def update_notes():
    """Update notes for an event."""
    if not session.get("manager"):
        return redirect(url_for("main.home"))
    
    d_str = request.form.get("date")
    notes = request.form.get("notes", "")
    
    if d_str:
        d_obj = datetime.datetime.strptime(d_str, "%B %d, %Y").date()
        event = Event.query.filter_by(date=d_obj).first()
        if event:
            event.notes = notes
            db.session.commit()
            flash("Notes updated", "success")
    
    return redirect(url_for("main.home"))

@bp.route("/update_title", methods=["POST"])
def update_title():
    """Update event title (manager only)."""
    if not session.get("manager"):
        return redirect(url_for("main.home"))
    
    d_str = request.form.get("date")
    new_title = request.form.get("title", "").strip()
    
    if d_str and new_title:
        d_obj = datetime.datetime.strptime(d_str, "%B %d, %Y").date()
        event = Event.query.filter_by(date=d_obj).first()
        if event:
            event.title = new_title
            db.session.commit()
            flash("Title updated!", "success")
    
    return redirect(url_for("main.home"))

# ============================================================
# STATISTICS DASHBOARD
# ============================================================
@bp.route("/stats")
def stats_page():
    """Show statistics dashboard with charts."""
    from collections import Counter, defaultdict
    
    # Get filters from query params
    selected_user = request.args.get('user', 'all')
    selected_month = request.args.get('month', 'all')
    
    # Get all events and assignments
    all_events = Event.query.order_by(Event.date).all()
    
    # Calculate available months for dropdown
    available_months = sorted(list(set(e.date.strftime("%B %Y") for e in all_events)))
    
    # Filter events by month if selected
    if selected_month != 'all':
        events = [e for e in all_events if e.date.strftime("%B %Y") == selected_month]
    else:
        events = all_events
    
    # Calculate basic stats
    total_events = len(events)
    total_assignments = sum(len(e.assignments) for e in events)
    team_members = set()
    
    # Per-person stats
    person_counts = Counter()
    role_counts = Counter()
    monthly_counts = defaultdict(int)
    
    # Personal role breakdown (for selected user)
    personal_role_counts = Counter()
    
    person_stats = defaultdict(lambda: {"total": 0, "sunday": 0, "friday": 0})
    
    for event in events:
        month_key = event.date.strftime("%b %Y")
        monthly_counts[month_key] += 1
        
        is_sunday = event.day_type == "Sunday" or event.date.weekday() == 6
        is_friday = event.day_type == "Friday" or event.date.weekday() == 4
        
        for a in event.assignments:
            worker = a.cover if a.cover else a.person
            if worker and worker not in ("TBD", "Select Helper"):
                team_members.add(worker)
                person_counts[worker] += 1
                role_counts[a.role] += 1
                
                # Track personal role breakdown
                if selected_user != 'all' and worker == selected_user:
                    personal_role_counts[a.role] += 1
                
                person_stats[worker]["total"] += 1
                if is_sunday:
                    person_stats[worker]["sunday"] += 1
                if is_friday:
                    person_stats[worker]["friday"] += 1
    
    # Prepare chart data
    sorted_persons = sorted(person_counts.items(), key=lambda x: -x[1])
    person_labels = [p[0] for p in sorted_persons[:10]]  # Top 10
    person_data = [p[1] for p in sorted_persons[:10]]
    
    role_labels = list(role_counts.keys())
    role_data = list(role_counts.values())
    
    # Monthly trend (last 6 months)
    month_labels = list(monthly_counts.keys())[-6:]
    month_data = [monthly_counts[m] for m in month_labels]
    
    # Leaderboard
    leaderboard = sorted([
        {"name": k, "total": v["total"], "sunday": v["sunday"], "friday": v["friday"]}
        for k, v in person_stats.items()
    ], key=lambda x: -x["total"])[:10]
    
    # Personal role data
    personal_role_labels = list(personal_role_counts.keys()) if selected_user != 'all' else []
    personal_role_data = list(personal_role_counts.values()) if selected_user != 'all' else []
    
    # Find top role for selected user
    personal_top_role = None
    personal_top_count = 0
    if personal_role_counts:
        top = personal_role_counts.most_common(1)[0]
        personal_top_role = top[0]
        personal_top_count = top[1]
    
    # Get all team names for the dropdown
    from .utils import ALL_NAMES
    all_names = ALL_NAMES
    
    return render_template("stats.html",
                          total_events=total_events,
                          total_assignments=total_assignments,
                          team_size=len(team_members),
                          person_labels=person_labels,
                          person_data=person_data,
                          role_labels=role_labels,
                          role_data=role_data,
                          month_labels=month_labels,
                          month_data=month_data,
                          leaderboard=leaderboard,
                          selected_user=selected_user,
                          all_names=all_names,
                          personal_role_labels=personal_role_labels,
                          personal_role_data=personal_role_data,
                          personal_top_role=personal_top_role,
                          personal_top_count=personal_top_count,
                          available_months=available_months,
                          selected_month=selected_month)


