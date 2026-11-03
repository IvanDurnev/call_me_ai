from __future__ import annotations

from datetime import datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import ChatMessage, ChatOperation


class ChatBusyError(Exception):
    pass


def begin_operation(user_id: int, slug: str, key: str, kind: str = "send") -> tuple[ChatOperation, bool]:
    existing = ChatOperation.query.filter_by(app_user_id=user_id, request_key=key).first()
    if existing:
        if existing.character_slug != slug or existing.kind != kind:
            raise ValueError("Этот идентификатор запроса уже использован в другом чате.")
        if existing.status == "pending" and existing.updated_at < datetime.utcnow() - timedelta(minutes=10):
            existing.status = "failed"
            db.session.commit()
        return existing, False
    # Recover abandoned requests after a worker restart. A committed user message
    # remains available in history; the same request can be retried without adding it twice.
    ChatOperation.query.filter(
        ChatOperation.app_user_id == user_id, ChatOperation.character_slug == slug,
        ChatOperation.status == "pending",
        ChatOperation.updated_at < datetime.utcnow() - timedelta(minutes=10),
    ).update({"status": "failed"}, synchronize_session=False)
    operation = ChatOperation(app_user_id=user_id, character_slug=slug, request_key=key, kind=kind)
    db.session.add(operation)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = ChatOperation.query.filter_by(app_user_id=user_id, request_key=key).first()
        if existing:
            if existing.character_slug != slug or existing.kind != kind:
                raise ValueError("Этот идентификатор запроса уже использован в другом чате.")
            return existing, False
        raise ChatBusyError("Персонаж уже отвечает в этой беседе. Дождитесь ответа.")
    return operation, True


def retry_operation(operation: ChatOperation) -> bool:
    try:
        changed = ChatOperation.query.filter_by(id=operation.id, status="failed").update(
            {"status": "pending", "updated_at": datetime.utcnow()}, synchronize_session=False)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ChatBusyError("Персонаж уже отвечает в этой беседе. Дождитесь ответа.")
    db.session.refresh(operation)
    return bool(changed)


def merge_legacy_messages(user_id: int, slug: str, incoming: list[dict]) -> int:
    """Merge overlapping browser snapshots, preserving repeated turns and existing rows.

    Legacy localStorage has no timestamps. New fragments are placed between their
    matching neighbours, or before/after the existing conversation where possible.
    """
    existing = (ChatMessage.query.filter_by(app_user_id=user_id, character_slug=slug)
                .order_by(ChatMessage.created_at, ChatMessage.id).all())
    signatures = [(m.role, m.text) for m in existing]
    desired = [(m["role"], m["text"]) for m in incoming]
    blocks = SequenceMatcher(a=signatures, b=desired, autojunk=False).get_matching_blocks()
    previous_b = 0
    previous_a = -1
    inserted = 0
    for block in blocks:
        chunk = incoming[previous_b:block.b]
        if chunk:
            left = existing[previous_a].created_at if previous_a >= 0 else None
            right = existing[block.a].created_at if block.a < len(existing) else None
            if right and left and right > left:
                start, step = left, (right - left) / (len(chunk) + 1)
            elif right:
                start, step = right - timedelta(seconds=len(chunk) + 1), timedelta(seconds=1)
            else:
                start, step = left or datetime.utcnow(), timedelta(microseconds=1000)
                if existing and left is None:
                    start = max(start, existing[-1].created_at)
            for index, message in enumerate(chunk, 1):
                db.session.add(ChatMessage(app_user_id=user_id, character_slug=slug,
                    role=message["role"], text=message["text"], source="web_import",
                    created_at=start + step * index))
                inserted += 1
        if block.size:
            previous_a = block.a + block.size - 1
        previous_b = block.b + block.size
    return inserted
