from __future__ import annotations

import json
import hashlib
import hmac
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from functools import wraps
from urllib.request import Request, urlopen
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy.exc import OperationalError, ProgrammingError
from werkzeug.utils import secure_filename

from .account_linking import link_max_account, link_telegram_account
from .characters import (
    DEFAULT_GREETING_PROMPT,
    NOISE_REDUCTION_OPTIONS,
    OPENAI_VOICE_OPTIONS,
    REALTIME_MODEL_OPTIONS,
    TRANSCRIPTION_MODEL_OPTIONS,
    get_character,
    list_characters,
    normalize_realtime_settings,
)
from .extensions import db
from .models import AdminUser, AppUser, Hero, CallSession, SubscriptionPurchase, PricingPlan
from .services import (
    build_user_access_state,
    build_voice_library_payload,
    cancel_cloudpayments_subscription,
    cloudpayments_enabled,
    convert_voice_sample_to_wav,
    create_custom_voice,
    create_voice_consent,
    find_payment,
    generate_speech_preview,
    issue_email_code,
    verify_cloudpayments_webhook_signature,
    verify_email_code,
)
from .services.voice_library import iter_voice_directories, normalize_voice_name, pick_voice_sample


main_bp = Blueprint("main", __name__)

TEXT_FILE_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv", ".yaml", ".yml"}
IMAGE_FILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ADMIN_SESSION_KEY = "admin_user_id"
APP_USER_SESSION_KEY = "app_user_id"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PRICING_PLAN_KINDS = {"call_package", "unlimited"}
EMAIL_SPAM_HINT = "Если не видите письмо во входящих, проверьте папку СПАМ."
LEGAL_BUSINESS_DETAILS = {
    "business_name": "ИП Дурнев И.В.",
    "inn": "281601583789",
    "ogrnip": "311282717200033",
    "phone": "89240254453",
    "email": "info@itd.dev",
}


def _current_admin() -> AdminUser | None:
    admin_id = session.get(ADMIN_SESSION_KEY)
    if not admin_id:
        return None
    try:
        return AdminUser.query.filter_by(id=admin_id, is_active=True).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        return None


def _find_admin(username: str) -> AdminUser | None:
    try:
        return AdminUser.query.filter_by(username=username).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        return None


def _admin_required(*, view_mode: str = "json"):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if _current_admin():
                return func(*args, **kwargs)
            if view_mode == "html":
                return redirect(url_for("main.admin_login", next=request.url))
            return jsonify({"ok": False, "error": "Authentication required."}), 401

        return wrapper

    return decorator


def _current_app_user() -> AppUser | None:
    user_id = session.get(APP_USER_SESSION_KEY)
    if not user_id:
        return None
    try:
        return AppUser.query.filter_by(id=user_id).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        return None


def _login_app_user(user: AppUser) -> None:
    session[APP_USER_SESSION_KEY] = user.id


def _logout_app_user() -> None:
    session.pop(APP_USER_SESSION_KEY, None)


def _find_app_user_by_email(email: str) -> AppUser | None:
    try:
        return AppUser.query.filter_by(email=email).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        return None


def _find_app_user_by_max_user_id(max_user_id: int | None) -> AppUser | None:
    if not max_user_id:
        return None
    try:
        return AppUser.query.filter_by(max_user_id=max_user_id).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        return None


def _find_app_user_by_telegram_user_id(telegram_user_id: int | None) -> AppUser | None:
    if not telegram_user_id:
        return None
    try:
        return AppUser.query.filter_by(telegram_user_id=telegram_user_id).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        return None


def _ensure_user_uuid(user: AppUser | None) -> AppUser | None:
    if not user:
        return None
    if user.user_uuid:
        return user
    user.user_uuid = str(uuid.uuid4())
    db.session.commit()
    return user


def _append_start_param(url: str, start_value: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["start"] = start_value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "7" + digits
    return digits


def _format_phone_for_display(value: str) -> str:
    digits = _normalize_phone(value)
    if len(digits) != 11:
        return value
    return f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"


def _is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match((value or "").strip()))


def _is_resend_wait_error(message: str) -> bool:
    return "Повторно запросить код можно через" in (message or "")


def _format_call_duration(total_seconds: int | float | None) -> str:
    seconds = max(0, int(total_seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _trial_calls_limit() -> int:
    return max(0, int(current_app.config.get("TRY_CALLS_NUMBER", 1)))


def _validate_max_init_data(init_data: str) -> dict:
    raw_value = (init_data or "").strip()
    if not raw_value:
        raise ValueError("Не удалось получить данные MAX mini app.")

    pairs = parse_qsl(raw_value, keep_blank_values=True, strict_parsing=True)
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("Некорректные параметры запуска MAX mini app.")

    values = dict(pairs)
    original_hash = values.pop("hash", None)
    if not original_hash:
        raise ValueError("Подпись MAX mini app не найдена.")

    launch_params = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    bot_token = (current_app.config.get("MAX_BOT_TOKEN") or "").strip()
    if not bot_token:
        raise ValueError("MAX_BOT_TOKEN не настроен.")

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, launch_params.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, original_hash):
        raise ValueError("Подпись MAX mini app не прошла проверку.")

    parsed = dict(values)
    user_payload = parsed.get("user")
    if user_payload:
        parsed["user"] = json.loads(user_payload)
    start_param_payload = parsed.get("start_param")
    if start_param_payload:
        parsed["start_param"] = str(start_param_payload)
    return parsed


def _validate_telegram_init_data(init_data: str) -> dict:
    raw_value = (init_data or "").strip()
    if not raw_value:
        raise ValueError("Не удалось получить данные Telegram mini app.")

    pairs = parse_qsl(raw_value, keep_blank_values=True, strict_parsing=True)
    values = dict(pairs)
    original_hash = values.pop("hash", None)
    if not original_hash:
        raise ValueError("Подпись Telegram mini app не найдена.")

    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    bot_token = (current_app.config.get("TG_BOT_TOKEN") or "").strip()
    if not bot_token:
        raise ValueError("TG_BOT_TOKEN не настроен.")

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, original_hash):
        raise ValueError("Подпись Telegram mini app не прошла проверку.")

    parsed = dict(values)
    user_payload = parsed.get("user")
    if user_payload:
        parsed["user"] = json.loads(user_payload)
    return parsed


def _send_max_registration_message(max_user_id: int | None) -> None:
    if not max_user_id:
        return

    token = (current_app.config.get("MAX_BOT_TOKEN") or "").strip()
    if not token:
        return

    site_url = current_app.config["PUBLIC_BASE_URL"].rstrip("/")
    auth_url = f"{site_url}{url_for('main.login_email')}"
    message = f"Для продолжения нужно зарегистрироваться или войти на сайте {auth_url}"
    url = f"https://platform-api.max.ru/messages?user_id={int(max_user_id)}"
    payload = json.dumps(
        {
            "text": message,
            "attachments": [
                {
                    "type": "inline_keyboard",
                    "payload": {
                        "buttons": [
                            [
                                {
                                    "type": "link",
                                    "text": "Войти или зарегистрироваться",
                                    "url": auth_url,
                                }
                            ]
                        ]
                    },
                }
            ],
            "link": None,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = Request(
        url=url,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": token,
            "User-Agent": "call-me-ai/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=10):
            return
    except Exception:
        return


def _send_telegram_registration_message(telegram_user_id: int | None) -> None:
    if not telegram_user_id:
        return

    token = (current_app.config.get("TG_BOT_TOKEN") or "").strip()
    if not token:
        return

    site_url = current_app.config["PUBLIC_BASE_URL"].rstrip("/")
    auth_url = f"{site_url}{url_for('main.login_email')}"
    payload = json.dumps(
        {
            "chat_id": int(telegram_user_id),
            "text": "Сначала зарегистрируйтесь, пожалуйста. И после регистрации нажмите кнопку Телеграм на главной странице сайта.",
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": "Войти или зарегистрироваться",
                            "url": auth_url,
                        }
                    ]
                ]
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = Request(
        url=f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "call-me-ai/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=10):
            return
    except Exception:
        return


def _app_user_access_state(user: AppUser | None) -> dict:
    return build_user_access_state(user, trial_minutes_limit=_trial_calls_limit())


def _app_user_remaining_trial_minutes(user: AppUser | None) -> int:
    return _app_user_access_state(user)["trial_remaining_minutes"]


def _app_user_has_trial_available(user: AppUser | None) -> bool:
    return bool(user) and _app_user_remaining_trial_minutes(user) > 0


def _app_user_has_call_access(user: AppUser | None) -> bool:
    return bool(user) and _app_user_access_state(user)["has_call_access"]


def _app_user_ready_for_calls(user: AppUser | None) -> bool:
    return bool(user and user.email_verified)


def _cloudpayments_ready() -> bool:
    return cloudpayments_enabled()


def _request_client_ip() -> str:
    forwarded_for = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return (request.remote_addr or "").strip()


def _legal_pricing_context() -> dict:
    pricing_plans = [_serialize_pricing_plan(plan) for plan in _list_pricing_plans(include_inactive=False)]
    subscription_plans = [plan for plan in pricing_plans if plan["kind"] == "unlimited"]
    return {
        "business": dict(LEGAL_BUSINESS_DETAILS),
        "pricing_plans": pricing_plans,
        "subscription_plans": subscription_plans,
    }


def _pricing_plan_kind_options() -> list[dict[str, str]]:
    return [
        {"value": "call_package", "label": "Пакет минут"},
        {"value": "unlimited", "label": "Безлимит на период"},
    ]


def _serialize_subscription_purchase(purchase: SubscriptionPurchase) -> dict:
    amount = purchase.amount
    if isinstance(amount, Decimal):
        amount_value = float(amount)
    else:
        amount_value = float(amount or 0)
    return {
        "id": purchase.id,
        "invoice_id": purchase.invoice_id,
        "plan_name": purchase.plan_name,
        "amount": amount_value,
        "currency": purchase.currency,
        "status": purchase.status,
        "transaction_id": purchase.transaction_id,
        "cloudpayments_token": purchase.cloudpayments_token,
        "cloudpayments_subscription_id": purchase.cloudpayments_subscription_id,
        "subscription_status": purchase.subscription_status,
        "next_transaction_at": purchase.next_transaction_at.isoformat() if purchase.next_transaction_at else None,
        "canceled_at": purchase.canceled_at.isoformat() if purchase.canceled_at else None,
        "paid_at": purchase.paid_at.isoformat() if purchase.paid_at else None,
        "created_at": purchase.created_at.isoformat() if purchase.created_at else None,
    }


def _get_pricing_plan(code: str) -> PricingPlan | None:
    try:
        return PricingPlan.query.filter_by(code=code).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        return None


def _list_pricing_plans(*, include_inactive: bool = True) -> list[PricingPlan]:
    try:
        query = PricingPlan.query.order_by(PricingPlan.sort_order.asc(), PricingPlan.id.asc())
        if not include_inactive:
            query = query.filter(PricingPlan.is_active.is_(True))
        return query.all()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        return []


def _serialize_pricing_plan(plan: PricingPlan) -> dict:
    return {
        "code": plan.code,
        "name": plan.name,
        "description": plan.description or "",
        "kind": plan.kind,
        "price": float(plan.price or 0),
        "currency": plan.currency or "RUB",
        "minutes_included": plan.calls_included,
        "calls_included": plan.calls_included,
        "period_days": plan.period_days,
        "sort_order": plan.sort_order,
        "is_active": plan.is_active,
    }


def _next_pricing_plan_sort_order() -> int:
    try:
        latest = PricingPlan.query.order_by(PricingPlan.sort_order.desc(), PricingPlan.id.desc()).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        latest = None
    return (latest.sort_order + 1) if latest else len(_list_pricing_plans(include_inactive=True))


def _coerce_plan_price(raw_value) -> Decimal:
    text = str(raw_value or "").strip().replace(",", ".")
    value = Decimal(text)
    if value <= 0:
        raise ValueError("Цена должна быть больше нуля.")
    return value.quantize(Decimal("0.01"))


def _coerce_optional_positive_int(raw_value, field_label: str) -> int | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    value = int(text)
    if value <= 0:
        raise ValueError(f"{field_label} должно быть больше нуля.")
    return value


def _coerce_non_negative_int(raw_value, field_label: str) -> int:
    text = "" if raw_value is None else str(raw_value).strip()
    if text == "":
        raise ValueError(f"{field_label} обязательно.")
    value = int(text)
    if value < 0:
        raise ValueError(f"{field_label} не может быть отрицательным.")
    return value


def _apply_pricing_plan_payload(plan: PricingPlan, payload: dict) -> None:
    kind = (payload.get("kind") or plan.kind or "call_package").strip()
    if kind not in PRICING_PLAN_KINDS:
        raise ValueError("Неизвестный тип тарифа.")

    price = _coerce_plan_price(payload.get("price"))
    calls_included = _coerce_optional_positive_int(
        payload.get("minutes_included", payload.get("calls_included")),
        "Количество минут",
    )
    period_days = _coerce_optional_positive_int(payload.get("period_days"), "Период")
    sort_order = _coerce_non_negative_int(payload.get("sort_order", plan.sort_order), "Порядок")

    if kind == "call_package":
        if not calls_included:
            raise ValueError("Для минутного тарифа укажите количество минут.")
        period_days = None
    else:
        if not period_days:
            raise ValueError("Для безлимитного тарифа укажите период в днях.")
        calls_included = None

    plan.name = (payload.get("name") or plan.name or "").strip()
    if not plan.name:
        raise ValueError("Название тарифа обязательно.")

    plan.description = (payload.get("description") or "").strip()
    plan.kind = kind
    plan.price = price
    plan.currency = (payload.get("currency") or plan.currency or "RUB").strip().upper() or "RUB"
    plan.calls_included = calls_included
    plan.period_days = period_days
    plan.sort_order = sort_order
    plan.is_active = bool(payload.get("is_active", plan.is_active))


def _plan_recurrent_payload(plan: PricingPlan) -> dict | None:
    if plan.kind != "unlimited" or not plan.period_days:
        return None
    start_date = datetime.utcnow() + timedelta(days=int(plan.period_days))
    return {
        "interval": "Day",
        "period": int(plan.period_days),
        "startDateIso": start_date.replace(microsecond=0).isoformat() + "Z",
    }


def _parse_cloudpayments_datetime(raw_value) -> datetime | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _cloudpayments_json_code(code: int = 0, *, status: int = 200) -> Response:
    return jsonify({"code": code}), status


def _cloudpayments_notification_payload() -> dict:
    if request.is_json:
        payload = request.get_json(silent=True) or {}
    elif request.form:
        payload = request.form.to_dict(flat=True)
    else:
        payload = dict(parse_qsl(request.get_data(as_text=True), keep_blank_values=True))

    data_value = payload.get("Data")
    if isinstance(data_value, str) and data_value.strip():
        try:
            payload["Data"] = json.loads(data_value)
        except json.JSONDecodeError:
            payload["Data"] = {"raw": data_value}
    return payload


def _cloudpayments_notification_user(payload: dict) -> AppUser | None:
    account_id = str(payload.get("AccountId") or "").strip()
    if not account_id:
        return None
    try:
        user_id = int(account_id)
    except ValueError:
        return None
    return AppUser.query.filter_by(id=user_id).first()


def _cloudpayments_payload_candidates(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []

    candidates: list[dict] = []
    queue = [payload]
    visited: set[int] = set()
    nested_keys = ("Model", "model", "Payment", "payment", "Transaction", "transaction", "Data", "data")

    while queue:
        current = queue.pop(0)
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)
        candidates.append(current)
        for key in nested_keys:
            nested_value = current.get(key)
            if isinstance(nested_value, dict):
                queue.append(nested_value)

    return candidates


def _cloudpayments_payload_value(payload: dict | None, *keys: str):
    for candidate in _cloudpayments_payload_candidates(payload):
        for key in keys:
            value = candidate.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return None


def _find_purchase_by_notification(
    payload: dict,
    *,
    user: AppUser | None = None,
) -> SubscriptionPurchase | None:
    transaction_id = str(_cloudpayments_payload_value(payload, "TransactionId") or "").strip()
    invoice_id = str(_cloudpayments_payload_value(payload, "InvoiceId") or "").strip()
    subscription_id = str(_cloudpayments_payload_value(payload, "SubscriptionId", "Id") or "").strip()

    if transaction_id:
        purchase = SubscriptionPurchase.query.filter_by(transaction_id=transaction_id).first()
        if purchase:
            return purchase
    if invoice_id:
        purchase = SubscriptionPurchase.query.filter_by(invoice_id=invoice_id).first()
        if purchase:
            return purchase
    if subscription_id:
        query = SubscriptionPurchase.query.filter_by(cloudpayments_subscription_id=subscription_id)
        if user:
            query = query.filter_by(app_user_id=user.id)
        purchase = query.order_by(SubscriptionPurchase.paid_at.desc(), SubscriptionPurchase.id.desc()).first()
        if purchase:
            return purchase
    return None


def _merge_provider_payload(existing_payload: dict | None, **updates) -> dict:
    payload = dict(existing_payload or {})
    for key, value in updates.items():
        if value is not None:
            payload[key] = value
    return payload


def _append_subscription_action(
    existing_payload: dict | None,
    *,
    action: str,
    actor: str,
    purchase: SubscriptionPurchase,
    details: dict | None = None,
) -> dict:
    payload = dict(existing_payload or {})
    history = list(payload.get("subscription_action_history") or [])
    history.append(
        {
            "action": action,
            "actor": actor,
            "app_user_id": purchase.app_user_id,
            "purchase_id": purchase.id,
            "timestamp": datetime.utcnow().isoformat(),
            "details": dict(details or {}),
        }
    )
    payload["subscription_action_history"] = history[-100:]
    return payload


def _record_cloudpayments_payment(payload: dict, *, mark_paid: bool) -> SubscriptionPurchase | None:
    user = _cloudpayments_notification_user(payload)
    purchase = _find_purchase_by_notification(payload, user=user)
    data = payload.get("Data") if isinstance(payload.get("Data"), dict) else {}
    plan_code = str((data or {}).get("plan_code") or "").strip()
    plan = _get_pricing_plan(plan_code) if plan_code else None

    if not purchase and user is None:
        return None

    if not purchase and user and not plan:
        subscription_id = str(_cloudpayments_payload_value(payload, "SubscriptionId", "Id") or "").strip()
        template_query = SubscriptionPurchase.query.filter_by(app_user_id=user.id)
        if subscription_id:
            template_query = template_query.filter_by(cloudpayments_subscription_id=subscription_id)
        template = template_query.order_by(SubscriptionPurchase.paid_at.desc(), SubscriptionPurchase.id.desc()).first()
        if template:
            plan_code = template.plan_code
            plan = _get_pricing_plan(plan_code)
            purchase = template if str(payload.get("TransactionId") or "").strip() == (template.transaction_id or "") else None

    amount = Decimal(str(_cloudpayments_payload_value(payload, "Amount", "TotalAmount") or "0").replace(",", ".") or "0")
    currency = str(_cloudpayments_payload_value(payload, "Currency") or "RUB").strip().upper() or "RUB"
    invoice_id = str(_cloudpayments_payload_value(payload, "InvoiceId") or "").strip()
    transaction_id = str(_cloudpayments_payload_value(payload, "TransactionId") or "").strip()
    cloudpayments_token = str(_cloudpayments_payload_value(payload, "Token") or "").strip()
    subscription_id = str(_cloudpayments_payload_value(payload, "SubscriptionId", "Id") or "").strip()
    paid_at = (
        _parse_cloudpayments_datetime(_cloudpayments_payload_value(payload, "DateTime", "TransactionDateTime"))
        or datetime.utcnow()
    )
    next_transaction_at = _parse_cloudpayments_datetime(_cloudpayments_payload_value(payload, "NextTransactionDateIso"))
    subscription_status = str(_cloudpayments_payload_value(payload, "SubscriptionStatus", "Status") or "").strip() or None

    if not purchase:
        if not user:
            return None
        source_purchase = None
        if subscription_id:
            source_purchase = (
                SubscriptionPurchase.query.filter_by(app_user_id=user.id, cloudpayments_subscription_id=subscription_id)
                .order_by(SubscriptionPurchase.paid_at.desc(), SubscriptionPurchase.id.desc())
                .first()
            )
        if not source_purchase and plan_code:
            source_purchase = (
                SubscriptionPurchase.query.filter_by(app_user_id=user.id, plan_code=plan_code)
                .order_by(SubscriptionPurchase.paid_at.desc(), SubscriptionPurchase.id.desc())
                .first()
            )
        invoice_id = invoice_id or f"cp-rec-{user.id}-{uuid.uuid4().hex[:16]}"
        purchase = SubscriptionPurchase(
            app_user_id=user.id,
            invoice_id=invoice_id,
            plan_code=(plan.code if plan else (source_purchase.plan_code if source_purchase else plan_code or "unknown-plan")),
            plan_name=(
                (plan.name if plan else None)
                or (source_purchase.plan_name if source_purchase else None)
                or str(payload.get("Description") or "Подписка").strip()
                or "Подписка"
            ),
            amount=amount,
            currency=currency,
            status="paid" if mark_paid else "failed",
            transaction_id=transaction_id or None,
            paid_at=paid_at if mark_paid else None,
            cloudpayments_token=cloudpayments_token or (source_purchase.cloudpayments_token if source_purchase else None),
            cloudpayments_subscription_id=subscription_id or (source_purchase.cloudpayments_subscription_id if source_purchase else None),
            subscription_status=subscription_status or (source_purchase.subscription_status if source_purchase else None),
            recurring_interval=(source_purchase.recurring_interval if source_purchase else None),
            recurring_period=(source_purchase.recurring_period if source_purchase else None),
            next_transaction_at=next_transaction_at,
            provider_payload_json={
                "kind": "cloudpayments_webhook",
                "webhook_origin": "cloudpayments",
                "initial_notification": payload,
            },
        )
        db.session.add(purchase)

    purchase.amount = amount or purchase.amount
    purchase.currency = currency or purchase.currency
    purchase.transaction_id = transaction_id or purchase.transaction_id
    purchase.cloudpayments_token = cloudpayments_token or purchase.cloudpayments_token
    purchase.cloudpayments_subscription_id = subscription_id or purchase.cloudpayments_subscription_id
    purchase.subscription_status = subscription_status or purchase.subscription_status
    purchase.next_transaction_at = next_transaction_at or purchase.next_transaction_at
    purchase.provider_payload_json = _merge_provider_payload(
        purchase.provider_payload_json,
        payment=payload if mark_paid else None,
        payment_failure=payload if not mark_paid else None,
        last_webhook_at=datetime.utcnow().isoformat(),
    )
    if mark_paid:
        purchase.status = "paid"
        purchase.paid_at = paid_at or purchase.paid_at or datetime.utcnow()
        if purchase.canceled_at and purchase.subscription_status and purchase.subscription_status.lower() == "active":
            purchase.canceled_at = None
    else:
        purchase.status = "failed"
    return purchase


def _update_cloudpayments_subscription_state(payload: dict) -> SubscriptionPurchase | None:
    user = _cloudpayments_notification_user(payload)
    purchase = _find_purchase_by_notification(payload, user=user)
    if not purchase and user:
        subscription_id = str(_cloudpayments_payload_value(payload, "Id", "SubscriptionId") or "").strip()
        if subscription_id:
            purchase = (
                SubscriptionPurchase.query.filter_by(app_user_id=user.id, cloudpayments_subscription_id=subscription_id)
                .order_by(SubscriptionPurchase.paid_at.desc(), SubscriptionPurchase.id.desc())
                .first()
            )
    if not purchase:
        return None

    subscription_id = str(_cloudpayments_payload_value(payload, "Id", "SubscriptionId") or "").strip()
    status_value = str(_cloudpayments_payload_value(payload, "SubscriptionStatus", "Status") or "").strip() or None
    next_transaction_at = _parse_cloudpayments_datetime(_cloudpayments_payload_value(payload, "NextTransactionDateIso"))
    canceled_at = None
    if status_value and status_value.lower() in {"cancelled", "canceled"}:
        canceled_at = datetime.utcnow()

    matching_purchases = SubscriptionPurchase.query.filter_by(
        app_user_id=purchase.app_user_id,
        cloudpayments_subscription_id=subscription_id or purchase.cloudpayments_subscription_id,
    ).all()
    for item in matching_purchases:
        item.cloudpayments_subscription_id = subscription_id or item.cloudpayments_subscription_id
        item.subscription_status = status_value or item.subscription_status
        item.next_transaction_at = next_transaction_at or item.next_transaction_at
        item.canceled_at = canceled_at or item.canceled_at
        item.provider_payload_json = _merge_provider_payload(
            item.provider_payload_json,
            recurrent=item.provider_payload_json.get("recurrent") if item.provider_payload_json else None,
            recurrent_notification=payload,
            last_webhook_at=datetime.utcnow().isoformat(),
        )
    return purchase


def _verify_cloudpayments_notification() -> bool:
    raw_body = request.get_data(cache=True)
    try:
        return verify_cloudpayments_webhook_signature(raw_body, request.headers)
    except RuntimeError:
        return False


@main_bp.get("/")
def index():
    return _render_index("telegram")


@main_bp.get("/ru")
@main_bp.get("/ru/")
def redirect_ru_to_index():
    return redirect(url_for("main.index"), code=301)


@main_bp.get("/max/miniapp")
def max_index():
    characters = _serialize_characters(list_characters())
    items = [{**character, "link": url_for("main.max_miniapp", slug=character["slug"])} for character in characters]
    return render_template("max_picker.html", characters=items, max_auth_url=url_for("main.max_miniapp_auth"))


@main_bp.get("/health")
def health():
    return jsonify({"status": "ok"})


@main_bp.get("/offer")
def public_offer():
    return render_template("offer.html", **_legal_pricing_context())


@main_bp.get("/privacy-policy")
def privacy_policy():
    return render_template("privacy_policy.html", **_legal_pricing_context())


@main_bp.get("/user-agreement")
def user_agreement():
    return render_template("user_agreement.html", **_legal_pricing_context())


@main_bp.get("/personal-data-consent")
def personal_data_consent():
    return render_template("personal_data_consent.html", **_legal_pricing_context())


@main_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if _current_admin():
        return redirect(url_for("main.admin_heroes"))

    error = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        admin = _find_admin(username)
        if admin and admin.is_active and admin.check_password(password):
            session[ADMIN_SESSION_KEY] = admin.id
            next_url = request.args.get("next") or url_for("main.admin_heroes")
            return redirect(next_url)
        error = "Неверный логин или пароль."

    return render_template("admin_login.html", error=error)


@main_bp.post("/admin/logout")
def admin_logout():
    session.pop(ADMIN_SESSION_KEY, None)
    return redirect(url_for("main.admin_login"))


@main_bp.get("/admin")
@main_bp.get("/admin/")
def admin_root():
    return redirect(url_for("main.admin_heroes"))


def _render_index(platform: str):
    characters = _serialize_characters(list_characters())
    items = []
    for character in characters:
        link = (
            url_for("main.max_miniapp", slug=character["slug"])
            if platform == "max"
            else url_for("main.miniapp", slug=character["slug"], source="telegram-miniapp")
        )
        items.append({**character, "link": link})

    public_base_url = current_app.config["PUBLIC_BASE_URL"].rstrip("/")
    current_user = _ensure_user_uuid(_current_app_user())
    telegram_bot_link = (current_app.config.get("TG_BOT_LINK") or "").strip()
    telegram_bot_name = (current_app.config.get("TG_BOT_NAME") or "").strip().lstrip("@")
    if not telegram_bot_link and telegram_bot_name:
        telegram_bot_link = f"https://t.me/{telegram_bot_name}"

    max_bot_link = (current_app.config.get("MAX_BOT_LINK") or "").strip()
    if not max_bot_link:
        max_bot_link = _absolute_url(public_base_url, current_app.config.get("MAX_BOT_APP_LINK") or "/max/miniapp")

    auth_url = url_for("main.login_email", next=request.url)
    if current_user:
        deeplink_value = f"user_id_{current_user.user_uuid}"
        if telegram_bot_link:
            telegram_bot_link = _append_start_param(telegram_bot_link, deeplink_value)
        if max_bot_link:
            max_bot_link = _append_start_param(max_bot_link, deeplink_value)
    else:
        telegram_bot_link = auth_url
        max_bot_link = auth_url

    return render_template(
        "index.html",
        characters=items,
        platform=platform,
        telegram_bot_link=telegram_bot_link,
        telegram_bot_name=telegram_bot_name,
        max_bot_link=max_bot_link,
        public_base_url=public_base_url,
        current_user=current_user,
    )


@main_bp.get("/miniapp/<slug>")
def miniapp(slug: str):
    return _render_miniapp(slug, request.args.get("source") or "web")


@main_bp.get("/miniapp")
def telegram_picker():
    characters = _serialize_characters(list_characters())
    items = [{**character, "link": url_for("main.miniapp", slug=character["slug"], source="telegram-miniapp")} for character in characters]
    return render_template("telegram_picker.html", characters=items, telegram_auth_url=url_for("main.telegram_miniapp_auth"))


@main_bp.get("/max/miniapp/<slug>")
def max_miniapp(slug: str):
    return _render_miniapp(slug, "max-miniapp")


def _render_miniapp(slug: str, started_from: str):
    app_user = _current_app_user()
    if not app_user:
        if started_from == "max-miniapp":
            return render_template(
                "max_auth_bridge.html",
                max_auth_url=url_for("main.max_miniapp_auth"),
                next_url=request.url,
            )
        if started_from == "telegram-miniapp":
            return render_template(
                "telegram_auth_bridge.html",
                telegram_auth_url=url_for("main.telegram_miniapp_auth"),
                next_url=request.url,
            )
        return redirect(url_for("main.login_email", next=request.url))
    if not _app_user_ready_for_calls(app_user):
        return redirect(url_for("main.verify_email", email=app_user.email, purpose="verify_email", next=request.url))

    character = get_character(slug, include_inactive=False)
    if not character:
        abort(404)

    ws_base = current_app.config["PUBLIC_BASE_URL"]
    if ws_base.startswith("https://"):
        ws_base = "wss://" + ws_base.removeprefix("https://")
    elif ws_base.startswith("http://"):
        ws_base = "ws://" + ws_base.removeprefix("http://")

    access_state = _app_user_access_state(app_user)

    return render_template(
        "miniapp.html",
        character=_serialize_character(character),
        websocket_url=f"{ws_base}/ws/call/{character['slug']}",
        started_from=started_from,
        app_user=app_user,
        call_access_available=access_state["has_call_access"],
        account_url=url_for("main.account"),
    )


@main_bp.post("/auth/max-miniapp")
def max_miniapp_auth():
    payload = request.get_json(silent=True) or {}
    init_data = (payload.get("init_data") or "").strip()

    try:
        launch_data = _validate_max_init_data(init_data)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    user_payload = launch_data.get("user") or {}
    max_user_id = user_payload.get("id")
    if not max_user_id:
        return jsonify({"ok": False, "error": "MAX не передал user.id."}), 400

    linked_user = None
    start_param = (launch_data.get("start_param") or "").strip()
    if start_param:
        linked_user = link_max_account(payload=start_param, max_user_id=max_user_id)

    user = _find_app_user_by_max_user_id(max_user_id)
    if not user and linked_user:
        user = AppUser.query.filter_by(id=linked_user["id"]).first() if linked_user.get("id") else None

    if not user:
        _send_max_registration_message(max_user_id)
        return jsonify(
            {
                "ok": False,
                "error": "Для этого MAX-аккаунта не найдена привязанная учётная запись.",
                "close_app": True,
            }
        ), 404

    _login_app_user(user)
    return jsonify(
        {
            "ok": True,
            "user": {
                "id": user.id,
                "name": user.name,
                "email_verified": bool(user.email_verified),
            },
            "close_app": False,
        }
    )


@main_bp.post("/auth/telegram-miniapp")
def telegram_miniapp_auth():
    payload = request.get_json(silent=True) or {}
    init_data = (payload.get("init_data") or "").strip()

    try:
        launch_data = _validate_telegram_init_data(init_data)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    user_payload = launch_data.get("user") or {}
    telegram_user_id = user_payload.get("id")
    if not telegram_user_id:
        return jsonify({"ok": False, "error": "Telegram не передал user.id."}), 400

    linked_user = None
    start_param = (launch_data.get("start_param") or "").strip()
    if start_param:
        linked_user = link_telegram_account(
            payload=start_param,
            telegram_user_id=telegram_user_id,
            telegram_username=user_payload.get("username"),
        )

    user = _find_app_user_by_telegram_user_id(telegram_user_id)
    if not user and linked_user:
        user = AppUser.query.filter_by(id=linked_user["id"]).first() if linked_user.get("id") else None

    if not user:
        _send_telegram_registration_message(telegram_user_id)
        return jsonify(
            {
                "ok": False,
                "error": "Для этого Telegram-аккаунта не найдена привязанная учётная запись.",
                "close_app": True,
            }
        ), 404

    _login_app_user(user)
    return jsonify(
        {
            "ok": True,
            "user": {
                "id": user.id,
                "name": user.name,
                "email_verified": bool(user.email_verified),
            },
            "close_app": False,
        }
    )


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    current_user = _current_app_user()
    next_url = request.values.get("next") or url_for("main.index")
    if current_user:
        if current_user.email_verified:
            return redirect(next_url if next_url else url_for("main.account"))
        return redirect(url_for("main.verify_email", email=current_user.email, purpose="verify_email", next=next_url))

    error = ""
    form_data = {"email": "", "phone": "", "name": ""}
    if request.method == "POST":
        form_data = {
            "email": (request.form.get("email") or "").strip(),
            "phone": _format_phone_for_display(request.form.get("phone") or ""),
            "name": (request.form.get("name") or "").strip(),
        }
        consent = request.form.get("consent_to_personal_data") == "on"
        normalized_phone = _normalize_phone(form_data["phone"])

        if not form_data["name"] or not form_data["phone"] or not form_data["email"]:
            error = "Заполните имя, телефон и электронную почту."
        elif not _is_valid_email(form_data["email"]):
            error = "Укажите корректную электронную почту."
        elif len(normalized_phone) != 11:
            error = "Укажите корректный телефон. Допустимы только цифры."
        elif not consent:
            error = "Нужно согласие на хранение и обработку персональных данных."
        elif _find_app_user_by_email(form_data["email"]):
            error = "Пользователь с такой электронной почтой уже зарегистрирован. Войдите по одноразовому коду."
        else:
            user = AppUser(
                email=form_data["email"].lower(),
                phone=normalized_phone,
                name=form_data["name"],
                consent_to_personal_data=True,
                consented_at=datetime.utcnow(),
            )
            db.session.add(user)
            db.session.commit()
            try:
                issue_email_code(user=user, purpose="verify_email")
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            else:
                _login_app_user(user)
                return redirect(url_for("main.verify_email", email=user.email, purpose="verify_email", next=next_url))

    return render_template("register.html", error=error, form_data=form_data, next_url=next_url, current_user=None)


@main_bp.route("/login", methods=["GET", "POST"])
def login_email():
    current_user = _current_app_user()
    next_url = request.values.get("next") or url_for("main.index")
    if current_user and current_user.email_verified:
        return redirect(next_url)

    error = ""
    info = ""
    code_sent = request.method == "GET" and request.args.get("sent") == "1"
    email = (request.values.get("email") or "").strip().lower()
    if request.method == "POST":
        action = (request.form.get("action") or "send").strip()
        if action == "verify":
            code = (request.form.get("code") or "").strip()
            purpose = (request.form.get("purpose") or "login_email").strip()
            try:
                user = verify_email_code(email=email, code=code, purpose=purpose)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                code_sent = True
            else:
                _login_app_user(user)
                return redirect(next_url)
        elif action == "resend":
            user = _find_app_user_by_email(email)
            if not user:
                error = "Пользователь с такой почтой не найден. Сначала зарегистрируйтесь."
            else:
                purpose = "login_email" if user.email_verified else "verify_email"
                try:
                    issue_email_code(user=user, purpose=purpose)
                except Exception as exc:  # noqa: BLE001
                    message = str(exc)
                    if _is_resend_wait_error(message):
                        info = message
                        code_sent = True
                    else:
                        error = message
                else:
                    info = f"Новый код отправлен на почту. {EMAIL_SPAM_HINT}"
                    code_sent = True
        else:
            user = _find_app_user_by_email(email)
            if not user:
                error = "Пользователь с такой почтой не найден. Сначала зарегистрируйтесь."
            else:
                purpose = "login_email" if user.email_verified else "verify_email"
                try:
                    issue_email_code(user=user, purpose=purpose)
                except Exception as exc:  # noqa: BLE001
                    message = str(exc)
                    if _is_resend_wait_error(message):
                        info = message
                        code_sent = True
                    else:
                        error = message
                else:
                    info = f"Код отправлен на почту. Введите его ниже. {EMAIL_SPAM_HINT}"
                    code_sent = True

    purpose = "verify_email"
    user = _find_app_user_by_email(email) if email else None
    if user and user.email_verified:
        purpose = "login_email"

    return render_template(
        "login_email.html",
        error=error,
        info=info,
        email=email,
        next_url=next_url,
        code_sent=code_sent,
        purpose=purpose,
    )


@main_bp.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    email = (request.values.get("email") or "").strip().lower()
    purpose = (request.values.get("purpose") or "verify_email").strip()
    next_url = request.values.get("next") or url_for("main.index")
    error = ""
    info = ""

    if request.method == "POST":
        action = request.form.get("action") or "verify"
        user = _find_app_user_by_email(email)
        if not user:
            error = "Пользователь не найден."
        elif action == "resend":
            try:
                issue_email_code(user=user, purpose=purpose)
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if _is_resend_wait_error(message):
                    info = message
                else:
                    error = message
            else:
                info = f"Новый код отправлен на почту. {EMAIL_SPAM_HINT}"
        else:
            code = (request.form.get("code") or "").strip()
            try:
                user = verify_email_code(email=email, code=code, purpose=purpose)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            else:
                _login_app_user(user)
                return redirect(next_url)

    return render_template(
        "verify_email.html",
        email=email,
        purpose=purpose,
        next_url=next_url,
        error=error,
        info=info,
    )


@main_bp.get("/account")
def account():
    user = _current_app_user()
    if not user:
        return redirect(url_for("main.login_email", next=request.url))
    if not user.email_verified:
        return redirect(url_for("main.verify_email", email=user.email, purpose="verify_email", next=request.url))

    try:
        calls = (
            CallSession.query.filter_by(app_user_id=user.id)
            .order_by(CallSession.started_at.desc(), CallSession.id.desc())
            .all()
        )
        purchases = (
            SubscriptionPurchase.query.filter_by(app_user_id=user.id)
            .order_by(SubscriptionPurchase.created_at.desc(), SubscriptionPurchase.id.desc())
            .all()
        )
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        calls = []
        purchases = []

    now = datetime.utcnow()
    for call in calls:
        duration_seconds = 0
        if call.started_at:
            duration_seconds = max(0, int(((call.ended_at or now) - call.started_at).total_seconds()))
        call.duration_seconds = duration_seconds
        call.duration_display = _format_call_duration(duration_seconds)

    access_state = _app_user_access_state(user)
    pricing_plans = [_serialize_pricing_plan(plan) for plan in _list_pricing_plans(include_inactive=False)]

    return render_template(
        "account.html",
        current_user=user,
        calls=calls,
        purchases=purchases,
        has_trial_available=access_state["trial_remaining_minutes"] > 0,
        remaining_trial_minutes=access_state["trial_remaining_minutes"],
        trial_calls_limit=_trial_calls_limit(),
        package_remaining_minutes=access_state["package_remaining_minutes"],
        cloudpayments_enabled=_cloudpayments_ready(),
        cloudpayments_public_id=current_app.config.get("CLOUDPAYMENTS_PUBLIC_ID", ""),
        current_subscription=access_state["current_subscription"],
        can_cancel_autorenew=bool(
            access_state["current_subscription"]
            and access_state["current_subscription"].get("kind") == "unlimited"
        ),
        can_resume_autorenew=bool(
            access_state["current_subscription"]
            and access_state["current_subscription"].get("kind") == "unlimited"
            and not access_state["current_subscription"].get("auto_renew_enabled")
            and (
                access_state["current_subscription"].get("cloudpayments_token")
                or access_state["current_subscription"].get("subscription_id")
            )
        ),
        pricing_plans=pricing_plans,
    )


@main_bp.post("/account/logout")
def account_logout():
    _logout_app_user()
    return redirect(url_for("main.index"))


@main_bp.post("/api/account/subscription/checkout")
def subscription_checkout():
    user = _current_app_user()
    if not user:
        return jsonify({"ok": False, "error": "Нужно войти в личный кабинет."}), 401
    if not user.email_verified:
        return jsonify({"ok": False, "error": "Сначала подтвердите электронную почту."}), 403
    if not _cloudpayments_ready():
        return jsonify({"ok": False, "error": "Оплата временно недоступна."}), 503

    payload = request.get_json(silent=True) or {}
    plan_code = (payload.get("plan_code") or "").strip()
    plan = _get_pricing_plan(plan_code)
    if not plan or not plan.is_active:
        return jsonify({"ok": False, "error": "Тариф не найден или выключен."}), 404

    recurring_consent_required = plan.kind == "unlimited"
    recurring_consent_accepted = bool(payload.get("recurring_consent"))
    if recurring_consent_required and not recurring_consent_accepted:
        return jsonify({"ok": False, "error": "Для подписки нужно согласиться на автоматические списания по оферте."}), 400

    offer_url = _absolute_url(current_app.config["PUBLIC_BASE_URL"], url_for("main.public_offer"))
    consent_snapshot = {
        "required": recurring_consent_required,
        "accepted": recurring_consent_accepted,
        "accepted_at": datetime.utcnow().isoformat() if recurring_consent_accepted else None,
        "ip_address": _request_client_ip(),
        "user_agent": (request.headers.get("User-Agent") or "")[:500],
        "text": "Я согласен на автоматические списания согласно условиям оферты",
        "offer_url": offer_url,
        "plan_code": plan.code,
        "plan_name": plan.name,
        "plan_kind": plan.kind,
        "period_days": plan.period_days,
        "amount": float(plan.price or 0),
        "currency": plan.currency,
    }

    invoice_id = f"sub-{user.id}-{uuid.uuid4().hex[:12]}"
    purchase = SubscriptionPurchase(
        app_user_id=user.id,
        invoice_id=invoice_id,
        plan_code=plan.code,
        plan_name=plan.name,
        amount=plan.price,
        currency=plan.currency,
        status="created",
        recurring_interval="Day" if plan.kind == "unlimited" and plan.period_days else None,
        recurring_period=int(plan.period_days) if plan.kind == "unlimited" and plan.period_days else None,
        provider_payload_json={
            "kind": "cloudpayments_widget",
            "created_at": datetime.utcnow().isoformat(),
            "pricing_plan": _serialize_pricing_plan(plan),
            "autopay_consent": consent_snapshot,
            "autopay_consent_history": [consent_snapshot] if recurring_consent_accepted else [],
        },
    )
    db.session.add(purchase)
    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "checkout": {
                "publicId": current_app.config.get("CLOUDPAYMENTS_PUBLIC_ID", ""),
                "description": plan.name,
                "amount": float(plan.price),
                "currency": plan.currency,
                "accountId": str(user.id),
                "invoiceId": invoice_id,
                "email": user.email,
                "skin": "modern",
                "data": {
                    "plan_code": plan.code,
                    "app_user_id": user.id,
                    "phone": user.phone,
                    "name": user.name,
                },
            },
            "purchase": _serialize_subscription_purchase(purchase),
        }
    )


@main_bp.post("/api/account/subscription/confirm")
def subscription_confirm():
    user = _current_app_user()
    if not user:
        return jsonify({"ok": False, "error": "Нужно войти в личный кабинет."}), 401

    payload = request.get_json(silent=True) or {}
    invoice_id = (payload.get("invoiceId") or "").strip()
    if not invoice_id:
        return jsonify({"ok": False, "error": "Не передан номер счёта."}), 400

    purchase = SubscriptionPurchase.query.filter_by(app_user_id=user.id, invoice_id=invoice_id).first()
    if not purchase:
        return jsonify({"ok": False, "error": "Платёж не найден."}), 404

    try:
        payment = find_payment(invoice_id)
    except Exception as exc:  # noqa: BLE001
        purchase.status = "verification_error"
        purchase.provider_payload_json = {
            **dict(purchase.provider_payload_json or {}),
            "verification_error": str(exc),
            "verified_at": datetime.utcnow().isoformat(),
        }
        db.session.commit()
        return jsonify({"ok": False, "error": str(exc)}), 502

    provider_status = str(_cloudpayments_payload_value(payment, "Status") or "").strip()
    transaction_id = _cloudpayments_payload_value(payment, "TransactionId")
    purchase.transaction_id = str(transaction_id) if transaction_id else purchase.transaction_id
    purchase.provider_payload_json = {
        **dict(purchase.provider_payload_json or {}),
        "payment": payment,
        "verified_at": datetime.utcnow().isoformat(),
    }

    if provider_status == "Completed":
        purchase.status = "paid"
        purchase.paid_at = purchase.paid_at or datetime.utcnow()
        purchase.cloudpayments_token = (
            str(_cloudpayments_payload_value(payment, "Token") or purchase.cloudpayments_token or "").strip()
            or purchase.cloudpayments_token
        )
        purchase.cloudpayments_subscription_id = (
            str(_cloudpayments_payload_value(payment, "SubscriptionId") or purchase.cloudpayments_subscription_id or "").strip()
            or purchase.cloudpayments_subscription_id
        )
        purchase.subscription_status = (
            str(_cloudpayments_payload_value(payment, "SubscriptionStatus", "Status") or purchase.subscription_status or "").strip()
            or purchase.subscription_status
        )
        purchase.next_transaction_at = (
            _parse_cloudpayments_datetime(_cloudpayments_payload_value(payment, "NextTransactionDateIso"))
            or purchase.next_transaction_at
        )
    elif provider_status:
        purchase.status = provider_status.lower()
    else:
        purchase.status = "unknown"

    db.session.commit()
    return jsonify({"ok": True, "purchase": _serialize_subscription_purchase(purchase)})


@main_bp.post("/api/account/subscription/cancel")
def subscription_cancel():
    user = _current_app_user()
    if not user:
        return jsonify({"ok": False, "error": "Нужно войти в личный кабинет."}), 401

    access_state = _app_user_access_state(user)
    current_subscription = access_state.get("current_subscription") or {}
    purchase_id = current_subscription.get("purchase_id")
    subscription_id = str(current_subscription.get("subscription_id") or "").strip()
    if not purchase_id:
        return jsonify({"ok": False, "error": "Активная подписка с автопродлением не найдена."}), 404

    if subscription_id:
        try:
            cancel_cloudpayments_subscription(subscription_id)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502

    canceled_at = datetime.utcnow()
    purchase = SubscriptionPurchase.query.filter_by(id=purchase_id, app_user_id=user.id).first()
    if not purchase:
        return jsonify({"ok": False, "error": "Активная подписка с автопродлением не найдена."}), 404

    purchase.subscription_status = "Canceled"
    purchase.canceled_at = canceled_at
    purchase.next_transaction_at = None
    purchase.provider_payload_json = _merge_provider_payload(
        _append_subscription_action(
            purchase.provider_payload_json,
            action="cancel_auto_renew",
            actor="user",
            purchase=purchase,
            details={"source": "account"},
        ),
        canceled_via="account",
        canceled_at=canceled_at.isoformat(),
    )

    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "message": "Автопродление отключено. Подписка останется активной до конца оплаченного периода.",
        }
    )


@main_bp.post("/api/account/subscription/resume")
def subscription_resume():
    user = _current_app_user()
    if not user:
        return jsonify({"ok": False, "error": "Нужно войти в личный кабинет."}), 401

    access_state = _app_user_access_state(user)
    current_subscription = access_state.get("current_subscription") or {}
    purchase_id = current_subscription.get("purchase_id")
    if not purchase_id:
        return jsonify({"ok": False, "error": "Подходящая подписка для автопродления не найдена."}), 404

    purchase = SubscriptionPurchase.query.filter_by(id=purchase_id, app_user_id=user.id).first()
    if not purchase:
        return jsonify({"ok": False, "error": "Подходящая подписка для автопродления не найдена."}), 404
    if not purchase.cloudpayments_token and not purchase.cloudpayments_subscription_id:
        return jsonify({"ok": False, "error": "Для этой подписки не найден источник автосписания."}), 400

    next_transaction_at = current_subscription.get("expires_at")
    if next_transaction_at is None and purchase.paid_at and purchase.recurring_period:
        next_transaction_at = purchase.paid_at + timedelta(days=int(purchase.recurring_period))

    purchase.subscription_status = "Active"
    purchase.canceled_at = None
    purchase.next_transaction_at = next_transaction_at
    purchase.provider_payload_json = _merge_provider_payload(
        _append_subscription_action(
            purchase.provider_payload_json,
            action="resume_auto_renew",
            actor="user",
            purchase=purchase,
            details={"source": "account"},
        ),
        resumed_via="account",
        resumed_at=datetime.utcnow().isoformat(),
    )
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "message": "Автопродление снова включено. Следующее списание запланировано на конец текущего периода.",
        }
    )


@main_bp.post("/api/cloudpayments/webhooks/pay")
def cloudpayments_pay_webhook():
    if not _verify_cloudpayments_notification():
        return _cloudpayments_json_code(13, status=403)

    payload = _cloudpayments_notification_payload()
    purchase = _record_cloudpayments_payment(payload, mark_paid=True)
    if purchase is None:
        return _cloudpayments_json_code()

    db.session.commit()
    return _cloudpayments_json_code()


@main_bp.post("/api/cloudpayments/webhooks/fail")
def cloudpayments_fail_webhook():
    if not _verify_cloudpayments_notification():
        return _cloudpayments_json_code(13, status=403)

    payload = _cloudpayments_notification_payload()
    purchase = _record_cloudpayments_payment(payload, mark_paid=False)
    if purchase is None:
        return _cloudpayments_json_code()

    db.session.commit()
    return _cloudpayments_json_code()


@main_bp.post("/api/cloudpayments/webhooks/recurrent")
def cloudpayments_recurrent_webhook():
    if not _verify_cloudpayments_notification():
        return _cloudpayments_json_code(13, status=403)

    payload = _cloudpayments_notification_payload()
    _update_cloudpayments_subscription_state(payload)
    db.session.commit()
    return _cloudpayments_json_code()


def _absolute_url(base_url: str, target: str) -> str:
    clean_target = (target or "").strip()
    if clean_target.startswith(("http://", "https://")):
        return clean_target
    return f"{base_url}/{clean_target.lstrip('/')}"


@main_bp.get("/miniapp/heroes")
@_admin_required(view_mode="html")
def heroes_miniapp():
    return redirect(url_for("main.admin_heroes"))


@main_bp.get("/admin/heroes")
@_admin_required(view_mode="html")
def admin_heroes():
    context = {
        "heroes": _serialize_characters(list_characters(include_inactive=True)),
        "voice_options": OPENAI_VOICE_OPTIONS,
        "realtime_model_options": REALTIME_MODEL_OPTIONS,
        "transcription_model_options": TRANSCRIPTION_MODEL_OPTIONS,
        "noise_reduction_options": NOISE_REDUCTION_OPTIONS,
    }
    return render_template("heroes.html", initial_state=json.dumps(context, ensure_ascii=False), admin_section="heroes")


@main_bp.get("/admin/pricing-plans")
@_admin_required(view_mode="html")
def admin_pricing_plans():
    context = {
        "pricing_plans": [_serialize_pricing_plan(plan) for plan in _list_pricing_plans(include_inactive=True)],
        "pricing_plan_kind_options": _pricing_plan_kind_options(),
        "heroes": [],
        "voice_options": [],
        "realtime_model_options": [],
        "transcription_model_options": [],
        "noise_reduction_options": [],
    }
    return render_template("pricing_plans.html", initial_state=json.dumps(context, ensure_ascii=False), admin_section="pricing_plans")


@main_bp.get("/max/miniapp/heroes")
def max_heroes_miniapp():
    return heroes_miniapp()


@main_bp.get("/miniapp/voices")
def voices_miniapp():
    library = build_voice_library_payload()
    return render_template("voices.html", library=library)


@main_bp.get("/max/miniapp/voices")
def max_voices_miniapp():
    return voices_miniapp()


@main_bp.get("/api/heroes")
@_admin_required()
def heroes_api():
    return jsonify(
        {
            "items": _serialize_characters(list_characters(include_inactive=True)),
            "pricing_plans": [_serialize_pricing_plan(plan) for plan in _list_pricing_plans(include_inactive=True)],
            "pricing_plan_kind_options": _pricing_plan_kind_options(),
            "voice_options": OPENAI_VOICE_OPTIONS,
            "realtime_model_options": REALTIME_MODEL_OPTIONS,
            "transcription_model_options": TRANSCRIPTION_MODEL_OPTIONS,
            "noise_reduction_options": NOISE_REDUCTION_OPTIONS,
        }
    )


@main_bp.post("/api/heroes")
@_admin_required()
def create_hero_api():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Hero name is required."}), 400

    slug = _build_unique_slug((payload.get("slug") or name).strip())
    hero = Hero(
        slug=slug,
        name=name,
        description=(payload.get("description") or "").strip(),
        emoji=(payload.get("emoji") or "AI").strip()[:16] or "AI",
        voice=(payload.get("voice") or "alloy").strip() or "alloy",
        greeting_prompt=DEFAULT_GREETING_PROMPT,
        sort_order=_next_hero_sort_order(),
        is_active=bool(payload.get("is_active", True)),
    )
    db.session.add(hero)
    db.session.commit()
    return jsonify({"ok": True, "hero": _serialize_character_from_model(hero)})


@main_bp.post("/api/pricing-plans")
@_admin_required()
def create_pricing_plan_api():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Название тарифа обязательно."}), 400

    code = _build_unique_pricing_plan_code((payload.get("code") or name).strip())
    plan = PricingPlan(
        code=code,
        name=name,
        description="",
        kind="call_package",
        price=Decimal("1.00"),
        currency="RUB",
        sort_order=_next_pricing_plan_sort_order(),
        is_active=bool(payload.get("is_active", True)),
    )

    try:
        _apply_pricing_plan_payload(
            plan,
            {
                "name": name,
                "description": payload.get("description"),
                "kind": payload.get("kind") or "call_package",
                "price": payload.get("price") or "99",
                "currency": payload.get("currency") or "RUB",
                "minutes_included": payload.get("minutes_included") or "15",
                "period_days": payload.get("period_days") or "30",
                "is_active": payload.get("is_active", True),
            },
        )
    except (ValueError, ArithmeticError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    db.session.add(plan)
    db.session.commit()
    return jsonify({"ok": True, "pricing_plan": _serialize_pricing_plan(plan)})


@main_bp.patch("/api/heroes/<slug>")
@_admin_required()
def update_hero_api(slug: str):
    hero = _get_hero_model(slug)
    if not hero:
        return jsonify({"ok": False, "error": "Hero not found."}), 404

    payload = request.get_json(silent=True) or {}
    hero.name = (payload.get("name") or hero.name or "").strip() or hero.name
    hero.emoji = (payload.get("emoji") or hero.emoji or "AI").strip()[:16] or hero.emoji or "AI"
    hero.description = (payload.get("description") or "").strip()
    hero.voice = (payload.get("voice") or hero.voice or "alloy").strip()
    hero.system_prompt = (payload.get("system_prompt") or "").strip() or None
    hero.greeting_prompt = (payload.get("greeting_prompt") or "").strip() or None
    hero.is_active = bool(payload.get("is_active", hero.is_active))

    realtime_settings = normalize_realtime_settings(
        {
            "model": payload.get("realtime_model"),
            "input_transcription_model": payload.get("input_transcription_model"),
            "input_transcription_language": payload.get("input_transcription_language"),
            "input_transcription_prompt": payload.get("input_transcription_prompt"),
            "noise_reduction_type": payload.get("noise_reduction_type"),
            "max_output_tokens": payload.get("max_output_tokens"),
            "output_audio_format": payload.get("output_audio_format"),
            "output_audio_speed": payload.get("output_audio_speed"),
            "instructions_override": payload.get("instructions_override"),
        }
    )
    hero.realtime_settings_json = realtime_settings or None

    db.session.commit()
    return jsonify({"ok": True, "hero": _serialize_character_from_model(hero)})


@main_bp.patch("/api/pricing-plans/<code>")
@_admin_required()
def update_pricing_plan_api(code: str):
    plan = _get_pricing_plan(code)
    if not plan:
        return jsonify({"ok": False, "error": "Тариф не найден."}), 404

    payload = request.get_json(silent=True) or {}
    try:
        _apply_pricing_plan_payload(plan, payload)
    except (ValueError, ArithmeticError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    db.session.commit()
    return jsonify({"ok": True, "pricing_plan": _serialize_pricing_plan(plan)})


@main_bp.delete("/api/heroes/<slug>")
@_admin_required()
def delete_hero_api(slug: str):
    hero = _get_hero_model(slug)
    if not hero:
        return jsonify({"ok": False, "error": "Hero not found."}), 404

    _delete_hero_uploads(slug)
    db.session.delete(hero)
    db.session.commit()
    return jsonify({"ok": True, "deleted_slug": slug})


@main_bp.delete("/api/pricing-plans/<code>")
@_admin_required()
def delete_pricing_plan_api(code: str):
    plan = _get_pricing_plan(code)
    if not plan:
        return jsonify({"ok": False, "error": "Тариф не найден."}), 404

    db.session.delete(plan)
    db.session.commit()
    return jsonify({"ok": True, "deleted_code": code})


@main_bp.post("/api/heroes/<slug>/knowledge")
@_admin_required()
def upload_hero_knowledge_api(slug: str):
    hero = _get_hero_model(slug)
    if not hero:
        return jsonify({"ok": False, "error": "Hero not found."}), 404

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"ok": False, "error": "Knowledge file is required."}), 400

    original_filename = uploaded.filename
    suffix = Path(original_filename).suffix.lower()
    if suffix not in TEXT_FILE_EXTENSIONS:
        return jsonify({"ok": False, "error": "Use a text file: txt, md, json, csv, yaml."}), 400

    filename = _safe_uploaded_name(original_filename, suffix)

    raw_bytes = uploaded.read()
    if not raw_bytes:
        return jsonify({"ok": False, "error": "The uploaded file is empty."}), 400

    knowledge_text = raw_bytes.decode("utf-8", errors="ignore").strip()
    if not knowledge_text:
        return jsonify({"ok": False, "error": "The uploaded file does not contain readable text."}), 400

    relative_path = _save_uploaded_file(slug, "knowledge", filename, raw_bytes)
    hero.knowledge_file_name = filename
    hero.knowledge_file_path = relative_path
    hero.knowledge_text = knowledge_text
    db.session.commit()

    return jsonify({"ok": True, "hero": _serialize_character_from_model(hero)})


@main_bp.post("/api/heroes/<slug>/avatar")
@_admin_required()
def upload_hero_avatar_api(slug: str):
    hero = _get_hero_model(slug)
    if not hero:
        return jsonify({"ok": False, "error": "Hero not found."}), 404

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"ok": False, "error": "Avatar image is required."}), 400

    original_filename = uploaded.filename
    suffix = Path(original_filename).suffix.lower()
    if suffix not in IMAGE_FILE_EXTENSIONS:
        return jsonify({"ok": False, "error": "Use an image: jpg, jpeg, png, webp, gif."}), 400

    filename = _safe_uploaded_name(original_filename, suffix)

    raw_bytes = uploaded.read()
    if not raw_bytes:
        return jsonify({"ok": False, "error": "The uploaded image is empty."}), 400

    relative_path = _save_avatar_image(slug, filename, raw_bytes)
    hero.avatar_path = relative_path
    db.session.commit()

    return jsonify({"ok": True, "hero": _serialize_character_from_model(hero)})


@main_bp.get("/api/voices")
def voices_api():
    return jsonify(build_voice_library_payload())


@main_bp.post("/api/voices/create-consent")
def create_voice_consent_api():
    payload = request.get_json(silent=True) or {}
    voice_name = (payload.get("name") or "").strip()
    if not voice_name:
        return jsonify({"ok": False, "error": "Voice folder name is required."}), 400

    directory = _find_voice_directory(voice_name)
    if not directory:
        return jsonify({"ok": False, "error": "Voice folder not found."}), 404

    sample = pick_voice_sample(directory)
    if not sample:
        return jsonify({"ok": False, "error": "No supported audio sample found in this folder."}), 400

    try:
        consent = create_voice_consent(
            name=directory.name,
            language=current_app.config["OPENAI_VOICE_CONSENT_LANGUAGE"],
            recording_path=sample,
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "consent": consent})


@main_bp.post("/api/voices/convert-wav")
def convert_voice_to_wav_api():
    payload = request.get_json(silent=True) or {}
    voice_name = (payload.get("name") or "").strip()
    if not voice_name:
        return jsonify({"ok": False, "error": "Voice folder name is required."}), 400

    directory = _find_voice_directory(voice_name)
    if not directory:
        return jsonify({"ok": False, "error": "Voice folder not found."}), 404

    try:
        result = convert_voice_sample_to_wav(directory)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "result": result})


@main_bp.post("/api/voices/create-voice")
def create_custom_voice_api():
    payload = request.get_json(silent=True) or {}
    voice_name = (payload.get("name") or "").strip()
    if not voice_name:
        return jsonify({"ok": False, "error": "Voice folder name is required."}), 400

    directory = _find_voice_directory(voice_name)
    if not directory:
        return jsonify({"ok": False, "error": "Voice folder not found."}), 404

    sample = pick_voice_sample(directory)
    if not sample:
        return jsonify({"ok": False, "error": "No supported audio sample found in this folder."}), 400

    library = build_voice_library_payload()
    entry = next((item for item in library["items"] if item["name"] == directory.name), None)
    consent = entry.get("consent") if entry else None
    if not consent:
        return jsonify({"ok": False, "error": "Matching consent not found. Create consent first."}), 400

    try:
        voice = create_custom_voice(
            name=directory.name,
            consent_id=consent["id"],
            audio_sample_path=sample,
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "voice": voice, "consent": consent})


@main_bp.post("/api/voices/preview")
@_admin_required()
def preview_voice_api() -> Response:
    payload = request.get_json(silent=True) or {}
    voice = (payload.get("voice") or "").strip()
    text = (payload.get("text") or "").strip()
    if not voice:
        return jsonify({"ok": False, "error": "Voice is required."}), 400

    if not text:
        text = "Привет. Я буду говорить именно этим голосом. Если тебе нравится, давай оставим его для персонажа."

    try:
        audio_bytes = generate_speech_preview(text=text, voice=voice)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

    return Response(audio_bytes, mimetype="audio/mpeg")


def _find_voice_directory(name: str):
    target = normalize_voice_name(name)
    for directory in iter_voice_directories():
        if normalize_voice_name(directory.name) == target:
            return directory
    return None


def _get_hero_model(slug: str) -> Hero | None:
    try:
        return Hero.query.filter_by(slug=slug).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        return None


def _build_unique_pricing_plan_code(raw_value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", secure_filename(raw_value).lower()).strip("-")
    if not base:
        base = "plan"

    code = base
    suffix = 2
    while _get_pricing_plan(code):
        code = f"{base}-{suffix}"
        suffix += 1
    return code


def _build_unique_slug(raw_value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", secure_filename(raw_value).lower()).strip("-")
    if not base:
        base = "hero"

    slug = base
    suffix = 2
    while _get_hero_model(slug):
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _next_hero_sort_order() -> int:
    try:
        latest = Hero.query.order_by(Hero.sort_order.desc(), Hero.id.desc()).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        latest = None
    return (latest.sort_order + 1) if latest else len(list_characters(include_inactive=True))


def _serialize_characters(characters: list[dict]) -> list[dict]:
    return [_serialize_character(character) for character in characters]


def _serialize_character(character: dict) -> dict:
    payload = dict(character)
    avatar_path = payload.get("avatar_path")
    avatar_path = _preferred_static_asset(avatar_path)
    payload["avatar_url"] = url_for("static", filename=avatar_path) if avatar_path else None
    payload["has_knowledge_file"] = bool(payload.get("knowledge_file_name"))
    payload["knowledge_summary"] = _knowledge_summary(payload.get("knowledge_text") or "")
    realtime_settings = normalize_realtime_settings(payload.get("realtime_settings"))
    payload["realtime_settings"] = realtime_settings
    payload["realtime_model"] = realtime_settings.get("model", "")
    payload["input_transcription_model"] = realtime_settings.get("input_transcription_model", "")
    payload["input_transcription_language"] = realtime_settings.get("input_transcription_language", "")
    payload["input_transcription_prompt"] = realtime_settings.get("input_transcription_prompt", "")
    payload["noise_reduction_type"] = realtime_settings.get("noise_reduction_type", "none")
    payload["max_output_tokens"] = realtime_settings.get("max_output_tokens", "inf")
    payload["output_audio_format"] = realtime_settings.get("output_audio_format", "pcm16")
    payload["output_audio_speed"] = realtime_settings.get("output_audio_speed", 1.0)
    payload["instructions_override"] = realtime_settings.get("instructions_override", "")
    return payload


def _serialize_character_from_model(hero: Hero) -> dict:
    payload = {
        "slug": hero.slug,
        "name": hero.name,
        "description": hero.description or "",
        "emoji": hero.emoji or "AI",
        "voice": hero.voice or "alloy",
        "avatar_path": hero.avatar_path,
        "knowledge_file_name": hero.knowledge_file_name,
        "knowledge_text": hero.knowledge_text or "",
        "system_prompt": hero.system_prompt or "",
        "greeting_prompt": hero.greeting_prompt or "",
        "realtime_settings": hero.realtime_settings_json or {},
        "sort_order": hero.sort_order,
        "is_active": hero.is_active,
    }
    return _serialize_character(payload)


def _save_uploaded_file(slug: str, kind: str, filename: str, raw_bytes: bytes) -> str:
    target_dir = Path(current_app.static_folder) / "uploads" / "heroes" / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{kind}{Path(filename).suffix.lower()}"
    target_path.write_bytes(raw_bytes)
    return target_path.relative_to(current_app.static_folder).as_posix()


def _save_avatar_image(slug: str, filename: str, raw_bytes: bytes) -> str:
    target_dir = Path(current_app.static_folder) / "uploads" / "heroes" / slug
    target_dir.mkdir(parents=True, exist_ok=True)

    original_suffix = Path(filename).suffix.lower() or ".png"
    source_path = target_dir / f"avatar-upload{original_suffix}"
    source_path.write_bytes(raw_bytes)

    cwebp_binary = shutil.which("cwebp")
    if cwebp_binary:
        target_path = target_dir / "avatar.webp"
        result = subprocess.run(
            [cwebp_binary, "-quiet", "-q", "82", str(source_path), "-o", str(target_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            source_path.unlink(missing_ok=True)
            return target_path.relative_to(current_app.static_folder).as_posix()

    fallback_path = target_dir / f"avatar{original_suffix}"
    source_path.replace(fallback_path)
    return fallback_path.relative_to(current_app.static_folder).as_posix()


def _safe_uploaded_name(original_filename: str, suffix: str) -> str:
    safe_name = secure_filename(original_filename or "")
    if safe_name and Path(safe_name).suffix.lower() == suffix:
        return safe_name
    return f"upload-{uuid.uuid4().hex}{suffix}"


def _preferred_static_asset(relative_path: str | None) -> str | None:
    if not relative_path:
        return None

    candidate = Path(current_app.static_folder) / relative_path
    if candidate.suffix.lower() != ".webp":
        webp_candidate = candidate.with_suffix(".webp")
        if webp_candidate.exists():
            return webp_candidate.relative_to(current_app.static_folder).as_posix()
    return relative_path


def _delete_hero_uploads(slug: str) -> None:
    target_dir = Path(current_app.static_folder) / "uploads" / "heroes" / slug
    if not target_dir.exists():
        return
    for item in target_dir.iterdir():
        if item.is_file():
            item.unlink()
    target_dir.rmdir()


def _knowledge_summary(text: str) -> str:
    compact = " ".join(text.split())
    if not compact:
        return "Файл базы знаний ещё не загружен."
    if len(compact) <= 140:
        return compact
    return compact[:137].rstrip() + "..."
