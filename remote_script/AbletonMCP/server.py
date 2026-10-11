"""Newline-delimited JSON over TCP, served without threads.

``poll()`` is called on Live's main thread (from a 10 ms Live.Base.Timer). It
accepts connections, reads whatever has arrived, executes complete requests
right there and writes the responses. With no extra threads there are no GIL
handoffs between Live's main thread and socket threads, which cost several
timer ticks per request.
"""

import json
import socket
import time

MAX_LINE = 16 * 1024 * 1024
SEND_TIMEOUT = 5.0


class _Client(object):
    __slots__ = ("sock", "buf")

    def __init__(self, sock):
        self.sock = sock
        self.buf = b""


class CommandServer(object):
    def __init__(self, host, port, execute, log, budget=0.05):
        self._addr = (host, port)
        self._execute = execute
        self._log = log
        self._budget = budget
        self._sock = None
        self._clients = []

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(self._addr)
        self._sock.listen(4)
        self._sock.setblocking(False)
        self._log("listening on %s:%d" % self._addr)

    @property
    def port(self):
        return self._sock.getsockname()[1] if self._sock else None

    def stop(self):
        for c in self._clients:
            _close(c.sock)
        self._clients = []
        if self._sock:
            _close(self._sock)
            self._sock = None

    def poll(self):
        """Do all pending socket work. Never blocks except while sending a reply."""
        if self._sock is None:
            return
        self._accept()
        deadline = time.time() + self._budget
        for client in list(self._clients):
            if not self._read(client):
                continue
            # Run complete requests; leave the rest for the next tick if over budget.
            while b"\n" in client.buf and time.time() < deadline:
                line, client.buf = client.buf.split(b"\n", 1)
                if line.strip() and not self._handle_line(client, line):
                    break

    def _accept(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError as e:
                self._log("accept failed: %s" % e)
                return
            conn.setblocking(False)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._clients.append(_Client(conn))

    def _read(self, client):
        while True:
            try:
                chunk = client.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                return True
            except OSError:
                self._drop(client)
                return False
            if not chunk:
                self._drop(client)
                return False
            client.buf += chunk
            if len(client.buf) > MAX_LINE:
                self._log("request too large; dropping client")
                self._drop(client)
                return False

    def _handle_line(self, client, line):
        req_id = None
        try:
            req = json.loads(line.decode("utf-8"))
            req_id = req.get("id")
            resp = self._execute(req)
        except Exception as e:
            resp = {"ok": False, "error": "bad request: %s" % e}
        resp["id"] = req_id
        data = (json.dumps(resp, default=str) + "\n").encode("utf-8")
        try:
            client.sock.settimeout(SEND_TIMEOUT)
            client.sock.sendall(data)
            client.sock.setblocking(False)
            return True
        except OSError as e:
            self._log("send failed: %s" % e)
            self._drop(client)
            return False

    def _drop(self, client):
        _close(client.sock)
        if client in self._clients:
            self._clients.remove(client)


def _close(s):
    try:
        s.close()
    except Exception:
        pass
