from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime
from decimal import Decimal
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from flask import Flask

from app.extensions import db
from app.models import AppUser, PricingPlan, SubscriptionPurchase
from app.routes import APP_USER_SESSION_KEY, _apply_pricing_plan_payload, main_bp


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


class CloudPaymentsRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret",
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            CLOUDPAYMENTS_PUBLIC_ID="pk_test",
            CLOUDPAYMENTS_API_PASSWORD="cp_secret",
        )
        db.init_app(self.app)
        self.app.register_blueprint(main_bp)
        with self.app.app_context():
            db.create_all()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _signature_headers(self, raw_body: bytes) -> dict[str, str]:
        signature = base64.b64encode(
            hmac.new(
                self.app.config["CLOUDPAYMENTS_API_PASSWORD"].encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).digest()
        ).decode("ascii")
        return {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Content-HMAC": signature,
        }

    def test_pay_webhook_marks_purchase_paid_and_stores_subscription_state(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="user@example.com",
                phone="+79990000000",
                name="Test User",
                consent_to_personal_data=True,
                email_verified=True,
            )
            plan = PricingPlan(
                code="unlimited-30",
                name="Unlimited 30",
                description="Unlimited plan",
                kind="unlimited",
                price=Decimal("199.00"),
                currency="RUB",
                period_days=30,
                sort_order=0,
                is_active=True,
            )
            db.session.add_all([user, plan])
            db.session.flush()
            user_id = user.id
            purchase = SubscriptionPurchase(
                app_user_id=user.id,
                invoice_id="inv-1",
                plan_code=plan.code,
                plan_name=plan.name,
                amount=plan.price,
                currency=plan.currency,
                status="created",
                recurring_interval="Day",
                recurring_period=30,
                provider_payload_json={"pricing_plan": {"code": plan.code, "kind": plan.kind, "period_days": 30}},
            )
            db.session.add(purchase)
            db.session.commit()

        raw_body = urlencode(
            [
                ("InvoiceId", "inv-1"),
                ("TransactionId", "tx-1"),
                ("AccountId", str(user_id)),
                ("Amount", "199.00"),
                ("Currency", "RUB"),
                ("SubscriptionId", "sub_1"),
                ("Status", "Active"),
                ("NextTransactionDateIso", "2026-05-10T10:00:00Z"),
            ]
        ).encode("utf-8")
        response = self.client.post(
            "/api/cloudpayments/webhooks/pay",
            data=raw_body,
            headers=self._signature_headers(raw_body),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"code": 0})
        with self.app.app_context():
            purchase = SubscriptionPurchase.query.filter_by(invoice_id="inv-1").first()
            self.assertIsNotNone(purchase)
            self.assertEqual(purchase.status, "paid")
            self.assertEqual(purchase.transaction_id, "tx-1")
            self.assertEqual(purchase.cloudpayments_subscription_id, "sub_1")
            self.assertEqual(purchase.subscription_status, "Active")
            self.assertEqual(purchase.next_transaction_at, datetime(2026, 5, 10, 10, 0, 0))
            self.assertIsNotNone(purchase.paid_at)

    def test_confirm_reads_subscription_fields_from_nested_cloudpayments_response(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="nested@example.com",
                phone="+79992222222",
                name="Nested User",
                consent_to_personal_data=True,
                email_verified=True,
            )
            plan = PricingPlan(
                code="unlimited-30",
                name="Unlimited 30",
                description="Unlimited plan",
                kind="unlimited",
                price=Decimal("199.00"),
                currency="RUB",
                period_days=30,
                sort_order=0,
                is_active=True,
            )
            db.session.add_all([user, plan])
            db.session.flush()
            user_id = user.id
            purchase = SubscriptionPurchase(
                app_user_id=user.id,
                invoice_id="inv-confirm-1",
                plan_code=plan.code,
                plan_name=plan.name,
                amount=plan.price,
                currency=plan.currency,
                status="created",
                recurring_interval="Day",
                recurring_period=30,
                provider_payload_json={"pricing_plan": {"code": plan.code, "kind": plan.kind, "period_days": 30}},
            )
            db.session.add(purchase)
            db.session.commit()

        with self.client.session_transaction() as session:
            session[APP_USER_SESSION_KEY] = user_id

        with patch(
            "app.routes.find_payment",
            return_value={
                "Model": {
                    "Status": "Completed",
                    "TransactionId": "tx-confirm-1",
                    "SubscriptionId": "sub-confirm-1",
                    "SubscriptionStatus": "Active",
                    "NextTransactionDateIso": "2026-05-10T11:30:00Z",
                }
            },
        ):
            response = self.client.post("/api/account/subscription/confirm", json={"invoiceId": "inv-confirm-1"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        with self.app.app_context():
            purchase = SubscriptionPurchase.query.filter_by(invoice_id="inv-confirm-1").first()
            self.assertIsNotNone(purchase)
            self.assertEqual(purchase.status, "paid")
            self.assertEqual(purchase.transaction_id, "tx-confirm-1")
            self.assertEqual(purchase.cloudpayments_subscription_id, "sub-confirm-1")
            self.assertEqual(purchase.subscription_status, "Active")
            self.assertEqual(purchase.next_transaction_at, datetime(2026, 5, 10, 11, 30, 0))

    def test_account_cancel_disables_autorenew_for_active_subscription(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="member@example.com",
                phone="+79991111111",
                name="Member",
                consent_to_personal_data=True,
                email_verified=True,
            )
            plan = PricingPlan(
                code="unlimited-30",
                name="Unlimited 30",
                description="Unlimited plan",
                kind="unlimited",
                price=Decimal("199.00"),
                currency="RUB",
                period_days=30,
                sort_order=0,
                is_active=True,
            )
            db.session.add_all([user, plan])
            db.session.flush()
            user_id = user.id
            purchase = SubscriptionPurchase(
                app_user_id=user.id,
                invoice_id="inv-cancel-1",
                plan_code=plan.code,
                plan_name=plan.name,
                amount=plan.price,
                currency=plan.currency,
                status="paid",
                paid_at=datetime(2026, 4, 10, 8, 0, 0),
                cloudpayments_subscription_id="sub_cancel",
                subscription_status="Active",
                recurring_interval="Day",
                recurring_period=30,
                next_transaction_at=datetime(2026, 5, 10, 8, 0, 0),
                provider_payload_json={"pricing_plan": {"code": plan.code, "kind": plan.kind, "period_days": 30}},
            )
            db.session.add(purchase)
            db.session.commit()

        with self.client.session_transaction() as session:
            session[APP_USER_SESSION_KEY] = user_id

        with patch("app.routes.cancel_cloudpayments_subscription", return_value={"Success": True}):
            response = self.client.post("/api/account/subscription/cancel", json={})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        with self.app.app_context():
            purchase = SubscriptionPurchase.query.filter_by(invoice_id="inv-cancel-1").first()
            self.assertEqual(purchase.subscription_status, "Canceled")
            self.assertIsNotNone(purchase.canceled_at)
            self.assertIsNone(purchase.next_transaction_at)


if __name__ == "__main__":
    unittest.main()
