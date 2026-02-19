"""
Web Search tool (Perplexity-based).

Search the web using Perplexity API.
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

# Import web search tool
try:
    import web_search_tool
except ImportError:
    web_search_tool = None


@register_tool("utilities")
@tool(
    name="web_search",
    description="""Search the web using Perplexity API.

Use this to find current information, news, documentation, or any web content.
Returns formatted search results with titles, URLs, source labels, snippets, and dates.

Each result includes a [Source Label] (e.g., [GitHub], [Blog], [MDN], [StackOverflow])
derived from the URL, so you can quickly assess credibility without parsing URLs.

Snippet modes control how much context you get per result:
- "brief": ~200 chars, truncated at sentence boundaries. Good for quick scans.
- "normal" (default): ~600 chars, truncated at sentence boundaries.
- "full": No truncation — full snippet from Perplexity. Use when you need max context.

Results include a quality warning when results appear thin or low-relevance.

Supports filtering by:
- recency: "day", "week", "month", "year"
- country: ISO 2-letter code (e.g., "US", "GB")
- domains: List of domains to include/exclude (prefix "-" to exclude)

For multiple parallel searches, call this tool multiple times.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query"
            },
            "max_results": {
                "type": "integer",
                "description": "Number of results (1-20, default 10)",
                "default": 10
            },
            "recency": {
                "type": "string",
                "enum": ["day", "week", "month", "year"],
                "description": "Filter results by recency"
            },
            "country": {
                "type": "string",
                "description": "ISO 2-letter country code for regional filtering"
            },
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Domains to include/exclude (prefix '-' to exclude)"
            },
            "snippet_mode": {
                "type": "string",
                "enum": ["brief", "normal", "full"],
                "description": "Snippet length: 'brief' (~200 chars), 'normal' (~600 chars, default), 'full' (no truncation)",
                "default": "normal"
            }
        },
        "required": ["query"]
    }
)
async def web_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Perform a web search using Perplexity."""
    try:
        if not web_search_tool:
            return {
                "content": [{"type": "text", "text": "Web search not available: perplexityai package not installed"}],
                "is_error": True
            }

        query = args.get("query", "")
        max_results = args.get("max_results", 10)
        recency = args.get("recency")
        country = args.get("country")
        domains = args.get("domains")
        snippet_mode = args.get("snippet_mode", "normal")

        result = await web_search_tool.web_search(
            query=query,
            max_results=max_results,
            recency=recency,
            country=country,
            domains=domains,
            snippet_mode=snippet_mode,
        )

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Web search error: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
