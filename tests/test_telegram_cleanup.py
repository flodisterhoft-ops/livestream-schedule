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


def _event_with_assignment(
    date_obj,
    role="Camera",
    person="David Fink",
    status="pending",
    day_type="Friday",
    location=None,
    cancelled=False,
):
    event = Event(date=date_obj, day_type=day_type, location=location, cancelled=cancelled)
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


def run_expired_swap_sweep_closes_undeletable_broadcast(app):
    with app.app_context():
        _clear_db()
        event, assignment = _event_with_assignment(
            datetime.date(2026, 6, 21),
            role="Camera 2",
            person="Stefan",
            status="swap_needed",
            day_type="Sunday",
        )
        assignment.telegram_message_id = 332
        swap = SwapRequest(
            assignment_id=assignment.id,
            requestor="Stefan",
            event_date=event.date,
            role=assignment.role,
            expires_at=(
                datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                - datetime.timedelta(minutes=5)
            ),
            status="active",
            telegram_message_id=332,
            telegram_chat_id="chat",
        )
        db.session.add(swap)
        db.session.commit()

        deleted = []
        edited = []
        old_delete = tg.delete_message_with_error
        old_edit = tg.edit_message_with_error
        old_notify = tg._notify_admin_text
        try:
            tg.delete_message_with_error = lambda chat_id, message_id: (
                deleted.append((chat_id, message_id)) or (False, "Bad Request: message can't be deleted")
            )
            tg.edit_message_with_error = lambda chat_id, message_id, text, reply_markup=None: (
                edited.append((chat_id, message_id, text, reply_markup)) or (True, None)
            )
            tg._notify_admin_text = lambda _text: None

            assert tg.sweep_expired_swaps() == 1
        finally:
            tg.delete_message_with_error = old_delete
            tg.edit_message_with_error = old_edit
            tg._notify_admin_text = old_notify

        db.session.expire_all()
        swap = db.session.get(SwapRequest, swap.id)
        assignment = db.session.get(Assignment, assignment.id)
        assert deleted == [("chat", 332)]
        assert len(edited) == 1
        assert edited[0][0:2] == ("chat", 332)
        assert "Stefan's coverage request is closed" in edited[0][2]
        assert "Sunday Service - June 21" in edited[0][2]
        assert "This service has passed." in edited[0][2]
        assert edited[0][3] == {"inline_keyboard": []}
        assert swap.status == "expired"
        assert swap.telegram_message_id is None
        assert swap.telegram_chat_id is None
        assert assignment.telegram_message_id is None


def _active_swap_for_assignment(assignment, message_id=None, chat_id="chat"):
    swap = SwapRequest(
        assignment_id=assignment.id,
        requestor=assignment.person,
        event_date=assignment.event.date,
        role=assignment.role,
        expires_at=(
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            + datetime.timedelta(days=1)
        ),
        status="active",
        telegram_message_id=message_id,
        telegram_chat_id=chat_id if message_id else None,
    )
    db.session.add(swap)
    db.session.commit()
    return swap


def run_swap_needed_reuses_existing_broadcast(app):
    with app.app_context():
        _clear_db()
        event, assignment = _event_with_assignment(
            datetime.date(2026, 6, 14),
            person="Marvin",
            status="swap_needed",
        )
        assignment.telegram_message_id = 700
        swap = _active_swap_for_assignment(assignment, message_id=700)

        edited = []
        sent = []
        old_edit = tg.edit_message_with_error
        old_send = tg.send_message
        try:
            tg.edit_message_with_error = lambda chat_id, message_id, text, reply_markup=None: (
                edited.append((chat_id, message_id, text)) or (True, None)
            )
            tg.send_message = lambda *args, **kwargs: sent.append((args, kwargs)) or 701

            assert tg.send_swap_needed(event, assignment, chat_id="chat") == 700
        finally:
            tg.edit_message_with_error = old_edit
            tg.send_message = old_send

        db.session.expire_all()
        swap = db.session.get(SwapRequest, swap.id)
        assignment = db.session.get(Assignment, assignment.id)
        assert [(chat_id, message_id) for chat_id, message_id, _text in edited] == [("chat", 700)]
        assert not sent
        assert swap.telegram_message_id == 700
        assert assignment.telegram_message_id == 700


def run_swap_needed_message_uses_open_shift_layout(app):
    with app.app_context():
        _clear_db()
        event, assignment = _event_with_assignment(
            datetime.date(2026, 6, 21),
            role="Computer",
            person="Andy",
            status="swap_needed",
            day_type="Sunday",
        )

        text, _buttons = tg._swap_needed_text_and_buttons(event, assignment)

        assert text == (
            f"{tg._weekly_decline_status_icon()} Andy can't make it to his shift:\n"
            "Sunday Service - June 21\n"
            " 🖥️ Computer\n\n"
            "Could someone please jump in for him?"
        )
        assert "<b>" not in text


def run_weekly_single_shift_decline_skips_confirmation(app):
    with app.app_context():
        _clear_db()
        event, assignment = _event_with_assignment(
            datetime.date(2026, 6, 21),
            role="Computer",
            person="Andy",
            status="pending",
            day_type="Sunday",
        )

        markups = []
        edits = []
        sent = []
        answers = []
        refreshed = []
        old_edit_markup = tg.edit_message_markup
        old_edit_message = tg.edit_message
        old_send_message = tg.send_message
        old_answer_callback = tg.answer_callback
        old_notify_admin = tg._notify_admin
        old_refresh_event = tg.refresh_event_telegram
        try:
            tg.edit_message_markup = lambda chat_id, message_id, reply_markup: (
                markups.append((chat_id, message_id, reply_markup)) or True
            )
            tg.edit_message = lambda chat_id, message_id, text, reply_markup=None: (
                edits.append((chat_id, message_id, text, reply_markup)) or True
            )
            tg.send_message = lambda text, chat_id=None, reply_markup=None, parse_mode="HTML": (
                sent.append((chat_id, text, reply_markup)) or 900
            )
            tg.answer_callback = lambda callback_id, text="", show_alert=False: (
                answers.append((callback_id, text, show_alert)) or True
            )
            tg._notify_admin = lambda *args, **kwargs: None
            tg.refresh_event_telegram = lambda refreshed_event: (
                refreshed.append(refreshed_event.id) or True
            )

            assert tg._weekly_select_shift(
                "cb", "chat", 321, "Andy", "decline",
                telegram_user_id="tg-andy", first_name="Andy", today=event.date,
            ) is True
        finally:
            tg.edit_message_markup = old_edit_markup
            tg.edit_message = old_edit_message
            tg.send_message = old_send_message
            tg.answer_callback = old_answer_callback
            tg._notify_admin = old_notify_admin
            tg.refresh_event_telegram = old_refresh_event

        db.session.expire_all()
        assignment = db.session.get(Assignment, assignment.id)
        swap = SwapRequest.query.filter_by(assignment_id=assignment.id, status="active").one()
        assert assignment.status == "swap_needed"
        assert assignment.telegram_message_id == 900
        assert swap.requestor == "Andy"
        assert swap.telegram_message_id == 900
        assert not markups
        assert edits and edits[-1][0:2] == ("chat", 321)
        assert sent and "Andy can't make it to his shift" in sent[-1][1]
        assert answers == [("cb", "", False)]
        assert refreshed == [event.id]


def run_weekly_multi_shift_decline_choice_is_final(app):
    with app.app_context():
        _clear_db()
        friday_event, friday_assignment = _event_with_assignment(
            datetime.date(2026, 6, 19),
            role="Camera",
            person="Andy",
            status="pending",
            day_type="Friday",
        )
        sunday_event, sunday_assignment = _event_with_assignment(
            datetime.date(2026, 6, 21),
            role="Computer",
            person="Andy",
            status="pending",
            day_type="Sunday",
        )

        markups = []
        answers = []
        old_edit_markup = tg.edit_message_markup
        old_answer_callback = tg.answer_callback
        try:
            tg.edit_message_markup = lambda chat_id, message_id, reply_markup: (
                markups.append((chat_id, message_id, reply_markup)) or True
            )
            tg.answer_callback = lambda callback_id, text="", show_alert=False: (
                answers.append((callback_id, text, show_alert)) or True
            )

            assert tg._weekly_select_shift(
                "cb", "chat", 321, "Andy", "decline",
                telegram_user_id="tg-andy", first_name="Andy", today=friday_event.date,
            ) is True
        finally:
            tg.edit_message_markup = old_edit_markup
            tg.answer_callback = old_answer_callback

        keyboard = markups[-1][2]["inline_keyboard"]
        callbacks = [row[0]["callback_data"] for row in keyboard]
        assert f"weekly_decline_shift:{friday_assignment.id}" in callbacks
        assert f"weekly_decline_shift:{sunday_assignment.id}" in callbacks
        assert not any("weekly_decline_yes" in callback for callback in callbacks)
        assert answers == [("cb", "", False)]
        assert SwapRequest.query.count() == 0

        markups = []
        edits = []
        sent = []
        answers = []
        refreshed = []
        old_override = dict(tg.TELEGRAM_PERSON_OVERRIDE)
        old_edit_markup = tg.edit_message_markup
        old_edit_message = tg.edit_message
        old_send_message = tg.send_message
        old_answer_callback = tg.answer_callback
        old_notify_admin = tg._notify_admin
        old_refresh_event = tg.refresh_event_telegram
        try:
            tg.TELEGRAM_PERSON_OVERRIDE["1"] = "Andy"
            tg.edit_message_markup = lambda chat_id, message_id, reply_markup: (
                markups.append((chat_id, message_id, reply_markup)) or True
            )
            tg.edit_message = lambda chat_id, message_id, text, reply_markup=None: (
                edits.append((chat_id, message_id, text, reply_markup)) or True
            )
            tg.send_message = lambda text, chat_id=None, reply_markup=None, parse_mode="HTML": (
                sent.append((chat_id, text, reply_markup)) or 901
            )
            tg.answer_callback = lambda callback_id, text="", show_alert=False: (
                answers.append((callback_id, text, show_alert)) or True
            )
            tg._notify_admin = lambda *args, **kwargs: None
            tg.refresh_event_telegram = lambda refreshed_event: (
                refreshed.append(refreshed_event.id) or True
            )

            tg.handle_callback_query({
                "id": "cb2",
                "from": {"id": 1, "first_name": "Andy"},
                "message": {"chat": {"id": "chat"}, "message_id": 321},
                "data": f"weekly_decline_shift:{friday_assignment.id}",
            })
        finally:
            tg.TELEGRAM_PERSON_OVERRIDE.clear()
            tg.TELEGRAM_PERSON_OVERRIDE.update(old_override)
            tg.edit_message_markup = old_edit_markup
            tg.edit_message = old_edit_message
            tg.send_message = old_send_message
            tg.answer_callback = old_answer_callback
            tg._notify_admin = old_notify_admin
            tg.refresh_event_telegram = old_refresh_event

        db.session.expire_all()
        friday_assignment = db.session.get(Assignment, friday_assignment.id)
        sunday_assignment = db.session.get(Assignment, sunday_assignment.id)
        swap = SwapRequest.query.filter_by(
            assignment_id=friday_assignment.id, status="active",
        ).one()
        assert friday_assignment.status == "swap_needed"
        assert sunday_assignment.status == "pending"
        assert swap.telegram_message_id == 901
        assert not markups
        assert edits and edits[-1][0:2] == ("chat", 321)
        assert sent and "Bible Study - June 19" in sent[-1][1]
        assert answers == [("cb2", "", False)]
        assert refreshed == [friday_event.id]


def run_weekly_confirm_on_covered_shift_stays_confirmed(app):
    with app.app_context():
        _clear_db()
        event, assignment = _event_with_assignment(
            datetime.date(2026, 6, 21),
            role="Computer",
            person="Rene",
            status="confirmed",
            day_type="Sunday",
        )
        assignment.cover = "Andy"
        db.session.commit()

        edits = []
        answers = []
        refreshed = []
        old_edit_message = tg.edit_message
        old_answer_callback = tg.answer_callback
        old_refresh_event = tg.refresh_event_telegram
        try:
            tg.edit_message = lambda chat_id, message_id, text, reply_markup=None: (
                edits.append((chat_id, message_id, text, reply_markup)) or True
            )
            tg.answer_callback = lambda callback_id, text="", show_alert=False: (
                answers.append((callback_id, text, show_alert)) or True
            )
            tg.refresh_event_telegram = lambda refreshed_event: (
                refreshed.append(refreshed_event.id) or True
            )

            assert tg._weekly_confirm_assignment(
                "cb", "chat", 321, assignment, "Andy",
                telegram_user_id="tg-andy", first_name="Andy",
            ) is True
        finally:
            tg.edit_message = old_edit_message
            tg.answer_callback = old_answer_callback
            tg.refresh_event_telegram = old_refresh_event

        db.session.expire_all()
        assignment = db.session.get(Assignment, assignment.id)
        log = InteractionLog.query.order_by(InteractionLog.id.desc()).first()
        assert assignment.status == "confirmed"
        assert assignment.cover == "Andy"
        assert not any(entry.get("action") == "undo" for entry in assignment.history)
        assert log.action == "confirm"
        assert log.details == "weekly_button_already_confirmed_cover"
        assert answers == [("cb", "Already confirmed.", False)]
        assert edits and edits[-1][0:2] == ("chat", 321)
        assert refreshed == [event.id]


def run_event_confirm_on_covered_shift_stays_confirmed(app):
    with app.app_context():
        _clear_db()
        event, assignment = _event_with_assignment(
            datetime.date(2026, 6, 21),
            role="Computer",
            person="Rene",
            status="confirmed",
            day_type="Sunday",
        )
        assignment.cover = "Andy"
        db.session.commit()

        edits = []
        answers = []
        weekly_updates = []
        old_edit_message = tg.edit_message
        old_answer_callback = tg.answer_callback
        old_update_weekly = tg.update_weekly_schedule_for_event
        try:
            tg.edit_message = lambda chat_id, message_id, text, reply_markup=None: (
                edits.append((chat_id, message_id, text, reply_markup)) or True
            )
            tg.answer_callback = lambda callback_id, text="", show_alert=False: (
                answers.append((callback_id, text, show_alert)) or True
            )
            tg.update_weekly_schedule_for_event = lambda updated_event: (
                weekly_updates.append(updated_event.id) or True
            )

            assert tg._event_confirm_assignment(
                "cb", "chat", 347, assignment, "Andy",
                telegram_user_id="tg-andy", first_name="Andy",
            ) is True
        finally:
            tg.edit_message = old_edit_message
            tg.answer_callback = old_answer_callback
            tg.update_weekly_schedule_for_event = old_update_weekly

        db.session.expire_all()
        assignment = db.session.get(Assignment, assignment.id)
        log = InteractionLog.query.order_by(InteractionLog.id.desc()).first()
        assert assignment.status == "confirmed"
        assert assignment.cover == "Andy"
        assert not any(entry.get("action") == "undo" for entry in assignment.history)
        assert log.action == "confirm"
        assert log.details == "event_reminder_already_confirmed_cover"
        assert answers == [("cb", "Already confirmed.", False)]
        assert edits and edits[-1][0:2] == ("chat", 347)
        assert weekly_updates == [event.id]


def run_swap_needed_replaces_missing_broadcast(app):
    with app.app_context():
        _clear_db()
        event, assignment = _event_with_assignment(
            datetime.date(2026, 6, 14),
            person="Marvin",
            status="swap_needed",
        )
        assignment.telegram_message_id = 700
        swap = _active_swap_for_assignment(assignment, message_id=700)

        edited = []
        sent = []
        old_edit = tg.edit_message_with_error
        old_send = tg.send_message
        try:
            tg.edit_message_with_error = lambda chat_id, message_id, text, reply_markup=None: (
                edited.append((chat_id, message_id)) or (False, "Bad Request: message to edit not found")
            )
            tg.send_message = lambda text, chat_id=None, reply_markup=None, parse_mode="HTML": (
                sent.append((chat_id, text)) or 701
            )

            assert tg.send_swap_needed(event, assignment, chat_id="chat") == 701
        finally:
            tg.edit_message_with_error = old_edit
            tg.send_message = old_send

        db.session.expire_all()
        swap = db.session.get(SwapRequest, swap.id)
        assignment = db.session.get(Assignment, assignment.id)
        assert edited == [("chat", 700)]
        assert sent and sent[-1][0] == "chat"
        assert swap.telegram_message_id == 701
        assert swap.telegram_chat_id == "chat"
        assert assignment.telegram_message_id == 701


def run_swap_needed_does_not_duplicate_on_refresh_error(app):
    with app.app_context():
        _clear_db()
        event, assignment = _event_with_assignment(
            datetime.date(2026, 6, 14),
            person="Marvin",
            status="swap_needed",
        )
        assignment.telegram_message_id = 700
        swap = _active_swap_for_assignment(assignment, message_id=700)

        sent = []
        old_edit = tg.edit_message_with_error
        old_send = tg.send_message
        try:
            tg.edit_message_with_error = lambda chat_id, message_id, text, reply_markup=None: (
                False, "Too Many Requests: retry later"
            )
            tg.send_message = lambda *args, **kwargs: sent.append((args, kwargs)) or 701

            assert tg.send_swap_needed(event, assignment, chat_id="chat") == 700
        finally:
            tg.edit_message_with_error = old_edit
            tg.send_message = old_send

        db.session.expire_all()
        assert not sent
        assert db.session.get(SwapRequest, swap.id).telegram_message_id == 700


def run_expired_uncovered_swap_renders_struck_through(app):
    with app.app_context():
        _clear_db()
        event, assignment = _event_with_assignment(
            datetime.date(2026, 6, 12),
            person="David Fink",
            status="swap_needed",
        )
        db.session.add(SwapRequest(
            assignment_id=assignment.id,
            requestor="David Fink",
            event_date=event.date,
            role=assignment.role,
            expires_at=(
                datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                - datetime.timedelta(minutes=5)
            ),
            status="expired",
        ))
        db.session.commit()

        weekly = tg.format_weekly_schedule(today=event.date)
        group_post = tg.format_today_group_post(event)
        monthly = tg.format_monthly_schedule(2026, 6)
        collapsed_buttons = tg._build_event_buttons(event)["inline_keyboard"]
        expanded_buttons = tg._build_event_buttons(event, expanded_id=assignment.id)["inline_keyboard"]

        assert "<s>David Fink</s>" in weekly
        assert "<s>David Fink</s>" in group_post
        assert "<s>David Fink</s>" in monthly
        assert tg.DECLINE_CUSTOM_EMOJI_ID not in weekly
        assert tg.DECLINE_CUSTOM_EMOJI_ID not in group_post
        assert collapsed_buttons[0][0]["text"].endswith("David Fink - closed")
        assert "🔴" not in collapsed_buttons[0][0]["text"]
        assert "I can cover" not in str(expanded_buttons)


def run_past_confirmed_event_hides_green_telegram_icon(app):
    with app.app_context():
        _clear_db()
        event_date = datetime.date(2026, 6, 16)
        event, assignment = _event_with_assignment(
            event_date,
            person="Rene",
            status="confirmed",
        )

        old_today = tg.vancouver_today
        try:
            tg.vancouver_today = lambda: event_date
            same_day_weekly = tg.format_weekly_schedule(today=event_date)
            same_day_group_post = tg.format_today_group_post(event)
            same_day_monthly = tg.format_monthly_schedule(2026, 6)

            tg.vancouver_today = lambda: event_date + datetime.timedelta(days=1)
            past_weekly = tg.format_weekly_schedule(today=event_date)
            past_group_post = tg.format_today_group_post(event)
            past_monthly = tg.format_monthly_schedule(2026, 6)
        finally:
            tg.vancouver_today = old_today

        assert tg.CONFIRM_CUSTOM_EMOJI_ID in same_day_weekly
        assert tg.CONFIRM_CUSTOM_EMOJI_ID in same_day_group_post
        assert "✅ Rene" in same_day_monthly

        assert tg.CONFIRM_CUSTOM_EMOJI_ID not in past_weekly
        assert tg.CONFIRM_CUSTOM_EMOJI_ID not in past_group_post
        assert "✅ Rene" not in past_monthly
        assert "Rene" in past_weekly
        assert "Rene" in past_group_post
        assert "Rene" in past_monthly


def run_rich_weekly_schedule_uses_role_comparison_table(app):
    with app.app_context():
        _clear_db()
        friday = Event(date=datetime.date(2026, 6, 12), day_type="Friday")
        sunday = Event(date=datetime.date(2026, 6, 14), day_type="Sunday")
        db.session.add_all([friday, sunday])
        db.session.flush()
        db.session.add_all([
            Assignment(event_id=friday.id, role="Computer", person="Marvin", status="pending"),
            Assignment(event_id=friday.id, role="Camera", person="David Fink", status="pending"),
            Assignment(event_id=sunday.id, role="Computer", person="Rene", status="pending"),
            Assignment(event_id=sunday.id, role="Camera 1", person="David Fink", status="pending"),
            Assignment(event_id=sunday.id, role="Camera 2", person="Marvin", status="pending"),
        ])
        db.session.commit()

        rich = tg.format_weekly_schedule_rich(today=datetime.date(2026, 6, 9))

        assert "<table bordered striped>" in rich
        assert "<th align=\"left\">Role</th><th>Bible Study</th><th>Sunday Service</th>" in rich
        assert "<td align=\"left\">Computer</td><td align=\"center\">Marvin</td><td align=\"center\">Rene</td>" in rich
        assert "<td align=\"left\">Camera</td><td align=\"center\">David Fink</td><td align=\"center\">David Fink</td>" in rich
        assert "<td align=\"left\">Camera 2</td><td align=\"center\">-</td><td align=\"center\">Marvin</td>" in rich


def run_weekly_moved_bible_study_uses_actual_weekday_without_missing_slot(app):
    with app.app_context():
        _clear_db()
        wednesday = datetime.date(2026, 6, 17)
        _event_with_assignment(wednesday, location="Pleasant Valley Church", cancelled=True)

        weekly = tg.format_weekly_schedule(today=datetime.date(2026, 6, 16))

        assert "<b>Wednesday Bible Study 📖</b>" in weekly
        assert "📍 Pleasant Valley Church" in weekly
        assert "June 17 @ 7:00 PM" in weekly
        assert "✅ <i>No livestream needed</i>" in weekly
        assert "No Bible Study scheduled." not in weekly


def run_weekly_non_default_bible_study_location_shows_when_active(app):
    with app.app_context():
        _clear_db()
        friday = datetime.date(2026, 6, 19)
        event, first = _event_with_assignment(
            friday,
            role="Computer",
            person="Stefan",
            location="Pleasant Valley Church",
        )
        db.session.add(Assignment(event_id=event.id, role="Camera", person="Patric", status="pending"))
        db.session.commit()

        weekly = tg.format_weekly_schedule(today=datetime.date(2026, 6, 16))

        assert "<b>Friday Bible Study 📖</b>" in weekly
        assert "📍 Pleasant Valley Church" in weekly
        assert "June 19 @ 7:00 PM" in weekly
        assert "🖥️ Stefan" in weekly
        assert "📹 Patric" in weekly
        assert first.person == "Stefan"


def run_midnight_cleanup_refreshes_yesterdays_weekly_schedule(app):
    with app.app_context():
        _clear_db()
        event_date = datetime.date(2026, 6, 16)
        _event_with_assignment(event_date, person="Rene", status="confirmed")

        refreshed = []
        old_update = tg.update_weekly_schedule_for_date
        try:
            tg.update_weekly_schedule_for_date = lambda date_obj: refreshed.append(date_obj) or True

            assert tg.delete_past_event_reminders(today=event_date + datetime.timedelta(days=1)) == 0
        finally:
            tg.update_weekly_schedule_for_date = old_update

        assert refreshed == [event_date]


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
        run_expired_swap_sweep_closes_undeletable_broadcast(app)
        run_swap_needed_reuses_existing_broadcast(app)
        run_swap_needed_message_uses_open_shift_layout(app)
        run_weekly_single_shift_decline_skips_confirmation(app)
        run_weekly_multi_shift_decline_choice_is_final(app)
        run_weekly_confirm_on_covered_shift_stays_confirmed(app)
        run_event_confirm_on_covered_shift_stays_confirmed(app)
        run_swap_needed_replaces_missing_broadcast(app)
        run_swap_needed_does_not_duplicate_on_refresh_error(app)
        run_expired_uncovered_swap_renders_struck_through(app)
        run_past_confirmed_event_hides_green_telegram_icon(app)
        run_rich_weekly_schedule_uses_role_comparison_table(app)
        run_weekly_moved_bible_study_uses_actual_weekday_without_missing_slot(app)
        run_weekly_non_default_bible_study_location_shows_when_active(app)
        run_midnight_cleanup_refreshes_yesterdays_weekly_schedule(app)
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
