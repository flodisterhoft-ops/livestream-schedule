from .extensions import db
from datetime import datetime
import json


class TeamMember(db.Model):
    """Tracks team members and their eligible roles."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    telegram_user_id = db.Column(db.String(20))
    _sunday_roles_json = db.Column(db.Text, default='[]')
    _friday_roles_json = db.Column(db.Text, default='[]')
    _role_preferences_json = db.Column(db.Text, default='{}')
    active = db.Column(db.Boolean, default=True)
    active_from = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def sunday_roles(self):
        try:
            return json.loads(self._sunday_roles_json or '[]')
        except (json.JSONDecodeError, TypeError):
            return []

    @sunday_roles.setter
    def sunday_roles(self, value):
        self._sunday_roles_json = json.dumps(value)

    @property
    def friday_roles(self):
        try:
            return json.loads(self._friday_roles_json or '[]')
        except (json.JSONDecodeError, TypeError):
            return []

    @friday_roles.setter
    def friday_roles(self, value):
        self._friday_roles_json = json.dumps(value)

    @property
    def role_preferences(self):
        try:
            return json.loads(self._role_preferences_json or '{}')
        except (json.JSONDecodeError, TypeError):
            return {}

    @role_preferences.setter
    def role_preferences(self, value):
        self._role_preferences_json = json.dumps(value or {})

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "telegram_user_id": self.telegram_user_id,
            "sunday_roles": self.sunday_roles,
            "friday_roles": self.friday_roles,
            "role_preferences": self.role_preferences,
            "active": self.active,
            "active_from": self.active_from.isoformat() if self.active_from else None,
        }

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False)
    day_type = db.Column(db.String(20), nullable=False)  # Sunday, Friday, Custom
    custom_title = db.Column(db.String(100))
    start_time = db.Column(db.Time)
    notes = db.Column(db.Text)  # Event notes/comments
    cancelled = db.Column(db.Boolean, default=False, nullable=False)  # No livestream needed (e.g., communion)
    telegram_message_id = db.Column(db.Integer)  # v2 reminder message ID
    telegram_chat_id = db.Column(db.String(30))  # Chat where reminder was sent
    assignments = db.relationship('Assignment', backref='event', lazy=True, cascade="all, delete-orphan", order_by="Assignment.id")

    def to_dict(self):
        return {
            "day_type": self.day_type,
            "custom_title": self.custom_title,
            "start_time": self.start_time.strftime("%H:%M") if self.start_time else None,
            "notes": self.notes,
            "cancelled": bool(self.cancelled),
            "assignments": [a.to_dict() for a in self.assignments]
        }

class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    person = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending, confirmed, swap_needed
    cover = db.Column(db.String(50))
    swapped_with = db.Column(db.String(50))
    _history_json = db.Column(db.Text, default="[]")
    telegram_message_id = db.Column(db.Integer)  # Track Telegram msg for edit/delete
    locked = db.Column(db.Boolean, default=False, nullable=False)  # Manager pin: never auto-replace

    @property
    def history(self):
        try:
            return json.loads(self._history_json)
        except:
            return []

    @history.setter
    def history(self, value):
        self._history_json = json.dumps(value)

    def to_dict(self):
        return {
            "role": self.role,
            "person": self.person,
            "status": self.status,
            "cover": self.cover,
            "swapped_with": self.swapped_with,
            "_hist": self.history
        }

class Availability(db.Model):
    """Tracks when team members are unavailable."""
    id = db.Column(db.Integer, primary_key=True)
    person = db.Column(db.String(50), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(200))
    # For recurring patterns like "never available on 1st Sundays"
    recurring = db.Column(db.Boolean, default=False)
    pattern = db.Column(db.String(50))  # e.g., "1st_sunday", "every_friday"
    
    def to_dict(self):
        return {
            "id": self.id,
            "person": self.person,
            "start_date": self.start_date.strftime("%Y-%m-%d") if self.start_date else None,
            "end_date": self.end_date.strftime("%Y-%m-%d") if self.end_date else None,
            "reason": self.reason,
            "recurring": self.recurring,
            "pattern": self.pattern
        }
class InteractionLog(db.Model):
    """Logs every Telegram button press for the admin stats page."""
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    telegram_user_id = db.Column(db.String(20))
    first_name = db.Column(db.String(100))
    action = db.Column(db.String(50))       # confirm, decline, expand, pickup_as, etc.
    person_name = db.Column(db.String(50))  # Resolved team member name
    assignment_id = db.Column(db.Integer)
    event_date = db.Column(db.Date)
    role = db.Column(db.String(50))
    details = db.Column(db.Text)            # Extra context


class SwapRequest(db.Model):
    """An open shift-swap created when someone declines an assignment.

    Lifecycle:
        active    — open for anyone eligible to pick up
        accepted  — someone covered; original person is off the hook
        expired   — deadline passed with no taker → triggers auto-reschedule
        cancelled — original person tapped "Undo" before deadline
    """
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignment.id'), nullable=False, index=True)
    requestor = db.Column(db.String(50), nullable=False)   # Person who declined
    event_date = db.Column(db.Date, nullable=False, index=True)
    role = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(20), default="active", index=True)
    accepted_by = db.Column(db.String(50))                 # Who picked it up, if any
    accepted_at = db.Column(db.DateTime)
    telegram_message_id = db.Column(db.Integer)            # Broadcast msg for edits
    telegram_chat_id = db.Column(db.String(30))
    reschedule_event_date = db.Column(db.Date)             # Where requestor got rebooked
    reschedule_notes = db.Column(db.Text)                  # "Displaced Rene from 2026-05-17"

    assignment = db.relationship('Assignment', backref=db.backref('swap_requests', cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            "id": self.id,
            "assignment_id": self.assignment_id,
            "requestor": self.requestor,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "role": self.role,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "status": self.status,
            "accepted_by": self.accepted_by,
            "reschedule_event_date": self.reschedule_event_date.isoformat() if self.reschedule_event_date else None,
        }


class EventSuggestion(db.Model):
    """A user-submitted suggestion for a new event awaiting manager review."""
    id = db.Column(db.Integer, primary_key=True)
    suggester_name = db.Column(db.String(100), nullable=False)
    event_type = db.Column(db.String(60), nullable=False)
    custom_title = db.Column(db.String(120))
    date = db.Column(db.Date, nullable=False, index=True)
    time = db.Column(db.String(8))
    notes = db.Column(db.Text)
    status = db.Column(db.String(20), default="pending", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    telegram_message_id = db.Column(db.Integer)
    telegram_chat_id = db.Column(db.String(30))
    accepted_event_date = db.Column(db.Date)

    def to_dict(self):
        return {
            "id": self.id,
            "suggester_name": self.suggester_name,
            "event_type": self.event_type,
            "custom_title": self.custom_title,
            "date": self.date.isoformat() if self.date else None,
            "time": self.time,
            "notes": self.notes,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "accepted_event_date": self.accepted_event_date.isoformat() if self.accepted_event_date else None,
        }


class SchedulingSnapshot(db.Model):
    """Stores a snapshot of future assignments before a scheduling-controls apply.
    Used by the admin Undo flow to revert the most recent rebalance."""
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    created_by = db.Column(db.String(50))
    label = db.Column(db.String(80))
    _snapshot_json = db.Column(db.Text, default='[]')

    @property
    def snapshot(self):
        try:
            return json.loads(self._snapshot_json or '[]')
        except (json.JSONDecodeError, TypeError):
            return []

    @snapshot.setter
    def snapshot(self, value):
        self._snapshot_json = json.dumps(value or [])

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
            "label": self.label,
            "size": len(self.snapshot),
        }


class SchedulingPreset(db.Model):
    """A saved target distribution preset for the Scheduling Controls modal."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(50))
    _targets_json = db.Column(db.Text, default='{}')

    @property
    def targets(self):
        try:
            return json.loads(self._targets_json or '{}')
        except (json.JSONDecodeError, TypeError):
            return {}

    @targets.setter
    def targets(self, value):
        self._targets_json = json.dumps(value or {})

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
            "targets": self.targets,
        }


class TempChat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.String(40), nullable=False, index=True)
    message_id = db.Column(db.Integer)
    kind = db.Column(db.String(30), nullable=False, index=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignment.id'), index=True)
    swap_request_id = db.Column(db.Integer, db.ForeignKey('swap_request.id'), index=True)
    future_assignment_id = db.Column(db.Integer)
    person = db.Column(db.String(50))
    recipient = db.Column(db.String(50))
    status = db.Column(db.String(20), default="active", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, index=True)
