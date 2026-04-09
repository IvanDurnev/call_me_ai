from __future__ import annotations

import asyncio
import fcntl
import logging
import threading
from http import HTTPStatus
from pathlib import Path

from flask import Blueprint, Response, current_app, request
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

from .account_linking import link_telegram_account
from .messenger_links import build_heroes_url, build_picker_url, build_voices_url


telegram_bp = Blueprint("telegram", __name__)

_telegram_lock = threading.Lock()
_polling_started = False
_polling_lock_handle = None


def build_start_keyboard(flask_app) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="Открыть миниапп",
                    web_app=WebAppInfo(url=build_picker_url(flask_app, platform="telegram")),
                )
            ]
        ]
    )


def build_voices_keyboard(flask_app) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="Управление голосами",
                    web_app=WebAppInfo(url=build_voices_url(flask_app, platform="telegram")),
                )
            ]
        ]
    )


def build_heroes_keyboard(flask_app) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="Открыть Heroes",
                    web_app=WebAppInfo(url=build_heroes_url(flask_app, platform="telegram")),
                )
            ]
        ]
    )


def _acquire_polling_file_lock() -> bool:
    global _polling_lock_handle
    if _polling_lock_handle is not None:
        return True

    lock_path = Path("/tmp/call_me_ai_telegram_polling.lock")
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


async def command_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flask_app = context.application.bot_data["flask_app"]
    with flask_app.app_context():
        linked_user = link_telegram_account(
            payload=context.args[0] if context.args else None,
            telegram_user_id=update.effective_user.id if update.effective_user else None,
            telegram_username=update.effective_user.username if update.effective_user else None,
        )
    if linked_user:
        await update.effective_message.reply_text(
            "Ваш аккаунт подключен к Telegram.\n\nТеперь можно звонить. Открывайте приложение.",
            reply_markup=build_start_keyboard(flask_app),
        )
        return

    await update.effective_message.reply_text(
        "✨ Добро пожаловать в «Звонок другу»!\n"
        "Привет! Здесь ваш малыш может позвонить любимым сказочным персонажам и поговорить с ними в реальном времени. "
        "Это пространство, где магия становится осязаемой, а герои книг и мультфильмов всегда готовы выслушать.\n\n"
        "Для чего можно позвонить герою?\n\n"
        "🌙 Послушать сказку на ночь. Уютная история от доброго персонажа поможет настроиться на спокойный сон.\n\n"
        "🎈 Поделиться радостью. Рассказать о первой пятерке, победе в игре или просто хорошем дне.\n\n"
        "🧸 Найти поддержку. Если малышу грустно или одиноко, надежный друг всегда выслушает и подберет нужные слова.\n\n"
        "🌟 Задать важный вопрос. Узнать, как приручить дракона или о чем мечтают звезды.",
        reply_markup=build_start_keyboard(flask_app),
    )


async def command_voices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flask_app = context.application.bot_data["flask_app"]
    await update.effective_message.reply_text(
        "Откройте миниапп, чтобы создавать consent и custom voice по папкам из static/voices.",
        reply_markup=build_voices_keyboard(flask_app),
    )


async def command_heroes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flask_app = context.application.bot_data["flask_app"]
    await update.effective_message.reply_text(
        "Откройте mini app и настройте каждого героя: имя, описание, база знаний, аватар, голос и параметры Realtime API.",
        reply_markup=build_heroes_keyboard(flask_app),
    )


def create_telegram_application(flask_app) -> Application:
    application = Application.builder().token(flask_app.config["TG_BOT_TOKEN"]).build()
    application.bot_data["flask_app"] = flask_app
    application.add_handler(CommandHandler("start", command_start))
    application.add_handler(CommandHandler("heroes", command_heroes))
    application.add_handler(CommandHandler("voices", command_voices))
    return application


async def configure_telegram_bot(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Выбрать персонажа"),
            BotCommand("heroes", "Настроить героев"),
            BotCommand("voices", "Управление голосами"),
        ]
    )


async def process_webhook_update(flask_app, update_payload: dict) -> None:
    application = create_telegram_application(flask_app)
    try:
        await application.initialize()
        await configure_telegram_bot(application)
        update = Update.de_json(update_payload, application.bot)
        await application.process_update(update)
    finally:
        await application.shutdown()


@telegram_bp.post("/webhook")
def telegram_webhook() -> Response:
    secret = current_app.config["TG_WEBHOOK_SECRET"]
    if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        return Response(status=HTTPStatus.FORBIDDEN)

    token = current_app.config["TG_BOT_TOKEN"]
    if not token:
        return Response(status=HTTPStatus.NO_CONTENT)

    asyncio.run(process_webhook_update(current_app._get_current_object(), request.json))
    return Response(status=HTTPStatus.OK)


def run_polling_bot(flask_app) -> None:
    if not _acquire_polling_file_lock():
        logging.info("Telegram polling skipped: another polling process is already running")
        return
    application = create_telegram_application(flask_app)

    async def runner():
        await application.initialize()
        await configure_telegram_bot(application)
        await application.start()
        await application.updater.start_polling(drop_pending_updates=False)
        logging.info("Telegram polling started")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()

    asyncio.run(runner())


def start_polling_bot_once(flask_app) -> None:
    global _polling_started
    if _polling_started or flask_app.config["TG_BOT_MODE"] != "polling":
        return

    with _telegram_lock:
        if _polling_started:
            return

        def polling_worker():
            try:
                run_polling_bot(flask_app)
            except Exception as exc:  # pragma: no cover
                logging.exception("Telegram polling crashed: %s", exc)

        thread = threading.Thread(target=polling_worker, name="telegram-polling", daemon=True)
        thread.start()
        _polling_started = True
