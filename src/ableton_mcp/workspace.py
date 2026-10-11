"""The agent's working folder (default: <repo>/agent, override with ABLETON_MCP_AGENT_DIR)."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUBFOLDERS = ("exports", "references", "captures", "reports", "presets", "projects")


def agent_dir() -> Path:
    root = Path(os.environ.get("ABLETON_MCP_AGENT_DIR", REPO_ROOT / "agent")).expanduser()
    for sub in SUBFOLDERS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def folder(name: str) -> Path:
    if name not in SUBFOLDERS:
        raise ValueError(f"Unknown agent folder {name!r}; use one of {', '.join(SUBFOLDERS)}")
    return agent_dir() / name
