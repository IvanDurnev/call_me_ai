from __future__ import annotations

from typing import Any

from .openai_client import build_openai_client


def generate_chat_reply(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout_seconds: float = 60.0,
) -> str:
    client = build_openai_client(api_key, timeout_seconds)
    response = client.chat.completions.create(
        model=model,
        messages=[_coerce_message(message) for message in messages],
        temperature=0.8,
    )
    text = response.choices[0].message.content if response.choices else ""
    return (text or "").strip()


def _coerce_message(message: dict[str, Any]) -> dict[str, str]:
    return {
        "role": str(message.get("role") or "user"),
        "content": str(message.get("content") or ""),
    }
