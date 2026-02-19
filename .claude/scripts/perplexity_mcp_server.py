#!/usr/bin/env python3
"""
Perplexity Web Search MCP Server

A standalone MCP server that provides web search via Perplexity API.
Can be used with Claude Code CLI via: claude mcp add perplexity -- python3 /path/to/perplexity_mcp_server.py
"""

import asyncio
import os
import re
import sys
import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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


# --- Domain label mapping ---
DOMAIN_LABELS: Dict[str, str] = {
    "github.com": "GitHub",
    "gitlab.com": "GitLab",
    "stackoverflow.com": "StackOverflow",
    "stackexchange.com": "StackExchange",
    "docs.python.org": "Python Docs",
    "docs.rs": "Rust Docs",
    "doc.rust-lang.org": "Rust Docs",
    "developer.mozilla.org": "MDN",
    "en.wikipedia.org": "Wikipedia",
    "medium.com": "Blog",
    "dev.to": "Blog",
    "hashnode.dev": "Blog",
    "substack.com": "Blog",
    "news.ycombinator.com": "HN",
    "reddit.com": "Reddit",
    "www.reddit.com": "Reddit",
    "arxiv.org": "arXiv",
    "huggingface.co": "HuggingFace",
    "pypi.org": "PyPI",
    "npmjs.com": "npm",
    "www.npmjs.com": "npm",
    "crates.io": "Crates.io",
    "docs.google.com": "Google Docs",
    "cloud.google.com": "Google Cloud",
    "aws.amazon.com": "AWS",
    "learn.microsoft.com": "Microsoft Docs",
    "anthropic.com": "Anthropic",
    "docs.anthropic.com": "Anthropic Docs",
    "platform.openai.com": "OpenAI Docs",
    "openai.com": "OpenAI",
}


def get_source_label(url: str) -> str:
    """Extract a human-readable source label from a URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Pass 1: exact match (with/without www)
        for domain, label in DOMAIN_LABELS.items():
            if hostname == domain or hostname == f"www.{domain}":
                return label
        # Pass 2: subdomain match — sort by length desc so specific domains match first
        for domain, label in sorted(DOMAIN_LABELS.items(), key=lambda x: len(x[0]), reverse=True):
            if hostname.endswith(f".{domain}"):
                return label
        clean = hostname.removeprefix("www.")
        parts = clean.split(".")
        if len(parts) >= 2:
            return parts[-2].title()
        return clean.title()
    except Exception:
        return "Web"


SNIPPET_LIMITS = {
    "brief": 200,
    "normal": 300,  # standalone server default was 300
    "full": 0,
}


def truncate_at_sentence(text: str, max_length: int) -> str:
    """Truncate text at a sentence boundary, falling back to word boundary."""
    if max_length <= 0 or len(text) <= max_length:
        return text
    truncated = text[:max_length]
    match = None
    for m in re.finditer(r'[.!?](?:\s|$)', truncated):
        match = m
    if match and match.end() > max_length * 0.4:
        return truncated[:match.end()].rstrip()
    last_space = truncated.rfind(" ")
    if last_space > max_length * 0.4:
        return truncated[:last_space].rstrip() + "..."
    return truncated.rstrip() + "..."


def check_result_quality(results: List[Dict[str, Any]], max_results: int) -> Optional[str]:
    """Check if results look low-quality and return a warning if so."""
    if not results:
        return None
    short_snippet_count = sum(1 for r in results if len(r.get("snippet", "")) < 50)
    all_snippets_short = short_snippet_count == len(results)
    very_few_results = len(results) <= 2 and max_results >= 5
    if all_snippets_short and very_few_results:
        return "⚠️ Results appear thin — snippets are very short and few results were returned. Try rephrasing your query or broadening search terms."
    elif all_snippets_short:
        return "⚠️ All result snippets are unusually short. The search may not have found strong matches — consider rephrasing."
    elif very_few_results:
        return f"⚠️ Only {len(results)} result(s) found (requested {max_results}). Results may not be directly relevant — try different keywords."
    return None


def format_results(results: List[Dict[str, Any]], snippet_mode: str = "normal") -> str:
    """Format search results for display."""
    max_length = SNIPPET_LIMITS.get(snippet_mode, SNIPPET_LIMITS["normal"])

    formatted = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        date = r.get("date", "")
        source_label = get_source_label(url)

        entry = f"{i}. **{title}** [{source_label}]\n   {url}"
        if date:
            entry += f"\n   Published: {date}"
        if snippet:
            snippet = truncate_at_sentence(snippet, max_length)
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
Returns formatted search results with titles, URLs, source labels, snippets, and dates.

Each result includes a [Source Label] (e.g., [GitHub], [Blog], [MDN], [StackOverflow])
derived from the URL, so you can quickly assess credibility without parsing URLs.

Snippet modes control how much context you get per result:
- "brief": ~200 chars, truncated at sentence boundaries. Good for quick scans.
- "normal" (default): ~300 chars, truncated at sentence boundaries.
- "full": No truncation — full snippet from Perplexity. Use when you need max context.

Results include a quality warning when results appear thin or low-relevance.

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
                    },
                    "snippet_mode": {
                        "type": "string",
                        "enum": ["brief", "normal", "full"],
                        "description": "Snippet length: 'brief' (~200 chars), 'normal' (~300 chars, default), 'full' (no truncation)",
                        "default": "normal"
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

    max_results = arguments.get("max_results", 10)
    snippet_mode = arguments.get("snippet_mode", "normal")
    if snippet_mode not in SNIPPET_LIMITS:
        snippet_mode = "normal"

    try:
        client = get_client()
        result = client.search(
            query=query.strip(),
            max_results=max_results,
            recency=arguments.get("recency"),
            country=arguments.get("country"),
            domains=arguments.get("domains"),
        )

        if result.get("success"):
            results = result.get("results", [])
            if results:
                formatted = format_results(results, snippet_mode=snippet_mode)
                quality_warning = check_result_quality(results, max_results)
                output = (
                    f"## Web Search Results for: '{result['query']}'\n\n"
                    f"{formatted}\n\n"
                    f"*{result['result_count']} results in {result['search_time']}s*"
                )
                if quality_warning:
                    output += f"\n\n{quality_warning}"
            else:
                output = f"No results found for: '{result['query']}'. Try different keywords or broader search terms."
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
