"""Fixtures: a fake Live set served by the real Remote Script server/dispatcher/handlers."""

from __future__ import annotations

import threading

import pytest

from AbletonMCP import dispatcher, handlers, server
from ableton_mcp import live_client

from .fake_live import Application, Song, install_fake_live_module

install_fake_live_module()


@pytest.fixture
def song():
    return Song()


@pytest.fixture
def client(song, monkeypatch):
    srv = start_server(song)
    stop = threading.Event()
    poller = threading.Thread(target=_poll_loop, args=(srv, stop), daemon=True)
    poller.start()
    c = live_client.LiveClient(port=srv.port, timeout=5)
    monkeypatch.setattr(live_client, "_client", c)
    yield c
    c.close()
    stop.set()
    poller.join(2)
    srv.stop()


def _poll_loop(srv, stop):
    """Stands in for Live's 10 ms main-thread Timer."""
    while not stop.wait(0.001):
        srv.poll()


def start_server(song, **kwargs):
    ctx = dispatcher.Context({"song": song, "app": Application(song)}, "test")
    disp = dispatcher.Dispatcher(lambda: handlers.COMMANDS, lambda: ctx, log=print)
    srv = server.CommandServer("127.0.0.1", 0, disp.execute, log=lambda m: None, **kwargs)
    srv.start()
    return srv
