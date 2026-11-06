# Production deployment

Verified on 2026-11-02:

- Site: https://domovoice.ai
- SSH: `ssh idurnev@212.24.99.132`
- Project: `/var/www/itd/call_me_ai`
- Git remote: `itd`, branch `master`
- Supervisor: `call_me_ai`, `call_me_ai_telegram_polling`, `call_me_ai_max_polling`
- Web listener: `127.0.0.1:10040`
- Supervisor configuration: `/etc/supervisor/conf.d/call_me_ai.conf`

The former deployment at `45.86.183.119:/home/idurnev/apps/call_me_ai`
is no longer active. Do not deploy there.

Before updating, require a clean production working tree, record `git rev-parse HEAD`,
and back up the production PostgreSQL database with `pg_dump -Fc`. Keep backups
on the server with access restricted to the owner. Never copy local `.env` or
local databases to production.

```sh
cd /var/www/itd/call_me_ai
git status --short
git pull --ff-only itd master
APP_ENABLE_RECURRING_WORKER=false venv/bin/python -m scripts.prepare_mobile_schema
APP_ENABLE_RECURRING_WORKER=false venv/bin/python -m unittest discover -s tests
sudo supervisorctl signal HUP call_me_ai
sudo supervisorctl status call_me_ai call_me_ai_telegram_polling call_me_ai_max_polling
curl --fail https://domovoice.ai/health
```

The schema preparation command only creates the missing mobile auth/chat tables.
It is idempotent, does not change the existing Alembic revision and does not drop
or rewrite existing data. If an existing mobile table has an incompatible schema,
it stops instead of altering it. Older production migrations are maintained
outside Git; do not run local Alembic migrations on production without reconciling
the revision history.

After restarting, verify the home page, login redirects, protected APIs and
updated static assets. If startup or smoke checks fail, restore the previous Git
revision and restart the same processes. The additive mobile tables can remain
when rolling back to the previous code.

For routine web code/template updates, keep the Gunicorn listener open and
gracefully replace its workers with `sudo supervisorctl signal HUP call_me_ai`
instead of stopping and starting the service. Check `/health` before and after,
and confirm a new worker booted in the Gunicorn log. A supervisor restart is
still required for changes to the master's environment or startup command.

CloudPayments notification URLs currently configured for the merchant:

- `https://domovoice.ai/payments/cloudpayments/pay`
- `https://domovoice.ai/payments/cloudpayments/fail`
- `https://domovoice.ai/payments/cloudpayments/recurrent`

All use POST and the same HMAC validation as the legacy
`/api/cloudpayments/webhooks/{pay,fail,recurrent}` endpoints. Both URL forms
are supported. Do not change merchant settings during routine deployment.

ElevenLabs call queueing is enabled for the production heroes with a 180-second
wait timeout. Agent creation and synchronization also enforce this setting.
Burst pricing remains controlled by the existing ElevenLabs settings. Browser
clients handle waiting, admission, cancellation and queue timeout; hold audio
does not start the conversation timer. Queue-aware calls exclude waiting from
their stored duration and minute consumption. Provider-reported conversation
duration takes precedence; server-recorded admission time is the fallback.

Run the browser call-state regression checks locally with:
`node --test tests/js/test_call_queue.cjs`.

Web and native text chat share `chat_message`. Before deploying the chat sync
release, create the additive idempotency/locking table with
`APP_ENABLE_RECURRING_WORKER=false venv/bin/python -m scripts.prepare_chat_sync_schema`.
This adds `chat_operation` with a unique request key per account and a partial
unique index permitting one pending operation per account/character. Existing
messages and Alembic history are preserved. Back up the database before running it.

The web client polls account-scoped history every four seconds while visible and
refreshes when returning online or to the tab. New messages are saved server-side;
the per-account browser outbox only retains unacknowledged requests for safe retry.
Legacy unscoped localStorage histories require the user's explicit import action
on each device. Matching fragments are merged without overwriting existing rows;
the original localStorage remains as a backup. Legacy timestamps were never stored,
so imported fragments are positioned relative to matching messages where possible.
Import does not activate or recreate deleted characters.

Corporate pages require the additive schema preparation command before reloading
workers: `APP_ENABLE_RECURRING_WORKER=false venv/bin/python -m scripts.prepare_corporate_schema`.
Back up the database first. This creates corporate pages, memberships and code
tables, adds the nullable company reference to calls, and adds the email-code
failed-attempt counter. It preserves existing records and Alembic history.
The `static/uploads/corporate` directory must persist across deployments.

## Games subdomains (2026-11-06)

`https://games.domovoice.ai/` and `https://www.games.domovoice.ai/` serve the
existing games catalogue directly. Nginx internally proxies the exact root `/`
to the Flask path `/games`; it does not redirect the browser to the main domain
or add `/games` to the address. Existing `/games/*`, `/api/games/*`, `/static/*`
and `/ws/games/*` paths retain their normal routes on both subdomains.
HTTP upgrades to HTTPS on the same hostname. The original main domain remains
available.

The deployed configuration is `/etc/nginx/sites-available/games_domovoice.conf`,
linked from `sites-enabled`; its source is `deploy/nginx/games_domovoice.conf`.
Both DNS A records point to `212.24.99.132`. The separate Let's Encrypt lineage
`games.domovoice.ai` covers both names and uses the webroot
`/var/www/letsencrypt-games`. The existing `certbot.timer` renews certificates;
`/etc/letsencrypt/renewal-hooks/deploy/reload-games-nginx` validates and reloads
Nginx after this lineage renews. Its source is in `deploy/letsencrypt/`.

Production `.env` now sets `GAMES_PUBLIC_URL=https://games.domovoice.ai` so room
invitations and QR codes consistently contain the HTTPS games address. This
setting is independent of the existing main-site `PUBLIC_BASE_URL`. A graceful
Gunicorn HUP loads the setting into new workers. Nginx and environment backups
were saved under `/home/idurnev/backups/call_me_ai/` before applying changes.

To recheck the proxy: run `sudo nginx -t`, request each HTTPS root without
following redirects (expect 200 and no Location), create a test room, check its
`join_url` and QR endpoint, then authenticate a host and phone at
`wss://<games-host>/ws/games/tag/<code>`. The backend service and games database
are shared with the original main domain. To roll back the new entrypoints,
disable this site and restore the previous `GAMES_PUBLIC_URL` setting from its
backup, then validate/reload Nginx and HUP Gunicorn.

Verification on 2026-11-06: Nginx validation and reload succeeded; a Certbot
renewal dry run succeeded and the deploy hook was exercised separately. External
checks using normal DNS and certificate verification passed for both roots
(HTTP 200, zero redirects), static assets, room creation, the HTTPS join URL,
QR endpoint, and authenticated host/phone WebSockets. Both root pages were also
opened in the browser with their original subdomain preserved. The main domain's
health and games page continued to respond normally.
