# Livestream Scheduler

Church media team scheduler for Friday/Sunday services, availability, swaps, pickup links, and Telegram reminders.

## Live Deployment Note

This project's current live hosting is documented as Oracle infrastructure, not Render.

It is bundled with the Young Couples Scheduler deployment, so when you are asked to make live-site or deployment changes, start from the Oracle-hosted stack and its public domains instead of the old `onrender.com` setup.

Treat old Render files, `onrender.com` URLs, and Render-specific notes in this repo as legacy references unless you are explicitly reviving that deployment path.

## Start Here

- `DEPLOYMENT_CONTEXT.md` contains the current hosting context and cross-references.
- `app/routes.py` contains the public URL helper used for pickup links and other external links.
- `config.py` contains the runtime config, including `BASE_URL`.

## Cross-Project Context

The bundled deployment notes live in the Young Couples project too:

- `C:\Users\Disterhoft\OneDrive\Documents\AI Projects\Young Couples Scheduler\README.md`
- `C:\Users\Disterhoft\OneDrive\Documents\AI Projects\Young Couples Scheduler\CLAUDE_HANDOFF_2026-03-22.md`

If there is ever a mismatch between old Render-era notes and the Oracle bundle notes, prefer the Oracle bundle notes.

## Project Layout

- `app/`: current Flask app package
- `config.py`: config and environment variable handling
- `run.py`: app entrypoint
- `schedule.db`: local SQLite snapshot for local work
- `render.yaml`: legacy Render manifest kept for historical reference
- `flask_app.py`: older single-file version kept in the repo for history/reference
