"""
Migration script: Render PostgreSQL -> Supabase
Copies all data from old database to new database.
"""
import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Connection strings
RENDER_URL = "postgresql://scheduler:KTCQXlX1SPbBU6Er0oMTLFMbYok3V0fC@dpg-d61fishr0fns73fvriq0-a.oregon-postgres.render.com/schedule_gl02"
SUPABASE_URL = "postgresql://postgres:5%23yHh7ZjWCMSJp%2B@db.oeyztipwxfnkpfnfestc.supabase.co:5432/postgres"
# Note: # encoded as %23, + encoded as %2B

def migrate():
    print("Connecting to Render database...")
    render_engine = create_engine(RENDER_URL)

    print("Connecting to Supabase database...")
    supabase_engine = create_engine(SUPABASE_URL)

    RenderSession = sessionmaker(bind=render_engine)
    SupabaseSession = sessionmaker(bind=supabase_engine)

    render_session = RenderSession()
    supabase_session = SupabaseSession()

    # Drop existing tables in Supabase to start fresh
    print("\nDropping existing tables in Supabase...")
    drop_order = ['pickup_token', 'assignment', 'availability', 'token', 'event']
    for table in drop_order:
        try:
            supabase_session.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
            print(f"  Dropped {table}")
        except Exception as e:
            print(f"  Could not drop {table}: {e}")
    supabase_session.commit()

    # Create tables on Supabase using our app's models
    print("\nCreating tables on Supabase...")
    os.environ['DATABASE_URL'] = SUPABASE_URL
    from app import create_app
    from app.extensions import db

    app = create_app()

    with app.app_context():
        db.create_all()
        print("Tables created!")

        # Now copy data using raw SQL
        print("\nCopying Events...")
        events = render_session.execute(text("SELECT id, date, day_type, custom_title, notes FROM event")).fetchall()
        print(f"  Found {len(events)} events")

        for e in events:
            supabase_session.execute(
                text("INSERT INTO event (id, date, day_type, custom_title, notes) VALUES (:id, :date, :day_type, :custom_title, :notes) ON CONFLICT (id) DO NOTHING"),
                {"id": e.id, "date": e.date, "day_type": e.day_type, "custom_title": e.custom_title, "notes": e.notes}
            )
        supabase_session.commit()
        print("  Events copied!")

        print("\nCopying Assignments...")
        assignments = render_session.execute(text(
            "SELECT id, event_id, role, person, status, cover, swapped_with FROM assignment"
        )).fetchall()
        print(f"  Found {len(assignments)} assignments")

        for a in assignments:
            supabase_session.execute(
                text("""INSERT INTO assignment (id, event_id, role, person, status, cover, swapped_with, "_history_json")
                        VALUES (:id, :event_id, :role, :person, :status, :cover, :swapped_with, '[]')
                        ON CONFLICT (id) DO NOTHING"""),
                {"id": a.id, "event_id": a.event_id, "role": a.role, "person": a.person,
                 "status": a.status, "cover": a.cover, "swapped_with": a.swapped_with}
            )
        supabase_session.commit()
        print("  Assignments copied!")

        print("\nCopying Availability...")
        try:
            avails = render_session.execute(text("SELECT id, person, start_date, end_date, reason, recurring, pattern FROM availability")).fetchall()
            print(f"  Found {len(avails)} availability records")
            for av in avails:
                supabase_session.execute(
                    text("""INSERT INTO availability (id, person, start_date, end_date, reason, recurring, pattern)
                            VALUES (:id, :person, :start_date, :end_date, :reason, :recurring, :pattern)
                            ON CONFLICT (id) DO NOTHING"""),
                    {"id": av.id, "person": av.person, "start_date": av.start_date,
                     "end_date": av.end_date, "reason": av.reason, "recurring": av.recurring, "pattern": av.pattern}
                )
            supabase_session.commit()
            print("  Availability copied!")
        except Exception as e:
            print(f"  Availability: {e}")

        print("\nCopying Tokens...")
        try:
            tokens = render_session.execute(text("SELECT id, token, created_at FROM token")).fetchall()
            print(f"  Found {len(tokens)} tokens")
            for t in tokens:
                supabase_session.execute(
                    text("INSERT INTO token (id, token, created_at) VALUES (:id, :token, :created_at) ON CONFLICT (id) DO NOTHING"),
                    {"id": t.id, "token": t.token, "created_at": t.created_at}
                )
            supabase_session.commit()
            print("  Tokens copied!")
        except Exception as e:
            print(f"  Tokens: {e}")

        print("\nCopying PickupTokens...")
        try:
            pickup_tokens = render_session.execute(text("SELECT id, token, assignment_id, person, used, created_at FROM pickup_token")).fetchall()
            print(f"  Found {len(pickup_tokens)} pickup tokens")
            for pt in pickup_tokens:
                supabase_session.execute(
                    text("""INSERT INTO pickup_token (id, token, assignment_id, person, used, created_at)
                            VALUES (:id, :token, :assignment_id, :person, :used, :created_at)
                            ON CONFLICT (id) DO NOTHING"""),
                    {"id": pt.id, "token": pt.token, "assignment_id": pt.assignment_id,
                     "person": pt.person, "used": pt.used, "created_at": pt.created_at}
                )
            supabase_session.commit()
            print("  PickupTokens copied!")
        except Exception as e:
            print(f"  PickupTokens: {e}")

        # Reset sequences to avoid ID conflicts
        print("\nResetting ID sequences...")
        tables = ['event', 'assignment', 'availability', 'token', 'pickup_token']
        for table in tables:
            try:
                result = supabase_session.execute(text(f"SELECT MAX(id) FROM {table}")).scalar()
                if result:
                    supabase_session.execute(text(f"SELECT setval('{table}_id_seq', {result})"))
                    print(f"  {table}_id_seq set to {result}")
            except Exception as e:
                pass
        supabase_session.commit()

    print("\n" + "="*50)
    print("MIGRATION COMPLETE!")
    print("="*50)

    render_session.close()
    supabase_session.close()

if __name__ == "__main__":
    migrate()
