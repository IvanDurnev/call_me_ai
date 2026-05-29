from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from app.routes import main_bp


class MiniappPickerVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(project_root / "templates"),
            static_folder=str(project_root / "static"),
        )
        self.app.config.update(
            SECRET_KEY="test-secret",
            TESTING=True,
            PUBLIC_BASE_URL="https://example.test",
        )
        self.app.register_blueprint(main_bp)
        self.client = self.app.test_client()

    def test_telegram_picker_shows_only_active_characters(self) -> None:
        characters = [
            {
                "slug": "active-hero",
                "name": "Active Hero",
                "description": "Visible character",
                "emoji": "A",
                "is_active": True,
            },
            {
                "slug": "inactive-hero",
                "name": "Inactive Hero",
                "description": "Should be hidden",
                "emoji": "I",
                "is_active": False,
            },
            {
                "slug": "inactive-hero-string",
                "name": "Inactive Hero String",
                "description": "Should be hidden",
                "emoji": "S",
                "is_active": "false",
            },
        ]

        with patch("app.routes.list_characters", return_value=characters):
            response = self.client.get("/miniapp")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Active Hero", html)
        self.assertNotIn("Inactive Hero", html)
        self.assertNotIn("Inactive Hero String", html)

    def test_max_picker_shows_only_active_characters(self) -> None:
        characters = [
            {
                "slug": "active-hero",
                "name": "Active Hero",
                "description": "Visible character",
                "emoji": "A",
                "is_active": True,
            },
            {
                "slug": "inactive-hero",
                "name": "Inactive Hero",
                "description": "Should be hidden",
                "emoji": "I",
                "is_active": False,
            },
            {
                "slug": "inactive-hero-zero",
                "name": "Inactive Hero Zero",
                "description": "Should be hidden",
                "emoji": "Z",
                "is_active": "0",
            },
        ]

        with patch("app.routes.list_characters", return_value=characters):
            response = self.client.get("/max/miniapp")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Active Hero", html)
        self.assertNotIn("Inactive Hero", html)
        self.assertNotIn("Inactive Hero Zero", html)

    def test_index_uses_web_character_links_without_telegram_source(self) -> None:
        characters = [
            {
                "slug": "active-hero",
                "name": "Active Hero",
                "description": "Visible character",
                "emoji": "A",
                "is_active": True,
            }
        ]

        with patch("app.routes.list_characters", return_value=characters):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('href="/miniapp/active-hero"', html)
        self.assertNotIn("source=telegram-miniapp", html)

    def test_anonymous_web_miniapp_redirects_to_login(self) -> None:
        response = self.client.get("/miniapp/active-hero")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/email", response.headers["Location"])

    def test_anonymous_web_messenger_redirects_to_login(self) -> None:
        response = self.client.get("/web")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/email", response.headers["Location"])

    def test_web_messenger_renders_single_page_chat_switcher(self) -> None:
        characters = [
            {
                "slug": "active-hero",
                "name": "Active Hero",
                "description": "Visible character",
                "emoji": "A",
                "is_active": True,
            },
            {
                "slug": "second-hero",
                "name": "Second Hero",
                "description": "Second visible character",
                "emoji": "B",
                "is_active": True,
            },
        ]

        with (
            patch("app.routes._current_app_user", return_value=SimpleNamespace(id=7, email="user@example.com")),
            patch("app.routes._app_user_ready_for_calls", return_value=True),
            patch("app.routes._app_user_access_state", return_value={"has_call_access": True}),
            patch("app.routes.list_characters", return_value=characters),
        ):
            response = self.client.get("/web")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Chats", html)
        self.assertIn("web-characters-data", html)
        self.assertIn("Second Hero", html)
        self.assertIn('class="web-contact-card is-active"', html)
        self.assertNotIn('href="/web/active-hero"', html)

    def test_web_slug_redirects_to_single_web_route(self) -> None:
        response = self.client.get("/web/second-hero", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/web")

    def test_web_chat_api_returns_character_reply(self) -> None:
        with (
            patch("app.routes._current_app_user", return_value=SimpleNamespace(id=7, email="user@example.com")),
            patch("app.routes._app_user_ready_for_calls", return_value=True),
            patch("app.routes.get_character", return_value={"slug": "active-hero", "name": "Active Hero", "description": "Visible character"}),
            patch("app.routes.generate_chat_reply", return_value="Привет! Я рядом.") as generate_reply,
        ):
            self.app.config["OPENAI_API_KEY"] = "test-key"
            self.app.config["OPENAI_CHAT_MODEL"] = "gpt-4o-mini"
            response = self.client.post(
                "/api/web-chat",
                json={
                    "character_slug": "active-hero",
                    "messages": [{"role": "user", "text": "Привет"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["message"]["text"], "Привет! Я рядом.")
        generate_reply.assert_called_once()

    def test_web_chat_api_requires_last_user_message(self) -> None:
        with (
            patch("app.routes._current_app_user", return_value=SimpleNamespace(id=7, email="user@example.com")),
            patch("app.routes._app_user_ready_for_calls", return_value=True),
            patch("app.routes.get_character", return_value={"slug": "active-hero", "name": "Active Hero", "description": "Visible character"}),
        ):
            self.app.config["OPENAI_API_KEY"] = "test-key"
            response = self.client.post(
                "/api/web-chat",
                json={
                    "character_slug": "active-hero",
                    "messages": [{"role": "assistant", "text": "Привет"}],
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Last message must be from the user", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
