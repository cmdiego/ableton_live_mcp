"""Guards that keep the server usable from any MCP client (Claude, Codex, OpenCode, Copilot, Gemini…)."""

import asyncio
import json
import os
import re
import subprocess
import sys

import pytest

from ableton_mcp.server import mcp

TOOL_BUDGET = 40  # Copilot caps at 128 tools across all servers; some clients degrade past ~40.
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")  # OpenAI/Copilot function-name rule
FORBIDDEN_KEYS = {"anyOf", "oneOf", "allOf", "$ref", "$defs", "title"}


def _tools():
    return asyncio.run(mcp.list_tools())


def _walk(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, k, v
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def test_tool_budget():
    assert len(_tools()) <= TOOL_BUDGET


@pytest.mark.parametrize("tool", _tools(), ids=lambda t: t.name)
def test_schema_is_portable(tool):
    assert NAME_RE.match(tool.name)
    assert tool.description and len(tool.description) <= 1024
    schema = tool.input_schema
    assert schema["type"] == "object"
    for path, key, value in _walk(schema):
        assert key not in FORBIDDEN_KEYS, f"{tool.name}: {key} at {path}"
        if key == "type":
            assert value != "null" and not isinstance(value, list), f"{tool.name}: type {value} at {path}"
        if key == "default":
            assert value is not None, f"{tool.name}: null default at {path}"
    for name, prop in schema.get("properties", {}).items():
        assert "type" in prop or "enum" in prop, f"{tool.name}.{name} has no type (Gemini rejects untyped params)"


def test_conventions_reach_parameters():
    tools = {t.name: t for t in _tools()}
    track = tools["set_track"].input_schema["properties"]["track"]
    assert track["type"] == "string" and "0-based" in track["description"]
    assert "Ask the user" in tools["delete"].input_schema["properties"]["confirm"]["description"]


def _rpc_session(messages, protocol):
    env = {**os.environ, "ABLETON_MCP_PORT": "1"}  # nothing listens there: tools must fail gracefully
    init = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": protocol, "capabilities": {}, "clientInfo": {"name": "compat-test", "version": "0"}}}
    lines = [init, {"jsonrpc": "2.0", "method": "notifications/initialized"}, *messages]
    wanted = {m["id"] for m in lines if "id" in m}
    proc = subprocess.Popen([sys.executable, "-m", "ableton_mcp.server"], env=env, text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    replies = {}
    try:
        proc.stdin.write("".join(json.dumps(m) + "\n" for m in lines))
        proc.stdin.flush()  # keep stdin open: the server exits (cancelling calls) on EOF
        while wanted - replies.keys():
            line = proc.stdout.readline()
            if not line:
                break
            reply = json.loads(line)
            if "id" in reply:
                replies[reply["id"]] = reply
    finally:
        proc.kill()
        proc.wait(timeout=10)
    return replies


@pytest.mark.parametrize("protocol", ["2024-11-05", "2025-03-26", "2025-06-18"])
def test_stdio_roundtrip_with_older_protocols(protocol):
    replies = _rpc_session([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_session_overview", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "set_mixer", "arguments": {"track": 2, "pan": "25L"}}},
    ], protocol)
    assert "result" in replies[0], replies[0]
    assert replies[0]["result"]["protocolVersion"] == protocol
    assert len(replies[1]["result"]["tools"]) == len(_tools())
    status = replies[2]["result"]
    assert status["isError"] and "AbletonMCP" in status["content"][0]["text"]
    # An integer where the schema says string is still accepted (fails later, on the connection).
    assert "Can't reach Ableton Live" in replies[3]["result"]["content"][0]["text"]


def test_every_tool_declares_safety_annotations():
    for t in _tools():
        a = t.annotations
        assert a is not None and a.read_only_hint is not None and a.destructive_hint is not None, t.name
        if t.name.startswith(("get_", "analyze_", "read_", "list_")):
            assert a.read_only_hint, f"{t.name} reads only and should say so"
        if t.name == "delete":
            assert a.destructive_hint
