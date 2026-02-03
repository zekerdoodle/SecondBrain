"""
Perplexity-based Web Search Tool for Second Brain

Provides web search capabilities using Perplexity's Search API.
Replaces the native WebSearch tool with more control and consistency.
"""

import asyncio
import os
import time
from typing import Any, Dict, List, Optional
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
            if len(snippet) > 600:
                snippet = snippet[:600] + "..."
            entry += f"\n   {snippet}"
        formatted.append(entry)

    return "\n\n".join(formatted)


async def web_search(
    query: str,
    max_results: int = 10,
    recency: Optional[str] = None,
    country: Optional[str] = None,
    domains: Optional[List[str]] = None,
) -> str:
    """
    Perform a web search using Perplexity API.

    Args:
        query: Search query string
        max_results: Number of results (1-20, default 10)
        recency: Filter by time ("day", "week", "month", "year")
        country: ISO 2-letter country code (e.g., "US")
        domains: List of domains to include/exclude (prefix "-" to exclude)

    Returns:
        Formatted search results as a string
    """
    if not query or not query.strip():
        return "Error: Search query cannot be empty"

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
                formatted = format_results(results)
                return (
                    f"## Web Search Results for: '{result['query']}'\n\n"
                    f"{formatted}\n\n"
                    f"*{result['result_count']} results in {result['search_time']}s*"
                )
            else:
                return f"No results found for: '{result['query']}'"
        else:
            return f"Search failed: {result.get('error', 'Unknown error')}"

    except Exception as e:
        logger.error(f"Web search error: {e}")
        return f"Web Search Error: {e}"
