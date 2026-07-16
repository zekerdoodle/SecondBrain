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

LEGACY_FALLBACK_TEXT_LIMIT_BYTES = 32 * 1024
LEGACY_FALLBACK_ITEM_LIMIT_BYTES = 4 * 1024
LEGACY_FALLBACK_TRUNCATION_MARKER = "\n[unsupported tool content truncated]"


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


def _utf8_bounded(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    marker = LEGACY_FALLBACK_TRUNCATION_MARKER
    marker_bytes = marker.encode("utf-8")
    if len(marker_bytes) >= limit:
        return marker_bytes[:limit].decode("utf-8", errors="ignore")
    prefix_limit = max(0, limit - len(marker_bytes))
    prefix = encoded[:prefix_limit].decode("utf-8", errors="ignore")
    return prefix + marker


def _is_text_item(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.get("type") == "text"
        and item.get("text") is not None
    )


def _is_image_item(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.get("type") == "image"
        and isinstance(item.get("data"), str)
        and bool(item.get("data"))
        and isinstance(item.get("mimeType"), str)
        and bool(item.get("mimeType"))
    )


def _fallback_item_text(item: Any) -> str:
    """Represent legacy/unknown content without dumping binary-like fields."""
    if isinstance(item, dict):
        if item.get("text") is not None:
            return _utf8_bounded(
                str(item.get("text")),
                LEGACY_FALLBACK_ITEM_LIMIT_BYTES,
            )
        item_type = str(item.get("type") or "unknown")
        keys = ",".join(sorted(str(key) for key in item.keys())[:32])
        return f"Unsupported tool content item (type={item_type}; keys={keys})"
    if isinstance(item, (bytes, bytearray, memoryview)):
        return f"Unsupported binary tool content ({len(item)} bytes omitted)"
    text = str(item)
    if text.lstrip().startswith("data:"):
        return "Unsupported data URL tool content omitted"
    return _utf8_bounded(text, LEGACY_FALLBACK_ITEM_LIMIT_BYTES)


def _as_mcp_content(result: Any) -> List[Any]:
    """Preserve typed MCP images while retaining all-text compatibility."""
    from mcp.types import ImageContent, TextContent

    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list):
        return [
            TextContent(
                type="text",
                text=_utf8_bounded(
                    _fallback_item_text(content if content is not None else result),
                    LEGACY_FALLBACK_TEXT_LIMIT_BYTES,
                ),
            )
        ]

    if all(_is_text_item(item) for item in content):
        return [TextContent(type="text", text=_as_text_content(result))]

    has_typed_image = any(_is_image_item(item) for item in content)
    if not has_typed_image:
        fallback = "\n".join(
            str(item.get("text")) if _is_text_item(item) else _fallback_item_text(item)
            for item in content
        )
        return [
            TextContent(
                type="text",
                text=_utf8_bounded(fallback, LEGACY_FALLBACK_TEXT_LIMIT_BYTES),
            )
        ]

    converted: List[Any] = []
    fallback_bytes = 0
    for item in content:
        if _is_text_item(item):
            converted.append(TextContent(type="text", text=str(item.get("text"))))
            continue
        if _is_image_item(item):
            converted.append(
                ImageContent(
                    type="image",
                    data=str(item.get("data")),
                    mimeType=str(item.get("mimeType")),
                )
            )
            continue
        remaining = LEGACY_FALLBACK_TEXT_LIMIT_BYTES - fallback_bytes
        if remaining <= 0:
            continue
        fallback_text = _utf8_bounded(
            _fallback_item_text(item),
            min(LEGACY_FALLBACK_ITEM_LIMIT_BYTES, remaining),
        )
        fallback_bytes += len(fallback_text.encode("utf-8"))
        converted.append(TextContent(type="text", text=fallback_text))
    return converted


async def main() -> None:
    _load_env_files()

    parser = argparse.ArgumentParser()
    parser.add_argument("--tools", nargs="*", default=[])
    parser.add_argument("--chat-id")
    parser.add_argument("--agent-name")
    parser.add_argument("--allowed-skills-json")
    parser.add_argument("--salon-id")
    parser.add_argument("--restart-consumer", default="none")
    args = parser.parse_args()

    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool
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
        call_args = dict(arguments or {})
        if name == "restart_server":
            call_args["_restart_consumer"] = args.restart_consumer or "none"
        with contextlib.redirect_stdout(sys.stderr):
            result = await by_name[name].handler(call_args)
        return _as_mcp_content(result)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("SECOND_BRAIN_MCP_LOG_LEVEL", "WARNING"))
    asyncio.run(main())
