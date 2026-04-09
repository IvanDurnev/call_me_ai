from __future__ import annotations

from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from .extensions import db
from .models import AppUser


DEEPLINK_PREFIX = "user_id_"


def extract_user_uuid_from_payload(payload: str | None) -> str | None:
    value = (payload or "").strip()
    if not value.startswith(DEEPLINK_PREFIX):
        return None
    user_uuid = value.removeprefix(DEEPLINK_PREFIX).strip()
    return user_uuid or None


def _serialize_linked_user(user: AppUser) -> dict[str, str | int | None]:
    return {
        "id": user.id,
        "name": user.name,
        "user_uuid": user.user_uuid,
        "telegram_username": user.telegram_username,
    }


def link_telegram_account(*, payload: str | None, telegram_user_id: int | None, telegram_username: str | None) -> dict[str, str | int | None] | None:
    user_uuid = extract_user_uuid_from_payload(payload)
    if not user_uuid or not telegram_user_id:
        return None

    try:
        user = AppUser.query.filter_by(user_uuid=user_uuid).first()
        if not user:
            return None

        user.telegram_user_id = telegram_user_id
        user.telegram_username = (telegram_username or "").strip() or None
        db.session.commit()
        return _serialize_linked_user(user)
    except (IntegrityError, OperationalError, ProgrammingError):
        db.session.rollback()
        return None


def link_max_account(*, payload: str | None, max_user_id: int | None) -> dict[str, str | int | None] | None:
    user_uuid = extract_user_uuid_from_payload(payload)
    if not user_uuid or not max_user_id:
        return None

    try:
        user = AppUser.query.filter_by(user_uuid=user_uuid).first()
        if not user:
            return None

        user.max_user_id = max_user_id
        db.session.commit()
        return _serialize_linked_user(user)
    except (IntegrityError, OperationalError, ProgrammingError):
        db.session.rollback()
        return None
