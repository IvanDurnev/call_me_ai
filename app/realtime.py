from __future__ import annotations

import json
import logging
from contextlib import suppress
from datetime import datetime
from threading import Lock, Thread

from flask import current_app
from simple_websocket.errors import ConnectionClosed as BrowserConnectionClosed
from websocket import WebSocket, WebSocketConnectionClosedException, WebSocketTimeoutException, create_connection

from .characters import build_character_identity_prompt, build_realtime_session_config, get_character
from .extensions import db
from .models import AppUser, CallSession
from .services.billing import build_user_access_state
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
        self.openai_ws: WebSocket | None = None
        self.browser_send_lock = Lock()
        self.openai_send_lock = Lock()
        self.closed = False
        self.call_session_id: int | None = None
        self.greeted = False
        self.pending_user_transcripts: dict[str, str] = {}
        self.seen_conversation_keys: set[str] = set()
        self.end_call_requested = False

    @staticmethod
    def _format_browser_close(exc: BrowserConnectionClosed) -> str:
        return f"code={exc.reason or 'unknown'} message={exc.message or ''}".strip()

    def connect(self) -> None:
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
        self._send_browser(
            {
                "type": "call.ready",
                "character": {
                    "slug": self.character["slug"],
                    "name": self.character["name"],
                    "emoji": self.character["emoji"],
                    "avatar_path": self.character.get("avatar_path"),
                },
            }
        )
        logging.info("Realtime session connected for %s", self.character_slug)

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
        if self.call_session_id is None:
            if dedupe_key:
                self.seen_conversation_keys.add(dedupe_key)
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
        if dedupe_key:
            self.seen_conversation_keys.add(dedupe_key)
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

    def _handle_browser_message(self, raw_message: str) -> None:
        payload = json.loads(raw_message)
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

    def pump_browser_to_openai(self) -> None:
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

        if not self.app.config["OPENAI_API_KEY"]:
            self.browser_ws.send(json.dumps({"type": "error", "message": "OPENAI_API_KEY is not configured"}))
            self.browser_ws.close()
            return

        self.connect()
        upstream_thread = Thread(target=self.pump_openai_to_browser, daemon=True)
        upstream_thread.start()
        self.pump_browser_to_openai()
