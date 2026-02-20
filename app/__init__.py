from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from .extensions import db
from .routes import bp as main_bp
import datetime


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

    # Fix for Render (handle reverse proxy headers for HTTPS)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    db.init_app(app)

    app.register_blueprint(main_bp)

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

        # Apply one-time corrective fixes on existing deployments.
        apply_data_hotfixes()

    return app
