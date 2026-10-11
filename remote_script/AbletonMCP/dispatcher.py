"""Executes one request against the handler table. Runs on Live's main thread."""

import contextlib
import traceback

from . import lom


class Context(object):
    """What handlers get: named roots, version, and an undo helper."""

    def __init__(self, roots, version):
        self.roots = roots
        self.version = version

    @contextlib.contextmanager
    def undo_step(self, name=None):
        song = self.roots.get("song")
        begin = getattr(song, "begin_undo_step", None)
        end = getattr(song, "end_undo_step", None)
        if name and begin and end:
            begin()
            try:
                yield
            finally:
                end()
        else:
            yield


class Dispatcher(object):
    def __init__(self, get_handlers, get_context, log):
        self._get_handlers = get_handlers
        self._get_context = get_context
        self._log = log

    def execute(self, request):
        cmd = request.get("cmd")
        handlers = self._get_handlers()
        fn = handlers.get(cmd)
        if fn is None:
            return {"ok": False, "error": "unknown command '%s'" % cmd, "commands": sorted(handlers)}
        try:
            return {"ok": True, "result": fn(self._get_context(), request.get("params") or {})}
        except lom.LomError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            self._log("command %s failed: %s" % (cmd, traceback.format_exc()))
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
