from .extensions import db
from datetime import datetime
import json

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False)
    day_type = db.Column(db.String(20), nullable=False)  # Sunday, Friday, Custom
    custom_title = db.Column(db.String(100))
    notes = db.Column(db.Text)  # Event notes/comments
    assignments = db.relationship('Assignment', backref='event', lazy=True, cascade="all, delete-orphan", order_by="Assignment.id")

    def to_dict(self):
        return {
            "day_type": self.day_type,
            "custom_title": self.custom_title,
            "notes": self.notes,
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

class Token(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(36), unique=True, nullable=False)
    created_at = db.Column(db.Date, default=datetime.utcnow)

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

