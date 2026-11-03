"""Branded corporate entry points and page-scoped registration."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime
from pathlib import Path

from flask import abort, current_app, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

from .extensions import db
from .models import AppUser, CorporatePage, CorporateMembership, CorporateRegistrationCode, Hero
from .routes import main_bp, _admin_required, _current_app_user, _login_app_user, _hero_provider, EMAIL_RE
from .services.email_auth import issue_email_code, verify_email_code
from .characters import get_character


def csrf_token():
    if "corporate_csrf" not in session:
        session["corporate_csrf"] = secrets.token_urlsafe(32)
    return session["corporate_csrf"]


def check_csrf():
    if not hmac.compare_digest(session.get("corporate_csrf", ""), request.form.get("csrf_token", "")) or not session.get("corporate_csrf"):
        abort(400, "Обновите страницу и повторите действие.")


def corporate_access(user, page_id, slug):
    page = db.session.get(CorporatePage, page_id)
    return bool(user and page and page.is_active and page.hero.is_active and page.hero.slug == slug
                and CorporateMembership.query.filter_by(page_id=page.id, app_user_id=user.id).first())


def _domains(value):
    domains = sorted(set(part.strip().lower().lstrip("@") for part in re.split(r"[,;\s]+", value) if part.strip()))
    for domain in domains:
        if len(domain) > 253 or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", domain):
            raise ValueError("Укажите домены почты, например company.ru, company.com.")
    return domains


def _upload(field):
    upload = request.files.get(field)
    if not upload or not upload.filename:
        return None
    data = upload.stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Изображение должно быть не больше 8 МБ.")
    # Only raster formats; never serve user-supplied SVG/HTML.
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        suffix = ".png"
    elif data.startswith(b"\xff\xd8\xff"):
        suffix = ".jpg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        suffix = ".webp"
    else:
        raise ValueError("Загрузите изображение PNG, JPEG или WebP.")
    relative = f"uploads/corporate/{uuid.uuid4().hex}{suffix}"
    target = Path(current_app.static_folder) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative


@main_bp.route("/admin/corporate-pages", methods=["GET", "POST"])
@_admin_required(view_mode="html")
def admin_corporate_pages():
    error = ""
    generated_codes = []
    selected = CorporatePage.query.filter_by(id=request.args.get("edit", type=int)).first()
    if request.method == "POST":
        check_csrf()
        selected = db.session.get(CorporatePage, request.form.get("page_id", type=int)) if request.form.get("page_id") else None
        if request.form.get("page_id") and not selected:
            abort(404)
        action = request.form.get("action", "save")
        try:
            if action == "codes" and selected:
                count = int(request.form.get("count", "1"))
                if not 1 <= count <= 100:
                    raise ValueError("За один раз можно выпустить от 1 до 100 кодов.")
                for _ in range(count):
                    code = secrets.token_hex(8).upper()
                    db.session.add(CorporateRegistrationCode(page_id=selected.id, code_hash=hashlib.sha256(code.encode()).hexdigest(), label=code[-4:]))
                    generated_codes.append(code)
                db.session.commit()
            elif action == "revoke" and selected:
                code = CorporateRegistrationCode.query.filter_by(id=request.form.get("code_id", type=int), page_id=selected.id).first_or_404()
                code.revoked_at = datetime.utcnow()
                db.session.commit()
            elif action == "save":
                endpoint = request.form.get("endpoint", "").strip().strip("/").lower()
                reserved = {rule.rule.strip("/").split("/")[0] for rule in current_app.url_map.iter_rules() if not rule.rule.startswith("/<")}
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,79}", endpoint) or endpoint in reserved:
                    raise ValueError("Endpoint: 2–80 латинских букв, цифр или дефисов; адрес не должен совпадать с разделами сайта.")
                duplicate = CorporatePage.query.filter_by(endpoint=endpoint).first()
                if duplicate and (not selected or duplicate.id != selected.id):
                    raise ValueError("Этот endpoint уже занят.")
                hero = db.session.get(Hero, request.form.get("hero_id", type=int))
                if not hero or not hero.is_active:
                    raise ValueError("Выберите активного персонажа.")
                values = {key: request.form.get(key, "").strip() for key in ("name", "title", "button_label")}
                if not all(values.values()) or any(len(values[key]) > limit for key, limit in (("name", 255), ("title", 255), ("button_label", 120))):
                    raise ValueError("Заполните название, заголовок (до 255 символов) и подпись кнопки (до 120 символов).")
                domains = ", ".join(_domains(request.form.get("email_domains", "")))
                background, logo = _upload("background"), _upload("logo")
                page = selected or CorporatePage()
                for key, value in values.items():
                    setattr(page, key, value)
                page.endpoint, page.hero_id, page.email_domains = endpoint, hero.id, domains
                page.is_active = request.form.get("is_active") == "on"
                for field, uploaded in (("background_path", background), ("logo_path", logo)):
                    if uploaded:
                        setattr(page, field, uploaded)
                    elif request.form.get("remove_" + field) == "on":
                        setattr(page, field, None)
                db.session.add(page)
                db.session.commit()
                return redirect(url_for("main.admin_corporate_pages", edit=page.id, saved=1))
            else:
                abort(400)
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            generated_codes = []
            error = str(exc) if isinstance(exc, ValueError) else "Этот endpoint уже занят."
    codes = CorporateRegistrationCode.query.filter_by(page_id=selected.id).order_by(CorporateRegistrationCode.id.desc()).all() if selected else []
    response = current_app.make_response(render_template("corporate_admin.html", pages=CorporatePage.query.order_by(CorporatePage.id.desc()).all(), selected=selected,
        heroes=Hero.query.filter_by(is_active=True).order_by(Hero.sort_order).all(), error=error, generated_codes=generated_codes, codes=codes, csrf_token=csrf_token()))
    response.headers["Cache-Control"] = "no-store"
    return response


@main_bp.route("/<page_endpoint>", methods=["GET", "POST"])
def corporate_page(page_endpoint):
    endpoint = page_endpoint
    page = CorporatePage.query.filter_by(endpoint=endpoint, is_active=True).first_or_404()
    if not page.hero.is_active:
        abort(404)
    user = _current_app_user()
    error = ""
    pending_key = f"corporate_pending_{page.id}"
    pending = session.get(pending_key)
    if request.method == "POST":
        check_csrf()
        action = request.form.get("action")
        try:
            if action == "email":
                email = request.form.get("email", "").strip().lower()
                name = request.form.get("name", "").strip()
                if not EMAIL_RE.fullmatch(email) or len(email) > 255 or email.rsplit("@", 1)[-1] not in _domains(page.email_domains):
                    raise ValueError("Используйте корпоративную почту разрешённого домена.")
                if not name or len(name) > 255 or request.form.get("consent") != "on":
                    raise ValueError("Укажите имя и согласие на обработку персональных данных.")
                target = AppUser.query.filter_by(email=email).first()
                if not target:
                    target = AppUser(email=email, name=name, phone="", consent_to_personal_data=True, consented_at=datetime.utcnow())
                    db.session.add(target)
                    db.session.commit()
                issue_email_code(user=target, purpose=f"corporate_{page.id}")
                session[pending_key] = {"email": email, "attempts": 0}
                return redirect(url_for("main.corporate_page", page_endpoint=endpoint))
            elif action == "verify" and pending:
                if pending["attempts"] >= 5:
                    session.pop(pending_key, None)
                    raise ValueError("Лимит попыток исчерпан. Запросите новый код.")
                session[pending_key] = {**pending, "attempts": pending["attempts"] + 1}
                if pending["email"].rsplit("@", 1)[-1] not in _domains(page.email_domains):
                    raise ValueError("Домен почты больше не разрешён.")
                user = verify_email_code(email=pending["email"], code=request.form.get("email_code", ""), purpose=f"corporate_{page.id}")
                user.email_verified = True
                user.email_verified_at = datetime.utcnow()
                user.consent_to_personal_data = True
                user.consented_at = user.consented_at or datetime.utcnow()
                if not CorporateMembership.query.filter_by(page_id=page.id, app_user_id=user.id).first():
                    db.session.add(CorporateMembership(page_id=page.id, app_user_id=user.id, source="email"))
                db.session.commit()
                _login_app_user(user)
                session.pop(pending_key, None)
                return redirect(url_for("main.corporate_page", page_endpoint=endpoint))
            elif action == "code":
                raw = request.form.get("registration_code", "").strip().upper()
                code_hash = hashlib.sha256(raw.encode()).hexdigest()
                code = CorporateRegistrationCode.query.filter_by(page_id=page.id, code_hash=code_hash, used_at=None, revoked_at=None).first()
                if not code:
                    raise ValueError("Код регистрации недействителен или уже использован.")
                name = request.form.get("name", "").strip()
                if not name or len(name) > 255 or request.form.get("consent") != "on":
                    raise ValueError("Укажите имя и согласие на обработку персональных данных.")
                if not user:
                    user = AppUser(email=f"corporate-{uuid.uuid4().hex}@registration.invalid", phone="", name=name, consent_to_personal_data=True, consented_at=datetime.utcnow())
                    db.session.add(user)
                    db.session.flush()
                user.consent_to_personal_data = True
                user.consented_at = user.consented_at or datetime.utcnow()
                # Atomic redemption prevents two concurrent registrations with the same code.
                updated = CorporateRegistrationCode.query.filter_by(id=code.id, used_at=None, revoked_at=None).update({"used_at": datetime.utcnow(), "used_by_id": user.id})
                if updated != 1:
                    raise ValueError("Код регистрации уже использован.")
                if not CorporateMembership.query.filter_by(page_id=page.id, app_user_id=user.id).first():
                    db.session.add(CorporateMembership(page_id=page.id, app_user_id=user.id, source="code"))
                db.session.commit()
                _login_app_user(user)
                return redirect(url_for("main.corporate_page", page_endpoint=endpoint))
            else:
                raise ValueError("Запросите код для входа.")
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            error = str(exc) if isinstance(exc, ValueError) else "Повторите вход."
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Corporate registration failed")
            error = "Не удалось отправить письмо. Попробуйте позже."
    allowed = corporate_access(user, page.id, page.hero.slug)
    character = get_character(page.hero.slug)
    response = current_app.make_response(render_template("corporate_page.html", page=page, user=user, allowed=allowed, character=character,
        realtime_provider=_hero_provider(character), pending=session.get(pending_key), error=error, csrf_token=csrf_token()))
    response.headers["Cache-Control"] = "no-store"
    return response
