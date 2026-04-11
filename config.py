from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'call_me_ai.db'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAIL_SERVER = os.getenv("MAIL_SERVER", os.getenv("SMTP_SERVER", "")).strip()
    MAIL_PORT = int(os.getenv("MAIL_PORT", os.getenv("SMTP_PORT", "587")))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "").strip()
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "").strip()
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "").strip() or None
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").strip().lower() in {"1", "true", "yes", "on"}
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "false").strip().lower() in {"1", "true", "yes", "on"}
    MAIL_SUPPRESS_SEND = os.getenv("MAIL_SUPPRESS_SEND", "false").strip().lower() in {"1", "true", "yes", "on"}
    MAIL_TIMEOUT = int(os.getenv("MAIL_TIMEOUT", os.getenv("MAIL_TIMEOUT_SECONDS", "10")))
    EMAIL_VERIFICATION_CODE_TTL_MINUTES = int(os.getenv("EMAIL_VERIFICATION_CODE_TTL_MINUTES", "10"))
    EMAIL_VERIFICATION_RESEND_INTERVAL_SECONDS = int(os.getenv("EMAIL_VERIFICATION_RESEND_INTERVAL_SECONDS", "60"))
    TRY_CALLS_NUMBER = max(0, int(os.getenv("TRY_CALLS_NUMBER", "1")))
    CLOUDPAYMENTS_PUBLIC_ID = os.getenv("CLOUDPAYMENTS_PUBLIC_ID", "").strip()
    CLOUDPAYMENTS_API_PASSWORD = os.getenv("CLOUDPAYMENTS_API_PASSWORD", "").strip()
    CLOUDPAYMENTS_CURRENCY = os.getenv("CLOUDPAYMENTS_CURRENCY", "RUB").strip().upper() or "RUB"
    CLOUDPAYMENTS_TEST_SUBSCRIPTION_AMOUNT = float(os.getenv("CLOUDPAYMENTS_TEST_SUBSCRIPTION_AMOUNT", "99"))
    CLOUDPAYMENTS_TEST_SUBSCRIPTION_NAME = os.getenv("CLOUDPAYMENTS_TEST_SUBSCRIPTION_NAME", "Тестовый абонемент").strip() or "Тестовый абонемент"
    SUBSCRIPTION_RENEW_CHECK_INTERVAL_SECONDS = int(os.getenv("SUBSCRIPTION_RENEW_CHECK_INTERVAL_SECONDS", "60"))

    TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
    TG_BOT_NAME = os.getenv("TG_BOT_NAME", "").strip().lstrip("@")
    TG_BOT_LINK = os.getenv("TG_BOT_LINK", "").strip()
    TG_BOT_MODE = os.getenv("TG_BOT_MODE", "webhook").strip().lower()
    TG_WEBHOOK_SECRET = os.getenv("TG_WEBHOOK_SECRET", "")
    MAX_BOT_ID = os.getenv("MAX_BOT_ID", "")
    MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "")
    MAX_BOT_MODE = os.getenv("MAX_BOT_MODE", "disabled").strip().lower()
    MAX_BOT_APP_LINK = os.getenv("MAX_BOT_APP_LINK", "/max/miniapp").strip()
    MAX_BOT_LINK = os.getenv("MAX_BOT_LINK", "").strip()
    MAX_WEBHOOK_SECRET = os.getenv("MAX_WEBHOOK_SECRET", "")
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:5000").rstrip("/")

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_PROXY = os.getenv("OPENAI_PROXY", os.getenv("OPENAI_PROXY_IP", "")).strip()
    OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini").strip()
    OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
    OPENAI_REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "alloy")
    OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
    OPENAI_VOICE_CONSENT_LANGUAGE = os.getenv("OPENAI_VOICE_CONSENT_LANGUAGE", "ru-RU")
    REALTIME_API_PROVIDER = os.getenv("REALTIME_API_PROVIDER", "openai").strip().lower() or "openai"
    ELEVEN_LABS_API_KEY = os.getenv("ELEVEN_LABS_API_KEY", os.getenv("ELEVENLABS_API_KEY", "")).strip()
    ELEVENLABS_API_KEY = ELEVEN_LABS_API_KEY
    ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
    ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5").strip()
    ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2").strip()

    @classmethod
    def realtime_url(cls) -> str:
        return f"wss://api.openai.com/v1/realtime?model={cls.OPENAI_REALTIME_MODEL}"
