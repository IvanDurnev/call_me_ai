from __future__ import annotations

import base64
import json
import hashlib
import hmac
from decimal import Decimal, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app


CLOUDPAYMENTS_FIND_URL = "https://api.cloudpayments.ru/v2/payments/find"
CLOUDPAYMENTS_CANCEL_SUBSCRIPTION_URL = "https://api.cloudpayments.ru/subscriptions/cancel"


def cloudpayments_enabled() -> bool:
    return bool(
        current_app.config.get("CLOUDPAYMENTS_PUBLIC_ID")
        and current_app.config.get("CLOUDPAYMENTS_API_PASSWORD")
    )


def _cloudpayments_credentials() -> tuple[str, str]:
    public_id = (current_app.config.get("CLOUDPAYMENTS_PUBLIC_ID") or "").strip()
    api_password = (current_app.config.get("CLOUDPAYMENTS_API_PASSWORD") or "").strip()
    if not public_id or not api_password:
        raise RuntimeError("CloudPayments не настроен.")
    return public_id, api_password


def _cloudpayments_request(url: str, payload: dict) -> dict:
    public_id, api_password = _cloudpayments_credentials()
    credentials = base64.b64encode(f"{public_id}:{api_password}".encode("utf-8")).decode("ascii")
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        try:
            raw_body = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            raw_body = ""
        raise RuntimeError(raw_body or f"CloudPayments вернул HTTP {exc.code}.") from exc
    except URLError as exc:
        raise RuntimeError("Не удалось связаться с CloudPayments.") from exc

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CloudPayments вернул некорректный ответ.") from exc

    if not data.get("Success"):
        raise RuntimeError(data.get("Message") or "CloudPayments вернул ошибку.")

    return data.get("Model") or {}


def build_test_subscription_offer() -> dict:
    amount = Decimal(str(current_app.config.get("CLOUDPAYMENTS_TEST_SUBSCRIPTION_AMOUNT", 99)))
    normalized_amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "plan_code": "test-plan",
        "plan_name": current_app.config.get("CLOUDPAYMENTS_TEST_SUBSCRIPTION_NAME", "Тестовый абонемент"),
        "amount": normalized_amount,
        "currency": current_app.config.get("CLOUDPAYMENTS_CURRENCY", "RUB"),
    }


def find_payment(invoice_id: str) -> dict:
    return _cloudpayments_request(CLOUDPAYMENTS_FIND_URL, {"InvoiceId": invoice_id})


def cancel_cloudpayments_subscription(subscription_id: str) -> dict:
    return _cloudpayments_request(CLOUDPAYMENTS_CANCEL_SUBSCRIPTION_URL, {"Id": subscription_id})


def verify_cloudpayments_webhook_signature(raw_body: bytes, headers) -> bool:
    _, api_password = _cloudpayments_credentials()
    if raw_body is None:
        return False

    received_signatures = [
        (headers.get("X-Content-HMAC") or "").strip(),
        (headers.get("Content-HMAC") or "").strip(),
    ]
    expected = base64.b64encode(hmac.new(api_password.encode("utf-8"), raw_body, hashlib.sha256).digest()).decode("ascii")
    return any(signature and hmac.compare_digest(signature, expected) for signature in received_signatures)
