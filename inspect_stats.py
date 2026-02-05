from app import create_app
from app.models import Event, Assignment
from app.utils import ALL_NAMES
from collections import defaultdict

app = create_app()

def analyze_stats():
    with app.app_context():
        # Fetch all events
        events = Event.query.all()
        
        stats = defaultdict(lambda: {"total": 0, "sunday": 0, "friday": 0, "role_counts": defaultdict(int)})
        
        for event in events:
            is_sunday = event.day_type == "Sunday" or event.date.weekday() == 6
            is_friday = event.day_type == "Friday" or event.date.weekday() == 4
            
            for assignment in event.assignments:
                person = assignment.person
                if person in ALL_NAMES:
                    stats[person]["total"] += 1
                    stats[person]["role_counts"][assignment.role] += 1
                    
                    if is_sunday:
                        stats[person]["sunday"] += 1
                    if is_friday:
                        stats[person]["friday"] += 1

        print(f"{'Name':<10} | {'Total':<5} | {'Sun':<3} | {'Fri':<3} |Roles")
        print("-" * 60)
        
        sorted_people = sorted(stats.items(), key=lambda x: x[1]['total'], reverse=True)
        
        for name, data in sorted_people:
            roles_str = ", ".join([f"{r}:{c}" for r, c in data["role_counts"].items()])
            print(f"{name:<10} | {data['total']:<5} | {data['sunday']:<3} | {data['friday']:<3} | {roles_str}")

if __name__ == "__main__":
    analyze_stats()
