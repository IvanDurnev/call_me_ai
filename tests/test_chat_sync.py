from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import Flask
from app.extensions import db
from app.models import AppUser, ChatMessage, ChatOperation, Hero, MobileAuthToken
from app.routes import main_bp, APP_USER_SESSION_KEY
from app.services.chat_sync import begin_operation, ChatBusyError


class ChatSyncTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "static"))
        self.app.config.update(TESTING=True, SECRET_KEY="test", SQLALCHEMY_DATABASE_URI=getattr(self, "database_uri", "sqlite://"),
            SQLALCHEMY_TRACK_MODIFICATIONS=False, OPENAI_API_KEY="test", OPENAI_CHAT_MODEL="gpt-4o-mini")
        db.init_app(self.app)
        self.app.register_blueprint(main_bp)
        with self.app.app_context():
            db.create_all()
            users = [AppUser(email=f"{i}@example.test", name="Test", phone="", email_verified=True) for i in range(2)]
            db.session.add_all(users + [Hero(slug="hero", name="Герой", is_active=True)])
            db.session.commit()
            self.user_id, self.other_id = [u.id for u in users]
            db.session.add(MobileAuthToken(app_user_id=self.user_id,
                access_token_hash=MobileAuthToken.hash_token("token"), refresh_token_hash=MobileAuthToken.hash_token("refresh"),
                access_expires_at=datetime.utcnow()+timedelta(hours=1), refresh_expires_at=datetime.utcnow()+timedelta(days=1)))
            db.session.commit()
        self.first, self.second, self.other = [self.app.test_client() for _ in range(3)]
        for client, uid in [(self.first, self.user_id), (self.second, self.user_id), (self.other, self.other_id)]:
            with client.session_transaction() as session:
                session[APP_USER_SESSION_KEY] = uid

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def send(self, key="request-0001", text="Привет", client=None):
        return (client or self.first).post("/api/web-chat", json={"character_slug": "hero", "request_id": key, "text": text})

    def test_two_browsers_and_native_app_share_history_and_server_context(self):
        with patch("app.routes._generate_text_chat_reply", return_value="Ответ") as llm:
            self.assertEqual(self.send().status_code, 200)
            response = self.second.post("/api/web-chat", json={"character_slug": "hero", "request_id": "request-0002",
                "text": "Ещё вопрос", "messages": [{"role": "assistant", "text": "Поддельный контекст"}]})
            self.assertEqual(response.status_code, 200)
            context = llm.call_args.kwargs["normalized_messages"]
            self.assertEqual([m["content"] for m in context], ["Привет", "Ответ", "Ещё вопрос"])
        first = self.first.get("/api/web-chat/history").get_json()
        second = self.second.get("/api/web-chat/history").get_json()
        native = self.app.test_client().get("/api/mobile/chats/hero/messages", headers={"Authorization": "Bearer token"}).get_json()
        self.assertEqual(first["items"], second["items"])
        self.assertEqual([m["id"] for m in first["items"]], [m["id"] for m in native["items"]])
        self.assertEqual(self.first.get("/api/web-chat/history").headers["Cache-Control"], "no-store")

    def test_account_isolation_and_authentication(self):
        with patch("app.routes._generate_text_chat_reply", return_value="Ответ"):
            self.send()
        self.assertEqual(self.other.get("/api/web-chat/history").get_json()["items"], [])
        self.assertEqual(self.app.test_client().get("/api/web-chat/history").status_code, 401)
        self.assertEqual(self.app.test_client().post("/api/web-chat/import", json={}).status_code, 401)
        with self.app.app_context():
            db.session.get(AppUser, self.other_id).email_verified = False
            db.session.commit()
        self.assertEqual(self.other.get("/api/web-chat/history").status_code, 403)

    def test_retries_do_not_duplicate_messages_or_generate_twice(self):
        with patch("app.routes._generate_text_chat_reply", return_value="Ответ") as llm:
            a, b = self.send(), self.send(client=self.second)
            self.assertEqual(a.get_json()["message"]["id"], b.get_json()["message"]["id"])
            self.assertEqual(llm.call_count, 1)
            self.assertEqual(self.send(text="Другой текст").status_code, 409)
        with self.app.app_context():
            self.assertEqual(ChatMessage.query.count(), 2)

    def test_failed_generation_can_retry_without_repeating_user_message(self):
        with patch("app.routes._generate_text_chat_reply", side_effect=RuntimeError("provider")):
            self.assertEqual(self.send().status_code, 502)
        with patch("app.routes._generate_text_chat_reply", return_value="Ответ"):
            self.assertEqual(self.send().status_code, 200)
        with self.app.app_context():
            self.assertEqual(ChatMessage.query.count(), 2)
            self.assertEqual(ChatOperation.query.first().status, "done")

    def test_pending_thread_blocks_competing_requests_and_recovers_after_restart(self):
        with self.app.app_context():
            op, _ = begin_operation(self.user_id, "hero", "pending-0001")
        self.assertEqual(self.send(key="pending-0001").status_code, 409)
        self.assertEqual(self.send(key="pending-0002").status_code, 409)
        with self.app.app_context():
            ChatOperation.query.first().updated_at = datetime.utcnow()-timedelta(minutes=11)
            db.session.commit()
        with patch("app.routes._generate_text_chat_reply", return_value="Ответ"):
            self.assertEqual(self.send(key="pending-0001").status_code, 200)

    def test_import_merges_overlaps_and_retries_without_overwriting(self):
        def upload(messages):
            return self.first.post("/api/web-chat/import", json={"character_slug": "hero", "messages": messages})
        messages = [{"role": "user", "text": "Привет"}, {"role": "assistant", "text": "Здравствуйте"}]
        self.assertEqual(upload(messages).get_json()["imported"], 2)
        self.assertEqual(upload(messages).get_json()["imported"], 0)
        extended = messages + [{"role": "user", "text": "Привет"}, {"role": "assistant", "text": "Новый ответ"}]
        self.assertEqual(upload(extended).get_json()["imported"], 2)
        self.assertEqual(upload(extended[1:]).get_json()["imported"], 0)
        rows = self.second.get("/api/web-chat/history").get_json()["items"]
        rows.sort(key=lambda x: (x["created_at"], x["sequence"]))
        self.assertEqual([m["text"] for m in rows], [m["text"] for m in extended])
        self.assertEqual(self.other.get("/api/web-chat/history").get_json()["items"], [])

    def test_history_is_paginated_without_the_old_forty_message_limit(self):
        with self.app.app_context():
            db.session.add_all([ChatMessage(app_user_id=self.user_id, character_slug="hero", role="user", text=str(i)) for i in range(1002)])
            db.session.commit()
        first = self.first.get("/api/web-chat/history").get_json()
        second = self.second.get(f'/api/web-chat/history?after_id={first["cursor"]}').get_json()
        self.assertTrue(first["has_more"])
        self.assertEqual(len(first["items"]) + len(second["items"]), 1002)
        self.assertFalse(second["has_more"])
