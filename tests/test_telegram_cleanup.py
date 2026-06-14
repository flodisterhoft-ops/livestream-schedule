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
from app.models import Assignment, Event, InteractionLog, SwapRequest, TempChat
import app.telegram_v2 as tg


def _make_app():
    temp_dir = Path(tempfile.mkdtemp(prefix="livestream-telegram-cleanup-"))
    db_path = temp_dir / "test.db"
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test",
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path.as_posix()}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
    return app, temp_dir


def _clear_db():
    for model in (TempChat, SwapRequest, InteractionLog, Assignment, Event):
        db.session.query(model).delete()
    db.session.commit()


def _event_with_assignment(date_obj, role="Camera", person="David Fink", status="pending"):
    event = Event(date=date_obj, day_type="Friday")
    db.session.add(event)
    db.session.flush()
    assignment = Assignment(event_id=event.id, role=role, person=person, status=status)
    db.session.add(assignment)
    db.session.flush()
    return event, assignment


def run_expired_swap_sweep_deletes_broadcast(app):
    with app.app_context():
        _clear_db()
        event, assignment = _event_with_assignment(
            datetime.date(2026, 6, 12),
            status="swap_needed",
        )
        assignment.telegram_message_id = 325
        swap = SwapRequest(
            assignment_id=assignment.id,
            requestor="David Fink",
            event_date=event.date,
            role=assignment.role,
            expires_at=(
                datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                - datetime.timedelta(minutes=5)
            ),
            status="active",
            telegram_message_id=325,
            telegram_chat_id="chat",
        )
        db.session.add(swap)
        db.session.commit()

        deleted = []
        old_delete = tg.delete_message_with_error
        old_notify = tg._notify_admin_text
        try:
            tg.delete_message_with_error = lambda chat_id, message_id: (
                deleted.append((chat_id, message_id)) or (True, None)
            )
            tg._notify_admin_text = lambda _text: None

            assert tg.sweep_expired_swaps() == 1
        finally:
            tg.delete_message_with_error = old_delete
            tg._notify_admin_text = old_notify

        db.session.expire_all()
        swap = db.session.get(SwapRequest, swap.id)
        assignment = db.session.get(Assignment, assignment.id)
        assert deleted == [("chat", 325)]
        assert swap.status == "expired"
        assert swap.telegram_message_id is None
        assert swap.telegram_chat_id is None
        assert assignment.telegram_message_id is None


def run_weekly_force_bypasses_existing_log(app):
    with app.app_context():
        _clear_db()
        today = datetime.date(2026, 6, 9)
        monday = datetime.date(2026, 6, 8)
        _event_with_assignment(datetime.date(2026, 6, 12))
        db.session.add(InteractionLog(
            action="weekly_schedule_sent",
            person_name="group",
            event_date=monday,
            details="weekly_schedule:2026-06-08|chat_id=chat|message_id=323",
        ))
        db.session.commit()

        sent = []
        old_send = tg.send_message
        try:
            tg.send_message = lambda text, chat_id=None, reply_markup=None, parse_mode="HTML": (
                sent.append((chat_id, text)) or 500
            )

            assert tg.send_weekly_schedule(chat_id="chat", today=today) == 0
            assert tg.send_weekly_schedule(chat_id="chat", force=True, today=today) == 1
        finally:
            tg.send_message = old_send

        latest = tg._weekly_schedule_log(monday)
        assert sent and sent[-1][0] == "chat"
        assert "message_id=500" in latest.details
        assert "reason=weekly_schedule_resent" in latest.details


def run_weekly_update_resends_missing_message(app):
    with app.app_context():
        _clear_db()
        monday = datetime.date(2026, 6, 8)
        friday = datetime.date(2026, 6, 12)
        _event_with_assignment(friday)
        db.session.add(InteractionLog(
            action="weekly_schedule_sent",
            person_name="group",
            event_date=monday,
            details="weekly_schedule:2026-06-08|chat_id=chat|message_id=323",
        ))
        db.session.commit()

        edited = []
        sent = []
        old_edit = tg.edit_message_with_error
        old_send = tg.send_message
        try:
            tg.edit_message_with_error = lambda chat_id, message_id, text, reply_markup=None: (
                edited.append((chat_id, message_id)) or (False, "Bad Request: message to edit not found")
            )
            tg.send_message = lambda text, chat_id=None, reply_markup=None, parse_mode="HTML": (
                sent.append((chat_id, text)) or 501
            )

            assert tg.update_weekly_schedule_for_date(friday) is True
        finally:
            tg.edit_message_with_error = old_edit
            tg.send_message = old_send

        latest = tg._weekly_schedule_log(monday)
        assert edited == [("chat", 323)]
        assert sent and sent[-1][0] == "chat"
        assert "message_id=501" in latest.details


def main():
    app, temp_dir = _make_app()
    try:
        run_expired_swap_sweep_deletes_broadcast(app)
        run_weekly_force_bypasses_existing_log(app)
        run_weekly_update_resends_missing_message(app)
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(temp_dir, ignore_errors=True)
    print("telegram cleanup tests passed")


if __name__ == "__main__":
    main()
