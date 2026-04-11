from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

from flask import Flask

from app.characters import build_realtime_session_config, build_runtime_instructions
from app.realtime import SocketBridge


class DummyBrowserWs:
    def send(self, _message: str) -> None:
        return


class RecordingSocketBridge(SocketBridge):
    def __init__(self, browser_ws, character_slug: str):
        super().__init__(browser_ws, character_slug)
        self.lines: list[tuple[str, str, str | None]] = []
        self.browser_payloads: list[dict] = []
        self.openai_payloads: list[dict] = []

    def _append_conversation_line(self, role: str, text: str, dedupe_key: str | None = None) -> bool:
        normalized_text = text.strip()
        if not normalized_text:
            return False
        if dedupe_key and dedupe_key in self.seen_conversation_keys:
            return False
        if dedupe_key:
            self.seen_conversation_keys.add(dedupe_key)
        self.lines.append((role, normalized_text, dedupe_key))
        return True

    def _send_browser(self, payload: dict) -> None:
        self.browser_payloads.append(payload)

    def _append_call_session_log(self, source: str, message: str, payload: dict | None = None) -> None:
        return

    def _send_openai(self, payload: dict) -> None:
        self.openai_payloads.append(payload)


class RealtimeBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)

    def _make_bridge(self, **config_overrides) -> RecordingSocketBridge:
        with self.app.app_context():
            self.app.config.update(config_overrides)
            with patch("app.realtime.get_character", return_value={"slug": "baba-yaga", "name": "Баба Яга"}):
                return RecordingSocketBridge(DummyBrowserWs(), "baba-yaga")

    def _make_bridge_for_character(self, character: dict, **config_overrides) -> RecordingSocketBridge:
        with self.app.app_context():
            self.app.config.update(config_overrides)
            with patch("app.realtime.get_character", return_value=character):
                return RecordingSocketBridge(DummyBrowserWs(), "baba-yaga")

    def test_user_transcript_is_saved_and_emitted_from_created_item(self) -> None:
        bridge = self._make_bridge()

        bridge._handle_openai_event(
            {
                "type": "conversation.item.created",
                "item": {
                    "id": "user-1",
                    "role": "user",
                    "content": [{"type": "input_audio", "transcript": "Привет"}],
                },
            }
        )

        self.assertEqual(bridge.lines, [("user", "Привет", "user:user-1")])
        self.assertEqual(
            bridge.browser_payloads,
            [{"type": "call.transcript", "role": "user", "transcript": "Привет"}],
        )

    def test_user_transcript_is_not_duplicated_between_completed_and_created_events(self) -> None:
        bridge = self._make_bridge()

        bridge._handle_openai_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "user-1",
                "transcript": "Как дела?",
            }
        )
        bridge._handle_openai_event(
            {
                "type": "conversation.item.created",
                "item": {
                    "id": "user-1",
                    "role": "user",
                    "content": [{"type": "input_audio", "transcript": "Как дела?"}],
                },
            }
        )

        self.assertEqual(bridge.lines, [("user", "Как дела?", "user:user-1")])
        self.assertEqual(len(bridge.browser_payloads), 1)

    def test_assistant_transcript_is_recorded_once_from_output_item(self) -> None:
        bridge = self._make_bridge()

        bridge._handle_openai_event(
            {
                "type": "response.audio_transcript.done",
                "transcript": "Здравствуйте!",
            }
        )
        bridge._handle_openai_event(
            {
                "type": "response.output_item.done",
                "item": {
                    "id": "assistant-1",
                    "role": "assistant",
                    "content": [{"type": "audio", "transcript": "Здравствуйте!"}],
                },
            }
        )

        self.assertEqual(bridge.lines, [("assistant", "Здравствуйте!", "assistant:assistant-1")])
        self.assertEqual(
            bridge.browser_payloads,
            [{"type": "call.transcript", "role": "assistant", "transcript": "Здравствуйте!"}],
        )

    def test_realtime_session_config_omits_unsupported_type_field(self) -> None:
        session = build_realtime_session_config(
            {
                "name": "Баба Яга",
                "description": "Лесная наставница",
                "voice": "alloy",
                "realtime_settings": {
                    "model": "gpt-4o-realtime-preview",
                    "input_transcription_model": "gpt-4o-mini-transcribe",
                    "input_transcription_language": "ru",
                },
            },
            fallback_model="gpt-4o-realtime-preview",
            fallback_voice="alloy",
        )

        self.assertNotIn("type", session)
        self.assertNotIn("audio", session)
        self.assertEqual(session["input_audio_transcription"]["model"], "gpt-4o-mini-transcribe")
        self.assertEqual(session["input_audio_format"], "pcm16")
        self.assertIsNone(session["turn_detection"])
        self.assertEqual(session["tool_choice"], "auto")
        self.assertEqual(session["tools"][0]["name"], "end_call")

    def test_marker_mode_instructions_do_not_mention_end_call_function(self) -> None:
        instructions = build_runtime_instructions(
            {
                "name": "Баба Яга",
                "description": "Лесная наставница",
            },
            end_call_mode="marker",
        )

        self.assertIn("<END_CALL:короткая причина>", instructions)
        self.assertNotIn("вызови функцию end_call", instructions)

    def test_end_call_function_requests_browser_close(self) -> None:
        bridge = self._make_bridge()

        bridge._handle_openai_event(
            {
                "type": "response.output_item.done",
                "item": {
                    "id": "fc-1",
                    "type": "function_call",
                    "name": "end_call",
                    "call_id": "call-1",
                    "arguments": "{\"reason\":\"пользователь попрощался\"}",
                },
            }
        )

        self.assertEqual(
            bridge.openai_payloads,
            [
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": "{\"ok\": true, \"ended\": true}",
                    },
                }
            ],
        )
        self.assertIn(
            {"type": "call.end_requested", "reason": "пользователь попрощался"},
            bridge.browser_payloads,
        )

    def test_elevenlabs_commit_transcribes_and_emits_user_transcript(self) -> None:
        bridge = self._make_bridge(
            REALTIME_API_PROVIDER="elevenlabs",
            ELEVEN_LABS_API_KEY="test-key",
        )

        with patch("app.realtime.transcribe_audio", return_value={"text": "Привет из ElevenLabs"}):
            bridge._handle_browser_message(
                json_dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(b"\x01\x02\x03\x04").decode("ascii"),
                    }
                )
            )
            bridge._handle_browser_message(json_dumps({"type": "input_audio_buffer.commit"}))

        self.assertIn(("user", "Привет из ElevenLabs", "user:elevenlabs:0"), bridge.lines)
        self.assertIn({"type": "input_audio_buffer.committed"}, bridge.browser_payloads)

    def test_character_provider_override_switches_bridge_to_elevenlabs(self) -> None:
        bridge = self._make_bridge_for_character(
            {
                "slug": "baba-yaga",
                "name": "Баба Яга",
                "realtime_settings": {"provider": "elevenlabs"},
            },
            REALTIME_API_PROVIDER="openai",
        )

        self.assertEqual(bridge.provider, "elevenlabs")

    def test_extract_end_call_marker_returns_clean_text_and_reason(self) -> None:
        text, reason = SocketBridge._extract_end_call_marker("До свидания! <END_CALL:пользователь попрощался>")
        self.assertEqual(text, "До свидания!")
        self.assertEqual(reason, "пользователь попрощался")

    def test_extract_end_call_marker_strips_spoken_endcall_function(self) -> None:
        text, reason = SocketBridge._extract_end_call_marker("До свидания! endcall()")
        self.assertEqual(text, "До свидания!")
        self.assertEqual(reason, "разговор завершён")


def json_dumps(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
