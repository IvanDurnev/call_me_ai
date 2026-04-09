# Call Me AI

Бот-приложение для Telegram и Max, которое по `/start` показывает персонажей и открывает mini app со звонком. Отдельная команда `/heroes` открывает mini app для настройки героев: имени, описания, базы знаний, аватара, голоса и параметров OpenAI Realtime API.

## Что внутри

- `python-telegram-bot` 22.x для Telegram WebApp-кнопок
- `maxbotlib` для Max-бота и кнопок-ссылок
- `Flask` + `Flask-Sock` для HTTP и websocket
- `Flask-SQLAlchemy` + `Flask-Migrate` для хранения звонков
- `Postgres` через `DATABASE_URL`
- фронтенд миниаппа в `templates/miniapp.html` и `static/call.js`

## Быстрый старт

1. Создайте `.env` из `.env.example`.
2. Установите зависимости:

```bash
pip install -r requirements.txt
```

3. Инициализируйте базу:

```bash
flask --app run.py db init
flask --app run.py db migrate -m "init"
flask --app run.py db upgrade
```

4. Запустите Flask:

```bash
python run.py
```

5. Для локальной разработки Telegram-бота в polling-режиме:

```bash
TG_BOT_MODE=polling python bot_polling.py
```

6. Для локальной разработки Max-бота в polling-режиме:

```bash
MAX_BOT_MODE=polling python max_bot_polling.py

Важно: polling-боты запускаются только через отдельные скрипты `bot_polling.py` и `max_bot_polling.py`.
Команды `flask ...` и запуск `run.py` больше не поднимают polling автоматически.
```

7. Для Telegram webhook-режима укажите в BotFather webhook на:

```text
https://your-domain.tld/telegram/webhook
```

Если используете secret token, тот же токен положите в `TG_WEBHOOK_SECRET`.

8. Для Max webhook-режима укажите webhook на:

```text
https://your-domain.tld/max/webhook
```

Если используете secret, положите его в `MAX_WEBHOOK_SECRET`.

## Что настроить под себя

- после обновления схемы настройка героев доступна в `/heroes`
- публичный домен для WebApp и websocket задаётся через `PUBLIC_BASE_URL`
- ссылка для открытия mini app из Max задаётся через `MAX_BOT_APP_LINK`
- `MAX_BOT_ID` сохранён в конфиге, но в текущей реализации не обязателен: для работы Max сейчас достаточно `MAX_BOT_TOKEN`
- модель Realtime API задаётся через `OPENAI_REALTIME_MODEL`

## Gunicorn

Пример запуска:

```bash
gunicorn --bind 0.0.0.0:8000 run:app
```
