from __future__ import annotations

import base64
import json
from decimal import Decimal, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app


CLOUDPAYMENTS_FIND_URL = "https://api.cloudpayments.ru/v2/payments/find"


def cloudpayments_enabled() -> bool:
    return bool(
        current_app.config.get("CLOUDPAYMENTS_PUBLIC_ID")
        and current_app.config.get("CLOUDPAYMENTS_API_PASSWORD")
    )


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
    public_id = (current_app.config.get("CLOUDPAYMENTS_PUBLIC_ID") or "").strip()
    api_password = (current_app.config.get("CLOUDPAYMENTS_API_PASSWORD") or "").strip()
    if not public_id or not api_password:
        raise RuntimeError("CloudPayments не настроен.")

    credentials = base64.b64encode(f"{public_id}:{api_password}".encode("utf-8")).decode("ascii")
    payload = json.dumps({"InvoiceId": invoice_id}).encode("utf-8")
    request = Request(
        CLOUDPAYMENTS_FIND_URL,
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            body = ""
        raise RuntimeError(body or f"CloudPayments вернул HTTP {exc.code}.") from exc
    except URLError as exc:
        raise RuntimeError("Не удалось связаться с CloudPayments.") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CloudPayments вернул некорректный ответ.") from exc

    if not data.get("Success"):
        raise RuntimeError(data.get("Message") or "CloudPayments не нашёл платёж.")

    return data.get("Model") or {}
