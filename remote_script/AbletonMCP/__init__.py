"""AbletonMCP control surface: exposes the Live API to the ableton-mcp server.

Select "AbletonMCP" in Settings > Link, Tempo & MIDI > Control Surface
(Input/Output: None). Listens on 127.0.0.1:9877.
"""


def create_instance(c_instance):
    # Live caches imported modules for the whole session, so toggling the control
    # surface would otherwise reuse stale code. Reload in dependency order so
    # reselecting AbletonMCP picks up every edit without restarting Live.
    import importlib

    from . import dispatcher, handlers, lom, server, surface

    for module in (lom, handlers, dispatcher, server, surface):
        importlib.reload(module)
    return surface.AbletonMCP(c_instance)
