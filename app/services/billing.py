from __future__ import annotations

from datetime import datetime, timedelta
import math

from sqlalchemy import or_

from ..extensions import db
from ..models import AppUser, CallSession, PricingPlan, SubscriptionPurchase


def _call_billed_minutes(call: CallSession) -> int:
    if not call.started_at or not call.ended_at:
        return 0
    duration_seconds = max(0.0, (call.ended_at - call.started_at).total_seconds())
    return max(0, math.floor(duration_seconds / 60))


def _purchase_plan_snapshot(
    purchase: SubscriptionPurchase,
    plan_cache: dict[str, PricingPlan | None],
) -> dict:
    provider_snapshot = dict((purchase.provider_payload_json or {}).get("pricing_plan") or {})
    plan_code = purchase.plan_code or ""

    if plan_code not in plan_cache:
        plan_cache[plan_code] = PricingPlan.query.filter_by(code=plan_code).first() if plan_code else None

    plan = plan_cache.get(plan_code)
    kind = provider_snapshot.get("kind") or (plan.kind if plan else None) or "call_package"
    minutes_included = provider_snapshot.get("minutes_included")
    if minutes_included is None:
        minutes_included = provider_snapshot.get("calls_included")
    if minutes_included is None and plan:
        minutes_included = plan.calls_included

    period_days = provider_snapshot.get("period_days")
    if period_days is None and plan:
        period_days = plan.period_days

    description = provider_snapshot.get("description") or (plan.description if plan else "") or ""
    paid_at = purchase.paid_at or purchase.created_at
    expires_at = None
    if kind == "unlimited" and paid_at and period_days:
        expires_at = paid_at + timedelta(days=int(period_days))

    return {
        "purchase": purchase,
        "kind": kind,
        "minutes_included": int(minutes_included or 0) if minutes_included is not None else 0,
        "period_days": int(period_days or 0) if period_days is not None else 0,
        "description": description,
        "paid_at": paid_at,
        "expires_at": expires_at,
        "subscription_id": purchase.cloudpayments_subscription_id,
        "subscription_status": purchase.subscription_status,
        "next_transaction_at": purchase.next_transaction_at,
        "canceled_at": purchase.canceled_at,
    }


def _find_active_unlimited_bucket(unlimited_buckets: list[dict], at: datetime) -> dict | None:
    return next(
        (
            bucket
            for bucket in unlimited_buckets
            if bucket["paid_at"]
            and bucket["paid_at"] <= at
            and bucket["expires_at"]
            and bucket["expires_at"] >= at
        ),
        None,
    )


def build_user_access_state(user: AppUser | None, *, trial_minutes_limit: int) -> dict:
    empty_state = {
        "trial_total_minutes": max(0, int(trial_minutes_limit)),
        "trial_remaining_minutes": 0,
        "trial_used_minutes": 0,
        "package_total_minutes": 0,
        "package_remaining_minutes": 0,
        "package_used_minutes": 0,
        "has_call_access": False,
        "current_subscription": None,
    }
    if not user:
        return empty_state

    purchases = (
        SubscriptionPurchase.query.filter_by(app_user_id=user.id, status="paid")
        .order_by(SubscriptionPurchase.paid_at.asc(), SubscriptionPurchase.created_at.asc(), SubscriptionPurchase.id.asc())
        .all()
    )
    calls = (
        CallSession.query.filter(
            CallSession.app_user_id == user.id,
            or_(CallSession.ended_at.isnot(None), CallSession.status == "finished"),
        )
        .order_by(CallSession.started_at.asc(), CallSession.id.asc())
        .all()
    )

    plan_cache: dict[str, PricingPlan | None] = {}
    package_buckets = []
    unlimited_buckets = []
    for purchase in purchases:
        snapshot = _purchase_plan_snapshot(purchase, plan_cache)
        if snapshot["kind"] == "unlimited":
            unlimited_buckets.append(snapshot)
        else:
            package_buckets.append(
                {
                    **snapshot,
                    "minutes_total": snapshot["minutes_included"],
                    "minutes_remaining": snapshot["minutes_included"],
                }
            )

    trial_remaining_minutes = max(0, int(trial_minutes_limit))

    for call in calls:
        billed_minutes = _call_billed_minutes(call)
        if billed_minutes <= 0:
            continue

        call_started_at = call.started_at or call.ended_at or datetime.utcnow()
        active_unlimited = _find_active_unlimited_bucket(unlimited_buckets, call_started_at)
        if active_unlimited:
            # While an unlimited plan is active, we do not spend any minute balance at all:
            # neither purchased minute packages nor trial minutes.
            continue

        remaining_to_bill = billed_minutes

        if remaining_to_bill > 0 and trial_remaining_minutes > 0:
            consume = min(trial_remaining_minutes, remaining_to_bill)
            trial_remaining_minutes -= consume
            remaining_to_bill -= consume

        # After trial minutes are exhausted, we start consuming purchased minute packages.
        for bucket in package_buckets:
            if remaining_to_bill <= 0:
                break
            if bucket["paid_at"] and bucket["paid_at"] > call_started_at:
                continue
            if bucket["minutes_remaining"] <= 0:
                continue
            consume = min(bucket["minutes_remaining"], remaining_to_bill)
            bucket["minutes_remaining"] -= consume
            remaining_to_bill -= consume

    now = datetime.utcnow()
    active_unlimited = _find_active_unlimited_bucket(list(reversed(unlimited_buckets)), now)

    package_total_minutes = sum(bucket["minutes_total"] for bucket in package_buckets)
    package_remaining_minutes = sum(bucket["minutes_remaining"] for bucket in package_buckets)
    trial_used_minutes = max(0, int(trial_minutes_limit) - trial_remaining_minutes)
    package_used_minutes = max(0, package_total_minutes - package_remaining_minutes)

    current_subscription = None
    if active_unlimited:
        purchase = active_unlimited["purchase"]
        current_subscription = {
            "plan_code": purchase.plan_code,
            "plan_name": purchase.plan_name,
            "kind": "unlimited",
            "status": purchase.status,
            "amount": float(purchase.amount or 0),
            "currency": purchase.currency,
            "paid_at": active_unlimited["paid_at"],
            "expires_at": active_unlimited["expires_at"],
            "minutes_included": None,
            "remaining_minutes": None,
            "period_days": active_unlimited["period_days"],
            "description": active_unlimited["description"],
            "subscription_id": active_unlimited["subscription_id"],
            "subscription_status": active_unlimited["subscription_status"],
            "next_transaction_at": active_unlimited["next_transaction_at"],
            "canceled_at": active_unlimited["canceled_at"],
            "auto_renew_enabled": bool(
                (active_unlimited["subscription_status"] or "active").lower() not in {"canceled", "cancelled"}
                and active_unlimited["canceled_at"] is None
            ),
        }
    elif package_remaining_minutes > 0:
        latest_package = next(
            (
                bucket
                for bucket in reversed(package_buckets)
                if bucket["minutes_remaining"] > 0
            ),
            None,
        )
        if latest_package:
            purchase = latest_package["purchase"]
            current_subscription = {
                "plan_code": purchase.plan_code,
                "plan_name": purchase.plan_name,
                "kind": "call_package",
                "status": purchase.status,
                "amount": float(purchase.amount or 0),
                "currency": purchase.currency,
                "paid_at": latest_package["paid_at"],
                "expires_at": None,
                "minutes_included": package_total_minutes,
                "remaining_minutes": package_remaining_minutes,
                "period_days": None,
                "description": latest_package["description"],
                "subscription_id": None,
                "subscription_status": None,
                "next_transaction_at": None,
                "canceled_at": None,
                "auto_renew_enabled": False,
            }

    return {
        "trial_total_minutes": max(0, int(trial_minutes_limit)),
        "trial_remaining_minutes": trial_remaining_minutes,
        "trial_used_minutes": trial_used_minutes,
        "package_total_minutes": package_total_minutes,
        "package_remaining_minutes": package_remaining_minutes,
        "package_used_minutes": package_used_minutes,
        "has_call_access": bool(active_unlimited or package_remaining_minutes > 0 or trial_remaining_minutes > 0),
        "current_subscription": current_subscription,
    }
