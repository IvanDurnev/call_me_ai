from __future__ import annotations

import unittest

from flask import Flask

from app.models import PricingPlan
from app.routes import _apply_pricing_plan_payload, main_bp


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


class PricingPlanPayloadTests(unittest.TestCase):
    def test_apply_pricing_plan_payload_keeps_zero_sort_order(self) -> None:
        plan = PricingPlan(
            code="starter",
            name="Starter",
            description="",
            kind="call_package",
            price=1,
            currency="RUB",
            calls_included=15,
            period_days=None,
            sort_order=0,
            is_active=True,
        )

        _apply_pricing_plan_payload(
            plan,
            {
                "name": "Starter",
                "kind": "call_package",
                "price": "99",
                "currency": "RUB",
                "minutes_included": "15",
                "is_active": True,
            },
        )

        self.assertEqual(plan.sort_order, 0)


if __name__ == "__main__":
    unittest.main()
