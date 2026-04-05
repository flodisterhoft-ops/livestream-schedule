from flask import Flask, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from .extensions import db
from .routes import bp as main_bp
from .api_v2 import api_v2
import datetime
import os


def _seed_team_members():
    """Seed the TeamMember table from the current ROLES_CONFIG."""
    from .models import TeamMember
    import json

    roster = {
        "Florian": {"sunday": ["Computer"], "friday": ["Leader"]},
        "Andy":    {"sunday": ["Computer", "Camera 1", "Camera 2"], "friday": ["Leader"]},
        "Marvin":  {"sunday": ["Computer", "Camera 1", "Camera 2"], "friday": ["Leader"]},
        "Patric":  {"sunday": ["Computer", "Camera 1", "Camera 2"], "friday": ["Leader"]},
        "Rene":    {"sunday": ["Computer", "Camera 1", "Camera 2"], "friday": ["Leader"]},
        "Stefan":  {"sunday": ["Computer", "Camera 1", "Camera 2"], "friday": ["Leader"]},
        "Viktor":  {"sunday": ["Camera 2"], "friday": ["Leader"]},
    }

    for name, config in roster.items():
        m = TeamMember(name=name)
        m.sunday_roles = config["sunday"]
        m.friday_roles = config["friday"]
        m.active = True
        db.session.add(m)

    db.session.commit()
    print(f"[v2] Seeded {len(roster)} team members")


def apply_data_hotfixes():
    """Apply idempotent production data fixes."""
    from .models import Event, Assignment

    target_date = datetime.date(2026, 2, 20)
    event = Event.query.filter_by(date=target_date).first()
    if not event:
        return

    event.day_type = "Friday"

    # Remove Sunday camera roles from this Bible study date.
    for assignment in list(event.assignments):
        if assignment.role in ("Camera 1", "Camera 2"):
            db.session.delete(assignment)

    # Ensure a single Computer assignment exists and is not Marvin.
    computers = Assignment.query.filter_by(event_id=event.id, role="Computer").order_by(Assignment.id).all()
    if computers:
        computer = computers[0]
        for extra in computers[1:]:
            db.session.delete(extra)
    else:
        leader = Assignment.query.filter_by(event_id=event.id, role="Leader").order_by(Assignment.id).first()
        if leader:
            leader.role = "Computer"
            computer = leader
        else:
            computer = Assignment(event_id=event.id, role="Computer", person="Rene", status="pending")
            db.session.add(computer)

    if computer.person in ("Marvin", "Stefan", "TBD", "Select Helper", None, ""):
        computer.person = "Rene"
    if not computer.status:
        computer.status = "pending"

    # Ensure exactly one Helper assignment exists.
    helpers = Assignment.query.filter_by(event_id=event.id, role="Helper").order_by(Assignment.id).all()
    if helpers:
        for extra in helpers[1:]:
            db.session.delete(extra)
    else:
        db.session.add(Assignment(event_id=event.id, role="Helper", person="Select Helper", status="pending"))

    # Remove any leftover unexpected roles for this date.
    for assignment in Assignment.query.filter_by(event_id=event.id).all():
        if assignment.role not in ("Computer", "Helper"):
            db.session.delete(assignment)

    db.session.commit()

def create_app(config_class='config.Config'):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Honor reverse-proxy headers from the live stack (for example Cloudflare/Nginx).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    db.init_app(app)

    app.register_blueprint(main_bp)
    app.register_blueprint(api_v2)  # v2 REST API at /api/v2/

    # ── Serve React v2 frontend ──────────────────────────────
    v2_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scheduler-site', 'dist')

    @app.route('/v2')
    @app.route('/v2/')
    @app.route('/v2/<path:path>')
    def serve_v2(path='index.html'):
        """Serve the React v2 frontend from scheduler-site/dist/."""
        if os.path.isdir(v2_dist):
            file_path = os.path.join(v2_dist, path)
            if os.path.isfile(file_path):
                return send_from_directory(v2_dist, path)
            # SPA fallback: serve index.html for client-side routing
            return send_from_directory(v2_dist, 'index.html')
        return "v2 frontend not built yet. Run: cd scheduler-site && npm install && npm run build", 404

    with app.app_context():
        # Create tables if they don't exist
        db.create_all()

        # Auto-migrate: add columns that db.create_all() won't add to existing tables
        from sqlalchemy import text, inspect
        insp = inspect(db.engine)
        cols = [c['name'] for c in insp.get_columns('assignment')]
        if 'telegram_message_id' not in cols:
            db.session.execute(text('ALTER TABLE assignment ADD COLUMN telegram_message_id INTEGER'))
            db.session.commit()

        # Seed database with schedule data if empty
        from .seed_data import seed_database
        seed_database()

        # Seed TeamMember table with current roster if empty
        from .models import TeamMember
        if TeamMember.query.count() == 0:
            _seed_team_members()

        # Apply one-time corrective fixes on existing deployments.
        apply_data_hotfixes()

    return app
