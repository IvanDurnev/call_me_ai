from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask

from app.characters import build_realtime_session_config
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

    def _make_bridge(self) -> RecordingSocketBridge:
        with self.app.app_context():
            with patch("app.realtime.get_character", return_value={"slug": "baba-yaga", "name": "Баба Яга"}):
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


if __name__ == "__main__":
    unittest.main()
