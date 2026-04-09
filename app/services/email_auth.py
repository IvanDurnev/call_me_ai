from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from flask import current_app
from flask_mail import Message

from ..extensions import db, mail
from ..models import AppUser, EmailCode


def issue_email_code(*, user: AppUser, purpose: str) -> str:
    resend_seconds = current_app.config["EMAIL_VERIFICATION_RESEND_INTERVAL_SECONDS"]
    ttl_minutes = current_app.config["EMAIL_VERIFICATION_CODE_TTL_MINUTES"]
    now = datetime.utcnow()

    recent = (
        EmailCode.query.filter_by(email=user.email, purpose=purpose)
        .order_by(EmailCode.created_at.desc(), EmailCode.id.desc())
        .first()
    )
    if recent and (now - recent.created_at).total_seconds() < resend_seconds:
        remaining = resend_seconds - int((now - recent.created_at).total_seconds())
        raise ValueError(f"Повторно запросить код можно через {remaining} сек.")

    code = f"{secrets.randbelow(1000000):06d}"
    entry = EmailCode(
        email=user.email,
        purpose=purpose,
        code_hash=EmailCode.hash_code(code),
        app_user_id=user.id,
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    db.session.add(entry)
    db.session.commit()
    send_email_code(user=user, code=code, purpose=purpose, ttl_minutes=ttl_minutes)
    return code


def verify_email_code(*, email: str, code: str, purpose: str) -> AppUser:
    normalized_email = email.strip().lower()
    normalized_code = code.strip()
    now = datetime.utcnow()

    entry = (
        EmailCode.query.filter_by(email=normalized_email, purpose=purpose, consumed_at=None)
        .order_by(EmailCode.created_at.desc(), EmailCode.id.desc())
        .first()
    )
    if not entry:
        raise ValueError("Код не найден. Запросите новый.")
    if entry.expires_at < now:
        raise ValueError("Срок действия кода истёк. Запросите новый.")
    if not entry.matches(normalized_code):
        raise ValueError("Неверный код.")

    user = db.session.get(AppUser, entry.app_user_id) if entry.app_user_id else None
    if not user:
        raise ValueError("Пользователь не найден.")

    entry.consumed_at = now
    if purpose == "verify_email":
        user.email_verified = True
        user.email_verified_at = now
    db.session.commit()
    return user


def send_email_code(*, user: AppUser, code: str, purpose: str, ttl_minutes: int) -> None:
    if purpose == "verify_email":
        subject = "Подтвердите электронную почту"
        title = "Подтверждение почты"
    else:
        subject = "Одноразовый код для входа"
        title = "Вход в личный кабинет"

    body = (
        f"{title}\n\n"
        f"Здравствуйте, {user.name}.\n\n"
        f"Ваш одноразовый код: {code}\n\n"
        f"Код действует {ttl_minutes} минут.\n"
        "Если это были не вы, просто проигнорируйте это письмо.\n"
    )
    message = Message(
        subject=subject,
        recipients=[user.email],
        body=body,
    )
    mail.send(message)
