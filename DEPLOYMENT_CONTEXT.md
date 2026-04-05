# Deployment Context

Use this file as the first stop when a request mentions the live livestream scheduler site.

## Current Hosting Assumption

- The livestream scheduler is documented as running on Oracle infrastructure.
- It is bundled with the Young Couples Scheduler deployment.
- Old Render deployment files in this repo are legacy and should not be treated as the current live environment by default.

## Documented Oracle Stack Notes

These details come from the bundled Young Couples Scheduler docs:

- Oracle host IP noted there: `192.18.138.167`
- Public domains noted there:
  - `https://livestream.disterhoft.com`
  - `https://cleaning.disterhoft.com`
- Cloudflare is in front of the Oracle server.
- Nginx is already set up on Oracle.

## What To Treat As Legacy

- `render.yaml`
- old `*.onrender.com` URLs
- Render keep-alive cron notes
- comments that assume Render is the current reverse proxy or deployment target

Those files can still be useful as history, but they are not the default answer to "where is this live?" anymore.

## When Making Changes

- For live links, pickup URLs, webhooks, and external callbacks, prefer the Oracle/public-domain setup.
- Check `BASE_URL` handling in `config.py` and `app/routes.py`.
- If deployment behavior is unclear, cross-check the Young Couples Scheduler docs before assuming Render.

## Cross-References

- `C:\Users\Disterhoft\OneDrive\Documents\AI Projects\Young Couples Scheduler\README.md`
- `C:\Users\Disterhoft\OneDrive\Documents\AI Projects\Young Couples Scheduler\CLAUDE_HANDOFF_2026-03-22.md`

## Maintenance Note

If the Oracle host, domains, or bundle arrangement change again, update this file and the Young Couples deployment note together so future chats do not drift back to stale hosting assumptions.
