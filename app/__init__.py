from flask import Flask, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from .extensions import db
from .routes import bp as main_bp
from .api_v2 import api_v2
import datetime
import os
import atexit


def _seed_team_members():
    """Seed the TeamMember table from the current ROLES_CONFIG."""
    from .models import TeamMember
    import json

    # Known Telegram user IDs (cross-referenced from Young Couples seen_users)
    # Viktor and Stefan's IDs still need to be looked up — they'll be None until
    # Florian provides them (see sync_telegram_ids() below for the update path).
    roster = {
        "Florian": {"sunday": ["Computer"],                             "friday": ["Computer"], "tg": "27859948"},
        "Andy":    {"sunday": ["Computer", "Camera 1", "Camera 2"],     "friday": ["Computer", "Camera"], "tg": "321688481"},
        "Marvin":  {"sunday": ["Computer", "Camera 1", "Camera 2"],     "friday": ["Computer", "Camera"], "tg": "1450399472"},
        "Patric":  {"sunday": ["Computer", "Camera 1", "Camera 2"],     "friday": ["Computer", "Camera"], "tg": "1026052892"},
        "Rene":    {"sunday": ["Computer", "Camera 1", "Camera 2"],     "friday": ["Computer", "Camera"], "tg": "740672775"},
        "Stefan":  {"sunday": ["Computer", "Camera 1", "Camera 2"],     "friday": ["Computer", "Camera"], "tg": "5693703380"},
        "Viktor":  {"sunday": ["Camera 2"],                             "friday": ["Camera"], "tg": "963201448"},
    }

    for name, config in roster.items():
        m = TeamMember(name=name)
        m.sunday_roles = config["sunday"]
        m.friday_roles = config["friday"]
        m.active = True
        m.telegram_user_id = config.get("tg")
        db.session.add(m)

    db.session.commit()
    print(f"[v2] Seeded {len(roster)} team members")


def sync_telegram_ids():
    """Idempotent: make sure known TG IDs exist on existing TeamMember rows.

    This runs on every startup so when you paste Viktor's / Stefan's IDs
    into KNOWN_TG_IDS they'll be attached without needing a wipe-and-reseed.
    """
    from .models import TeamMember

    KNOWN_TG_IDS = {
        "Florian": "27859948",
        "Andy":    "321688481",
        "Marvin":  "1450399472",
        "Patric":  "1026052892",
        "Rene":    "740672775",
        "Stefan":  "5693703380",
        "Viktor":  "963201448",
    }

    changed = False
    for name, tg_id in KNOWN_TG_IDS.items():
        if not tg_id:
            continue
        m = TeamMember.query.filter_by(name=name).first()
        if m and not m.telegram_user_id:
            m.telegram_user_id = tg_id
            print(f"[v2] Linked Telegram ID for {name}: {tg_id}")
            changed = True

    if changed:
        db.session.commit()


def sync_team_scheduling_defaults():
    from .models import TeamMember
    from .scheduler_v2 import _default_friday_roles, _default_role_preferences

    changed = False
    for member in TeamMember.query.all():
        new_friday_roles = _default_friday_roles(member.name, member.friday_roles)
        if new_friday_roles != member.friday_roles:
            member.friday_roles = new_friday_roles
            changed = True

        new_preferences = _default_role_preferences(member.name, member.role_preferences)
        if new_preferences != member.role_preferences:
            member.role_preferences = new_preferences
            changed = True

    if changed:
        db.session.commit()


def ensure_schedule_horizon():
    """Keep the schedule generated through the end of the active schedule year."""
    from .models import Event
    from .scheduler_v2 import generate_month_v2
    from .utils import vancouver_today

    today = vancouver_today()
    target_year = today.year + 1 if today.month == 12 else today.year
    target_end = datetime.date(target_year, 12, 31)
    while target_end.weekday() not in (4, 6):
        target_end -= datetime.timedelta(days=1)

    last_event = Event.query.order_by(Event.date.desc()).first()
    last_date = last_event.date if last_event else today

    if last_event is not None and last_date >= target_end:
        print(f"[Horizon] Schedule already generated through {last_date.isoformat()} — no top-up needed")
        return

    print(f"[Horizon] Generating schedule up to {target_end.isoformat()} "
          f"(currently through {last_date.isoformat()})")

    cursor_year = last_date.year
    cursor_month = last_date.month
    end_year, end_month = target_end.year, target_end.month
    total_created = 0

    while (cursor_year, cursor_month) <= (end_year, end_month):
        try:
            created = generate_month_v2(cursor_year, cursor_month)
            total_created += created
            if created:
                print(f"[Horizon]   {cursor_year}-{cursor_month:02d}: +{created} events")
        except Exception as e:
            print(f"[Horizon]   {cursor_year}-{cursor_month:02d} failed: {e}")
            db.session.rollback()
        cursor_month += 1
        if cursor_month > 12:
            cursor_month = 1
            cursor_year += 1

    print(f"[Horizon] Done — created {total_created} events across horizon")


def apply_data_hotfixes():
    """Apply idempotent production data fixes."""
    from .models import Event, Assignment

    changed = False
    for event in Event.query.filter_by(day_type="Friday").all():
        leader = Assignment.query.filter_by(event_id=event.id, role="Leader").order_by(Assignment.id).first()
        if leader:
            leader.role = "Computer"
            changed = True

        helper = Assignment.query.filter_by(event_id=event.id, role="Helper").order_by(Assignment.id).first()
        if helper:
            helper.role = "Camera"
            changed = True

        computers = Assignment.query.filter_by(event_id=event.id, role="Computer").order_by(Assignment.id).all()
        if computers:
            for extra in computers[1:]:
                db.session.delete(extra)
                changed = True
        else:
            db.session.add(Assignment(event_id=event.id, role="Computer", person="Select Helper", status="pending"))
            changed = True

        cameras = Assignment.query.filter_by(event_id=event.id, role="Camera").order_by(Assignment.id).all()
        if cameras:
            for extra in cameras[1:]:
                db.session.delete(extra)
                changed = True
        else:
            db.session.add(Assignment(event_id=event.id, role="Camera", person="Select Helper", status="pending"))
            changed = True

        for assignment in Assignment.query.filter_by(event_id=event.id).all():
            if assignment.role not in ("Computer", "Camera"):
                db.session.delete(assignment)
                changed = True

    if changed:
        db.session.commit()

def create_app(config_class='config.Config'):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Honor reverse-proxy headers from the live stack (for example Cloudflare/Nginx).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    db.init_app(app)

    # ── Serve React frontend at / (main) and /v2 (back-compat) ──
    react_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scheduler-site', 'dist')

    def _serve_react(path='index.html'):
        if os.path.isdir(react_dist):
            file_path = os.path.join(react_dist, path)
            if os.path.isfile(file_path):
                return send_from_directory(react_dist, path)
            # SPA fallback: serve index.html for client-side routing
            return send_from_directory(react_dist, 'index.html')
        return "Frontend not built yet. Run: cd scheduler-site && npm install && npm run build", 404

    @app.route('/')
    def serve_root():
        return _serve_react('index.html')

    @app.route('/assets/<path:path>')
    def serve_root_assets(path):
        return _serve_react(os.path.join('assets', path))

    # Back-compat: /v2 → still serves the same SPA
    @app.route('/v2')
    @app.route('/v2/')
    @app.route('/v2/<path:path>')
    def serve_v2(path='index.html'):
        return _serve_react(path)

    app.register_blueprint(main_bp)
    app.register_blueprint(api_v2)  # v2 REST API at /api/v2/

    with app.app_context():
        # Create tables if they don't exist (covers new models like SwapRequest)
        db.create_all()

        # Auto-migrate: add columns that db.create_all() won't add to existing tables
        from sqlalchemy import text, inspect
        insp = inspect(db.engine)
        cols = [c['name'] for c in insp.get_columns('assignment')]
        if 'telegram_message_id' not in cols:
            db.session.execute(text('ALTER TABLE assignment ADD COLUMN telegram_message_id INTEGER'))
            db.session.commit()
        if 'locked' not in cols:
            db.session.execute(text('ALTER TABLE assignment ADD COLUMN locked BOOLEAN DEFAULT false NOT NULL'))
            db.session.commit()

        team_member_cols = [c['name'] for c in insp.get_columns('team_member')]
        if '_role_preferences_json' not in team_member_cols:
            db.session.execute(text("ALTER TABLE team_member ADD COLUMN _role_preferences_json TEXT DEFAULT '{}'"))
            db.session.commit()

        event_cols = [c['name'] for c in insp.get_columns('event')]
        if 'telegram_message_id' not in event_cols:
            db.session.execute(text('ALTER TABLE event ADD COLUMN telegram_message_id INTEGER'))
            db.session.execute(text('ALTER TABLE event ADD COLUMN telegram_chat_id VARCHAR(30)'))
            db.session.commit()

        # Seed database with schedule data if empty
        from .seed_data import seed_database
        seed_database()

        # Seed TeamMember table with current roster if empty
        from .models import TeamMember
        if TeamMember.query.count() == 0:
            _seed_team_members()
        else:
            # Top up known Telegram IDs on existing rows (idempotent)
            sync_telegram_ids()
        sync_team_scheduling_defaults()

        # Apply one-time corrective fixes on existing deployments.
        apply_data_hotfixes()

        # Keep the schedule generated through the active schedule year.
        ensure_schedule_horizon()

    # ── Start the daily-reminder scheduler (9 AM Vancouver time) ──
    _start_daily_scheduler(app)

    return app


def _start_daily_scheduler(app):
    """Start an APScheduler that fires send_daily_reminders_v2() at 9 AM Vancouver.

    Uses a pid-lock file so that only ONE gunicorn worker owns the scheduler
    (otherwise every worker would fire the job and we'd send duplicate messages).
    """
    # Allow disabling via env var (useful for local dev)
    if os.environ.get("DISABLE_SCHEDULER", "").lower() in ("1", "true", "yes"):
        print("[Scheduler] Disabled via DISABLE_SCHEDULER env var")
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("[Scheduler] APScheduler not installed — skipping automatic reminders")
        return

    # Single-worker lock: only the first process to grab this lock starts the scheduler.
    # This avoids duplicate reminders when running with multiple gunicorn workers.
    lock_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".scheduler.lock")
    try:
        # O_CREAT | O_EXCL so exactly one process can create it
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(os.getpid()).encode())
        os.close(lock_fd)
    except FileExistsError:
        # Another worker already owns the scheduler. Check if that pid is still alive;
        # if not, steal the lock.
        try:
            with open(lock_path) as f:
                other_pid = int(f.read().strip() or 0)
            if other_pid and _pid_alive(other_pid):
                print(f"[Scheduler] Another worker (pid {other_pid}) owns the scheduler — skipping")
                return
            # Stale lock — overwrite
            with open(lock_path, "w") as f:
                f.write(str(os.getpid()))
        except Exception as e:
            print(f"[Scheduler] Could not resolve lock file: {e}")
            return

    # Clean up lock file on exit
    def _cleanup_lock():
        try:
            with open(lock_path) as f:
                if int(f.read().strip() or 0) == os.getpid():
                    os.remove(lock_path)
        except Exception:
            pass
    atexit.register(_cleanup_lock)

    def _fire_daily_reminders():
        """Run send_daily_reminders_v2() inside app context at 9 AM Vancouver."""
        with app.app_context():
            try:
                from .telegram_v2 import send_daily_reminders_v2
                sent = send_daily_reminders_v2()
                print(f"[Scheduler] 9AM reminder fired — sent {sent} message(s)")
            except Exception as e:
                print(f"[Scheduler] Reminder job failed: {e}")

    def _fire_weekday_5pm_reminders():
        """Run weekday 5 PM reminders inside app context."""
        with app.app_context():
            try:
                from .telegram_v2 import send_weekday_5pm_reminders_v2
                sent = send_weekday_5pm_reminders_v2()
                print(f"[Scheduler] 5PM weekday reminder fired — sent {sent} message(s)")
            except Exception as e:
                print(f"[Scheduler] 5PM reminder job failed: {e}")

    def _fire_noon_response_followups():
        with app.app_context():
            try:
                from .telegram_v2 import send_noon_response_followups
                sent = send_noon_response_followups()
                print(f"[Scheduler] Noon follow-up fired — sent {sent} message(s)")
            except Exception as e:
                print(f"[Scheduler] Noon follow-up failed: {e}")

    def _fire_weekly_schedule():
        with app.app_context():
            try:
                from .telegram_v2 import send_weekly_schedule
                sent = send_weekly_schedule()
                print(f"[Scheduler] Weekly schedule fired — sent {sent} message(s)")
            except Exception as e:
                print(f"[Scheduler] Weekly schedule failed: {e}")

    def _fire_deadline_sweep():
        """Run sweep_expired_swaps() every hour — clean up unresolved shifts."""
        with app.app_context():
            try:
                from .telegram_v2 import sweep_expired_swaps, sweep_expired_temp_chats
                sweep_expired_swaps()
                sweep_expired_temp_chats()
            except Exception as e:
                print(f"[Scheduler] Deadline sweep failed: {e}")

    def _fire_horizon_topup():
        """Daily check: keep the schedule generated through the active schedule year."""
        with app.app_context():
            try:
                ensure_schedule_horizon()
            except Exception as e:
                print(f"[Scheduler] Horizon top-up failed: {e}")

    scheduler = BackgroundScheduler(timezone="America/Vancouver", daemon=True)
    scheduler.add_job(
        _fire_daily_reminders,
        trigger=CronTrigger(hour=9, minute=0, timezone="America/Vancouver"),
        id="daily_reminder_v2",
        replace_existing=True,
        misfire_grace_time=3600,  # If server was down, still fire if within an hour
    )
    scheduler.add_job(
        _fire_weekday_5pm_reminders,
        trigger=CronTrigger(hour=17, minute=0, timezone="America/Vancouver"),
        id="weekday_5pm_reminder_v2",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _fire_noon_response_followups,
        trigger=CronTrigger(hour=12, minute=0, timezone="America/Vancouver"),
        id="noon_response_followup",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _fire_weekly_schedule,
        trigger=CronTrigger(day_of_week="mon,tue", hour=8, minute=0, timezone="America/Vancouver"),
        id="weekly_schedule",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _fire_deadline_sweep,
        trigger=CronTrigger(minute=5, timezone="America/Vancouver"),  # hourly at :05
        id="deadline_sweep",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    scheduler.add_job(
        _fire_horizon_topup,
        trigger=CronTrigger(hour=2, minute=0, timezone="America/Vancouver"),
        id="horizon_topup",
        replace_existing=True,
        misfire_grace_time=7200,
    )
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))
    print(f"[Scheduler] Started (pid {os.getpid()}) — daily reminders at 9:00 AM America/Vancouver")


def _pid_alive(pid):
    """Return True if a process with this pid is currently running."""
    try:
        if os.name == "nt":
            # Windows: use tasklist
            import subprocess
            out = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}"],
                                          stderr=subprocess.DEVNULL).decode(errors="ignore")
            return str(pid) in out
        else:
            # POSIX: signal 0 raises if the pid is gone
            os.kill(pid, 0)
            return True
    except Exception:
        return False
