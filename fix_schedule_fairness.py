from app import create_app, db
from app.models import Event, Assignment
import datetime

app = create_app()

def regenerate():
    from app.scheduler import generate_month
    from inspect_stats import analyze_stats
    
    with app.app_context():
        print("Clearing schedule from March 1st, 2026 onwards...")
        # Delete future events/assignments
        start_date = datetime.date(2026, 3, 1)
        events = Event.query.filter(Event.date >= start_date).all()
        for e in events:
            # Delete assignments first
            Assignment.query.filter_by(event_id=e.id).delete()
            db.session.delete(e)
        db.session.commit()
        print("Cleared.")
        
        # Regenerate Mar-Dec
        for month in range(3, 13):
            print(f"Generating Month {month}...")
            generate_month(2026, month)
            
        print("\nNew Stats:")
        # We can just call analyze_stats from here or let the user run it
        # But let's just run it
        analyze_stats()

if __name__ == "__main__":
    regenerate()
