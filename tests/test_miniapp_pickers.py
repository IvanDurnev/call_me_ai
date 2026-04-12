from __future__ import annotations

import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
