# Livestream Scheduler

React/Vite + Flask scheduler for the church livestream team.

The React frontend in `scheduler-site/` is the only website UI. Flask serves it at the main production URL:

- `https://livestream.disterhoft.com/`

`/v2` is kept only as a backwards-compatible alias for older Telegram/browser links. Do not treat it as a separate site.

## Start Here

- `scheduler-site/src/`: active frontend UI
- `app/api_v2.py`: JSON API consumed by the frontend (`/api/v2/*`)
- `app/telegram_v2.py`: Telegram bot callbacks, reminders, temp-chat coverage workflow, and suggestion alerts
- `app/scheduler_v2.py`: active schedule generation/fairness logic
- `app/routes.py`: small non-UI compatibility blueprint for calendar feeds and optional cron webhook only
- `app/__init__.py`: app factory, React serving, DB startup migration, data hotfixes, APScheduler jobs
- `config.py`: runtime config, database URL, Telegram env vars, `BASE_URL`
- `DEPLOYMENT_CONTEXT.md`: Oracle deployment notes

## Removed Legacy UI

The old Flask/Jinja website has been removed. There are no active Jinja templates, old static assets, v1 Telegram module, v1 scheduler, or PythonAnywhere task script.

## Local Development

Backend:

```powershell
python run.py
```

Frontend:

```powershell
cd scheduler-site
npm install
npm run dev
```

Build frontend assets for Flask to serve:

```powershell
cd scheduler-site
npm run build
```

## Deployment

Live deployment is the Oracle VM documented in `DEPLOYMENT_CONTEXT.md`. Push to `origin/main`, update the server repo, then restart `livestream.service`.