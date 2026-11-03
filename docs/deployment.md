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
