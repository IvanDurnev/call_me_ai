from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
from flask import current_app, has_app_context
from openai import OpenAI


def get_openai_proxy_url() -> str:
    if not has_app_context():
        return ""
    return (current_app.config.get("OPENAI_PROXY") or "").strip()


def build_openai_client(api_key: str, timeout_seconds: float) -> OpenAI:
    proxy_url = get_openai_proxy_url()
    if not proxy_url:
        return OpenAI(api_key=api_key, timeout=timeout_seconds)

    return OpenAI(
        api_key=api_key,
        timeout=timeout_seconds,
        http_client=_build_httpx_client(proxy_url, timeout_seconds),
    )


def build_openai_websocket_options() -> dict[str, Any]:
    proxy_url = get_openai_proxy_url()
    if not proxy_url:
        return {}

    parsed = urlparse(proxy_url)
    if not parsed.hostname:
        return {}

    proxy_options: dict[str, Any] = {
        "http_proxy_host": parsed.hostname,
        "http_proxy_port": parsed.port,
    }

    scheme = (parsed.scheme or "http").lower()
    if scheme.startswith("socks"):
        proxy_options["proxy_type"] = scheme
    else:
        proxy_options["proxy_type"] = "http"

    if parsed.username or parsed.password:
        proxy_options["http_proxy_auth"] = (parsed.username or "", parsed.password or "")

    return {key: value for key, value in proxy_options.items() if value not in {None, ""}}


def _build_httpx_client(proxy_url: str, timeout_seconds: float) -> httpx.Client:
    try:
        return httpx.Client(proxy=proxy_url, timeout=timeout_seconds)
    except TypeError:
        proxies = {
            "http://": proxy_url,
            "https://": proxy_url,
        }
        return httpx.Client(proxies=proxies, timeout=timeout_seconds)
