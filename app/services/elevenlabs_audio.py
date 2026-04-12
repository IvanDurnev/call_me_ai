from __future__ import annotations

import io
from typing import Any, Iterator

import httpx
from flask import current_app, has_app_context


ELEVENLABS_API_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_TTS_MODEL = "eleven_flash_v2_5"
DEFAULT_STT_MODEL = "scribe_v2"


def list_voices(
    *,
    api_key: str | None = None,
    limit: int = 100,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    response = _request(
        "GET",
        "/v2/voices",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        params={
            "page_size": max(1, min(limit, 100)),
            "include_total_count": "false",
        },
    )
    payload = response.json()
    voices = payload.get("voices", []) if isinstance(payload, dict) else []
    return [item for item in voices if isinstance(item, dict)]


def list_llms(
    *,
    api_key: str | None = None,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    response = _request(
        "GET",
        "/v1/convai/llm/list",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    payload = response.json()
    llms = payload.get("llms", []) if isinstance(payload, dict) else []
    return [item for item in llms if isinstance(item, dict)]


def get_signed_url(
    *,
    agent_id: str,
    api_key: str | None = None,
    timeout_seconds: float = 30.0,
) -> str:
    if not agent_id.strip():
        raise ValueError("ElevenLabs agent_id is required.")
    response = _request(
        "GET",
        "/v1/convai/conversation/get-signed-url",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        params={"agent_id": agent_id.strip()},
    )
    payload = response.json()
    if not isinstance(payload, dict) or not str(payload.get("signed_url") or "").strip():
        raise ValueError("ElevenLabs returned an unexpected signed URL payload.")
    return str(payload["signed_url"]).strip()


def create_agent(
    *,
    conversation_config: dict[str, Any],
    name: str | None = None,
    tags: list[str] | None = None,
    api_key: str | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    if not isinstance(conversation_config, dict) or not conversation_config:
        raise ValueError("conversation_config is required.")
    payload: dict[str, Any] = {
        "conversation_config": conversation_config,
    }
    if name and name.strip():
        payload["name"] = name.strip()
    if tags:
        payload["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]
    response = _request(
        "POST",
        "/v1/convai/agents/create",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        json=payload,
    )
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("ElevenLabs returned an unexpected create agent payload.")
    return data


def generate_speech_preview(
    *,
    text: str,
    voice: str,
    speed: float | None = None,
    api_key: str | None = None,
    timeout_seconds: float = 60.0,
) -> bytes:
    return b"".join(
        stream_speech(
            text=text,
            voice=voice,
            output_format="mp3_44100_128",
            speed=speed,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
    )


def stream_speech(
    *,
    text: str,
    voice: str,
    output_format: str = "pcm_24000",
    speed: float | None = None,
    api_key: str | None = None,
    timeout_seconds: float = 60.0,
) -> Iterator[bytes]:
    preview_text = text.strip()
    if not preview_text:
        raise ValueError("Preview text must not be empty.")
    if not voice.strip():
        raise ValueError("Voice is required.")

    payload: dict[str, Any] = {
        "text": preview_text,
        "model_id": _get_tts_model_from_context(),
    }
    if speed not in {None, ""}:
        payload["voice_settings"] = {"speed": float(speed)}

    with _client(api_key=api_key, timeout_seconds=timeout_seconds).stream(
        "POST",
        f"{ELEVENLABS_API_BASE_URL}/v1/text-to-speech/{voice.strip()}/stream",
        params={"output_format": output_format},
        json=payload,
    ) as response:
        _raise_for_status(response)
        for chunk in response.iter_bytes():
            if chunk:
                yield chunk


def transcribe_audio(
    *,
    wav_bytes: bytes,
    language_code: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    if not wav_bytes:
        raise ValueError("Audio payload is empty.")

    files = {"file": ("turn.wav", io.BytesIO(wav_bytes), "audio/wav")}
    data: dict[str, Any] = {"model_id": _get_stt_model_from_context()}
    if language_code:
        data["language_code"] = language_code.strip()

    response = _request(
        "POST",
        "/v1/speech-to-text",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        data=data,
        files=files,
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("ElevenLabs returned an unexpected transcription payload.")
    return payload


def get_conversation_details(
    *,
    conversation_id: str,
    api_key: str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    if not conversation_id.strip():
        raise ValueError("Conversation ID is required.")
    response = _request(
        "GET",
        f"/v1/convai/conversations/{conversation_id.strip()}",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("ElevenLabs returned an unexpected conversation payload.")
    return payload


def update_agent(
    *,
    agent_id: str,
    conversation_config: dict[str, Any],
    name: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    if not agent_id.strip():
        raise ValueError("Agent ID is required.")
    if not isinstance(conversation_config, dict) or not conversation_config:
        raise ValueError("conversation_config is required.")
    payload: dict[str, Any] = {"conversation_config": conversation_config}
    if name and name.strip():
        payload["name"] = name.strip()
    response = _request(
        "PATCH",
        f"/v1/convai/agents/{agent_id.strip()}",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        json=payload,
    )
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("ElevenLabs returned an unexpected update agent payload.")
    return data


def get_agent(
    *,
    agent_id: str,
    api_key: str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    if not agent_id.strip():
        raise ValueError("Agent ID is required.")
    response = _request(
        "GET",
        f"/v1/convai/agents/{agent_id.strip()}",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("ElevenLabs returned an unexpected agent payload.")
    return payload


def _request(
    method: str,
    path: str,
    *,
    api_key: str | None,
    timeout_seconds: float,
    **kwargs: Any,
) -> httpx.Response:
    client = _client(api_key=api_key, timeout_seconds=timeout_seconds)
    response = client.request(method, f"{ELEVENLABS_API_BASE_URL}{path}", **kwargs)
    _raise_for_status(response)
    return response


def _client(*, api_key: str | None, timeout_seconds: float) -> httpx.Client:
    resolved_api_key = (api_key or _get_api_key_from_context()).strip()
    if not resolved_api_key:
        raise ValueError("ELEVEN_LABS_API_KEY is not configured.")
    return httpx.Client(
        timeout=timeout_seconds,
        headers={
            "xi-api-key": resolved_api_key,
            "Accept": "application/json",
        },
    )


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            exc.response.read()
            detail = exc.response.text.strip()
        except Exception:
            detail = ""
        raise ValueError(detail or f"ElevenLabs request failed with status {exc.response.status_code}.") from exc


def _get_api_key_from_context() -> str:
    if not has_app_context():
        return ""
    return (
        current_app.config.get("ELEVEN_LABS_API_KEY")
        or current_app.config.get("ELEVENLABS_API_KEY")
        or ""
    ).strip()


def _get_tts_model_from_context() -> str:
    if not has_app_context():
        return DEFAULT_TTS_MODEL
    return (current_app.config.get("ELEVENLABS_TTS_MODEL") or DEFAULT_TTS_MODEL).strip() or DEFAULT_TTS_MODEL


def _get_stt_model_from_context() -> str:
    if not has_app_context():
        return DEFAULT_STT_MODEL
    return (current_app.config.get("ELEVENLABS_STT_MODEL") or DEFAULT_STT_MODEL).strip() or DEFAULT_STT_MODEL
