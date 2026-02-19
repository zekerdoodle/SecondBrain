"""
Perplexity-based Web Search Tool for Second Brain

Provides web search capabilities using Perplexity's Search API.
Replaces the native WebSearch tool with more control and consistency.
"""

import asyncio
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# Optional import for Perplexity SDK
try:
    from perplexity import Perplexity
except ImportError:
    Perplexity = None


class PerplexitySearchClient:
    """Client for performing web searches using Perplexity's Search API."""

    def __init__(self):
        if not Perplexity:
            raise ImportError(
                "perplexityai package is required. Install with: pip install perplexityai"
            )

        api_key = os.environ.get("PERPLEXITY_API_KEY")
        if not api_key:
            raise ValueError("PERPLEXITY_API_KEY environment variable is not set.")

        self.client = Perplexity(api_key=api_key)
        self.max_parallel_searches = 5
        self.default_max_results = 10
        self.default_max_tokens_per_page = 2048  # Higher for more context per result
        logger.info("PerplexitySearchClient initialized")

    async def search_single(
        self,
        query: str,
        max_results: Optional[int] = None,
        recency: Optional[str] = None,
        country: Optional[str] = None,
        domains: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Perform a single web search."""
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
            if languages:
                search_params["search_language_filter"] = languages[:10]

            # Execute in thread pool to avoid blocking
            def _call():
                return self.client.search.create(**search_params)

            response = await asyncio.to_thread(_call)

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

            search_time = round(time.time() - start_time, 2)
            logger.info(f"Search for '{query[:50]}...' returned {len(results)} results in {search_time}s")

            return {
                "query": query,
                "results": results,
                "result_count": len(results),
                "search_time": search_time,
                "success": True,
            }

        except Exception as e:
            logger.error(f"Web search failed for '{query}': {e}")
            return {
                "query": query,
                "error": str(e),
                "search_time": round(time.time() - start_time, 2),
                "success": False,
            }

    async def search_parallel(
        self,
        queries: List[str],
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Perform multiple web searches in parallel (max 5)."""
        queries = queries[:self.max_parallel_searches]
        tasks = [self.search_single(q, **kwargs) for q in queries]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed.append({
                    "query": queries[i],
                    "error": str(result),
                    "success": False,
                })
            else:
                processed.append(result)
        return processed


# Global singleton
_client: Optional[PerplexitySearchClient] = None


def get_client() -> PerplexitySearchClient:
    """Get or create the global search client."""
    global _client
    if _client is None:
        _client = PerplexitySearchClient()
    return _client


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
    """Extract a human-readable source label from a URL.

    Checks DOMAIN_LABELS for known domains (exact match first, then
    subdomain match), then falls back to title-casing the base domain name.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""

        # Pass 1: exact match (with/without www)
        for domain, label in DOMAIN_LABELS.items():
            if hostname == domain or hostname == f"www.{domain}":
                return label

        # Pass 2: subdomain match (e.g., "blog.anthropic.com" matches "anthropic.com")
        # Sort by domain length descending so more specific domains match first
        for domain, label in sorted(DOMAIN_LABELS.items(), key=lambda x: len(x[0]), reverse=True):
            if hostname.endswith(f".{domain}"):
                return label

        # Fallback: strip www., strip TLD, title-case
        clean = hostname.removeprefix("www.")
        parts = clean.split(".")
        if len(parts) >= 2:
            return parts[-2].title()
        return clean.title()
    except Exception:
        return "Web"


# --- Snippet truncation modes ---
SNIPPET_LIMITS = {
    "brief": 200,
    "normal": 600,
    "full": 0,  # 0 = no truncation
}


def truncate_at_sentence(text: str, max_length: int) -> str:
    """Truncate text at a sentence boundary, falling back to word boundary.

    Returns the original text if it's already within max_length.
    """
    if max_length <= 0 or len(text) <= max_length:
        return text

    # Look for the last sentence-ending punctuation before the limit
    truncated = text[:max_length]
    # Find last sentence boundary (. ! ? followed by space or end)
    match = None
    for m in re.finditer(r'[.!?](?:\s|$)', truncated):
        match = m
    if match and match.end() > max_length * 0.4:
        # Only use sentence boundary if it captures at least 40% of the text
        return truncated[:match.end()].rstrip()

    # Fall back to word boundary
    last_space = truncated.rfind(" ")
    if last_space > max_length * 0.4:
        return truncated[:last_space].rstrip() + "..."

    return truncated.rstrip() + "..."


# --- Low-quality result detection ---
def check_result_quality(results: List[Dict[str, Any]], max_results: int) -> Optional[str]:
    """Check if results look low-quality and return a warning if so.

    Heuristics:
    - All snippets very short (< 50 chars)
    - Very few results returned relative to what was requested
    """
    if not results:
        return None

    short_snippet_count = sum(
        1 for r in results
        if len(r.get("snippet", "")) < 50
    )
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
    """Format search results for display.

    Args:
        results: List of result dicts with title, url, snippet, date.
        snippet_mode: Controls snippet length — "brief" (~200 chars),
                      "normal" (~600 chars, default), "full" (no truncation).
    """
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


async def web_search(
    query: str,
    max_results: int = 10,
    recency: Optional[str] = None,
    country: Optional[str] = None,
    domains: Optional[List[str]] = None,
    snippet_mode: str = "normal",
) -> str:
    """
    Perform a web search using Perplexity API.

    Args:
        query: Search query string
        max_results: Number of results (1-20, default 10)
        recency: Filter by time ("day", "week", "month", "year")
        country: ISO 2-letter country code (e.g., "US")
        domains: List of domains to include/exclude (prefix "-" to exclude)
        snippet_mode: Controls snippet length — "brief" (~200 chars, sentence-aware),
                      "normal" (~600 chars, default), "full" (no truncation)

    Returns:
        Formatted search results as a string
    """
    if not query or not query.strip():
        return "Error: Search query cannot be empty"

    # Validate snippet_mode
    if snippet_mode not in SNIPPET_LIMITS:
        snippet_mode = "normal"

    try:
        client = get_client()
        result = await client.search_single(
            query=query.strip(),
            max_results=max_results,
            recency=recency,
            country=country,
            domains=domains,
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
                return output
            else:
                return f"No results found for: '{result['query']}'. Try different keywords or broader search terms."
        else:
            return f"Search failed: {result.get('error', 'Unknown error')}"

    except Exception as e:
        logger.error(f"Web search error: {e}")
        return f"Web Search Error: {e}"
