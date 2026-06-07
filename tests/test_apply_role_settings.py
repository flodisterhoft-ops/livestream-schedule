import datetime
import shutil
import sys
import tempfile
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api_v2 import api_v2
from app.extensions import db
from app.models import (
    Assignment,
    Availability,
    Event,
    EventSuggestion,
    InteractionLog,
    SchedulingPreset,
    SchedulingSnapshot,
    SwapRequest,
    TeamMember,
    TempChat,
)
from app.scheduler_v2 import DEFAULT_ROLE_PREFERENCES
from app.utils import vancouver_today


def _make_app():
    temp_dir = Path(tempfile.mkdtemp(prefix="livestream-role-settings-"))
    db_path = temp_dir / "test.db"
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test",
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path.as_posix()}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    app.register_blueprint(api_v2)
    with app.app_context():
        db.create_all()
    return app, temp_dir


def _clear_db():
    for model in (
        TempChat,
        SwapRequest,
        Availability,
        InteractionLog,
        EventSuggestion,
        SchedulingSnapshot,
        SchedulingPreset,
        Assignment,
        Event,
        TeamMember,
    ):
        db.session.query(model).delete()
    db.session.commit()


def _client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_name"] = "Florian"
    return client


def _member(name, sunday_roles=None, friday_roles=None, preferences=None):
    member = TeamMember(name=name)
    member.sunday_roles = sunday_roles or ["Computer", "Camera 1", "Camera 2"]
    member.friday_roles = friday_roles or ["Computer", "Camera"]
    member.role_preferences = preferences or {}
    member.active = True
    member.active_from = vancouver_today()
    db.session.add(member)
    db.session.flush()
    return member


def _member_payload(member, name=None, sunday_roles=None, friday_roles=None, preferences=None, caps=None):
    return {
        "id": member.id,
        "name": name or member.name,
        "sunday_roles": sunday_roles if sunday_roles is not None else member.sunday_roles,
        "friday_roles": friday_roles if friday_roles is not None else member.friday_roles,
        "role_preferences": preferences if preferences is not None else member.role_preferences,
        "caps": caps or {
            "sunday_per_month": 2,
            "friday_per_month": 2,
            "total_per_month": 4,
        },
        "active": member.active,
        "active_from": member.active_from.isoformat() if member.active_from else None,
    }


def run_rename_preserves_schedule_and_updates_references(app):
    with app.app_context():
        _clear_db()
        old = _member("Old Name", sunday_roles=["Computer"], friday_roles=["Computer"])
        helper = _member("Helper")

        event_date = vancouver_today() + datetime.timedelta(days=30)
        event = Event(date=event_date, day_type="Sunday")
        db.session.add(event)
        db.session.flush()

        computer = Assignment(event_id=event.id, role="Computer", person="Old Name", status="pending")
        camera = Assignment(
            event_id=event.id,
            role="Camera 1",
            person="Helper",
            cover="Old Name",
            swapped_with="Old Name",
            status="pending",
        )
        db.session.add_all([computer, camera])
        db.session.flush()

        db.session.add_all([
            Availability(person="Old Name", start_date=event_date, end_date=event_date),
            SwapRequest(
                assignment_id=computer.id,
                requestor="Old Name",
                accepted_by="Old Name",
                event_date=event_date,
                role="Computer",
                expires_at=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(days=1),
            ),
            InteractionLog(person_name="Old Name", action="confirm", assignment_id=computer.id, event_date=event_date),
            TempChat(chat_id="1", kind="swap", person="Old Name", recipient="Old Name"),
            EventSuggestion(suggester_name="Old Name", event_type="custom", date=event_date),
        ])
        db.session.commit()

        response = _client(app).post("/api/v2/team/apply-role-settings", json={
            "members": [
                _member_payload(old, name="New Name", sunday_roles=["Computer"], friday_roles=["Computer"]),
                _member_payload(helper),
            ],
            "removed_ids": [],
        })

        assert response.status_code == 200, response.get_data(as_text=True)
        result = response.get_json()
        assert result["future_assignments_replaced"] == 0
        assert result["future_events_touched"] == 0
        assert result["snapshot"] is None
        assert result["renamed"] == [{"from": "Old Name", "to": "New Name"}]
        assert result["rename_updates"] >= 1

        db.session.expire_all()
        assert db.session.get(Assignment, computer.id).person == "New Name"
        updated_camera = db.session.get(Assignment, camera.id)
        assert updated_camera.cover == "New Name"
        assert updated_camera.swapped_with == "New Name"
        assert Availability.query.filter_by(person="New Name").count() == 1
        assert Availability.query.filter_by(person="Old Name").count() == 0
        swap = SwapRequest.query.filter_by(assignment_id=computer.id).one()
        assert swap.requestor == "New Name"
        assert swap.accepted_by == "New Name"
        assert InteractionLog.query.filter_by(person_name="New Name").count() == 1
        assert TempChat.query.filter_by(person="New Name", recipient="New Name").count() == 1
        assert EventSuggestion.query.filter_by(suggester_name="New Name").count() == 1


def run_default_caps_and_role_order_round_trip_is_noop(app):
    with app.app_context():
        _clear_db()
        rene = _member("Rene", preferences=DEFAULT_ROLE_PREFERENCES["Rene"])
        helper = _member("Andy", preferences=DEFAULT_ROLE_PREFERENCES["Andy"])

        event_date = vancouver_today() + datetime.timedelta(days=37)
        event = Event(date=event_date, day_type="Friday")
        db.session.add(event)
        db.session.flush()
        assignment = Assignment(event_id=event.id, role="Computer", person="Rene", status="pending")
        db.session.add(assignment)
        db.session.commit()

        response = _client(app).post("/api/v2/team/apply-role-settings", json={
            "members": [
                _member_payload(
                    rene,
                    friday_roles=["Camera", "Computer"],
                    caps={"sunday_per_month": 2, "friday_per_month": 2, "total_per_month": 4},
                ),
                _member_payload(helper),
            ],
            "removed_ids": [],
        })

        assert response.status_code == 200, response.get_data(as_text=True)
        result = response.get_json()
        assert result["future_assignments_replaced"] == 0
        assert result["future_events_touched"] == 0
        assert result["snapshot"] is None

        db.session.expire_all()
        assert db.session.get(Assignment, assignment.id).person == "Rene"
        assert TeamMember.query.filter_by(name="Rene").one().friday_roles == ["Computer", "Camera"]


def main():
    app, temp_dir = _make_app()
    try:
        run_rename_preserves_schedule_and_updates_references(app)
        run_default_caps_and_role_order_round_trip_is_noop(app)
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(temp_dir, ignore_errors=True)
    print("apply-role-settings tests passed")


if __name__ == "__main__":
    main()
