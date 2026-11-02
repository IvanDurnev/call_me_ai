from __future__ import annotations

import re

from .openai_client import build_openai_client


def supports_text_chat(model_id: str) -> bool:
    """Models API has no capability metadata; exclude specialised model families."""
    base = model_id.split(':')[1] if model_id.startswith('ft:') else model_id
    if any(part in base for part in (
        'audio', 'realtime', 'transcribe', 'tts', 'image', 'instruct',
        'deep-research', 'computer-use', 'gpt-live',
    )):
        return False
    return bool(re.match(r'^(gpt-\d|chatgpt-|chat-latest$|o\d(?:-|$))', base))


def list_openai_models(*, api_key: str) -> list[dict]:
    with build_openai_client(api_key, 12).with_options(max_retries=0) as client:
        model_ids = sorted({model.id for model in client.models.list()})
    return [{'id': model_id, 'text_chat': supports_text_chat(model_id)} for model_id in model_ids]
