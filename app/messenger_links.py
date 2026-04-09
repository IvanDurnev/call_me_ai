from __future__ import annotations

from urllib.parse import urljoin


def _get_config_value(flask_app, key: str, default: str = "") -> str:
    if hasattr(flask_app, "config"):
        return flask_app.config.get(key, default)
    return default


def build_miniapp_url(flask_app, slug: str, *, platform: str) -> str:
    base_url = _get_config_value(flask_app, "PUBLIC_BASE_URL", "http://127.0.0.1:5000").rstrip("/") + "/"
    if platform == "max":
        max_base = _resolve_max_app_base(flask_app)
        return urljoin(max_base.rstrip("/") + "/", slug)
    return urljoin(base_url, f"miniapp/{slug}?source=telegram-miniapp")


def build_picker_url(flask_app, *, platform: str) -> str:
    base_url = _get_config_value(flask_app, "PUBLIC_BASE_URL", "http://127.0.0.1:5000").rstrip("/") + "/"
    if platform == "max":
        max_base = _resolve_max_app_base(flask_app)
        return max_base.rstrip("/")
    return urljoin(base_url, "miniapp?source=telegram-miniapp")


def build_heroes_url(flask_app, *, platform: str) -> str:
    if platform == "max":
        max_base = _resolve_max_app_base(flask_app)
        return urljoin(max_base.rstrip("/") + "/", "heroes")
    return f"{_get_config_value(flask_app, 'PUBLIC_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')}/miniapp/heroes?source=telegram-miniapp"


def build_voices_url(flask_app, *, platform: str) -> str:
    if platform == "max":
        max_base = _resolve_max_app_base(flask_app)
        return urljoin(max_base.rstrip("/") + "/", "voices")
    return f"{_get_config_value(flask_app, 'PUBLIC_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')}/miniapp/voices?source=telegram-miniapp"


def _resolve_max_app_base(flask_app) -> str:
    raw_link = (_get_config_value(flask_app, "MAX_BOT_APP_LINK", "/max/miniapp") or "/max/miniapp").strip()
    public_base = _get_config_value(flask_app, "PUBLIC_BASE_URL", "http://127.0.0.1:5000").rstrip("/") + "/"
    if raw_link.startswith("http://") or raw_link.startswith("https://"):
        return raw_link
    return urljoin(public_base, raw_link.lstrip("/"))
