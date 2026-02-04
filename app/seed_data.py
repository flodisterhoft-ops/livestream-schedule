"""
Seed data for the Livestream Schedule app.
This will pre-populate the database with historical schedule data.
Run this on app startup if the database is empty.
"""
import datetime
from .models import Event, Assignment
from .extensions import db


def seed_database():
    """
    Seed the database with schedule data if no events exist.
    This ensures data persists across Render deployments.
    """
    # Check if there are already events (don't overwrite)
    if Event.query.first():
        print("Database already has data, skipping seed.")
        return False
    
    print("Seeding database with schedule data...")
    
    # ============================================
    # JANUARY 2026
    # ============================================
    
    # Jan 1 - New Year's Day Service
    e = Event(date=datetime.date(2026, 1, 1), day_type="Sunday", custom_title="New Year's Day Service")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Computer", person="Florian", status="confirmed"),
        Assignment(event_id=e.id, role="Camera 1", person="Marvin", status="confirmed"),
        Assignment(event_id=e.id, role="Camera 2", person="Viktor", status="confirmed"),
    ])
    
    # Jan 4 - Sunday Service
    e = Event(date=datetime.date(2026, 1, 4), day_type="Sunday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Computer", person="Stefan", status="confirmed"),
        Assignment(event_id=e.id, role="Camera 1", person="Andy", status="confirmed"),
        Assignment(event_id=e.id, role="Camera 2", person="Viktor", status="confirmed"),
    ])
    
    # Jan 11 - Sunday Service
    e = Event(date=datetime.date(2026, 1, 11), day_type="Sunday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Computer", person="Patric", status="confirmed"),
        Assignment(event_id=e.id, role="Camera 1", person="Marvin", status="confirmed"),
        Assignment(event_id=e.id, role="Camera 2", person="Rene", status="confirmed"),
    ])
    
    # Jan 16 - Bible Study (NEEDED)
    e = Event(date=datetime.date(2026, 1, 16), day_type="Friday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Leader", person="Select Helper", status="swap_needed"),
        Assignment(event_id=e.id, role="Helper", person="Select Helper", status="swap_needed"),
    ])
    
    # Jan 18 - Sunday Service
    e = Event(date=datetime.date(2026, 1, 18), day_type="Sunday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Computer", person="Stefan", status="confirmed"),
        Assignment(event_id=e.id, role="Camera 1", person="Andy", status="confirmed"),
        Assignment(event_id=e.id, role="Camera 2", person="Viktor", status="confirmed"),
    ])
    
    # Jan 23 - Bible Study
    e = Event(date=datetime.date(2026, 1, 23), day_type="Friday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Leader", person="Marvin", status="confirmed"),
        Assignment(event_id=e.id, role="Helper", person="Viktor", status="confirmed"),
    ])
    
    # Jan 25 - Sunday Service (with swaps)
    e = Event(date=datetime.date(2026, 1, 25), day_type="Sunday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Computer", person="Florian", status="confirmed", 
                   swapped_with="Patric", cover="Marvin"),
        Assignment(event_id=e.id, role="Camera 1", person="Patric", status="confirmed", 
                   swapped_with="Florian"),
        Assignment(event_id=e.id, role="Camera 2", person="Rene", status="confirmed", 
                   swapped_with="Viktor"),
    ])
    
    # Jan 30 - Bible Study
    e = Event(date=datetime.date(2026, 1, 30), day_type="Friday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Leader", person="Stefan", status="pending"),
        Assignment(event_id=e.id, role="Helper", person="Viktor", status="confirmed"),
    ])
    
    # ============================================
    # FEBRUARY 2026
    # ============================================
    
    # Feb 1 - Sunday Service
    e = Event(date=datetime.date(2026, 2, 1), day_type="Sunday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Computer", person="Florian", status="confirmed"),
        Assignment(event_id=e.id, role="Camera 1", person="Rene", status="confirmed", 
                   swapped_with="Patric"),
        Assignment(event_id=e.id, role="Camera 2", person="Viktor", status="confirmed"),
    ])
    
    # Feb 6 - Bible Study
    e = Event(date=datetime.date(2026, 2, 6), day_type="Friday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Leader", person="Stefan", status="pending"),
        Assignment(event_id=e.id, role="Helper", person="Select Helper", status="swap_needed"),
    ])
    
    # Feb 8 - Sunday Service
    e = Event(date=datetime.date(2026, 2, 8), day_type="Sunday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Computer", person="Andy", status="pending"),
        Assignment(event_id=e.id, role="Camera 1", person="Marvin", status="pending"),
        Assignment(event_id=e.id, role="Camera 2", person="Patric", status="confirmed", 
                   swapped_with="Rene"),
    ])
    
    # Feb 13 - Bible Study
    e = Event(date=datetime.date(2026, 2, 13), day_type="Friday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Leader", person="Florian", status="confirmed"),
        Assignment(event_id=e.id, role="Helper", person="Select Helper", status="swap_needed"),
    ])
    
    # Feb 15 - Sunday Service
    e = Event(date=datetime.date(2026, 2, 15), day_type="Sunday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Computer", person="Marvin", status="pending"),
        Assignment(event_id=e.id, role="Camera 1", person="Stefan", status="pending"),
        Assignment(event_id=e.id, role="Camera 2", person="Viktor", status="confirmed"),
    ])
    
    # Feb 20 - Sunday Service
    e = Event(date=datetime.date(2026, 2, 20), day_type="Sunday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Computer", person="TBD", status="pending"),
        Assignment(event_id=e.id, role="Camera 1", person="Select Helper", status="swap_needed"),
        Assignment(event_id=e.id, role="Camera 2", person="Select Helper", status="swap_needed"),
    ])
    
    # Feb 22 - Sunday Service
    e = Event(date=datetime.date(2026, 2, 22), day_type="Sunday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Computer", person="Rene", status="pending"),
        Assignment(event_id=e.id, role="Camera 1", person="Patric", status="pending"),
        Assignment(event_id=e.id, role="Camera 2", person="Viktor", status="confirmed"),
    ])
    
    # Feb 27 - Bible Study
    e = Event(date=datetime.date(2026, 2, 27), day_type="Friday")
    db.session.add(e)
    db.session.flush()
    db.session.add_all([
        Assignment(event_id=e.id, role="Leader", person="Marvin", status="pending"),
        Assignment(event_id=e.id, role="Helper", person="Select Helper", status="swap_needed"),
    ])
    
    db.session.commit()
    print(f"Database seeded with schedule data!")
    return True
