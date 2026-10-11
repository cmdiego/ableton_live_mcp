"""The ControlSurface subclass Live instantiates. Only importable inside Live."""

import importlib

from _Framework.ControlSurface import ControlSurface

from . import dispatcher, handlers, lom, server

VERSION = "0.2.0"
HOST = "127.0.0.1"
PORT = 9877
# Requests are served from a main-thread Live.Base.Timer: ~10 ms latency.
# update_display (~300 ms ticks) is only a fallback if Timer is missing.
POLL_INTERVAL_MS = 10


class AbletonMCP(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self._handlers = handlers
        self._dispatcher = dispatcher.Dispatcher(self._commands, self._context, self._log)
        self._server = server.CommandServer(HOST, PORT, self._dispatcher.execute, self._log)
        self._timer = None
        try:
            self._server.start()
        except Exception as e:
            self._log("failed to start server: %s" % e)
            self.show_message("AbletonMCP: could not open port %d (%s)" % (PORT, e))
            return
        try:
            import Live
            self._timer = Live.Base.Timer(callback=self._server.poll, interval=POLL_INTERVAL_MS, repeat=True)
            self._timer.start()
        except Exception as e:
            self._log("no Live.Base.Timer (%s); serving from update_display" % e)
        self.show_message("AbletonMCP %s listening on port %d" % (VERSION, PORT))

    def _log(self, msg):
        self.log_message("AbletonMCP: " + msg)

    def _context(self):
        import Live
        return dispatcher.Context(
            {"song": self.song(), "app": Live.Application.get_application()}, VERSION)

    def _commands(self):
        commands = dict(self._handlers.COMMANDS)
        commands["reload"] = self._reload
        return commands

    def _reload(self, ctx, params):
        """Hot-reload lom.py and handlers.py without restarting Live."""
        importlib.reload(lom)
        self._handlers = importlib.reload(handlers)
        self._log("handlers reloaded")
        return {"reloaded": True, "commands": sorted(self._handlers.COMMANDS)}

    def update_display(self):
        ControlSurface.update_display(self)
        if self._timer is None:
            self._server.poll()

    def disconnect(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._server.stop()
        self._log("stopped")
        ControlSurface.disconnect(self)
