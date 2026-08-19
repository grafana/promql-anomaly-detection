"""
Peer-RF control HTTP API (port PEER_RF_CONTROL_PORT, default 8001).

GET  /health
GET  /peer-rf/machines
GET  /peer-rf/machines/{id}
POST /peer-rf/machines   body: {"machineId":"2505-200033","backfill":true}

Auth: Authorization: Bearer <PEER_RF_CONTROL_TOKEN> (required when token is set).
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from bridge_peer_rf import (
    PEER_RF_CONFIG_PATH,
    backfill_peer_rf_machine,
    enroll_peer_rf_machine,
    load_peer_rf_machines,
    merge_field_roles_from_peer_config,
    reload_peer_rf_config,
)

PEER_RF_CONTROL_PORT = int(os.environ.get("PEER_RF_CONTROL_PORT", "8001"))
PEER_RF_CONTROL_TOKEN = os.environ.get("PEER_RF_CONTROL_TOKEN", "").strip()

_backfill_lock = threading.Lock()
_backfill_state: dict[str, Any] = {
    "running": False,
    "machineId": None,
    "error": None,
    "startedAt": None,
    "finishedAt": None,
}


def _refresh_field_roles() -> None:
    try:
        from bridge import FIELD_ROLES

        FIELD_ROLES.update(merge_field_roles_from_peer_config())
    except Exception as e:
        print(f"[PEER-RF API] FIELD_ROLES update failed: {e}")


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not PEER_RF_CONTROL_TOKEN:
        # Token unset = refuse mutating + listing in production; allow health only.
        return False
    auth = handler.headers.get("Authorization", "")
    if auth == f"Bearer {PEER_RF_CONTROL_TOKEN}":
        return True
    if auth == PEER_RF_CONTROL_TOKEN:
        return True
    return False


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _start_backfill_async(machine_id: str) -> bool:
    with _backfill_lock:
        if _backfill_state["running"]:
            return False
        _backfill_state.update(
            {
                "running": True,
                "machineId": machine_id,
                "error": None,
                "startedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                "finishedAt": None,
            }
        )

    def _run() -> None:
        try:
            print(f"[PEER-RF API] Backfill starting for {machine_id}")
            backfill_peer_rf_machine(machine_id)
            with _backfill_lock:
                _backfill_state["error"] = None
        except Exception as e:
            traceback.print_exc()
            with _backfill_lock:
                _backfill_state["error"] = str(e)
        finally:
            with _backfill_lock:
                _backfill_state["running"] = False
                _backfill_state["finishedAt"] = (
                    __import__("datetime").datetime.utcnow().isoformat() + "Z"
                )
            print(f"[PEER-RF API] Backfill finished for {machine_id}")

    threading.Thread(target=_run, name=f"PeerRfBackfill-{machine_id}", daemon=True).start()
    return True


class PeerRfControlHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[PEER-RF API] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/health":
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "service": "peer-rf-control",
                    "configPath": PEER_RF_CONFIG_PATH,
                    "authRequired": bool(PEER_RF_CONTROL_TOKEN),
                },
            )
            return

        if not _authorized(self):
            _json_response(self, 401, {"ok": False, "error": "unauthorized"})
            return

        if path == "/peer-rf/machines":
            machines = {
                mid: [{"target": t.target, "peerFeatures": t.peer_features} for t in entries]
                for mid, entries in load_peer_rf_machines().items()
            }
            with _backfill_lock:
                backfill = dict(_backfill_state)
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "configPath": PEER_RF_CONFIG_PATH,
                    "machines": machines,
                    "backfill": backfill,
                },
            )
            return

        if path.startswith("/peer-rf/machines/"):
            machine_id = unquote(path[len("/peer-rf/machines/") :])
            entries = load_peer_rf_machines().get(machine_id, [])
            with _backfill_lock:
                backfill = dict(_backfill_state)
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "machineId": machine_id,
                    "enrolled": len(entries) > 0,
                    "targets": [
                        {"target": e.target, "peerFeatures": e.peer_features} for e in entries
                    ],
                    "backfill": backfill,
                },
            )
            return

        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if not _authorized(self):
            _json_response(self, 401, {"ok": False, "error": "unauthorized"})
            return

        if path != "/peer-rf/machines":
            _json_response(self, 404, {"ok": False, "error": "not found"})
            return

        try:
            body = _read_json(self)
        except Exception as e:
            _json_response(self, 400, {"ok": False, "error": f"invalid json: {e}"})
            return

        machine_id = str(body.get("machineId") or body.get("machine") or "").strip()
        if not machine_id:
            _json_response(self, 400, {"ok": False, "error": "machineId is required"})
            return

        backfill = body.get("backfill", True)
        if isinstance(backfill, str):
            backfill = backfill.strip().lower() in ("1", "true", "yes")

        try:
            result = enroll_peer_rf_machine(machine_id, targets=body.get("targets"))
            _refresh_field_roles()
        except Exception as e:
            traceback.print_exc()
            _json_response(self, 500, {"ok": False, "error": str(e)})
            return

        queued = False
        if backfill:
            queued = _start_backfill_async(machine_id)
            if not queued:
                result["backfillWarning"] = "another backfill is already running"

        result["ok"] = True
        result["backfillQueued"] = bool(backfill and queued)
        _json_response(self, 200, result)


def start_peer_rf_control_server(port: int | None = None) -> ThreadingHTTPServer | None:
    if not PEER_RF_CONTROL_TOKEN:
        print(
            "[PEER-RF API] PEER_RF_CONTROL_TOKEN unset — control API disabled "
            "(set token to enable enroll)"
        )
        return None
    listen_port = port if port is not None else PEER_RF_CONTROL_PORT
    server = ThreadingHTTPServer(("0.0.0.0", listen_port), PeerRfControlHandler)
    thread = threading.Thread(target=server.serve_forever, name="PeerRfControlHTTP", daemon=True)
    thread.start()
    print(f"[PEER-RF API] ✓ Control server on 0.0.0.0:{listen_port}")
    return server
