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


def _add_event(date_obj, day_type, assignments):
    event = Event(date=date_obj, day_type=day_type)
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
        )
        _add_event(
            datetime.date(2026, 6, 14),
            "Sunday",
            [("Camera 1", "Florian", "pending"), ("Camera 2", "Marvin", "pending")],
        )

    response = app.test_client().get("/calendar/Florian.ics")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/calendar")
    assert 'inline; filename="florian_schedule.ics"' in response.headers["Content-Disposition"]
    assert "BEGIN:VTIMEZONE" in body
    assert "X-WR-TIMEZONE:America/Vancouver" in body
    assert body.count("SUMMARY:Livestream schedule this week") == 1
    assert "DTSTART;TZID=America/Vancouver:20260609T080000" in body
    assert body.count("TRIGGER;VALUE=DATE-TIME:20260609T150000Z") == 1
    assert "DTSTART;TZID=America/Vancouver:20260612T190000" in body
    assert "TRIGGER;VALUE=DATE-TIME:20260612T150000Z" in body
    assert "DTSTART;TZID=America/Vancouver:20260614T143000" in body
    assert "TRIGGER;VALUE=DATE-TIME:20260614T150000Z" in body
    assert "David Fink" not in body


def run_download_query_returns_attachment(app):
    with app.app_context():
        _clear_db()
        _add_event(datetime.date(2026, 6, 12), "Friday", [("Computer", "Florian", "pending")])

    response = app.test_client().get("/calendar/Florian.ics?download=1")

    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith("attachment;")


def main():
    app, temp_dir = _make_app()
    try:
        run_person_calendar_has_weekly_and_event_day_alarms(app)
        run_download_query_returns_attachment(app)
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(temp_dir, ignore_errors=True)
    print("calendar feed tests passed")


if __name__ == "__main__":
    main()
