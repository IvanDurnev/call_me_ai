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
from app.models import AdminUser, AppUser, CallSession, Hero, PricingPlan, SubscriptionPurchase
from app.routes import ADMIN_SESSION_KEY, APP_USER_SESSION_KEY, _apply_pricing_plan_payload, _build_runtime_diagnostics, main_bp
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


if __name__ == "__main__":
    unittest.main()
