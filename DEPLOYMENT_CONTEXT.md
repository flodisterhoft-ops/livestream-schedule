# Deployment Context

Use this file as the first stop when a request mentions the live livestream scheduler site.

## Live Server — Oracle Cloud

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

- **DNS provider:** Cloudflare (SSL mode: Flexible — Cloudflare terminates HTTPS, talks HTTP to Oracle)
- **No SSL certificates on Oracle itself** — Cloudflare handles all TLS

## Nginx

- **Config file:** `/etc/nginx/sites-available/church-apps`
- Routes by `server_name` to the correct localhost port
- Forwards headers: `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`
- Default fallback server → port 5000 (livestream)

## Systemd Service — Livestream

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

## Systemd Service — Young Couples (sister app)

| Item | Value |
|------|-------|
| **Service name** | `young-couples-scheduler.service` |
| **Working directory** | `/home/ubuntu/young-couples-scheduler` |
| **Gunicorn bind** | `127.0.0.1:5001` |

## Environment Variables (set via systemd override or service file)

| Variable | Value / Source |
|----------|---------------|
| `TELEGRAM_BOT_TOKEN` | Livestream bot token (see below) |
| `TELEGRAM_CHAT_ID` | Livestream group chat ID |
| `TELEGRAM_PERSONAL_CHAT_ID` | `27859948` (Flo) |
| `TELEGRAM_WEBHOOK_SECRET` | Random secret for webhook verification |
| `BASE_URL` | `https://livestream.disterhoft.com` |
| `PYTHONUNBUFFERED` | `1` |

## Telegram Bot

| Field | Value |
|-------|-------|
| **Bot Token** | `8194048646:AAFGx1WrZYSlBs0Ef2WY0aENoq40VHWlpwU` |
| **Personal Chat ID** | `27859948` |
| **Webhook URL** | `https://livestream.disterhoft.com/api/v2/telegram/webhook` |

## Database

- **Current:** SQLite (`schedule.db` in working directory)
- **Supabase migration script exists** (`migrate_to_supabase.py`) but is **not active**
- The `config.py` `DATABASE_URL` env var can point to PostgreSQL if needed

## Deployment Workflow

1. Push changes to `origin/main` on GitHub (`flodisterhoft-ops/livestream-schedule`)
2. SSH into Oracle: `ssh -i ~/.ssh/oracle_vm ubuntu@192.18.138.167`
3. Pull changes:
   ```bash
   cd /home/ubuntu/livestream-schedule
   git pull origin main
   ```
4. Install any new deps: `venv/bin/pip install -r requirements.txt`
5. Restart: `sudo systemctl restart livestream.service`

The Young Couples Scheduler has a PowerShell deploy script (`oracle_deploy.ps1`) — a similar one could be created for this repo if desired.

## Legacy (NOT live)

- `render.yaml` — old Render config, kept for history only
- Any `*.onrender.com` URLs — dead
- Render keep-alive cron — not needed
- **Do NOT treat Render as the deployment target**

## Cross-References

- Young Couples Scheduler repo: `C:\Users\Disterhoft\OneDrive\Documents\AI Projects\Young Couples Scheduler\`
- YCS deployment docs: `CLAUDE_HANDOFF_2026-04-05.md`, `ORACLE_DEPLOY.md`, `ORACLE_MIGRATION.md`
- Shared Nginx config example: `oracle_nginx_church-apps.conf.example` (in YCS repo)

## Maintenance

If the Oracle host, domains, ports, or arrangement change, update:
1. This file
2. The corresponding YCS handoff doc
3. The Copilot memory notes (so future chats don't drift back to stale info)
