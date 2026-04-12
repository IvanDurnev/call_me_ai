from __future__ import annotations

import unittest

from flask import Flask

from app.characters import DEFAULT_HEROES, ensure_default_heroes
from app.extensions import db
from app.models import Hero


class EnsureDefaultHeroesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret",
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_seeds_defaults_into_empty_database(self) -> None:
        with self.app.app_context():
            ensure_default_heroes()

            heroes = Hero.query.order_by(Hero.sort_order.asc()).all()
            self.assertEqual(len(heroes), len(DEFAULT_HEROES))
            self.assertEqual(heroes[0].slug, DEFAULT_HEROES[0]["slug"])

    def test_does_not_recreate_deleted_default_hero_when_db_not_empty(self) -> None:
        with self.app.app_context():
            ensure_default_heroes()
            deleted_slug = DEFAULT_HEROES[0]["slug"]

            hero = Hero.query.filter_by(slug=deleted_slug).first()
            self.assertIsNotNone(hero)
            db.session.delete(hero)
            db.session.commit()

            ensure_default_heroes()

            self.assertIsNone(Hero.query.filter_by(slug=deleted_slug).first())


if __name__ == "__main__":
    unittest.main()
