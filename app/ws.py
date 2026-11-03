from __future__ import annotations

from flask import request, session

from .extensions import sock, db
from .models import CallSession, AppUser
from .realtime import SocketBridge


def register_ws_routes() -> None:
    @sock.route("/ws/call/<slug>")
    def call_socket(ws, slug: str):
        corporate_call_id = None
        if "corporate_call" in request.args:
            from .corporate import corporate_access
            call = db.session.get(CallSession, request.args.get("corporate_call", type=int))
            user = db.session.get(AppUser, session.get("app_user_id")) if session.get("app_user_id") else None
            if (not call or not user or call.app_user_id != user.id or call.ended_at
                    or not corporate_access(user, call.corporate_page_id, slug)):
                ws.close()
                return
            corporate_call_id = call.id
        bridge = SocketBridge(ws, slug, corporate_call_id=corporate_call_id)
        bridge.serve()
