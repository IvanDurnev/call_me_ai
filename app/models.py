from __future__ import annotations

from datetime import datetime
import hashlib
import uuid as uuid_lib

from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


class AdminUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(128), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class AppUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_uuid = db.Column(db.String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid_lib.uuid4()))
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    phone = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    telegram_user_id = db.Column(db.BigInteger, nullable=True, unique=True, index=True)
    telegram_username = db.Column(db.String(255), nullable=True)
    max_user_id = db.Column(db.BigInteger, nullable=True, unique=True, index=True)
    consent_to_personal_data = db.Column(db.Boolean, nullable=False, default=False)
    consented_at = db.Column(db.DateTime, nullable=True)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    email_verified_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    purpose = db.Column(db.String(32), nullable=False, index=True)
    code_hash = db.Column(db.String(64), nullable=False)
    app_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"), nullable=True, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    consumed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @staticmethod
    def hash_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    def matches(self, code: str) -> bool:
        return self.code_hash == self.hash_code(code)


class Hero(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(64), nullable=False, unique=True, index=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    emoji = db.Column(db.String(16), nullable=False, default="AI")
    voice = db.Column(db.String(128), nullable=False, default="alloy")
    avatar_path = db.Column(db.String(512), nullable=True)
    knowledge_file_name = db.Column(db.String(255), nullable=True)
    knowledge_file_path = db.Column(db.String(512), nullable=True)
    knowledge_text = db.Column(db.Text, nullable=True)
    system_prompt = db.Column(db.Text, nullable=True)
    greeting_prompt = db.Column(db.Text, nullable=True)
    realtime_settings_json = db.Column(db.JSON, nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class CallSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    app_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"), nullable=True, index=True)
    telegram_user_id = db.Column(db.BigInteger, nullable=True, index=True)
    telegram_username = db.Column(db.String(255), nullable=True)
    character_slug = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, default="created")
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    meta_json = db.Column(db.JSON, nullable=True)

    def mark_finished(self) -> None:
        self.status = "finished"
        self.ended_at = datetime.utcnow()


class SubscriptionPurchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    app_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"), nullable=False, index=True)
    invoice_id = db.Column(db.String(128), nullable=False, unique=True, index=True)
    plan_code = db.Column(db.String(64), nullable=False, default="test-plan")
    plan_name = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(8), nullable=False, default="RUB")
    status = db.Column(db.String(32), nullable=False, default="created", index=True)
    transaction_id = db.Column(db.String(64), nullable=True, index=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    cloudpayments_subscription_id = db.Column(db.String(64), nullable=True, index=True)
    subscription_status = db.Column(db.String(32), nullable=True, index=True)
    recurring_interval = db.Column(db.String(16), nullable=True)
    recurring_period = db.Column(db.Integer, nullable=True)
    next_transaction_at = db.Column(db.DateTime, nullable=True)
    canceled_at = db.Column(db.DateTime, nullable=True)
    provider_payload_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class PricingPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), nullable=False, unique=True, index=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    kind = db.Column(db.String(32), nullable=False, default="call_package", index=True)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(8), nullable=False, default="RUB")
    calls_included = db.Column(db.Integer, nullable=True)
    period_days = db.Column(db.Integer, nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
