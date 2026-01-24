"""
Migration script to import data from schedule_db.json into SQLite database.

Usage:
    python migrate_json_to_sqlite.py path/to/schedule_db.json
    
Or for the default location:
    python migrate_json_to_sqlite.py
"""
import os
import sys
import json
import datetime

# Add the project to path
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

from app import create_app
from app.extensions import db
from app.models import Event, Assignment, Token


def parse_date(date_str: str) -> datetime.date:
    """Parse date string like 'January 04, 2026' to date object."""
    return datetime.datetime.strptime(date_str, "%B %d, %Y").date()


def migrate_json_to_sqlite(json_path: str):
    """
    Migrate data from JSON file to SQLite database.
    
    Args:
        json_path: Path to the schedule_db.json file
    """
    app = create_app()
    
    with app.app_context():
        # Load JSON data
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        roster = data.get('roster', {})
        tokens = data.get('tokens', {})
        
        print(f"Found {len(roster)} events and {len(tokens)} tokens in JSON")
        
        # Migrate events
        events_created = 0
        events_skipped = 0
        assignments_created = 0
        
        for date_str, event_data in roster.items():
            try:
                date_obj = parse_date(date_str)
            except ValueError as e:
                print(f"  Skipping invalid date: {date_str} - {e}")
                continue
            
            # Check if event already exists
            existing = Event.query.filter_by(date=date_obj).first()
            if existing:
                print(f"  Skipping existing event: {date_str}")
                events_skipped += 1
                continue
            
            # Create event
            day_type = event_data.get('day_type', 'Custom')
            # Handle 'Special' day_type from old format
            if day_type == 'Special':
                day_type = 'Custom'
            
            event = Event(
                date=date_obj,
                day_type=day_type,
                custom_title=event_data.get('custom_title')
            )
            db.session.add(event)
            db.session.flush()  # Get the ID
            
            # Create assignments
            for assign_data in event_data.get('assignments', []):
                role = assign_data.get('role', 'Team')
                person = assign_data.get('person', 'TBD')
                status = assign_data.get('status', 'pending')
                cover = assign_data.get('cover')
                swapped = assign_data.get('swapped_with')
                
                # Store history if present
                hist = assign_data.get('_hist', [])
                
                assignment = Assignment(
                    event_id=event.id,
                    role=role,
                    person=person,
                    status=status,
                    cover=cover,
                    swapped_with=swapped,
                )
                assignment.history = hist
                db.session.add(assignment)
                assignments_created += 1
            
            events_created += 1
            print(f"  Created: {date_str} - {day_type}")
        
        # Migrate tokens
        tokens_created = 0
        for token_str, created_date in tokens.items():
            existing = Token.query.filter_by(token=token_str).first()
            if existing:
                continue
            
            # Parse created date (might be just date or datetime string)
            try:
                if 'T' in created_date:
                    parsed = datetime.datetime.fromisoformat(created_date).date()
                else:
                    parsed = datetime.datetime.strptime(created_date, "%Y-%m-%d").date()
            except ValueError:
                parsed = datetime.date.today()
            
            token = Token(token=token_str, created_at=parsed)
            db.session.add(token)
            tokens_created += 1
        
        # Commit all changes
        db.session.commit()
        
        print("\n=== Migration Complete ===")
        print(f"Events created: {events_created}")
        print(f"Events skipped (already existed): {events_skipped}")
        print(f"Assignments created: {assignments_created}")
        print(f"Tokens created: {tokens_created}")


def main():
    if len(sys.argv) > 1:
        json_path = sys.argv[1]
    else:
        # Default paths to check
        possible_paths = [
            'schedule_db.json',
            os.path.join(os.path.dirname(__file__), 'schedule_db.json'),
            r'C:\Users\Disterhoft\Downloads\Files\schedule_db.json',
        ]
        
        json_path = None
        for path in possible_paths:
            if os.path.exists(path):
                json_path = path
                break
        
        if not json_path:
            print("Error: Could not find schedule_db.json")
            print("Usage: python migrate_json_to_sqlite.py path/to/schedule_db.json")
            sys.exit(1)
    
    if not os.path.exists(json_path):
        print(f"Error: File not found: {json_path}")
        sys.exit(1)
    
    print(f"Migrating data from: {json_path}")
    migrate_json_to_sqlite(json_path)


if __name__ == "__main__":
    main()
