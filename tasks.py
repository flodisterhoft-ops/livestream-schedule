"""
Scheduled tasks for the Livestream Schedule app.
This script is designed to be run as a daily scheduled task on PythonAnywhere.

Usage (add to PythonAnywhere scheduled tasks):
    python /home/yourusername/mysite/tasks.py

The task runs once daily and:
1. Sends morning reminders for today's events (run at 8 AM)
2. Optionally sends day-before reminders (if run in evening)
"""
import os
import sys
import datetime

# Add the app to Python path (adjust path for PythonAnywhere)
# This allows importing the Flask app modules
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

# Set up Flask app context
from app import create_app
from app.telegram import send_daily_reminders, get_upcoming_events, send_event_reminder

app = create_app()


def run_morning_reminders():
    """Send reminders for today's events."""
    with app.app_context():
        count = send_daily_reminders()
        print(f"Sent {count} morning reminder(s)")
        return count


def run_day_before_reminders():
    """Send reminders for tomorrow's events."""
    with app.app_context():
        events = get_upcoming_events(days_ahead=1)
        sent = 0
        for event in events:
            if send_event_reminder(event):
                sent += 1
        print(f"Sent {sent} day-before reminder(s)")
        return sent


def cleanup_old_tokens():
    """Clean up tokens older than 7 days."""
    from app.extensions import db
    from app.models import Token
    
    with app.app_context():
        cutoff = datetime.date.today() - datetime.timedelta(days=7)
        old_tokens = Token.query.filter(Token.created_at < cutoff).all()
        count = len(old_tokens)
        for token in old_tokens:
            db.session.delete(token)
        db.session.commit()
        print(f"Cleaned up {count} old token(s)")
        return count


def main():
    """
    Main task runner.
    
    On PythonAnywhere free tier, you get ONE scheduled task per day.
    We recommend scheduling this at 8:00 AM to send morning reminders.
    """
    print(f"=== Scheduled Task Running at {datetime.datetime.now()} ===")
    
    # Determine what to do based on current hour
    current_hour = datetime.datetime.now().hour
    
    # Morning (6 AM - 10 AM): Send today's reminders
    if 6 <= current_hour < 10:
        run_morning_reminders()
    
    # Evening (6 PM - 10 PM): Send tomorrow's reminders
    elif 18 <= current_hour < 22:
        run_day_before_reminders()
    
    # Any time: Can run both if needed
    else:
        # Default: send morning reminders
        run_morning_reminders()
    
    # Always clean up old tokens
    cleanup_old_tokens()
    
    print("=== Task Complete ===")


if __name__ == "__main__":
    main()
