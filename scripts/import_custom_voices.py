from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.openai_audio import create_custom_voice, list_voice_consents
from config import Config


SUPPORTED_EXTENSIONS = {".aac", ".flac", ".mp3", ".mp4", ".ogg", ".wav", ".webm", ".m4a"}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    api_key = (Config.OPENAI_API_KEY or "").strip()
    if not api_key:
        print(json.dumps({"error": "OPENAI_API_KEY is not configured"}, ensure_ascii=False))
        return 1

    voices_dir = Path(args.voices_dir).expanduser().resolve()
    if not voices_dir.is_dir():
        print(json.dumps({"error": f"Voices directory not found: {voices_dir}"}, ensure_ascii=False))
        return 1

    consents = list_voice_consents(api_key=api_key, limit=100)
    consents_by_name = {normalize_name(item.get("name", "")): item for item in consents}

    results: list[dict] = []
    for character_dir in sorted(path for path in voices_dir.iterdir() if path.is_dir()):
        sample_path = pick_sample_file(character_dir)
        if not sample_path:
            results.append(
                {
                    "name": character_dir.name,
                    "status": "skipped",
                    "reason": "no_supported_audio_sample",
                }
            )
            continue

        consent = consents_by_name.get(normalize_name(character_dir.name))
        if not consent:
            results.append(
                {
                    "name": character_dir.name,
                    "sample": str(sample_path),
                    "status": "skipped",
                    "reason": "matching_consent_not_found",
                }
            )
            continue

        try:
            voice = create_custom_voice(
                name=character_dir.name,
                consent_id=consent["id"],
                audio_sample_path=sample_path,
                api_key=api_key,
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "name": character_dir.name,
                    "sample": str(sample_path),
                    "consent_id": consent.get("id"),
                    "status": "failed",
                    "reason": str(exc),
                }
            )
            continue

        results.append(
            {
                "name": character_dir.name,
                "sample": str(sample_path),
                "consent_id": consent.get("id"),
                "status": "created",
                "voice_id": voice.get("id"),
                "response": voice,
            }
        )

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create custom OpenAI voices from subdirectories under a voices folder.",
    )
    parser.add_argument(
        "--voices-dir",
        default="static/voices",
        help="Directory whose direct subdirectories are treated as voice names.",
    )
    return parser


def pick_sample_file(character_dir: Path) -> Path | None:
    candidates = sorted(
        path for path in character_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    return candidates[0] if candidates else None


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


if __name__ == "__main__":
    sys.exit(main())
