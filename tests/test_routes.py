from __future__ import annotations

import base64
import hashlib
import hmac
import io
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from flask import Flask

from app.extensions import db, mail
from app.models import AdminUser, AppUser, CallSession, ChatMessage, EmailCode, Hero, MobileAuthToken, PricingPlan, SubscriptionPurchase
from app.routes import (
    ADMIN_SESSION_KEY,
    APP_USER_SESSION_KEY,
    _apply_pricing_plan_payload,
    _build_runtime_diagnostics,
    _sync_pending_subscription_purchases,
    main_bp,
)
from app.services.recurring import process_due_recurring_purchases


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


class AdminUsersRoutesTests(unittest.TestCase):
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
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        mail.init_app(self.app)
        self.app.register_blueprint(main_bp)
        with self.app.app_context():
            db.create_all()
            admin = AdminUser(username="admin", is_active=True)
            admin.set_password("secret")
            user = AppUser(
                email="admin-users@example.com",
                phone="+79998887766",
                name="Admin Users Test",
                consent_to_personal_data=True,
                email_verified=True,
            )
            db.session.add_all([admin, user])
            db.session.commit()
            self.admin_id = admin.id
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_admin_users_requires_auth(self) -> None:
        response = self.client.get("/admin/users", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.headers["Location"])

    def test_admin_users_page_shows_users_table(self) -> None:
        with self.client.session_transaction() as session:
            session[ADMIN_SESSION_KEY] = self.admin_id

        response = self.client.get("/admin/users")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Список пользователей", html)
        self.assertIn("admin-users@example.com", html)


class CloudPaymentsRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret",
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            PUBLIC_BASE_URL="https://example.test",
            CLOUDPAYMENTS_PUBLIC_ID="pk_test",
            CLOUDPAYMENTS_API_PASSWORD="cp_secret",
        )
        db.init_app(self.app)
        mail.init_app(self.app)
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
                ("Token", "tok_1"),
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
            self.assertEqual(purchase.cloudpayments_token, "tok_1")
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
                    "Token": "tok-confirm-1",
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
            self.assertEqual(purchase.cloudpayments_token, "tok-confirm-1")
            self.assertEqual(purchase.cloudpayments_subscription_id, "sub-confirm-1")
            self.assertEqual(purchase.subscription_status, "Active")
            self.assertEqual(purchase.next_transaction_at, datetime(2026, 5, 10, 11, 30, 0))

    def test_checkout_returns_recurrent_payload_for_unlimited_plan(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="checkout@example.com",
                phone="+79990000001",
                name="Checkout User",
                consent_to_personal_data=True,
                email_verified=True,
            )
            plan = PricingPlan(
                code="unlimited-7",
                name="Unlimited 7",
                description="Unlimited weekly",
                kind="unlimited",
                price=Decimal("999.00"),
                currency="RUB",
                period_days=7,
                sort_order=0,
                is_active=True,
            )
            db.session.add_all([user, plan])
            db.session.commit()
            user_id = user.id

        with self.client.session_transaction() as session:
            session[APP_USER_SESSION_KEY] = user_id

        response = self.client.post(
            "/api/account/subscription/checkout",
            json={
                "plan_code": "unlimited-7",
                "legal_consent": True,
                "recurring_terms_consent": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        checkout = payload["checkout"]
        self.assertIn("recurrent", checkout)
        self.assertEqual(checkout["recurrent"]["interval"], "Day")
        self.assertEqual(checkout["recurrent"]["period"], 7)
        self.assertTrue(checkout["recurrent"]["startDateIso"].endswith("Z"))
        with self.app.app_context():
            purchase = SubscriptionPurchase.query.filter_by(invoice_id=checkout["invoiceId"]).first()
            self.assertIsNotNone(purchase)
            self.assertEqual(purchase.recurring_interval, "Day")
            self.assertEqual(purchase.recurring_period, 7)
            self.assertEqual((purchase.provider_payload_json or {}).get("recurrent", {}).get("period"), 7)
            autopay_consent = (purchase.provider_payload_json or {}).get("autopay_consent", {})
            self.assertTrue((autopay_consent.get("legal_consent") or {}).get("accepted"))
            self.assertTrue((autopay_consent.get("recurring_terms_consent") or {}).get("accepted"))

    def test_checkout_requires_both_consents_for_unlimited_plan(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="checkout-consents@example.com",
                phone="+79990000011",
                name="Checkout Consents User",
                consent_to_personal_data=True,
                email_verified=True,
            )
            plan = PricingPlan(
                code="unlimited-7-consents",
                name="Unlimited 7 Consents",
                description="Unlimited weekly",
                kind="unlimited",
                price=Decimal("999.00"),
                currency="RUB",
                period_days=7,
                sort_order=0,
                is_active=True,
            )
            db.session.add_all([user, plan])
            db.session.commit()
            user_id = user.id

        with self.client.session_transaction() as session:
            session[APP_USER_SESSION_KEY] = user_id

        response_missing_legal = self.client.post(
            "/api/account/subscription/checkout",
            json={"plan_code": "unlimited-7-consents", "recurring_terms_consent": True},
        )
        self.assertEqual(response_missing_legal.status_code, 400)
        self.assertIn("правовыми документами", response_missing_legal.get_json()["error"])

        response_missing_recurring = self.client.post(
            "/api/account/subscription/checkout",
            json={"plan_code": "unlimited-7-consents", "legal_consent": True},
        )
        self.assertEqual(response_missing_recurring.status_code, 400)
        self.assertIn("автоматических продлений", response_missing_recurring.get_json()["error"])

    def test_sync_pending_purchase_updates_status_from_cloudpayments(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="sync@example.com",
                phone="+79990000002",
                name="Sync User",
                consent_to_personal_data=True,
                email_verified=True,
            )
            plan = PricingPlan(
                code="unlimited-30",
                name="Unlimited 30",
                description="Unlimited monthly",
                kind="unlimited",
                price=Decimal("1999.00"),
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
                invoice_id="inv-sync-1",
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
            user_id = user.id

        with patch(
            "app.routes.find_payment",
            return_value={
                "Model": {
                    "Status": "Completed",
                    "TransactionId": "tx-sync-1",
                    "Token": "tok-sync-1",
                    "SubscriptionId": "sub-sync-1",
                    "SubscriptionStatus": "Active",
                    "NextTransactionDateIso": "2026-05-10T10:00:00Z",
                    "ConfirmDateIso": "2026-04-10T10:00:00Z",
                }
            },
        ):
            with self.app.app_context():
                user = AppUser.query.filter_by(id=user_id).first()
                self.assertIsNotNone(user)
                _sync_pending_subscription_purchases(user)

        with self.app.app_context():
            purchase = SubscriptionPurchase.query.filter_by(invoice_id="inv-sync-1").first()
            self.assertIsNotNone(purchase)
            self.assertEqual(purchase.status, "paid")
            self.assertEqual(purchase.transaction_id, "tx-sync-1")
            self.assertEqual(purchase.cloudpayments_token, "tok-sync-1")
            self.assertEqual(purchase.cloudpayments_subscription_id, "sub-sync-1")
            self.assertEqual(purchase.subscription_status, "Active")
            self.assertEqual(purchase.next_transaction_at, datetime(2026, 5, 10, 10, 0, 0))
            self.assertIsNotNone(purchase.paid_at)

    def test_confirm_marks_authorized_payment_as_paid(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="authorized@example.com",
                phone="+79993333333",
                name="Authorized User",
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
                invoice_id="inv-authorized-1",
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
                    "Status": "Authorized",
                    "TransactionId": "tx-authorized-1",
                    "Token": "tok-authorized-1",
                    "SubscriptionId": "sub-authorized-1",
                    "NextTransactionDateIso": "2026-05-10T12:00:00Z",
                }
            },
        ):
            response = self.client.post("/api/account/subscription/confirm", json={"invoiceId": "inv-authorized-1"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        with self.app.app_context():
            purchase = SubscriptionPurchase.query.filter_by(invoice_id="inv-authorized-1").first()
            self.assertIsNotNone(purchase)
            self.assertEqual(purchase.status, "paid")
            self.assertEqual(purchase.transaction_id, "tx-authorized-1")
            self.assertEqual(purchase.cloudpayments_token, "tok-authorized-1")
            self.assertEqual(purchase.cloudpayments_subscription_id, "sub-authorized-1")
            self.assertEqual(purchase.next_transaction_at, datetime(2026, 5, 10, 12, 0, 0))
            self.assertIsNotNone(purchase.paid_at)

    def test_confirm_uses_moscow_timezone_for_naive_confirm_date(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="tz@example.com",
                phone="+79990000003",
                name="TZ User",
                consent_to_personal_data=True,
                email_verified=True,
            )
            plan = PricingPlan(
                code="unlimited-7",
                name="Unlimited 7",
                description="Unlimited weekly",
                kind="unlimited",
                price=Decimal("999.00"),
                currency="RUB",
                period_days=7,
                sort_order=0,
                is_active=True,
            )
            db.session.add_all([user, plan])
            db.session.flush()
            user_id = user.id
            purchase = SubscriptionPurchase(
                app_user_id=user.id,
                invoice_id="inv-tz-1",
                plan_code=plan.code,
                plan_name=plan.name,
                amount=plan.price,
                currency=plan.currency,
                status="created",
                recurring_interval="Day",
                recurring_period=7,
                provider_payload_json={"pricing_plan": {"code": plan.code, "kind": plan.kind, "period_days": 7}},
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
                    "TransactionId": "tx-tz-1",
                    "Token": "tok-tz-1",
                    "SubscriptionId": "sub-tz-1",
                    "ConfirmDateIso": "2026-04-12T12:11:11",
                }
            },
        ):
            response = self.client.post("/api/account/subscription/confirm", json={"invoiceId": "inv-tz-1"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        with self.app.app_context():
            purchase = SubscriptionPurchase.query.filter_by(invoice_id="inv-tz-1").first()
            self.assertIsNotNone(purchase)
            # 12:11:11 Moscow -> 09:11:11 UTC
            self.assertEqual(purchase.paid_at, datetime(2026, 4, 12, 9, 11, 11))

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
                cloudpayments_token="tok_cancel",
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
            history = (purchase.provider_payload_json or {}).get("subscription_action_history") or []
            self.assertEqual(history[-1]["action"], "cancel_auto_renew")
            self.assertEqual(history[-1]["actor"], "user")

    def test_account_cancel_disables_local_autorenew_without_subscription_id(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="token-cancel@example.com",
                phone="+79994444444",
                name="Token Cancel",
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
                invoice_id="inv-cancel-local-1",
                plan_code=plan.code,
                plan_name=plan.name,
                amount=plan.price,
                currency=plan.currency,
                status="paid",
                paid_at=datetime(2026, 4, 10, 8, 0, 0),
                cloudpayments_token="tok_local_cancel",
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

        response = self.client.post("/api/account/subscription/cancel", json={})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        with self.app.app_context():
            purchase = SubscriptionPurchase.query.filter_by(invoice_id="inv-cancel-local-1").first()
            self.assertEqual(purchase.subscription_status, "Canceled")
            self.assertIsNotNone(purchase.canceled_at)
            history = (purchase.provider_payload_json or {}).get("subscription_action_history") or []
            self.assertEqual(history[-1]["action"], "cancel_auto_renew")

    def test_account_resume_reenables_local_autorenew(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="resume@example.com",
                phone="+79996666666",
                name="Resume User",
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
                invoice_id="inv-resume-1",
                plan_code=plan.code,
                plan_name=plan.name,
                amount=plan.price,
                currency=plan.currency,
                status="paid",
                paid_at=datetime(2026, 4, 10, 8, 0, 0),
                cloudpayments_token="tok_resume",
                subscription_status="Canceled",
                recurring_interval="Day",
                recurring_period=30,
                canceled_at=datetime(2026, 4, 11, 8, 0, 0),
                provider_payload_json={"pricing_plan": {"code": plan.code, "kind": plan.kind, "period_days": 30}},
            )
            db.session.add(purchase)
            db.session.commit()

        with self.client.session_transaction() as session:
            session[APP_USER_SESSION_KEY] = user_id

        response = self.client.post("/api/account/subscription/resume", json={})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        with self.app.app_context():
            purchase = SubscriptionPurchase.query.filter_by(invoice_id="inv-resume-1").first()
            self.assertEqual(purchase.subscription_status, "Active")
            self.assertIsNone(purchase.canceled_at)
            self.assertEqual(purchase.next_transaction_at, datetime(2026, 5, 10, 8, 0, 0))
            history = (purchase.provider_payload_json or {}).get("subscription_action_history") or []
            self.assertEqual(history[-1]["action"], "resume_auto_renew")
            self.assertEqual(history[-1]["actor"], "user")

    def test_process_due_recurring_purchases_uses_saved_token(self) -> None:
        with self.app.app_context():
            user = AppUser(
                email="renew@example.com",
                phone="+79995555555",
                name="Renew User",
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
            purchase = SubscriptionPurchase(
                app_user_id=user.id,
                invoice_id="inv-rec-source-1",
                plan_code=plan.code,
                plan_name=plan.name,
                amount=plan.price,
                currency=plan.currency,
                status="paid",
                paid_at=datetime(2026, 3, 1, 8, 0, 0),
                cloudpayments_token="tok_saved",
                subscription_status="Active",
                recurring_interval="Day",
                recurring_period=30,
                provider_payload_json={"pricing_plan": {"code": plan.code, "kind": plan.kind, "period_days": 30}},
            )
            db.session.add(purchase)
            db.session.commit()

            with patch(
                "app.services.recurring.charge_cloudpayments_token",
                return_value={"TransactionId": "tx-rec-1", "Success": True},
            ) as mocked_charge:
                messages = process_due_recurring_purchases(now=datetime(2026, 4, 10, 10, 0, 0))

            self.assertEqual(messages, [f"Подписка пользователя #{user.id}: запрос на автосписание отправлен."])
            mocked_charge.assert_called_once()
            self.assertEqual(mocked_charge.call_args.kwargs["token"], "tok_saved")
            recurring_purchase = SubscriptionPurchase.query.filter_by(invoice_id=mocked_charge.call_args.kwargs["invoice_id"]).first()
            self.assertIsNotNone(recurring_purchase)
            self.assertEqual(recurring_purchase.status, "pending")
            self.assertEqual(recurring_purchase.transaction_id, "tx-rec-1")


class CallSessionRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret",
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            PUBLIC_BASE_URL="https://example.test",
            REALTIME_API_PROVIDER="elevenlabs",
            ELEVEN_LABS_API_KEY="el-test",
            ELEVENLABS_AGENT_ID="agent-default",
            OPENAI_REALTIME_MODEL="gpt-realtime",
            OPENAI_REALTIME_VOICE="alloy",
        )
        db.init_app(self.app)
        mail.init_app(self.app)
        self.app.register_blueprint(main_bp)
        with self.app.app_context():
            db.create_all()
            admin = AdminUser(username="admin", is_active=True)
            admin.set_password("secret")
            db.session.add(admin)
            user = AppUser(
                email="caller@example.com",
                phone="+79990001122",
                name="Caller",
                consent_to_personal_data=True,
                email_verified=True,
            )
            db.session.add(user)
            hero = Hero(
                slug="domovenok-kuzya",
                name="Домовёнок Кузя",
                description="Тестовый герой",
                emoji="AI",
                voice="alloy",
                greeting_prompt="Привет, это Кузя.",
                system_prompt="Будь добрым сказочным героем.",
                is_active=True,
            )
            db.session.add(hero)
            db.session.commit()
            self.user_id = user.id
            self.admin_id = admin.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session[APP_USER_SESSION_KEY] = self.user_id

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_start_call_session_returns_signed_url_for_elevenlabs(self) -> None:
        with patch("app.routes.get_signed_url", return_value="wss://signed.example/socket"):
            response = self.client.post(
                "/api/call-sessions/start",
                json={"character_slug": "domovenok-kuzya", "started_from": "web"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "elevenlabs")
        self.assertEqual(payload["signed_url"], "wss://signed.example/socket")
        self.assertEqual(payload["conversation_initiation_client_data"]["type"], "conversation_initiation_client_data")
        self.assertEqual(payload["conversation_initiation_client_data"]["conversation_config_override"], {})
        with self.app.app_context():
            session = db.session.get(CallSession, payload["call_session_id"])
            self.assertIsNotNone(session)
            self.assertEqual(session.status, "active")

    def test_start_call_session_uses_hero_provider_override(self) -> None:
        self.app.config["REALTIME_API_PROVIDER"] = "openai"
        with self.app.app_context():
            hero = Hero.query.filter_by(slug="domovenok-kuzya").first()
            self.assertIsNotNone(hero)
            hero.realtime_settings_json = {"provider": "elevenlabs"}
            db.session.commit()

        with patch("app.routes.get_signed_url", return_value="wss://signed.example/socket"):
            response = self.client.post(
                "/api/call-sessions/start",
                json={"character_slug": "domovenok-kuzya", "started_from": "web"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "elevenlabs")
        self.assertEqual(payload["signed_url"], "wss://signed.example/socket")

    def test_runtime_diagnostics_reports_voice_and_agent_checks(self) -> None:
        with self.app.app_context():
            with patch(
                "app.routes.list_elevenlabs_voices",
                return_value=[{"voice_id": "alloy", "name": "Alloy Imported"}],
            ), patch(
                "app.routes.get_agent",
                return_value={
                    "conversation_config": {
                        "asr": {"user_input_audio_format": "pcm_16000"},
                        "tts": {"agent_output_audio_format": "pcm_16000"},
                    }
                },
            ):
                diagnostics = _build_runtime_diagnostics(
                    [
                        {
                            "slug": "domovenok-kuzya",
                            "name": "Домовёнок Кузя",
                            "voice": "alloy",
                            "realtime_settings": {"elevenlabs_agent_id": "agent-1"},
                        }
                    ]
                )

        hero_diagnostics = diagnostics["domovenok-kuzya"]
        self.assertEqual(hero_diagnostics["provider"], "elevenlabs")
        self.assertIn(hero_diagnostics["summary"], {"Ready", "Check settings"})
        labels = {item["label"] for item in hero_diagnostics["checks"]}
        self.assertIn("Voice lookup", labels)
        self.assertIn("Agent lookup", labels)

    def test_test_hero_agent_endpoint_runs_smoke_check(self) -> None:
        with self.client.session_transaction() as session:
            session[ADMIN_SESSION_KEY] = self.admin_id

        with patch(
            "app.routes.list_elevenlabs_voices",
            return_value=[{"voice_id": "alloy", "name": "Alloy Imported"}],
        ), patch(
            "app.routes.get_agent",
            return_value={
                "conversation_config": {
                    "asr": {"user_input_audio_format": "pcm_16000"},
                    "tts": {"agent_output_audio_format": "pcm_16000"},
                }
            },
        ), patch(
            "app.routes.get_signed_url",
            return_value="wss://signed.example/socket",
        ):
            response = self.client.post("/api/heroes/domovenok-kuzya/test-agent")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIn(payload["diagnostics"]["summary"], {"Smoke test passed", "Smoke test passed with warnings"})
        labels = {item["label"] for item in payload["diagnostics"]["checks"]}
        self.assertIn("Signed URL", labels)

    def test_create_hero_agent_endpoint_saves_agent_id(self) -> None:
        with self.client.session_transaction() as session:
            session[ADMIN_SESSION_KEY] = self.admin_id

        with self.app.app_context():
            hero = Hero.query.filter_by(slug="domovenok-kuzya").first()
            self.assertIsNotNone(hero)
            hero.realtime_settings_json = {"provider": "elevenlabs", "elevenlabs_llm": "gpt-4o-mini"}
            hero.elevenlabs_voice_id = "alloy"
            db.session.commit()

        with patch(
            "app.routes.create_agent",
            return_value={"agent_id": "agent-created"},
        ) as mocked_create_agent, patch(
            "app.routes.list_elevenlabs_voices",
            return_value=[{"voice_id": "alloy", "name": "Alloy Imported"}],
        ), patch(
            "app.routes.get_agent",
            return_value={
                "conversation_config": {
                    "asr": {"user_input_audio_format": "pcm_16000"},
                    "tts": {"agent_output_audio_format": "pcm_16000"},
                }
            },
        ), patch(
            "app.routes.get_signed_url",
            return_value="wss://signed.example/socket",
        ):
            response = self.client.post("/api/heroes/domovenok-kuzya/create-agent")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["created"])
        self.assertEqual(payload["agent_id"], "agent-created")
        mocked_create_agent.assert_called_once()
        conversation_config = mocked_create_agent.call_args.kwargs["conversation_config"]
        self.assertEqual(conversation_config["agent"]["prompt"]["llm"], "gpt-4o-mini")
        self.assertIn("<END_CALL:короткая причина>", conversation_config["agent"]["prompt"]["prompt"])
        self.assertNotIn("вызови функцию end_call", conversation_config["agent"]["prompt"]["prompt"])

        with self.app.app_context():
            hero = Hero.query.filter_by(slug="domovenok-kuzya").first()
            self.assertIsNotNone(hero)
            self.assertEqual((hero.realtime_settings_json or {}).get("elevenlabs_agent_id"), "agent-created")

    def test_start_call_session_syncs_agent_before_signed_url(self) -> None:
        with self.app.app_context():
            hero = Hero.query.filter_by(slug="domovenok-kuzya").first()
            self.assertIsNotNone(hero)
            hero.realtime_settings_json = {"provider": "elevenlabs", "elevenlabs_llm": "gpt-4o-mini"}
            hero.elevenlabs_voice_id = "alloy"
            db.session.commit()

        with patch("app.routes.update_agent") as mocked_update_agent, patch(
            "app.routes.get_signed_url",
            return_value="wss://signed.example/socket",
        ):
            response = self.client.post(
                "/api/call-sessions/start",
                json={"character_slug": "domovenok-kuzya", "started_from": "web"},
            )

        self.assertEqual(response.status_code, 200)
        mocked_update_agent.assert_called_once()
        conversation_config = mocked_update_agent.call_args.kwargs["conversation_config"]
        self.assertIn("<END_CALL:короткая причина>", conversation_config["agent"]["prompt"]["prompt"])
        self.assertNotIn("вызови функцию end_call", conversation_config["agent"]["prompt"]["prompt"])

    def test_update_hero_saves_provider_in_realtime_settings(self) -> None:
        with self.client.session_transaction() as session:
            session[ADMIN_SESSION_KEY] = self.admin_id

        response = self.client.patch(
            "/api/heroes/domovenok-kuzya",
            json={
                "name": "Домовёнок Кузя",
                "emoji": "AI",
                "description": "Тестовый герой",
                "provider": "openai",
                "voice": "verse",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["hero"]["provider"], "openai")

        with self.app.app_context():
            hero = Hero.query.filter_by(slug="domovenok-kuzya").first()
            self.assertIsNotNone(hero)
            self.assertEqual((hero.realtime_settings_json or {}).get("provider"), "openai")

    def test_update_hero_saves_elevenlabs_llm_setting(self) -> None:
        with self.client.session_transaction() as session:
            session[ADMIN_SESSION_KEY] = self.admin_id

        response = self.client.patch(
            "/api/heroes/domovenok-kuzya",
            json={
                "name": "Домовёнок Кузя",
                "provider": "elevenlabs",
                "elevenlabs_llm": "gpt-4o",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["hero"]["elevenlabs_llm"], "gpt-4o")

        with self.app.app_context():
            hero = Hero.query.filter_by(slug="domovenok-kuzya").first()
            self.assertIsNotNone(hero)
            self.assertEqual((hero.realtime_settings_json or {}).get("elevenlabs_llm"), "gpt-4o")

    def test_update_hero_preserves_hidden_provider_settings_when_keys_omitted(self) -> None:
        with self.app.app_context():
            hero = Hero.query.filter_by(slug="domovenok-kuzya").first()
            self.assertIsNotNone(hero)
            hero.realtime_settings_json = {
                "provider": "elevenlabs",
                "elevenlabs_agent_id": "agent-keep",
                "output_audio_speed": 0.9,
            }
            hero.elevenlabs_voice_id = "voice-keep"
            hero.elevenlabs_first_message = "Привет"
            db.session.commit()

        with self.client.session_transaction() as session:
            session[ADMIN_SESSION_KEY] = self.admin_id

        response = self.client.patch(
            "/api/heroes/domovenok-kuzya",
            json={
                "name": "Домовёнок Кузя",
                "provider": "openai",
                "voice": "verse",
            },
        )

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            hero = Hero.query.filter_by(slug="domovenok-kuzya").first()
            self.assertIsNotNone(hero)
            self.assertEqual(hero.elevenlabs_voice_id, "voice-keep")
            self.assertEqual(hero.elevenlabs_first_message, "Привет")
            self.assertEqual((hero.realtime_settings_json or {}).get("elevenlabs_agent_id"), "agent-keep")

    def test_update_hero_allows_slug_change_and_rewrites_upload_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_static_dir:
            self.app.static_folder = temp_static_dir
            old_dir = Path(temp_static_dir) / "uploads" / "heroes" / "domovenok-kuzya"
            old_dir.mkdir(parents=True, exist_ok=True)
            avatar = old_dir / "avatar.png"
            knowledge = old_dir / "knowledge.txt"
            avatar.write_bytes(b"avatar")
            knowledge.write_text("knowledge", encoding="utf-8")

            with self.app.app_context():
                hero = Hero.query.filter_by(slug="domovenok-kuzya").first()
                self.assertIsNotNone(hero)
                hero.avatar_path = "uploads/heroes/domovenok-kuzya/avatar.png"
                hero.knowledge_file_path = "uploads/heroes/domovenok-kuzya/knowledge.txt"
                db.session.commit()

            with self.client.session_transaction() as session:
                session[ADMIN_SESSION_KEY] = self.admin_id

            response = self.client.patch(
                "/api/heroes/domovenok-kuzya",
                json={"slug": "kuzya-renamed", "name": "Домовёнок Кузя"},
            )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["hero"]["slug"], "kuzya-renamed")
            self.assertFalse(old_dir.exists())
            self.assertTrue((Path(temp_static_dir) / "uploads" / "heroes" / "kuzya-renamed" / "avatar.png").exists())
            self.assertTrue((Path(temp_static_dir) / "uploads" / "heroes" / "kuzya-renamed" / "knowledge.txt").exists())

            with self.app.app_context():
                hero = Hero.query.filter_by(slug="kuzya-renamed").first()
                self.assertIsNotNone(hero)
                self.assertEqual(hero.avatar_path, "uploads/heroes/kuzya-renamed/avatar.png")
                self.assertEqual(hero.knowledge_file_path, "uploads/heroes/kuzya-renamed/knowledge.txt")

    def test_upload_avatar_replaces_previous_avatar_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_static_dir:
            self.app.static_folder = temp_static_dir
            hero_dir = Path(temp_static_dir) / "uploads" / "heroes" / "domovenok-kuzya"
            hero_dir.mkdir(parents=True, exist_ok=True)
            old_avatar = hero_dir / "avatar.png"
            old_avatar.write_bytes(b"old-avatar")

            with self.client.session_transaction() as session:
                session[ADMIN_SESSION_KEY] = self.admin_id

            response = self.client.post(
                "/api/heroes/domovenok-kuzya/avatar",
                data={"file": (io.BytesIO(b"new-avatar"), "new-avatar.jpg")},
                content_type="multipart/form-data",
            )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["ok"])

            avatar_files = sorted(item.name for item in hero_dir.glob("avatar*") if item.is_file())
            self.assertEqual(len(avatar_files), 1)
            self.assertNotIn("avatar.png", avatar_files)
            self.assertTrue(payload["hero"]["avatar_url"])

    def test_create_hero_auto_creates_agent_for_elevenlabs_provider(self) -> None:
        with self.client.session_transaction() as session:
            session[ADMIN_SESSION_KEY] = self.admin_id

        with patch(
            "app.routes.create_agent",
            return_value={"agent_id": "agent-new-hero"},
        ) as mocked_create_agent:
            response = self.client.post(
                "/api/heroes",
                json={"name": "Новый герой", "slug": "new-hero", "emoji": "✨"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["agent_created"])
        self.assertEqual(payload["agent_id"], "agent-new-hero")
        mocked_create_agent.assert_called_once()

        with self.app.app_context():
            hero = Hero.query.filter_by(slug="new-hero").first()
            self.assertIsNotNone(hero)
            self.assertEqual((hero.realtime_settings_json or {}).get("elevenlabs_agent_id"), "agent-new-hero")

    def test_finish_call_session_persists_elevenlabs_transcript(self) -> None:
        with self.app.app_context():
            session = CallSession(
                app_user_id=self.user_id,
                character_slug="domovenok-kuzya",
                status="active",
                meta_json={"provider": "elevenlabs", "conversation_log": [], "technical_log": []},
            )
            db.session.add(session)
            db.session.commit()
            session_id = session.id

        with patch(
            "app.routes.get_conversation_details",
            return_value={
                "conversation_id": "conv-1",
                "status": "done",
                "has_audio": True,
                "has_user_audio": True,
                "has_response_audio": True,
                "transcript": [
                    {"role": "user", "message": "Привет"},
                    {"role": "agent", "message": "Здравствуйте!"},
                ],
            },
        ):
            response = self.client.post(
                f"/api/call-sessions/{session_id}/finish",
                json={"provider": "elevenlabs", "conversation_id": "conv-1", "reason": "manual"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        with self.app.app_context():
            session = db.session.get(CallSession, session_id)
            self.assertEqual(session.status, "finished")
            self.assertEqual(
                session.meta_json["conversation_log"],
                [
                    {"role": "user", "text": "Привет"},
                    {"role": "agent", "text": "Здравствуйте!"},
                ],
            )
            self.assertEqual(session.meta_json["provider_conversation_id"], "conv-1")

    def test_finish_call_session_uses_local_transcript_fallback_when_remote_empty(self) -> None:
        with self.app.app_context():
            session = CallSession(
                app_user_id=self.user_id,
                character_slug="domovenok-kuzya",
                status="active",
                meta_json={"provider": "elevenlabs", "conversation_log": [], "technical_log": []},
            )
            db.session.add(session)
            db.session.commit()
            session_id = session.id

        with patch(
            "app.routes.get_conversation_details",
            return_value={
                "conversation_id": "conv-1",
                "status": "done",
                "has_audio": True,
                "has_user_audio": True,
                "has_response_audio": True,
                "transcript": [],
            },
        ):
            response = self.client.post(
                f"/api/call-sessions/{session_id}/finish",
                json={
                    "provider": "elevenlabs",
                    "conversation_id": "conv-1",
                    "reason": "manual",
                    "local_conversation_log": [
                        {"role": "user", "text": "Пока"},
                        {"role": "agent", "text": "До свидания!"},
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        with self.app.app_context():
            session = db.session.get(CallSession, session_id)
            self.assertEqual(
                session.meta_json["conversation_log"],
                [
                    {"role": "user", "text": "Пока"},
                    {"role": "agent", "text": "До свидания!"},
                ],
            )


class MobileAuthRoutesTests(unittest.TestCase):
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
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            MAIL_SUPPRESS_SEND=True,
            MAIL_DEFAULT_SENDER="test@example.com",
            EMAIL_VERIFICATION_CODE_TTL_MINUTES=10,
            EMAIL_VERIFICATION_RESEND_INTERVAL_SECONDS=0,
            MOBILE_ACCESS_TOKEN_TTL_SECONDS=3600,
            MOBILE_REFRESH_TOKEN_TTL_DAYS=30,
            TRY_CALLS_NUMBER=1,
        )
        db.init_app(self.app)
        mail.init_app(self.app)
        self.app.register_blueprint(main_bp)
        with self.app.app_context():
            db.create_all()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_request_code_creates_email_user_and_returns_masked_destination(self) -> None:
        response = self.client.post("/api/mobile/auth/request-code", json={"login": "new-user@example.com"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["channel"], "email")
        self.assertEqual(payload["masked_destination"], "n***@example.com")

        with self.app.app_context():
            user = AppUser.query.filter_by(email="new-user@example.com").first()
            self.assertIsNotNone(user)
            self.assertFalse(user.email_verified)
            code = EmailCode.query.filter_by(email="new-user@example.com", purpose="mobile_login").first()
            self.assertIsNotNone(code)

    def test_verify_code_returns_mobile_tokens_and_marks_email_verified(self) -> None:
        self.client.post("/api/mobile/auth/request-code", json={"login": "verify-me@example.com"})
        with self.app.app_context():
            code_entry = (
                EmailCode.query.filter_by(email="verify-me@example.com", purpose="mobile_login")
                .order_by(EmailCode.id.desc())
                .first()
            )
            self.assertIsNotNone(code_entry)
            code_entry.code_hash = EmailCode.hash_code("123456")
            db.session.commit()

        response = self.client.post(
            "/api/mobile/auth/verify-code",
            json={
                "login": "verify-me@example.com",
                "code": "123456",
                "purpose": "mobile_login",
                "device_name": "QA iPhone",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["access_token"])
        self.assertTrue(payload["refresh_token"])
        self.assertEqual(payload["user"]["email"], "verify-me@example.com")
        self.assertTrue(payload["user"]["email_verified"])

        with self.app.app_context():
            user = AppUser.query.filter_by(email="verify-me@example.com").first()
            self.assertIsNotNone(user)
            self.assertTrue(user.email_verified)
            token = MobileAuthToken.query.filter_by(app_user_id=user.id).first()
            self.assertIsNotNone(token)
            self.assertEqual(token.device_name, "QA iPhone")

    def test_mobile_me_requires_bearer_token_and_returns_user(self) -> None:
        self.client.post("/api/mobile/auth/request-code", json={"login": "me@example.com"})
        with self.app.app_context():
            code_entry = (
                EmailCode.query.filter_by(email="me@example.com", purpose="mobile_login")
                .order_by(EmailCode.id.desc())
                .first()
            )
            self.assertIsNotNone(code_entry)
            code_entry.code_hash = EmailCode.hash_code("654321")
            db.session.commit()

        verify_response = self.client.post(
            "/api/mobile/auth/verify-code",
            json={"login": "me@example.com", "code": "654321", "purpose": "mobile_login"},
        )
        access_token = verify_response.get_json()["access_token"]

        unauthorized_response = self.client.get("/api/mobile/me")
        self.assertEqual(unauthorized_response.status_code, 401)

        authorized_response = self.client.get(
            "/api/mobile/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        self.assertEqual(authorized_response.status_code, 200)
        payload = authorized_response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["user"]["email"], "me@example.com")


class MobileChatRoutesTests(unittest.TestCase):
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
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            MAIL_SUPPRESS_SEND=True,
            MAIL_DEFAULT_SENDER="test@example.com",
            OPENAI_API_KEY="test-openai-key",
            OPENAI_CHAT_MODEL="gpt-4o-mini",
            TRY_CALLS_NUMBER=1,
        )
        db.init_app(self.app)
        mail.init_app(self.app)
        self.app.register_blueprint(main_bp)
        with self.app.app_context():
            db.create_all()
            self.user = AppUser(
                email="mobile-chat@example.com",
                phone="",
                name="Mobile Chat",
                consent_to_personal_data=True,
                email_verified=True,
            )
            db.session.add(self.user)
            db.session.commit()
            self.user_id = self.user.id
            self.access_token = "mobile-access-token"
            token = MobileAuthToken(
                app_user_id=self.user_id,
                access_token_hash=MobileAuthToken.hash_token(self.access_token),
                refresh_token_hash=MobileAuthToken.hash_token("mobile-refresh-token"),
                access_expires_at=datetime.utcnow() + timedelta(hours=1),
                refresh_expires_at=datetime.utcnow() + timedelta(days=30),
            )
            db.session.add(token)
            db.session.commit()
        self.client = self.app.test_client()
        self.auth_headers = {"Authorization": f"Bearer {self.access_token}"}

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_chat_list_returns_characters_for_mobile_user(self) -> None:
        response = self.client.get("/api/mobile/chats", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["items"])
        first_item = payload["items"][0]
        self.assertIn("character_slug", first_item)
        self.assertIn("last_message", first_item)
        self.assertIn("can_call", first_item)

    def test_chat_messages_returns_empty_list_when_history_is_empty(self) -> None:
        response = self.client.get("/api/mobile/chats/domovenok-kuzya/messages", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["character"]["slug"], "domovenok-kuzya")
        self.assertEqual(payload["items"], [])

    def test_send_message_persists_user_and_assistant_messages(self) -> None:
        with patch("app.routes.generate_chat_reply", return_value="Привет! Я рядом.") as reply_mock:
            response = self.client.post(
                "/api/mobile/chats/domovenok-kuzya/messages",
                headers=self.auth_headers,
                json={"text": "Привет!"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["user_message"]["role"], "user")
        self.assertEqual(payload["assistant_message"]["role"], "assistant")
        reply_mock.assert_called_once()

        with self.app.app_context():
            messages = (
                ChatMessage.query.filter_by(app_user_id=self.user_id, character_slug="domovenok-kuzya")
                .order_by(ChatMessage.id.asc())
                .all()
            )
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0].text, "Привет!")
            self.assertEqual(messages[1].text, "Привет! Я рядом.")

    def test_send_message_uses_web_aligned_default_reply_timeout(self) -> None:
        captured_kwargs: dict[str, object] = {}

        def fake_generate_chat_reply(**kwargs):
            captured_kwargs.update(kwargs)
            return "Привет! Я рядом."

        with patch("app.routes.generate_chat_reply", side_effect=fake_generate_chat_reply):
            response = self.client.post(
                "/api/mobile/chats/domovenok-kuzya/messages",
                headers=self.auth_headers,
                json={"text": "Привет!"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured_kwargs["timeout_seconds"], 60.0)

    def test_send_message_sends_latest_mobile_history_to_reply_generator(self) -> None:
        with self.app.app_context():
            for index in range(15):
                db.session.add(
                    ChatMessage(
                        app_user_id=self.user_id,
                        character_slug="domovenok-kuzya",
                        role="user",
                        text=f"Старый вопрос {index}",
                        source="mobile",
                    )
                )
                db.session.add(
                    ChatMessage(
                        app_user_id=self.user_id,
                        character_slug="domovenok-kuzya",
                        role="assistant",
                        text=f"Старый ответ {index}",
                        source="mobile",
                    )
                )
            db.session.commit()

        captured_messages: list[dict[str, str]] = []

        def fake_generate_chat_reply(**kwargs):
            captured_messages.extend(kwargs["messages"])
            return "Отвечаю на новый вопрос."

        with patch("app.routes.generate_chat_reply", side_effect=fake_generate_chat_reply):
            response = self.client.post(
                "/api/mobile/chats/domovenok-kuzya/messages",
                headers=self.auth_headers,
                json={"text": "Новый вопрос"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured_messages[-1]["role"], "user")
        self.assertEqual(captured_messages[-1]["content"], "Новый вопрос")
        self.assertNotIn("Старый вопрос 0", [message["content"] for message in captured_messages])

    def test_web_chat_accepts_mobile_bearer_token(self) -> None:
        with patch("app.routes.generate_chat_reply", return_value="Привет из общего чата.") as reply_mock:
            response = self.client.post(
                "/api/web-chat",
                headers=self.auth_headers,
                json={
                    "character_slug": "domovenok-kuzya",
                    "messages": [{"role": "user", "text": "Привет"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["message"]["text"], "Привет из общего чата.")
        reply_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
