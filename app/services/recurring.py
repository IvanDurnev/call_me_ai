from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import logging
from pathlib import Path
import threading
import time
import uuid

import fcntl

from ..extensions import db
from ..models import PricingPlan, SubscriptionPurchase
from .cloudpayments import charge_cloudpayments_token


_worker_started = False
_worker_lock = threading.Lock()
_worker_file_handle = None


def _acquire_recurring_file_lock() -> bool:
    global _worker_file_handle
    if _worker_file_handle is not None:
        return True

    handle = Path("/tmp/call_me_ai_recurring.lock").open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False

    _worker_file_handle = handle
    return True


def _purchase_expires_at(purchase: SubscriptionPurchase, plan: PricingPlan | None) -> datetime | None:
    period_days = purchase.recurring_period or (plan.period_days if plan else None)
    paid_at = purchase.paid_at or purchase.created_at
    if not paid_at or not period_days:
        return None
    return paid_at + timedelta(days=int(period_days))


def _latest_recurring_sources() -> list[SubscriptionPurchase]:
    purchases = (
        SubscriptionPurchase.query.filter_by(status="paid")
        .order_by(
            SubscriptionPurchase.app_user_id.asc(),
            SubscriptionPurchase.paid_at.desc(),
            SubscriptionPurchase.id.desc(),
        )
        .all()
    )
    latest_by_user: dict[int, SubscriptionPurchase] = {}
    for purchase in purchases:
        if purchase.app_user_id in latest_by_user:
            continue
        if not purchase.recurring_period or not purchase.cloudpayments_token or purchase.canceled_at is not None:
            continue
        latest_by_user[purchase.app_user_id] = purchase
    return list(latest_by_user.values())


def process_due_recurring_purchases(now: datetime | None = None) -> list[str]:
    current_time = now or datetime.utcnow()
    messages: list[str] = []
    plan_cache: dict[str, PricingPlan | None] = {}

    for source_purchase in _latest_recurring_sources():
        plan_code = source_purchase.plan_code or ""
        if plan_code not in plan_cache:
            plan_cache[plan_code] = PricingPlan.query.filter_by(code=plan_code).first() if plan_code else None
        plan = plan_cache.get(plan_code)
        if not plan or plan.kind != "unlimited" or not plan.is_active:
            continue

        expires_at = _purchase_expires_at(source_purchase, plan)
        if not expires_at or expires_at > current_time:
            continue

        pending_purchase = (
            SubscriptionPurchase.query.filter_by(
                app_user_id=source_purchase.app_user_id,
                plan_code=source_purchase.plan_code,
                status="pending",
            )
            .order_by(SubscriptionPurchase.id.desc())
            .first()
        )
        if pending_purchase and (pending_purchase.created_at or current_time) >= expires_at:
            continue

        invoice_id = f"rec-{source_purchase.app_user_id}-{uuid.uuid4().hex[:12]}"
        purchase = SubscriptionPurchase(
            app_user_id=source_purchase.app_user_id,
            invoice_id=invoice_id,
            plan_code=source_purchase.plan_code,
            plan_name=source_purchase.plan_name,
            amount=Decimal(str(source_purchase.amount or plan.price or 0)),
            currency=source_purchase.currency or plan.currency,
            status="pending",
            cloudpayments_token=source_purchase.cloudpayments_token,
            cloudpayments_subscription_id=source_purchase.cloudpayments_subscription_id,
            subscription_status="Pending",
            recurring_interval=source_purchase.recurring_interval,
            recurring_period=source_purchase.recurring_period,
            next_transaction_at=expires_at,
            provider_payload_json={
                "kind": "cloudpayments_recurring_charge",
                "recurring_parent_invoice_id": source_purchase.invoice_id,
                "scheduled_at": current_time.isoformat(),
            },
        )
        db.session.add(purchase)
        db.session.flush()

        try:
            result = charge_cloudpayments_token(
                token=source_purchase.cloudpayments_token,
                invoice_id=invoice_id,
                amount=purchase.amount,
                currency=purchase.currency,
                description=source_purchase.plan_name,
                account_id=str(source_purchase.app_user_id),
            )
        except Exception as exc:  # noqa: BLE001
            purchase.status = "failed"
            purchase.provider_payload_json = {
                **dict(purchase.provider_payload_json or {}),
                "error": str(exc),
                "failed_at": datetime.utcnow().isoformat(),
            }
            db.session.commit()
            messages.append(f"Подписка пользователя #{source_purchase.app_user_id}: автосписание не запущено.")
            continue

        purchase.transaction_id = str(result.get("TransactionId") or purchase.transaction_id or "").strip() or purchase.transaction_id
        purchase.provider_payload_json = {
            **dict(purchase.provider_payload_json or {}),
            "charge_response": result,
            "requested_at": datetime.utcnow().isoformat(),
        }
        db.session.commit()
        messages.append(f"Подписка пользователя #{source_purchase.app_user_id}: запрос на автосписание отправлен.")

    return messages


def start_recurring_worker_once(app) -> None:
    global _worker_started
    if _worker_started:
        return

    with _worker_lock:
        if _worker_started:
            return
        if not _acquire_recurring_file_lock():
            logging.info("Recurring worker skipped: another process already owns the lock")
            return

        interval_seconds = max(30, int(app.config.get("SUBSCRIPTION_RENEW_CHECK_INTERVAL_SECONDS", 60)))

        def worker() -> None:
            while True:
                try:
                    with app.app_context():
                        process_due_recurring_purchases()
                except Exception as exc:  # noqa: BLE001
                    logging.exception("Recurring worker cycle failed: %s", exc)
                time.sleep(interval_seconds)

        thread = threading.Thread(target=worker, name="subscription-recurring", daemon=True)
        thread.start()
        _worker_started = True
