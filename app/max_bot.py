from __future__ import annotations

import fcntl
import json
import logging
import sys
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from flask import Blueprint, Response, current_app, request

try:
    from maxbotlib import Bot, MaxAPIError, MaxClient, UpdateTypes, models
except ImportError:  # pragma: no cover
    maxbotlib_src = Path(__file__).resolve().parents[2] / "maxbotlib" / "src"
    if maxbotlib_src.exists():
        sys.path.insert(0, str(maxbotlib_src))
        from maxbotlib import Bot, MaxAPIError, MaxClient, UpdateTypes, models
    else:  # pragma: no cover
        raise

from .account_linking import link_max_account
from .messenger_links import build_heroes_url, build_voices_url


max_bp = Blueprint("max", __name__)

_max_lock = threading.Lock()
_polling_started = False
_polling_lock_handle = None


class MaxHeaderClient(MaxClient):
    def _request(
        self,
        *,
        method: str,
        path: str,
        path_params: dict[str, Any],
        query_params: dict[str, Any],
        body: Any | None,
    ) -> Any:
        for key, value in path_params.items():
            path = path.replace("{" + key + "}", str(value))

        encoded_query = self._encode_query(dict(query_params))
        url = f"{self.base_url}{path}"
        if encoded_query:
            url = f"{url}?{encoded_query}"

        payload: bytes | None = None
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "Authorization": self.access_token,
        }

        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = Request(url=url, data=payload, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                ct = resp.headers.get("Content-Type", "")
                if "application/json" in ct:
                    return json.loads(raw)
                return raw
        except Exception as exc:  # pragma: no cover
            status = getattr(exc, "code", 0)
            payload_data: Any | None = None
            message = str(exc)

            if hasattr(exc, "read"):
                try:
                    raw_error = exc.read().decode("utf-8")
                    payload_data = json.loads(raw_error)
                    message = payload_data.get("message", message)
                except Exception:
                    pass

            raise MaxAPIError(status, message, payload_data) from exc


def _build_start_message(flask_app) -> dict:
    return {
        "text": (
            "✨ Добро пожаловать в «Звонок другу»!\n"
            "Привет! Здесь ваш малыш может позвонить любимым сказочным персонажам и поговорить с ними в реальном времени. "
            "Это пространство, где магия становится осязаемой, а герои книг и мультфильмов всегда готовы выслушать.\n\n"
            "Для чего можно позвонить герою?\n\n"
            "🌙 Послушать сказку на ночь. Уютная история от доброго персонажа поможет настроиться на спокойный сон.\n\n"
            "🎈 Поделиться радостью. Рассказать о первой пятерке, победе в игре или просто хорошем дне.\n\n"
            "🧸 Найти поддержку. Если малышу грустно или одиноко, надежный друг всегда выслушает и подберет нужные слова.\n\n"
            "🌟 Задать важный вопрос. Узнать, как приручить дракона или о чем мечтают звезды."
        ),
        "attachments": [],
        "link": None,
    }


def _build_linked_account_message() -> dict:
    return {
        "text": "Ваш аккаунт подключен к Max.\nЧтобы позвонить, нажмите кнопку Старт слева внизу.",
        "attachments": [],
        "link": None,
    }


def _build_heroes_message(flask_app) -> dict:
    return {
        "text": "Откройте mini app и настройте каждого героя: имя, описание, база знаний, аватар, голос и параметры Realtime API.",
        "attachments": [
            models.inline_keyboard(
                models.keyboard_row(
                    models.link_button("Открыть Heroes", build_heroes_url(flask_app, platform="max"))
                )
            )
        ],
        "link": None,
    }


def _build_voices_message(flask_app) -> dict:
    return {
        "text": "Откройте миниапп, чтобы создавать consent и custom voice по папкам из static/voices.",
        "attachments": [
            models.inline_keyboard(
                models.keyboard_row(
                    models.link_button("Управление голосами", build_voices_url(flask_app, platform="max"))
                )
            )
        ],
        "link": None,
    }


def _send_to_update_recipient(update, api: MaxClient, body: dict) -> None:
    message = update.get("message") if hasattr(update, "get") else None
    recipient = message.get("recipient") if isinstance(message, dict) else None
    user_id = recipient.get("user_id") if isinstance(recipient, dict) else None
    chat_id = recipient.get("chat_id") if isinstance(recipient, dict) else None
    reply_mid = None
    if isinstance(message, dict):
        message_body = message.get("body")
        if isinstance(message_body, dict):
            reply_mid = message_body.get("mid")

    payload = dict(body)
    if reply_mid:
        payload["link"] = {"type": "reply", "mid": reply_mid}
    api.send_message(user_id=user_id, chat_id=chat_id, body=payload)


def _send_to_chat(chat_id: int, user_id: int | None, api: MaxClient, body: dict) -> None:
    api.send_message(user_id=user_id, chat_id=chat_id, body=dict(body))


def _extract_start_payload(update) -> str | None:
    if isinstance(update, dict):
        text = str((((update.get("message") or {}).get("body") or {}).get("text") or "")).strip()
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            return parts[1].strip() if len(parts) > 1 else None
        return update.get("payload")

    text = str(getattr(getattr(getattr(update, "message", None), "body", None), "text", "") or "").strip()
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else None
    return getattr(update, "payload", None) or getattr(update, "start_payload", None)


def _acquire_polling_file_lock() -> bool:
    global _polling_lock_handle
    if _polling_lock_handle is not None:
        return True

    lock_path = Path("/tmp/call_me_ai_max_polling.lock")
    handle = lock_path.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return False

    handle.write(str(Path(__file__).resolve()))
    handle.flush()
    _polling_lock_handle = handle
    return True


def create_max_bot(flask_app) -> Bot:
    bot = Bot(MaxHeaderClient(access_token=flask_app.config["MAX_BOT_TOKEN"]))

    @bot.command("start")
    def command_start(update, api: MaxClient) -> None:
        with flask_app.app_context():
            linked_user = link_max_account(
                payload=_extract_start_payload(update),
                max_user_id=getattr(getattr(update, "user", None), "user_id", None),
            )
        if linked_user:
            _send_to_update_recipient(update, api, _build_linked_account_message())
            return
        _send_to_update_recipient(update, api, _build_start_message(flask_app))

    @bot.command("heroes")
    def command_heroes(update, api: MaxClient) -> None:
        _send_to_update_recipient(update, api, _build_heroes_message(flask_app))

    @bot.command("voices")
    def command_voices(update, api: MaxClient) -> None:
        _send_to_update_recipient(update, api, _build_voices_message(flask_app))

    @bot.on_update(UpdateTypes.BOT_STARTED)
    def on_bot_started(update, api: MaxClient) -> None:
        with flask_app.app_context():
            linked_user = link_max_account(
                payload=getattr(update, "payload", None),
                max_user_id=getattr(update.user, "user_id", None),
            )
        if linked_user:
            _send_to_chat(update.chat_id, getattr(update.user, "user_id", None), api, _build_linked_account_message())
            return
        _send_to_chat(update.chat_id, getattr(update.user, "user_id", None), api, _build_start_message(flask_app))

    return bot


@max_bp.post("/webhook")
def max_webhook() -> Response:
    token = current_app.config["MAX_BOT_TOKEN"]
    if not token:
        return Response(status=HTTPStatus.NO_CONTENT)

    secret = current_app.config.get("MAX_WEBHOOK_SECRET")
    if secret and request.headers.get("X-Max-Bot-Api-Secret") != secret:
        return Response(status=HTTPStatus.FORBIDDEN)

    bot = create_max_bot(current_app._get_current_object())
    bot.handle_webhook(request.get_json(silent=True) or {})
    return Response(status=HTTPStatus.OK)


def run_polling_bot(flask_app) -> None:
    if not flask_app.config["MAX_BOT_TOKEN"]:
        return
    if not _acquire_polling_file_lock():
        logging.info("Max polling skipped: another polling process is already running")
        return
    bot = create_max_bot(flask_app)
    try:
        bot_info = bot.client.get_my_info()
        bot_title = bot_info.get("name") or bot_info.get("first_name") or bot_info.get("username") or "unknown"
        logging.info("Max polling auth ok for bot: %s", bot_title)
    except Exception as exc:
        logging.exception("Max polling auth check failed: %s", exc)
        return

    logging.info("Max polling started")

    def handle_polling_error(exc: Exception) -> None:
        if isinstance(exc, MaxAPIError) and exc.status_code == 429:
            logging.warning("Max polling rate limited, backing off for 2 seconds")
            time.sleep(2.0)
            return
        if isinstance(exc, MaxAPIError) and exc.status_code == 401 and "No access token" in str(exc):
            logging.warning("Max polling received 401 without token details, backing off for 5 seconds")
            time.sleep(5.0)
            return
        raise exc

    bot.run_polling(
        timeout=30,
        types=[UpdateTypes.MESSAGE_CREATED, UpdateTypes.BOT_STARTED],
        pause_on_empty=1.0,
        on_error=handle_polling_error,
    )


def start_polling_bot_once(flask_app) -> None:
    global _polling_started
    if _polling_started or flask_app.config["MAX_BOT_MODE"] != "polling" or not flask_app.config["MAX_BOT_TOKEN"]:
        return

    with _max_lock:
        if _polling_started:
            return

        def polling_worker():
            try:
                run_polling_bot(flask_app)
            except Exception as exc:  # pragma: no cover
                logging.exception("Max polling crashed: %s", exc)

        thread = threading.Thread(target=polling_worker, name="max-polling", daemon=True)
        thread.start()
        _polling_started = True
