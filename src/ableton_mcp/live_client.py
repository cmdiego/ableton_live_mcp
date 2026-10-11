"""Client for the AbletonMCP Remote Script (JSON lines over TCP)."""

from __future__ import annotations

import itertools
import json
import os
import socket
import threading
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

DEFAULT_HOST = os.environ.get("ABLETON_MCP_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("ABLETON_MCP_PORT", "9877"))


class LiveError(ToolError):
    """Live rejected a command (bad path, invalid value, ...)."""


class LiveConnectionError(LiveError):
    """Could not reach the Remote Script."""


CONNECT_HELP = (
    "Can't reach Ableton Live on {host}:{port}. Is Live running with the "
    "'AbletonMCP' control surface selected (Settings > Link, Tempo & MIDI)?"
)


class LiveClient:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 20.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._sock: socket.socket | None = None
        self._buf = b""
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    # -- transport -----------------------------------------------------------

    def _connect(self) -> socket.socket:
        if self._sock is None:
            try:
                self._sock = socket.create_connection((self.host, self.port), timeout=3)
            except OSError as e:
                raise LiveConnectionError(CONNECT_HELP.format(host=self.host, port=self.port)) from e
            self._sock.settimeout(self.timeout)
            self._buf = b""
        return self._sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _roundtrip(self, payload: bytes, req_id: int) -> dict:
        sock = self._connect()
        sock.sendall(payload)
        while True:
            while b"\n" not in self._buf:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionResetError("Live closed the connection")
                self._buf += chunk
            line, self._buf = self._buf.split(b"\n", 1)
            resp = json.loads(line)
            if resp.get("id") == req_id:
                return resp

    def call(self, cmd: str, **params: Any) -> Any:
        """Send one command; returns its result or raises LiveError."""
        with self._lock:
            req_id = next(self._ids)
            payload = (json.dumps({"id": req_id, "cmd": cmd, "params": params}) + "\n").encode()
            try:
                resp = self._roundtrip(payload, req_id)
            except TimeoutError as e:
                # The command may still run in Live; don't retry and risk doing it twice.
                self.close()
                raise LiveError(f"Live did not answer '{cmd}' within {self.timeout:.0f}s") from e
            except (OSError, ConnectionError, json.JSONDecodeError):
                # Live may have restarted or reloaded the script: retry once on a fresh socket.
                self.close()
                try:
                    resp = self._roundtrip(payload, req_id)
                except LiveConnectionError:
                    raise
                except (OSError, ConnectionError, json.JSONDecodeError) as e:
                    self.close()
                    raise LiveConnectionError(CONNECT_HELP.format(host=self.host, port=self.port)) from e
        if not resp.get("ok"):
            raise LiveError(resp.get("error", "unknown error"))
        return resp.get("result")

    # -- LOM helpers ---------------------------------------------------------

    def get(self, path: str, *props: str) -> Any:
        return self.call("lom_get", path=path, props=list(props))

    def set(self, path: str, **values: Any) -> Any:
        return self.call("lom_set", path=path, values=values)

    def invoke(self, path: str, method: str, *args: Any, **kwargs: Any) -> Any:
        return self.call("lom_call", path=path, method=method, args=list(args), kwargs=kwargs)

    def query(self, path: str, spec: dict) -> Any:
        return self.call("lom_query", path=path, spec=spec)

    def describe(self, path: str) -> Any:
        return self.call("lom_describe", path=path)

    def batch(self, commands: list[dict], undo_name: str | None = None) -> list:
        return self.call("batch", commands=commands, undo_name=undo_name)


def ref(path: str) -> dict:
    """Pass a Live object as an argument to invoke()/set()."""
    return {"$path": path}


_client: LiveClient | None = None


def get_client() -> LiveClient:
    global _client
    if _client is None:
        _client = LiveClient()
    return _client
