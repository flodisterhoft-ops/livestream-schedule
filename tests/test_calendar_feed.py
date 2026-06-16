import datetime
import shutil
import sys
import tempfile
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.extensions import db
from app.models import Assignment, Event
from app.routes import bp
from app.utils import vancouver_today


def _make_app():
    temp_dir = Path(tempfile.mkdtemp(prefix="livestream-calendar-feed-"))
    db_path = temp_dir / "test.db"
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test",
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path.as_posix()}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        BASE_URL="https://livestream.example.test",
        WEEKLY_SCHEDULE_HOUR=8,
    )
    db.init_app(app)
    app.register_blueprint(bp)
    with app.app_context():
        db.create_all()
    return app, temp_dir


def _clear_db():
    db.session.query(Assignment).delete()
    db.session.query(Event).delete()
    db.session.commit()


def _add_event(date_obj, day_type, assignments, location=None, cancelled=False):
    event = Event(date=date_obj, day_type=day_type, location=location, cancelled=cancelled)
    db.session.add(event)
    db.session.flush()
    for role, person, status in assignments:
        db.session.add(Assignment(
            event_id=event.id,
            role=role,
            person=person,
            status=status,
        ))
    db.session.commit()
    return event


def run_person_calendar_has_weekly_and_event_day_alarms(app):
    with app.app_context():
        _clear_db()
        _add_event(
            datetime.date(2026, 6, 12),
            "Friday",
            [("Computer", "Florian", "confirmed"), ("Camera", "David Fink", "pending")],
            location="Pleasant Valley Church",
        )
        _add_event(
            datetime.date(2026, 6, 14),
            "Sunday",
            [("Camera 1", "Florian", "pending"), ("Camera 2", "Marvin", "pending")],
        )

    response = app.test_client().get("/calendar/Florian.ics?archive=1")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/calendar")
    assert response.headers["Cache-Control"] == "no-cache, max-age=0, must-revalidate"
    assert response.headers["ETag"]
    assert 'inline; filename="florian_schedule.ics"' in response.headers["Content-Disposition"]
    assert "BEGIN:VTIMEZONE" in body
    assert "X-WR-TIMEZONE:America/Vancouver" in body
    assert body.count("SUMMARY:Livestream schedule this week") == 1
    assert body.count("LAST-MODIFIED:") == 3
    assert body.count("SEQUENCE:") == 3
    assert "DTSTART;TZID=America/Vancouver:20260609T080000" in body
    assert body.count("TRIGGER:PT0S") == 1
    assert "DTSTART;TZID=America/Vancouver:20260612T190000" in body
    assert "TRIGGER:-PT11H" in body
    assert "LOCATION:Pleasant Valley Church" in body
    assert "Location: Pleasant Valley Church" in body
    assert "DTSTART;TZID=America/Vancouver:20260614T143000" in body
    assert "TRIGGER:-PT6H30M" in body
    assert "TRIGGER;VALUE=DATE-TIME" not in body
    assert "David Fink" not in body


def run_download_query_returns_attachment(app):
    with app.app_context():
        _clear_db()
        _add_event(datetime.date(2026, 6, 12), "Friday", [("Computer", "Florian", "pending")])

    response = app.test_client().get("/calendar/Florian.ics?download=1")

    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith("attachment;")


def run_default_feed_omits_old_history(app):
    old_date = vancouver_today() - datetime.timedelta(days=30)
    upcoming_date = vancouver_today() + datetime.timedelta(days=5)
    with app.app_context():
        _clear_db()
        _add_event(old_date, "Friday", [("Computer", "Florian", "pending")])
        _add_event(upcoming_date, "Friday", [("Computer", "Florian", "pending")])

    response = app.test_client().get("/calendar/Florian.ics")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert old_date.strftime("%Y%m%d") not in body
    assert upcoming_date.strftime("%Y%m%d") in body


def main():
    app, temp_dir = _make_app()
    try:
        run_person_calendar_has_weekly_and_event_day_alarms(app)
        run_download_query_returns_attachment(app)
        run_default_feed_omits_old_history(app)
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(temp_dir, ignore_errors=True)
    print("calendar feed tests passed")


if __name__ == "__main__":
    main()
