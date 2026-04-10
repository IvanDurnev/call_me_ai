from __future__ import annotations

import unittest

from flask import Flask

from app.routes import main_bp


class RedirectRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.app.register_blueprint(main_bp)
        self.client = self.app.test_client()

    def test_ru_redirects_to_index(self) -> None:
        response = self.client.get("/ru", follow_redirects=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "/")

    def test_ru_with_trailing_slash_redirects_to_index(self) -> None:
        response = self.client.get("/ru/", follow_redirects=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "/")


if __name__ == "__main__":
    unittest.main()
