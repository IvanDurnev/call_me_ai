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
    normalized_messages = [_coerce_message(message) for message in messages]
    base_model = model.split(":")[1] if model.startswith("ft:") else model
    legacy_chat = base_model.startswith(("gpt-3.5", "gpt-4", "chatgpt-4o"))
    search_model = "search" in base_model
    if not legacy_chat and not search_model:
        # Newer reasoning, Pro and Codex models use Responses without sampling overrides.
        response = client.responses.create(model=model, input=normalized_messages, store=False)
        return (response.output_text or "").strip()
    options = {"temperature": 0.8} if legacy_chat and not search_model else {}
    response = client.chat.completions.create(model=model, messages=normalized_messages, **options)
    text = response.choices[0].message.content if response.choices else ""
    return (text or "").strip()


def _coerce_message(message: dict[str, Any]) -> dict[str, str]:
    return {
        "role": str(message.get("role") or "user"),
        "content": str(message.get("content") or ""),
    }
