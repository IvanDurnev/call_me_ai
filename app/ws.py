from __future__ import annotations

from .extensions import sock
from .realtime import SocketBridge


def register_ws_routes() -> None:
    @sock.route("/ws/call/<slug>")
    def call_socket(ws, slug: str):
        bridge = SocketBridge(ws, slug)
        bridge.serve()
