from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from flask import current_app

from .elevenlabs_audio import list_voices as list_elevenlabs_voices
from .openai_audio import list_voice_consents


SUPPORTED_VOICE_SAMPLE_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".mp4", ".ogg", ".wav", ".webm"}


def get_voices_root() -> Path:
    return current_app.static_folder and Path(current_app.static_folder) / "voices"


def iter_voice_directories() -> list[Path]:
    root = get_voices_root()
    if not root or not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def pick_voice_sample(directory: Path) -> Path | None:
    candidates = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_VOICE_SAMPLE_EXTENSIONS),
        key=_voice_sample_sort_key,
    )
    return candidates[0] if candidates else None


def normalize_voice_name(value: str) -> str:
    return " ".join(value.casefold().split())


def convert_voice_sample_to_wav(directory: Path) -> dict[str, Any]:
    sample = pick_voice_sample(directory)
    if not sample:
        raise ValueError("No supported audio sample found in this folder.")

    if sample.suffix.lower() == ".wav":
        return {
            "source": str(sample),
            "wav_path": str(sample),
            "already_wav": True,
        }

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg is not installed on the server.")

    wav_path = sample.with_suffix(".wav")
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(sample),
        "-acodec",
        "pcm_s16le",
        "-ar",
        "24000",
        "-ac",
        "1",
        str(wav_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "ffmpeg conversion failed.").strip())

    return {
        "source": str(sample),
        "wav_path": str(wav_path),
        "already_wav": False,
    }


def build_voice_library_payload() -> dict[str, Any]:
    provider = (current_app.config.get("REALTIME_API_PROVIDER") or "openai").strip().lower() or "openai"
    if provider == "elevenlabs":
        api_key = (current_app.config.get("ELEVEN_LABS_API_KEY") or "").strip()
        items: list[dict[str, Any]] = []
        error: str | None = None
        if api_key:
            try:
                voices = list_elevenlabs_voices(api_key=api_key, limit=100)
                items = [
                    {
                        "name": item.get("name") or item.get("voice_id") or "Unnamed voice",
                        "voice_id": item.get("voice_id"),
                        "category": item.get("category"),
                        "description": item.get("description"),
                    }
                    for item in voices
                ]
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        else:
            error = "ELEVEN_LABS_API_KEY is not configured."

        return {
            "provider": provider,
            "items": items,
            "consents_error": error,
            "consent_language": None,
            "api_ready": bool(api_key),
        }

    folders = iter_voice_directories()
    api_key = (current_app.config.get("OPENAI_API_KEY") or "").strip()

    consents: list[dict[str, Any]] = []
    consents_error: str | None = None
    if api_key:
        try:
            consents = list_voice_consents(api_key=api_key, limit=100)
        except Exception as exc:  # noqa: BLE001
            consents_error = str(exc)
    else:
        consents_error = "OPENAI_API_KEY is not configured."

    consents_by_name = {normalize_voice_name(item.get("name", "")): item for item in consents}

    items = []
    for directory in folders:
        sample = pick_voice_sample(directory)
        consent = consents_by_name.get(normalize_voice_name(directory.name))
        items.append(
            {
                "name": directory.name,
                "sample_file": sample.name if sample else None,
                "sample_path": str(sample) if sample else None,
                "sample_format": sample.suffix.lower().removeprefix(".") if sample else None,
                "consent": consent,
                "has_sample": sample is not None,
            }
        )

    return {
        "provider": provider,
        "items": items,
        "consents_error": consents_error,
        "consent_language": current_app.config["OPENAI_VOICE_CONSENT_LANGUAGE"],
        "api_ready": bool(api_key),
    }


def _voice_sample_sort_key(path: Path) -> tuple[int, str]:
    return (0 if path.suffix.lower() == ".wav" else 1, path.name.casefold())
