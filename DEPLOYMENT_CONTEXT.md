# Deployment Context

Use this file as the first stop when a request mentions the live livestream scheduler site.

## Live Server - Oracle Cloud

| Item | Value |
|------|-------|
| **Host IP** | `192.18.138.167` |
| **SSH User** | `ubuntu` |
| **SSH Key** | `~/.ssh/oracle_vm` |
| **Shape** | VM.Standard.E2.1.Micro (1 OCPU, 1 GB RAM) |
| **OS** | Ubuntu |

Both the Livestream Scheduler and the Young Couples Scheduler run on this single Oracle instance, separated by port.

## Domains & DNS

| Domain | Backend Port | App |
|--------|-------------|-----|
| `https://livestream.disterhoft.com` | `127.0.0.1:5000` | Livestream Scheduler |
| `https://cleaning.disterhoft.com` | `127.0.0.1:5001` | Young Couples Scheduler |

- **DNS provider:** Cloudflare (SSL mode: Flexible - Cloudflare terminates HTTPS, talks HTTP to Oracle)
- **No SSL certificates on Oracle itself** - Cloudflare handles all TLS

## Nginx

- **Config file:** `/etc/nginx/sites-available/church-apps`
- Routes by `server_name` to the correct localhost port
- Forwards headers: `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`
- Default fallback server -> port 5000 (livestream)

## Systemd Service - Livestream

| Item | Value |
|------|-------|
| **Service name** | `livestream.service` |
| **Working directory** | `/home/ubuntu/livestream-schedule` |
| **Gunicorn bind** | `127.0.0.1:5000` |
| **Workers / Threads** | 1 worker, 4 threads |

Manage with:

```bash
sudo systemctl restart livestream.service
sudo systemctl status livestream.service
sudo journalctl -u livestream.service -f
```

## Environment Variables

Set secrets via systemd override or service file. Do not commit secret values.

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Optional DB URL; defaults to local SQLite `schedule.db` |
| `SCHEDULE_SECRET_KEY` | Flask/session/signing secret |
| `TELEGRAM_BOT_TOKEN` | Livestream bot token |
| `TELEGRAM_CHAT_ID` | Livestream group chat ID |
| `TELEGRAM_PERSONAL_CHAT_ID` | Florian/admin DM chat ID |
| `TELEGRAM_WEBHOOK_SECRET` | Telegram webhook verification secret |
| `TELEGRAM_LOGIN_URL_ENABLED` | Enables Telegram `login_url` schedule buttons |
| `BASE_URL` | `https://livestream.disterhoft.com` |
| `PYTHONUNBUFFERED` | `1` |

## Telegram Bot

- Webhook URL: `https://livestream.disterhoft.com/api/v2/telegram/webhook`
- Do not store bot tokens in this repository.

## Database

- Current: SQLite (`schedule.db` in working directory)
- `config.py` can use `DATABASE_URL` for PostgreSQL if needed

## Deployment Workflow

1. Push changes to `origin/main` on GitHub (`flodisterhoft-ops/livestream-schedule`)
2. SSH into Oracle: `ssh -i ~/.ssh/oracle_vm ubuntu@192.18.138.167`
3. Pull/reset changes in `/home/ubuntu/livestream-schedule`
4. Install any new deps: `venv/bin/pip install -r requirements.txt`
5. Restart: `sudo systemctl restart livestream.service`

## Current Architecture

- Main website: React/Vite frontend in `scheduler-site/`, served at `/`
- Backwards-compatible frontend alias: `/v2`
- API: `/api/v2/*`
- Telegram integration: `app/telegram_v2.py`
- Scheduler/fairness: `app/scheduler_v2.py`
- Non-UI compatibility routes: `app/routes.py` (`/calendar.ics`, `/calendar/<person>.ics`, `/cron/daily-reminder`)

## Cross-References

- Young Couples Scheduler repo: `C:\Users\Disterhoft\OneDrive\Documents\AI Projects\Young Couples Scheduler\`
- Shared Oracle/Nginx context lives in the Young Couples repo deployment docs too.

## Maintenance

If the Oracle host, domains, ports, or arrangement change, update this file and related deployment docs.