from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import load_only

from ..models import AppUser, CallSession, PricingPlan, SubscriptionPurchase
from .billing import build_user_access_state


def build_admin_user_summaries(users: list[AppUser], *, trial_minutes_limit: int) -> dict[int, dict]:
    if not users:
        return {}
    user_ids = [user.id for user in users]
    purchases_by_user = defaultdict(list)
    calls_by_user = defaultdict(list)
    purchases = (
        SubscriptionPurchase.query.filter(SubscriptionPurchase.app_user_id.in_(user_ids))
        .order_by(SubscriptionPurchase.paid_at.asc(), SubscriptionPurchase.created_at.asc(), SubscriptionPurchase.id.asc())
        .all()
    )
    for purchase in purchases:
        purchases_by_user[purchase.app_user_id].append(purchase)
    calls = (
        CallSession.query.options(load_only(
            CallSession.id, CallSession.app_user_id, CallSession.status,
            CallSession.started_at, CallSession.ended_at, CallSession.corporate_page_id,
        ))
        .filter(CallSession.app_user_id.in_(user_ids),
                or_(CallSession.ended_at.isnot(None), CallSession.status == 'finished'))
        .order_by(CallSession.started_at.asc(), CallSession.id.asc())
        .all()
    )
    for call in calls:
        calls_by_user[call.app_user_id].append(call)
    plans = {plan.code: plan for plan in PricingPlan.query.all()}
    now = datetime.utcnow()
    summaries = {}
    for user in users:
        all_purchases = purchases_by_user[user.id]
        paid = [purchase for purchase in all_purchases if purchase.status == 'paid']
        state = build_user_access_state(
            user, trial_minutes_limit=trial_minutes_limit, purchases=paid,
            calls=calls_by_user[user.id], plan_cache=plans, now=now,
        )
        current = state['current_subscription']
        if current:
            if current['kind'] == 'unlimited':
                status = 'Активна' if current['auto_renew_enabled'] else 'Активна, автопродление отключено'
            else:
                status = 'Пакет минут активен'
        elif paid:
            # Active packages/unlimited periods are resolved by the same billing logic as the account page.
            has_unlimited = any(
                (
                    ((purchase.provider_payload_json or {}).get('pricing_plan') or {}).get('kind')
                    or (plans[purchase.plan_code].kind if plans.get(purchase.plan_code) else 'call_package')
                ) == 'unlimited'
                for purchase in paid
            )
            status = 'Истекла' if has_unlimited else 'Минуты исчерпаны'
        elif all_purchases:
            latest = max(all_purchases, key=lambda purchase: (purchase.created_at, purchase.id))
            status = 'Ожидает оплаты' if latest.status in {'created', 'pending'} else 'Не оплачена'
        else:
            status = 'Нет подписки'
        summaries[user.id] = {
            'trial_calls_tried': state['trial_calls_tried'],
            'has_paid': bool(paid),
            'subscription_status': status,
            'subscription_name': current['plan_name'] if current else '',
        }
    return summaries
