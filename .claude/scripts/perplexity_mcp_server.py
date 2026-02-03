#!/usr/bin/env python3
"""
Perplexity Web Search MCP Server

A standalone MCP server that provides web search via Perplexity API.
Can be used with Claude Code CLI via: claude mcp add perplexity -- python3 /path/to/perplexity_mcp_server.py
"""

import asyncio
import os
import sys
import json
import time
from typing import Any, Dict, List, Optional

# Ensure we can import from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("MCP package not installed. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)

try:
    from perplexity import Perplexity
except ImportError:
    print("Perplexity package not installed. Install with: pip install perplexityai", file=sys.stderr)
    sys.exit(1)


class PerplexitySearchClient:
    """Client for performing web searches using Perplexity's Search API."""

    def __init__(self):
        api_key = os.environ.get("PERPLEXITY_API_KEY")
        if not api_key:
            raise ValueError("PERPLEXITY_API_KEY environment variable is not set.")

        self.client = Perplexity(api_key=api_key)
        self.default_max_results = 10
        self.default_max_tokens_per_page = 1024

    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        recency: Optional[str] = None,
        country: Optional[str] = None,
        domains: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Perform a web search."""
        start_time = time.time()

        try:
            search_params = {
                "query": query,
                "max_results": max_results or self.default_max_results,
                "max_tokens_per_page": self.default_max_tokens_per_page,
            }

            if recency:
                search_params["search_recency_filter"] = recency
            if country:
                search_params["country"] = country
            if domains:
                search_params["search_domain_filter"] = domains[:20]

            response = self.client.search.create(**search_params)

            results = []
            if hasattr(response, "results") and response.results:
                for r in response.results:
                    result_item = {
                        "title": getattr(r, "title", ""),
                        "url": getattr(r, "url", ""),
                        "snippet": getattr(r, "snippet", ""),
                    }
                    if hasattr(r, "date") and r.date:
                        result_item["date"] = r.date
                    results.append(result_item)

            return {
                "query": query,
                "results": results,
                "result_count": len(results),
                "search_time": round(time.time() - start_time, 2),
                "success": True,
            }

        except Exception as e:
            return {
                "query": query,
                "error": str(e),
                "search_time": round(time.time() - start_time, 2),
                "success": False,
            }


def format_results(results: List[Dict[str, Any]]) -> str:
    """Format search results for display."""
    formatted = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        date = r.get("date", "")

        entry = f"{i}. **{title}**\n   {url}"
        if date:
            entry += f"\n   Published: {date}"
        if snippet:
            if len(snippet) > 300:
                snippet = snippet[:300] + "..."
            entry += f"\n   {snippet}"
        formatted.append(entry)

    return "\n\n".join(formatted)


# Initialize MCP server
server = Server("perplexity-search")
_client: Optional[PerplexitySearchClient] = None


def get_client() -> PerplexitySearchClient:
    global _client
    if _client is None:
        _client = PerplexitySearchClient()
    return _client


@server.list_tools()
async def list_tools() -> List[Tool]:
    return [
        Tool(
            name="web_search",
            description="""Search the web using Perplexity API.

Use this to find current information, news, documentation, or any web content.
Returns formatted search results with titles, URLs, snippets, and dates.

Supports filtering by:
- recency: "day", "week", "month", "year"
- country: ISO 2-letter code (e.g., "US", "GB")
- domains: List of domains to include/exclude (prefix "-" to exclude)""",
            inputSchema={
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
                    }
                },
                "required": ["query"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    if name != "web_search":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    query = arguments.get("query", "")
    if not query or not query.strip():
        return [TextContent(type="text", text="Error: Search query cannot be empty")]

    try:
        client = get_client()
        result = client.search(
            query=query.strip(),
            max_results=arguments.get("max_results", 10),
            recency=arguments.get("recency"),
            country=arguments.get("country"),
            domains=arguments.get("domains"),
        )

        if result.get("success"):
            results = result.get("results", [])
            if results:
                formatted = format_results(results)
                output = (
                    f"## Web Search Results for: '{result['query']}'\n\n"
                    f"{formatted}\n\n"
                    f"*{result['result_count']} results in {result['search_time']}s*"
                )
            else:
                output = f"No results found for: '{result['query']}'"
        else:
            output = f"Search failed: {result.get('error', 'Unknown error')}"

        return [TextContent(type="text", text=output)]

    except Exception as e:
        return [TextContent(type="text", text=f"Web Search Error: {e}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
