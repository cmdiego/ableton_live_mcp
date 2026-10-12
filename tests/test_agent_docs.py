"""Keeps the agent instruction files in sync: AGENTS.md is the shared source, CLAUDE.md imports it
and only adds Claude Code specifics, and facts both state match the server (D26)."""

import asyncio
import re
from pathlib import Path

from ableton_mcp.app import TOOL_BUDGET
from ableton_mcp.server import mcp

ROOT = Path(__file__).resolve().parents[1]
AGENTS = (ROOT / "AGENTS.md").read_text()
CLAUDE = (ROOT / "CLAUDE.md").read_text()
# Other clients' instruction files; if one is added it must defer to AGENTS.md as well.
OTHER_AGENT_FILES = ["GEMINI.md", ".cursorrules", ".github/copilot-instructions.md", ".windsurfrules"]


def _headings(text: str) -> set[str]:
    return {line.lstrip("#").strip().lower() for line in text.splitlines() if line.startswith("#")}


def _sentences(text: str) -> set[str]:
    """Normalized sentences of at least 8 words, so line wrapping and list markers don't matter."""
    flat = re.sub(r"\s+", " ", re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", text, flags=re.M))
    return {s.strip().lower() for s in re.split(r"(?<=[.!?:])\s", flat) if len(s.split()) >= 8}


def test_claude_md_imports_agents_md_first():
    first = next(line.strip() for line in CLAUDE.splitlines() if line.strip())
    assert first == "@AGENTS.md", "CLAUDE.md must start with '@AGENTS.md' so Claude Code loads the shared rules"


def test_claude_md_does_not_restate_agents_md():
    shared = _headings(CLAUDE) & _headings(AGENTS)
    assert not shared, f"Sections in both files (keep them in AGENTS.md only): {sorted(shared)}"
    repeated = _sentences(CLAUDE) & _sentences(AGENTS)
    assert not repeated, f"CLAUDE.md repeats AGENTS.md (keep it in AGENTS.md only): {sorted(repeated)}"


def test_claude_md_prompt_names_match_server():
    named = set(re.findall(r"(?:/mcp__ableton__|…)(\w+)", CLAUDE))
    served = {p.name for p in asyncio.run(mcp.list_prompts())}
    assert named == served, f"CLAUDE.md lists {sorted(named)}, the server has {sorted(served)}"


def test_agents_md_tool_budget_matches_server():
    m = re.search(r"Tool budget: (\d+) total, (\d+) used", AGENTS)
    assert m, "AGENTS.md lost its 'Tool budget: N total, M used' line"
    assert int(m.group(1)) == TOOL_BUDGET
    assert int(m.group(2)) == len(asyncio.run(mcp.list_tools())), "Update the tool count in AGENTS.md"


def test_other_agent_files_defer_to_agents_md():
    for name in OTHER_AGENT_FILES:
        path = ROOT / name
        if path.exists():
            assert "AGENTS.md" in path.read_text(), f"{name} should point to AGENTS.md instead of copying it"
