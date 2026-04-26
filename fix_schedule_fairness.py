"""
Reset future generated events and regenerate the schedule using the
updated fairness algorithm (plain fair-share + Florian Friday bias).

Run from the project root:
    python fix_schedule_fairness.py
"""
import datetime
from collections import defaultdict

from app import create_app
from app.extensions import db
from app.models import Event, Assignment

app = create_app()


def _is_pristine(event):
    """True if this event was auto-generated and has not been touched."""
    for a in event.assignments:
        if a.status not in ("pending", None, ""):
            return False        # confirmed/declined/swapped — keep it
        if a.cover:
            return False        # someone already covered it — keep it
        if a.telegram_message_id:
            return False        # reminder was sent — keep it
    return True


def reset_future_schedule(full_wipe=False):
    today = datetime.date.today()
    print(f"Today is {today}. Wiping future events …")

    events = (
        Event.query
        .filter(Event.date > today)
        .order_by(Event.date)
        .all()
    )

    wiped = 0
    kept = 0
    for e in events:
        if full_wipe or _is_pristine(e):
            db.session.delete(e)   # cascades to Assignments
            wiped += 1
        else:
            kept += 1

    db.session.commit()
    print(f"  Wiped {wiped} events, kept {kept} (touched/confirmed/sent).")


def regenerate(years=10):
    from app.scheduler_v2 import generate_month_v2
    import calendar as _cal

    today = datetime.date.today()
    end = today + datetime.timedelta(days=365 * years)
    cursor_year, cursor_month = today.year, today.month
    end_year, end_month = end.year, end.month
    total = 0

    print("Regenerating schedule month by month ...")
    while (cursor_year, cursor_month) <= (end_year, end_month):
        try:
            n = generate_month_v2(cursor_year, cursor_month)
            total += n
            if n:
                print(f"  {cursor_year}-{cursor_month:02d}: +{n} events")
        except Exception as e:
            print(f"  {cursor_year}-{cursor_month:02d} FAILED: {e}")
            db.session.rollback()
        cursor_month += 1
        if cursor_month > 12:
            cursor_month = 1
            cursor_year += 1

    print(f"Done. Created {total} events.")


def print_1year_table(years=10):
    """Print a per-role assignment table for the generated schedule window."""
    from app.scheduler_v2 import get_roster

    today = datetime.date.today()
    end = today + datetime.timedelta(days=365 * years)

    events = (
        Event.query
        .filter(Event.date >= today, Event.date <= end)
        .order_by(Event.date)
        .all()
    )

    ROLES = ["Computer", "Camera 1", "Camera 2", "Camera"]
    counts = defaultdict(lambda: defaultdict(int))

    for e in events:
        for a in e.assignments:
            person = a.cover or a.person
            if person and person not in ("TBD", "Select Helper"):
                key = f"{e.day_type}:{a.role}" if a.role == "Computer" else a.role
                counts[person][key] += 1

    # Totals columns
    names = sorted(get_roster().keys())

    header = f"{'Name':<10}  {'Sun PC':>6}  {'Cam1':>5}  {'Cam2':>5}  {'Sun':>4}  {'Fri PC':>6}  {'Fri Cam':>7}  {'TOTAL':>6}"
    sep = "-" * len(header)
    print("\n" + sep)
    print(header)
    print(sep)

    for name in names:
        sunday_pc = counts[name]["Sunday:Computer"]
        c1   = counts[name]["Camera 1"]
        c2   = counts[name]["Camera 2"]
        friday_pc = counts[name]["Friday:Computer"]
        friday_cam = counts[name]["Camera"]
        sun  = sunday_pc + c1 + c2
        total = sun + friday_pc + friday_cam
        print(f"{name:<10}  {sunday_pc:>6}  {c1:>5}  {c2:>5}  {sun:>4}  {friday_pc:>6}  {friday_cam:>7}  {total:>6}")

    print(sep)
    print(f"(Events from {today} to {end})\n")


if __name__ == "__main__":
    with app.app_context():
        reset_future_schedule(full_wipe=True)
        regenerate(years=10)
        print_1year_table(years=10)
