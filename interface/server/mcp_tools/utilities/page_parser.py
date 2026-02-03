"""
Page Parser tool.

Fetches and parses web pages into clean Markdown.
"""

import os
import sys
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


@register_tool("utilities")
@tool(
    name="page_parser",
    description="""Fetch and parse web pages into clean Markdown.

Use this to extract readable content from URLs. Supports:
- HTML pages (uses Readability + markdownify for clean extraction)
- PDF documents (extracts text)
- Multiple URLs in one call

The tool:
1. Fetches the URL with proper headers and retries
2. Extracts main content (removes nav, ads, boilerplate)
3. Converts to clean Markdown with metadata header
4. Optionally saves to docs/webresults for future reference

Content is truncated at ~50k chars by default. Truncated/saved pages are stored for full retrieval later.""",
    input_schema={
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "URL(s) to fetch and parse"
            },
            "save": {
                "type": "boolean",
                "description": "Save parsed content to docs/webresults (default: false, but always saves if truncated)",
                "default": False
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters per page (default: 50000)",
                "default": 50000
            }
        },
        "required": ["urls"]
    }
)
async def page_parser(args: Dict[str, Any]) -> Dict[str, Any]:
    """Parse web pages into clean Markdown."""
    try:
        import page_parser as pp

        urls = args.get("urls", [])
        save = args.get("save", False)
        max_chars = args.get("max_chars", 50000)

        if not urls:
            return {"content": [{"type": "text", "text": "No URLs provided"}], "is_error": True}

        # Parse all URLs
        results = []
        for url in urls:
            result = pp.page_parser_single(url, save=save, max_chars=max_chars)
            if result["success"]:
                results.append(result["content"])
            else:
                results.append(f"Error parsing {url}: {result.get('error', 'Unknown error')}")

        combined = "\n\n---\n\n".join(results)
        return {"content": [{"type": "text", "text": combined}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
