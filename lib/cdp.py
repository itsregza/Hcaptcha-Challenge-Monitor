from __future__ import annotations

import json
import queue
import socket
import threading
import time
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import urlopen

import websocket


def list_targets(port: int, host: str = "127.0.0.1") -> list[dict]:
    with urlopen(f"http://{host}:{port}/json", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_for_cdp(port: int, host: str = "127.0.0.1", timeout: float = 20.0) -> list[dict]:
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            return list_targets(port, host)
        except (URLError, OSError, json.JSONDecodeError) as e:
            last = e
            time.sleep(0.3)
    raise RuntimeError(f"cdp not up on {host}:{port} ({last!r})")


def cdp_alive(host: str, port: int, timeout: float = 0.28) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            return s.connect_ex((host, int(port))) == 0
        finally:
            s.close()
    except OSError:
        return False


def find_demo_page(targets: list[dict]) -> Optional[dict]:
    for t in targets:
        if not isinstance(t, dict):
            continue
        u = (t.get("url") or "").lower()
        if "accounts.hcaptcha.com/demo" in u and t.get("webSocketDebuggerUrl"):
            return t
    return None


class CdpSession:
    def __init__(self, ws_url: str, recv_timeout: float = 30.0):
        self._ws_url = ws_url
        self._recv_timeout = recv_timeout
        self._ws = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, dict] = {}
        self._event_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()

    def connect(self) -> None:
        self._ws = websocket.create_connection(self._ws_url, timeout=self._recv_timeout)
        threading.Thread(target=self._recv_loop, daemon=True).start()
        threading.Thread(target=self._event_loop, daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _recv_loop(self) -> None:
        ws = self._ws
        while not self._stop.is_set() and ws is not None:
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if "id" in msg:
                with self._state_lock:
                    self._pending[msg["id"]] = msg
            else:
                self._event_queue.put(msg)

    def _event_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._event_queue.get(timeout=0.2)
            except queue.Empty:
                continue

    def send(self, method: str, params: Optional[dict] = None, timeout: float = 10.0) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("not connected")
        with self._state_lock:
            self._next_id += 1
            mid = self._next_id
        payload: dict[str, Any] = {"id": mid, "method": method}
        if params:
            payload["params"] = params
        with self._send_lock:
            self._ws.send(json.dumps(payload))
        end = time.time() + timeout
        while time.time() < end:
            with self._state_lock:
                if mid in self._pending:
                    msg = self._pending.pop(mid)
                    if "error" in msg:
                        raise RuntimeError(f"CDP {method} error: {msg['error']}")
                    return msg.get("result") or {}
            time.sleep(0.01)
        raise TimeoutError(f"CDP {method} timed out")


def attach_to_target(target: dict) -> CdpSession:
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("no websocket url")
    s = CdpSession(ws_url)
    s.connect()
    return s
