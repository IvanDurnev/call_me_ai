from __future__ import annotations

import base64
import io
import json
import logging
import re
import wave
from contextlib import suppress
from datetime import datetime
from threading import Event, Lock, Thread

from flask import current_app
from simple_websocket.errors import ConnectionClosed as BrowserConnectionClosed
from websocket import WebSocket, WebSocketConnectionClosedException, WebSocketTimeoutException, create_connection

from .characters import build_character_identity_prompt, build_realtime_session_config, build_runtime_instructions, get_character, normalize_realtime_settings
from .extensions import db
from .models import AppUser, CallSession
from .services.billing import build_user_access_state
from .services.elevenlabs_audio import stream_speech, transcribe_audio
from .services.llm import generate_chat_reply
from .services.openai_client import build_openai_websocket_options


def build_openai_headers(app) -> list[str]:
    return [
        f"Authorization: Bearer {app.config['OPENAI_API_KEY']}",
        "OpenAI-Beta: realtime=v1",
    ]


class SocketBridge:
    def __init__(self, browser_ws, character_slug: str):
        self.browser_ws = browser_ws
        self.character_slug = character_slug
        self.character = get_character(character_slug)
        self.app = current_app._get_current_object()
        settings = normalize_realtime_settings(self.character.get("realtime_settings") if self.character else None)
        self.provider = str(settings.get("provider") or self.app.config.get("REALTIME_API_PROVIDER") or "openai").strip().lower() or "openai"
        self.openai_ws: WebSocket | None = None
        self.browser_send_lock = Lock()
        self.openai_send_lock = Lock()
        self.closed = False
        self.call_session_id: int | None = None
        self.greeted = False
        self.pending_user_transcripts: dict[str, str] = {}
        self.seen_conversation_keys: set[str] = set()
        self.end_call_requested = False
        self.conversation_history: list[dict[str, str]] = []
        self.pending_audio_buffer = bytearray()
        self.response_thread: Thread | None = None
        self.response_cancel_event = Event()
        self.response_sequence = 0
        self.response_sequence_lock = Lock()

    @staticmethod
    def _format_browser_close(exc: BrowserConnectionClosed) -> str:
        return f"code={exc.reason or 'unknown'} message={exc.message or ''}".strip()

    def connect(self) -> None:
        if self.provider == "elevenlabs":
            self._connect_elevenlabs()
            return
        self._connect_openai()

    def _connect_openai(self) -> None:
        session_config = build_realtime_session_config(
            self.character,
            self.app.config["OPENAI_REALTIME_MODEL"],
            self.app.config["OPENAI_REALTIME_VOICE"],
        )
        model = session_config.get("model") or self.app.config["OPENAI_REALTIME_MODEL"]
        realtime_url = f"wss://api.openai.com/v1/realtime?model={model}"
        self.openai_ws = create_connection(
            realtime_url,
            header=build_openai_headers(self.app),
            enable_multithread=True,
            timeout=30,
            **build_openai_websocket_options(),
        )
        self._send_openai(
            {
                "type": "session.update",
                "session": session_config,
            }
        )
        self._send_browser(self._call_ready_payload())
        logging.info("OpenAI realtime session connected for %s", self.character_slug)

    def _connect_elevenlabs(self) -> None:
        self._send_browser(self._call_ready_payload())
        self._send_browser({"type": "session.created", "session": {"provider": "elevenlabs"}})
        self._send_browser({"type": "session.updated", "session": {"provider": "elevenlabs"}})
        logging.info("ElevenLabs realtime session prepared for %s", self.character_slug)

    def _call_ready_payload(self) -> dict:
        return {
            "type": "call.ready",
            "character": {
                "slug": self.character["slug"],
                "name": self.character["name"],
                "emoji": self.character["emoji"],
                "avatar_path": self.character.get("avatar_path"),
            },
        }

    def _send_browser(self, payload: dict) -> None:
        if self.closed:
            return
        with self.browser_send_lock:
            try:
                self.browser_ws.send(json.dumps(payload))
            except BrowserConnectionClosed:
                self.closed = True

    def _send_openai(self, payload: dict) -> None:
        if self.closed or not self.openai_ws:
            return
        with self.openai_send_lock:
            self.openai_ws.send(json.dumps(payload))

    def _create_call_session(self, payload: dict) -> None:
        if self.call_session_id is not None:
            return
        with self.app.app_context():
            started_from = payload.get("started_from") or "miniapp"
            app_user_id = payload.get("app_user_id")
            app_user = db.session.get(AppUser, app_user_id) if app_user_id else None
            if not app_user:
                raise ValueError("Регистрация обязательна перед первым звонком.")

            access_state = build_user_access_state(
                app_user,
                trial_minutes_limit=max(0, int(self.app.config.get("TRY_CALLS_NUMBER", 1))),
            )
            if not access_state["has_call_access"]:
                raise ValueError("Доступные минуты исчерпаны. Продлите тариф в личном кабинете.")

            call_session = CallSession(
                app_user_id=app_user.id,
                telegram_user_id=payload.get("telegram_user_id"),
                telegram_username=payload.get("telegram_username"),
                character_slug=self.character_slug,
                status="active",
                meta_json={
                    "platform": started_from,
                    "started_from": started_from,
                    "app_user_name": app_user.name,
                    "character_name": self.character["name"],
                    "provider": self.provider,
                    "created_at": datetime.utcnow().isoformat(),
                    "conversation_log": [],
                    "technical_log": [],
                },
            )
            db.session.add(call_session)
            db.session.commit()
            self.call_session_id = call_session.id

    def _append_call_session_log(self, source: str, message: str, payload: dict | None = None) -> None:
        if self.call_session_id is None:
            return
        with self.app.app_context():
            call_session = db.session.get(CallSession, self.call_session_id)
            if not call_session:
                return

            meta = dict(call_session.meta_json or {})
            technical_log = list(meta.get("technical_log") or [])
            entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "source": source,
                "message": message,
            }
            if payload:
                entry["payload"] = payload

            technical_log.append(entry)
            meta["technical_log"] = technical_log[-200:]
            call_session.meta_json = meta
            db.session.commit()

    def _append_conversation_line(self, role: str, text: str, dedupe_key: str | None = None) -> bool:
        normalized_text = text.strip()
        if not normalized_text:
            return False
        if dedupe_key and dedupe_key in self.seen_conversation_keys:
            return False
        if dedupe_key:
            self.seen_conversation_keys.add(dedupe_key)
        self.conversation_history.append({"role": role, "text": normalized_text})
        if self.call_session_id is None:
            return True
        with self.app.app_context():
            call_session = db.session.get(CallSession, self.call_session_id)
            if not call_session:
                return False

            meta = dict(call_session.meta_json or {})
            conversation_log = list(meta.get("conversation_log") or [])
            conversation_log.append(
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "role": role,
                    "text": normalized_text,
                }
            )
            meta["conversation_log"] = conversation_log[-200:]
            call_session.meta_json = meta
            db.session.commit()
        return True

    def _emit_browser_transcript(self, role: str, text: str) -> None:
        normalized_text = text.strip()
        if not normalized_text:
            return
        self._send_browser(
            {
                "type": "call.transcript",
                "role": role,
                "transcript": normalized_text,
            }
        )

    def _store_and_emit_conversation_line(self, role: str, text: str, dedupe_key: str | None = None) -> None:
        if self._append_conversation_line(role, text, dedupe_key=dedupe_key):
            self._emit_browser_transcript(role, text)

    def _request_browser_end_call(self, reason: str | None = None) -> None:
        if self.end_call_requested:
            return
        self.end_call_requested = True
        payload = {"type": "call.end_requested"}
        if reason:
            payload["reason"] = reason
        self._send_browser(payload)

    def _handle_function_call(self, item: dict) -> None:
        if item.get("type") != "function_call" or item.get("name") != "end_call":
            return

        arguments_raw = item.get("arguments") or "{}"
        reason = None
        with suppress(Exception):
            arguments = json.loads(arguments_raw) if isinstance(arguments_raw, str) else {}
            if isinstance(arguments, dict):
                reason = str(arguments.get("reason") or "").strip() or None

        call_id = item.get("call_id")
        if call_id:
            self._send_openai(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({"ok": True, "ended": True}, ensure_ascii=False),
                    },
                }
            )
        self._append_call_session_log(
            source="openai",
            message="Function call: end_call",
            payload=item,
        )
        self._request_browser_end_call(reason)

    def _append_user_transcription_delta(self, item_id: str | None, delta: str | None) -> None:
        if not item_id or not delta:
            return
        previous = self.pending_user_transcripts.get(item_id, "")
        self.pending_user_transcripts[item_id] = f"{previous}{delta}"

    def _flush_user_transcription(self, item_id: str | None, transcript: str | None = None) -> None:
        if not item_id and not transcript:
            return
        text = (transcript or self.pending_user_transcripts.get(item_id) or "").strip()
        if item_id:
            self.pending_user_transcripts.pop(item_id, None)
        if text:
            dedupe_key = f"user:{item_id}" if item_id else f"user:text:{text}"
            self._store_and_emit_conversation_line("user", text, dedupe_key=dedupe_key)

    @staticmethod
    def _extract_item_transcript(payload: dict) -> str:
        item = payload.get("item") or {}
        if isinstance(item.get("transcript"), str) and item.get("transcript").strip():
            return item["transcript"].strip()

        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            transcript = content.get("transcript") or content.get("text")
            if isinstance(transcript, str) and transcript.strip():
                return transcript.strip()

        return ""

    def _handle_openai_event(self, payload: dict) -> None:
        event_type = payload.get("type", "unknown")
        if event_type == "error":
            self._append_call_session_log(
                source="openai",
                message=payload.get("message")
                or payload.get("error", {}).get("message")
                or "Unknown realtime error",
                payload=payload,
            )
        if event_type == "conversation.item.input_audio_transcription.delta":
            self._append_user_transcription_delta(payload.get("item_id"), payload.get("delta"))
        if event_type == "conversation.item.input_audio_transcription.completed" and payload.get("transcript"):
            self._flush_user_transcription(payload.get("item_id"), payload.get("transcript"))
        if event_type == "conversation.item.input_audio_transcription.failed":
            error = payload.get("error") or {}
            self._append_call_session_log(
                source="openai",
                message=error.get("message") or "Input audio transcription failed",
                payload=payload,
            )
        if event_type == "conversation.item.created":
            item = payload.get("item") or {}
            if item.get("role") == "user":
                self._flush_user_transcription(item.get("id"), self._extract_item_transcript(payload))
        if event_type == "response.output_item.done":
            item = payload.get("item") or {}
            transcript = self._extract_item_transcript(payload)
            if item.get("role") == "assistant" and transcript:
                item_id = item.get("id")
                dedupe_key = f"assistant:{item_id}" if item_id else f"assistant:text:{transcript}"
                self._store_and_emit_conversation_line("assistant", transcript, dedupe_key=dedupe_key)
            if item.get("type") == "function_call":
                self._handle_function_call(item)
        if event_type in {
            "conversation.item.input_audio_transcription.delta",
            "conversation.item.input_audio_transcription.completed",
            "conversation.item.input_audio_transcription.failed",
            "conversation.item.created",
            "response.output_item.done",
        }:
            self._append_call_session_log(
                source="openai",
                message=f"Transcript event: {event_type}",
                payload=payload,
            )
        if event_type in {"error", "session.created", "session.updated", "response.done"}:
            logging.info("Realtime event %s for %s", event_type, self.character_slug)

    def _finish_call_session(self) -> None:
        if self.call_session_id is None:
            return
        with self.app.app_context():
            call_session = db.session.get(CallSession, self.call_session_id)
            if not call_session:
                return
            call_session.mark_finished()
            db.session.commit()

    def _handle_openai_browser_message(self, payload: dict) -> None:
        message_type = payload.get("type")

        if message_type == "call.start":
            self._create_call_session(payload)
            if not self.greeted:
                self.greeted = True
                self._send_openai(
                    {
                        "type": "response.create",
                        "response": {
                            "modalities": ["audio", "text"],
                            "instructions": (
                                f"{build_character_identity_prompt(self.character)}\n\n"
                                f"{self.character.get('greeting_prompt') or ('Начни разговор первым. Коротко поздоровайся по-русски, как будто это живой телефонный звонок, и спроси, чем помочь.')}"
                            ),
                        },
                    }
                )
            return

        if message_type == "call.stop":
            self._send_openai({"type": "input_audio_buffer.commit"})
            self._send_openai({"type": "response.cancel"})
            return

        if message_type == "client.error":
            self._append_call_session_log(
                source="client",
                message=payload.get("message") or "Unknown client error",
                payload=payload,
            )
            return

        if message_type in {
            "input_audio_buffer.append",
            "input_audio_buffer.commit",
            "input_audio_buffer.clear",
            "response.create",
            "response.cancel",
        }:
            self._send_openai(payload)
            return

        self._send_browser({"type": "warning", "message": f"Unsupported event: {message_type}"})

    def _handle_elevenlabs_browser_message(self, payload: dict) -> None:
        message_type = payload.get("type")

        if message_type == "call.start":
            self._create_call_session(payload)
            if not self.greeted:
                self.greeted = True
                self._start_elevenlabs_response(greeting=True)
            return

        if message_type == "call.stop":
            self._cancel_elevenlabs_response()
            return

        if message_type == "client.error":
            self._append_call_session_log(
                source="client",
                message=payload.get("message") or "Unknown client error",
                payload=payload,
            )
            return

        if message_type == "input_audio_buffer.clear":
            self.pending_audio_buffer.clear()
            self._send_browser({"type": "input_audio_buffer.cleared"})
            return

        if message_type == "input_audio_buffer.append":
            audio_payload = str(payload.get("audio") or "").strip()
            if audio_payload:
                self.pending_audio_buffer.extend(base64.b64decode(audio_payload))
            return

        if message_type == "input_audio_buffer.commit":
            self._commit_elevenlabs_input_audio()
            return

        if message_type == "response.create":
            self._start_elevenlabs_response(greeting=False)
            return

        if message_type == "response.cancel":
            self._cancel_elevenlabs_response()
            return

        self._send_browser({"type": "warning", "message": f"Unsupported event: {message_type}"})

    def _commit_elevenlabs_input_audio(self) -> None:
        if not self.pending_audio_buffer:
            self._send_browser({"type": "error", "message": "buffer too small"})
            return

        audio_bytes = bytes(self.pending_audio_buffer)
        self.pending_audio_buffer.clear()
        transcript_payload = transcribe_audio(
            wav_bytes=self._pcm16_to_wav(audio_bytes),
            language_code=self._transcription_language(),
            api_key=self.app.config["ELEVEN_LABS_API_KEY"],
        )
        transcript = str(transcript_payload.get("text") or "").strip()
        if transcript:
            dedupe_key = f"user:elevenlabs:{len(self.conversation_history)}"
            self._store_and_emit_conversation_line("user", transcript, dedupe_key=dedupe_key)
        self._send_browser({"type": "input_audio_buffer.committed"})

    @staticmethod
    def _pcm16_to_wav(audio_bytes: bytes, sample_rate: int = 24000) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_bytes)
        return buffer.getvalue()

    def _transcription_language(self) -> str | None:
        settings = normalize_realtime_settings(self.character.get("realtime_settings"))
        value = str(settings.get("input_transcription_language") or "").strip()
        return value or None

    def _start_elevenlabs_response(self, *, greeting: bool) -> None:
        with self.response_sequence_lock:
            self.response_sequence += 1
            response_id = self.response_sequence
        self.response_cancel_event = Event()
        self._send_browser({"type": "response.created"})
        self.response_thread = Thread(
            target=self._run_elevenlabs_response,
            args=(response_id, greeting),
            daemon=True,
        )
        self.response_thread.start()

    def _cancel_elevenlabs_response(self) -> None:
        self.response_cancel_event.set()
        self._send_browser({"type": "response.done"})

    def _run_elevenlabs_response(self, response_id: int, greeting: bool) -> None:
        try:
            reply_text = self._generate_elevenlabs_reply(greeting=greeting)
            if not reply_text:
                raise ValueError("Не удалось сгенерировать ответ.")
            reply_text, end_reason = self._extract_end_call_marker(reply_text)
            if reply_text:
                dedupe_key = f"assistant:elevenlabs:{response_id}"
                self._store_and_emit_conversation_line("assistant", reply_text, dedupe_key=dedupe_key)
            if end_reason:
                self._request_browser_end_call(end_reason)

            settings = normalize_realtime_settings(self.character.get("realtime_settings"))
            speed = settings.get("output_audio_speed")
            for chunk in stream_speech(
                text=reply_text,
                voice=str(self.character.get("voice") or "").strip(),
                output_format="pcm_24000",
                speed=float(speed) if speed not in {None, ""} else None,
                api_key=self.app.config["ELEVEN_LABS_API_KEY"],
            ):
                if self.closed or self.response_cancel_event.is_set() or response_id != self.response_sequence:
                    break
                self._send_browser(
                    {
                        "type": "response.audio.delta",
                        "delta": base64.b64encode(chunk).decode("ascii"),
                    }
                )
        except Exception as exc:
            logging.exception("ElevenLabs response loop failed")
            self._append_call_session_log(source="server", message=str(exc))
            if not self.closed:
                self._send_browser({"type": "error", "message": str(exc)})
        finally:
            if not self.closed and response_id == self.response_sequence:
                self._send_browser({"type": "response.done"})

    def _generate_elevenlabs_reply(self, *, greeting: bool) -> str:
        system_prompt = build_runtime_instructions(self.character, end_call_mode="marker")
        messages = [{"role": "system", "content": system_prompt}]
        for item in self.conversation_history[-20:]:
            messages.append({"role": item["role"], "content": item["text"]})
        if greeting:
            messages.append(
                {
                    "role": "user",
                    "content": self.character.get("greeting_prompt")
                    or "Начни разговор первым. Коротко поздоровайся по-русски и спроси, чем помочь.",
                }
            )
        return generate_chat_reply(
            api_key=self.app.config["OPENAI_API_KEY"],
            model=self.app.config["OPENAI_CHAT_MODEL"],
            messages=messages,
        )

    @staticmethod
    def _extract_end_call_marker(text: str) -> tuple[str, str | None]:
        marker = "<END_CALL:"
        function_call_pattern = re.compile(r"\b(?:end_call|endcall)\s*\((?:[^()]|\([^)]*\))*\)\s*$", re.IGNORECASE)
        function_match = function_call_pattern.search(text.strip())
        function_reason = None
        if function_match:
            function_reason = "разговор завершён"
            text = text[:function_match.start()].rstrip()
        start = text.rfind(marker)
        if start == -1:
            return text.strip(), function_reason
        end = text.find(">", start)
        if end == -1:
            return text.strip(), function_reason
        reason = text[start + len(marker):end].strip() or "разговор завершён"
        cleaned = f"{text[:start]}{text[end + 1:]}".strip()
        return cleaned, reason

    def _handle_browser_message(self, raw_message: str) -> None:
        payload = json.loads(raw_message)
        if self.provider == "elevenlabs":
            self._handle_elevenlabs_browser_message(payload)
            return
        self._handle_openai_browser_message(payload)

    def pump_browser_loop(self) -> None:
        try:
            while not self.closed:
                raw_message = self.browser_ws.receive()
                if raw_message is None:
                    break
                self._handle_browser_message(raw_message)
        except BrowserConnectionClosed as exc:
            logging.info(
                "Browser websocket closed for %s: %s",
                self.character_slug,
                self._format_browser_close(exc),
            )
        except Exception as exc:
            logging.exception("Browser websocket loop failed")
            self._append_call_session_log(source="server", message=str(exc))
            with suppress(Exception):
                self._send_browser({"type": "error", "message": str(exc)})
        finally:
            self.close()

    def pump_openai_to_browser(self) -> None:
        try:
            while not self.closed and self.openai_ws:
                try:
                    raw_message = self.openai_ws.recv()
                except WebSocketTimeoutException:
                    continue
                if not raw_message:
                    break
                with suppress(Exception):
                    payload = json.loads(raw_message)
                    self._handle_openai_event(payload)
                self.browser_ws.send(raw_message)
        except BrowserConnectionClosed as exc:
            logging.info(
                "Browser websocket already closed while streaming %s: %s",
                self.character_slug,
                self._format_browser_close(exc),
            )
        except WebSocketConnectionClosedException:
            logging.info("OpenAI websocket closed for %s", self.character_slug)
        except Exception as exc:
            logging.exception("OpenAI websocket loop failed")
            self._append_call_session_log(source="server", message=str(exc))
            with suppress(Exception):
                self._send_browser({"type": "error", "message": str(exc)})
        finally:
            self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.response_cancel_event.set()
        for item_id in list(self.pending_user_transcripts):
            self._flush_user_transcription(item_id)
        with suppress(Exception):
            if self.openai_ws:
                self.openai_ws.close()
        with suppress(Exception):
            self.browser_ws.close()
        with suppress(Exception):
            self._finish_call_session()
        logging.info("Call closed for %s", self.character_slug)

    def serve(self) -> None:
        if not self.character:
            self.browser_ws.send(json.dumps({"type": "error", "message": "Unknown character"}))
            self.browser_ws.close()
            return

        if self.provider == "elevenlabs":
            if not self.app.config["ELEVEN_LABS_API_KEY"]:
                self.browser_ws.send(json.dumps({"type": "error", "message": "ELEVEN_LABS_API_KEY is not configured"}))
                self.browser_ws.close()
                return
            if not self.app.config["OPENAI_API_KEY"]:
                self.browser_ws.send(
                    json.dumps(
                        {
                            "type": "error",
                            "message": "OPENAI_API_KEY is required for ElevenLabs conversation generation.",
                        }
                    )
                )
                self.browser_ws.close()
                return
            if not str(self.character.get("voice") or "").strip():
                self.browser_ws.send(json.dumps({"type": "error", "message": "Hero voice is not configured."}))
                self.browser_ws.close()
                return
            self.connect()
            self.pump_browser_loop()
            return

        if not self.app.config["OPENAI_API_KEY"]:
            self.browser_ws.send(json.dumps({"type": "error", "message": "OPENAI_API_KEY is not configured"}))
            self.browser_ws.close()
            return

        self.connect()
        upstream_thread = Thread(target=self.pump_openai_to_browser, daemon=True)
        upstream_thread.start()
        self.pump_browser_loop()
