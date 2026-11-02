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
sudo supervisorctl restart call_me_ai call_me_ai_telegram_polling call_me_ai_max_polling
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

CloudPayments notification URLs currently configured for the merchant:

- `https://domovoice.ai/payments/cloudpayments/pay`
- `https://domovoice.ai/payments/cloudpayments/fail`
- `https://domovoice.ai/payments/cloudpayments/recurrent`

All use POST and the same HMAC validation as the legacy
`/api/cloudpayments/webhooks/{pay,fail,recurrent}` endpoints. Both URL forms
are supported. Do not change merchant settings during routine deployment.
