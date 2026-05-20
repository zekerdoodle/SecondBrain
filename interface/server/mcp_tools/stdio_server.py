"""Stdio MCP bridge for Second Brain tools used by Codex CLI."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT_DIR = Path(__file__).resolve().parents[3]
SERVER_DIR = ROOT_DIR / "interface" / "server"
AGENTS_DIR = ROOT_DIR / ".claude" / "agents"
TOOLS_DIR = Path(__file__).resolve().parent
while str(TOOLS_DIR) in sys.path:
    sys.path.remove(str(TOOLS_DIR))
for _path in (SERVER_DIR, AGENTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

logger = logging.getLogger("mcp_tools.stdio_server")


def _load_env_files() -> None:
    """Load local secret env files for MCP subprocesses without exposing them in CLI args."""
    candidates = [
        ROOT_DIR / ".env",
        Path("/home/debian/second_brain/.env"),
        Path("/home/debian/second_brain_prime/.env"),
        Path("/home/debian/second_brain_v2/.env"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ[key] = value
        except Exception as exc:
            logger.warning("Failed to load MCP env file %s: %s", path, exc)


def _normal_tool_names(names: Iterable[str]) -> List[str]:
    out = []
    for name in names:
        if not name:
            continue
        if name.startswith("mcp__brain__"):
            out.append(name[len("mcp__brain__"):])
        else:
            out.append(name)
    return out


def _as_text_content(result: Any) -> str:
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", item)))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        if content is not None:
            return str(content)
        return json.dumps(result, ensure_ascii=False, default=str)
    return str(result)


async def main() -> None:
    _load_env_files()

    parser = argparse.ArgumentParser()
    parser.add_argument("--tools", nargs="*", default=[])
    parser.add_argument("--chat-id")
    parser.add_argument("--agent-name")
    parser.add_argument("--allowed-skills-json")
    parser.add_argument("--salon-id")
    args = parser.parse_args()

    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except Exception as exc:
        raise RuntimeError(
            "The Python 'mcp' package is required for Codex stdio MCP bridging. "
            "Install requirements.txt in the Linux runtime."
        ) from exc

    from mcp_tools import create_mcp_server

    allowed_skills: Any = None
    if args.allowed_skills_json:
        allowed_skills = json.loads(args.allowed_skills_json)

    tool_names = _normal_tool_names(args.tools)
    legacy_server = create_mcp_server(
        name="brain",
        include_tools=tool_names or None,
        chat_id=args.chat_id,
        agent_name=args.agent_name,
        allowed_skills=allowed_skills,
        salon_id=args.salon_id,
    )
    tools = list(legacy_server["tools"])
    by_name = {tool.name: tool for tool in tools}

    server = Server("second-brain")

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [
            Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema or {"type": "object", "properties": {}},
            )
            for t in tools
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: Dict[str, Any] | None):
        if name not in by_name:
            raise ValueError(f"Unknown Second Brain tool: {name}")
        # In stdio MCP, stdout is the protocol stream. Tool-side prints and
        # legacy CLI logger output must go to stderr so they cannot corrupt JSON-RPC framing.
        with contextlib.redirect_stdout(sys.stderr):
            result = await by_name[name].handler(arguments or {})
        return [TextContent(type="text", text=_as_text_content(result))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("SECOND_BRAIN_MCP_LOG_LEVEL", "WARNING"))
    asyncio.run(main())
