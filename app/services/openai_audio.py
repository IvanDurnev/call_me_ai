from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from flask import current_app, has_app_context

from .openai_client import build_openai_client


OPENAI_AUDIO_VOICES_URL = "https://api.openai.com/v1/audio/voices"
OPENAI_AUDIO_VOICE_CONSENTS_URL = "https://api.openai.com/v1/audio/voice_consents"
SUPPORTED_AUDIO_MIME_TYPES = {
    "audio/aac",
    "audio/flac",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-wav",
}


def list_voice_consents(
    *,
    api_key: str | None = None,
    limit: int = 100,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    resolved_api_key = api_key or _get_api_key_from_context()
    if not resolved_api_key:
        raise ValueError("OPENAI_API_KEY is not configured.")

    client = build_openai_client(resolved_api_key, timeout_seconds)
    payload = client.get(
        "/audio/voice_consents",
        cast_to=dict,
        options={"extra_query": {"limit": max(1, min(limit, 100))}},
    )
    if not isinstance(payload, dict):
        raise ValueError("OpenAI returned an unexpected response while listing voice consents.")

    data = payload.get("data", [])
    if not isinstance(data, list):
        raise ValueError("OpenAI returned an unexpected consent list payload.")
    return [item for item in data if isinstance(item, dict)]


def create_voice_consent(
    *,
    name: str,
    language: str,
    recording_path: str | Path,
    api_key: str | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    recording_file_path = Path(recording_path).expanduser().resolve()
    if not recording_file_path.is_file():
        raise FileNotFoundError(f"Consent recording file not found: {recording_file_path}")

    if not name.strip():
        raise ValueError("Consent name must not be empty.")
    if not language.strip():
        raise ValueError("Consent language must not be empty.")

    resolved_api_key = api_key or _get_api_key_from_context()
    if not resolved_api_key:
        raise ValueError("OPENAI_API_KEY is not configured.")

    mime_type = _guess_mime_type(recording_file_path)
    client = build_openai_client(resolved_api_key, timeout_seconds)
    with recording_file_path.open("rb") as recording_file:
        payload = client.post(
            "/audio/voice_consents",
            cast_to=dict,
            body={
                "name": name.strip(),
                "language": language.strip(),
            },
            files={"recording": (recording_file_path.name, recording_file, mime_type)},
            options={"extra_headers": {"Content-Type": "multipart/form-data"}},
        )
    if not isinstance(payload, dict):
        raise ValueError("OpenAI returned an unexpected response while creating the consent recording.")
    return payload


def create_custom_voice(
    *,
    name: str,
    consent_id: str,
    audio_sample_path: str | Path,
    api_key: str | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    sample_path = Path(audio_sample_path).expanduser().resolve()
    if not sample_path.is_file():
        raise FileNotFoundError(f"Audio sample file not found: {sample_path}")

    if not name.strip():
        raise ValueError("Voice name must not be empty.")
    if not consent_id.strip():
        raise ValueError("Consent ID must not be empty.")

    resolved_api_key = api_key or _get_api_key_from_context()
    if not resolved_api_key:
        raise ValueError("OPENAI_API_KEY is not configured.")

    mime_type = _guess_mime_type(sample_path)
    client = build_openai_client(resolved_api_key, timeout_seconds)
    with sample_path.open("rb") as audio_file:
        payload = client.post(
            "/audio/voices",
            cast_to=dict,
            body={
                "name": name.strip(),
                "consent": consent_id.strip(),
            },
            files={"audio_sample": (sample_path.name, audio_file, mime_type)},
            options={"extra_headers": {"Content-Type": "multipart/form-data"}},
        )
    if not isinstance(payload, dict):
        raise ValueError("OpenAI returned an unexpected response while creating the custom voice.")
    return payload


def generate_speech_preview(
    *,
    text: str,
    voice: str,
    api_key: str | None = None,
    timeout_seconds: float = 60.0,
) -> bytes:
    preview_text = text.strip()
    if not preview_text:
        raise ValueError("Preview text must not be empty.")

    resolved_api_key = api_key or _get_api_key_from_context()
    if not resolved_api_key:
        raise ValueError("OPENAI_API_KEY is not configured.")

    client = build_openai_client(resolved_api_key, timeout_seconds)
    response = client.audio.speech.create(
        model="gpt-4o-mini-tts",
        voice=voice.strip(),
        input=preview_text,
        instructions="Speak warmly and clearly in Russian, like a kind fairytale character on a phone call.",
        response_format="mp3",
    )
    return response.content


def _get_api_key_from_context() -> str:
    if not has_app_context():
        return ""
    return (current_app.config.get("OPENAI_API_KEY") or "").strip()


def _guess_mime_type(sample_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(sample_path.name)
    if mime_type == "audio/x-m4a":
        mime_type = "audio/mp4"
    if mime_type not in SUPPORTED_AUDIO_MIME_TYPES:
        raise ValueError(
            "Unsupported audio sample format. Use one of: "
            + ", ".join(sorted(SUPPORTED_AUDIO_MIME_TYPES))
        )
    return mime_type
