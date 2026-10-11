"""Entry point: `uv run ableton-mcp` starts the MCP server on stdio."""

from .app import mcp
from .tools import (  # noqa: F401  (registers tools)
    analysis, arrangement, compose, devices, live_audio, mastering, midi, mixer, mixing, session, workflow,
)  # noqa: F401  (registers tools)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
