"""
Page Parser tool.

Fetches and parses web pages into clean Markdown.
Supports full content mode and summary mode (AI-condensed via Haiku).
"""

import logging
import os
import sys
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.page_parser")

# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


# Summary mode system prompt — general-purpose document summarizer
SUMMARY_SYSTEM_PROMPT = """You are a document summarizer. Produce a clear, well-structured summary of the provided web page content.

Adapt your summary style to the document type:
- **Code documentation / API docs**: Highlight key APIs, function signatures, usage patterns, important caveats, and code examples (preserve small code snippets verbatim in fenced blocks).
- **Research papers / academic**: Extract thesis, methodology, key findings, conclusions, and notable figures/data.
- **News articles / blog posts**: Capture the main story, key facts, quotes, and conclusions.
- **Tutorials / how-to guides**: Outline the steps, prerequisites, and final outcome.
- **Reference pages / specs**: List the most important items, constraints, and relationships.
- **General long-form content**: Identify the core argument, supporting evidence, and takeaways.

Guidelines:
- Start with a 1-2 sentence overview of what the document is about
- Use markdown headers (##) to organize sections
- Preserve exact numbers, names, dates, URLs, and code snippets — never paraphrase these
- If the content was truncated, note that the summary covers only the available portion
- Aim for 15-25% of the original length, but prioritize completeness of key information over brevity
- Use bullet points for lists of items, numbered steps for procedures
- Flag anything that seems contradictory or notably surprising"""


async def _summarize_content(title: str, url: str, content: str, truncated: bool) -> str:
    """
    Summarize page content using Haiku via Claude Agent SDK.

    Returns the summary text, or falls back to truncated content on error.
    """
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

    trunc_note = ""
    if truncated:
        trunc_note = "\n\n[NOTE: This content was truncated. Summarize what's available and mention the truncation.]"

    prompt = f"""Summarize the following web page:

Title: {title}
URL: {url}
{trunc_note}

---
{content}
---

Produce a structured summary following your guidelines."""

    logger.info(f"Running summary subagent on {len(content)} chars from {url}")

    try:
        result_text = None

        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="haiku",
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                max_turns=1,
                permission_mode="bypassPermissions",
                allowed_tools=[],
                setting_sources=[],
            ),
        ):
            if isinstance(message, ResultMessage) and message.result:
                result_text = message.result

        if result_text:
            logger.info(f"Summary subagent produced {len(result_text)} char summary")
            return result_text

        logger.warning("Summary subagent returned no result, falling back to full content")
        return None

    except Exception as e:
        logger.error(f"Summary subagent failed: {e}")
        return None


@register_tool("utilities")
@tool(
    name="page_parser",
    description="""Fetch and parse web pages into clean Markdown, with optional AI summarization.

Use this to extract readable content from URLs. Supports:
- HTML pages (uses Readability + markdownify for clean extraction)
- PDF documents (extracts text)
- Multiple URLs in one call
- **Summary mode**: AI-condensed version via Haiku — great for long docs, papers, or any content where you need the key points fast

The tool:
1. Fetches the URL with proper headers and retries
2. Extracts main content (removes nav, ads, boilerplate)
3. Converts to clean Markdown with proper code block fencing
4. In summary mode, runs the content through Haiku for an intelligent summary
5. Optionally saves to docs/webresults for future reference

Content is truncated at ~50k chars by default. Truncated/saved pages are stored for full retrieval later.""",
    input_schema={
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "URL(s) to fetch and parse"
            },
            "mode": {
                "type": "string",
                "enum": ["full", "summary"],
                "description": "Output mode. 'full' returns the complete parsed content (default). 'summary' returns an AI-condensed summary — ideal for long documents, research papers, API docs, or any content where you need key points quickly.",
                "default": "full"
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
    """Parse web pages into clean Markdown, optionally summarized."""
    try:
        import page_parser as pp

        urls = args.get("urls", [])
        mode = args.get("mode", "full")
        save = args.get("save", False)
        max_chars = args.get("max_chars", 50000)

        if not urls:
            return {"content": [{"type": "text", "text": "No URLs provided"}], "is_error": True}

        if mode not in ("full", "summary"):
            return {"content": [{"type": "text", "text": f"Invalid mode: {mode}. Use 'full' or 'summary'."}], "is_error": True}

        # Parse all URLs
        results = []
        for url in urls:
            result = pp.page_parser_single(url, save=save, max_chars=max_chars)

            if not result["success"]:
                results.append(f"Error parsing {url}: {result.get('error', 'Unknown error')}")
                continue

            if mode == "summary":
                # Run the content through Haiku for summarization
                summary = await _summarize_content(
                    title=result.get("title", ""),
                    url=result.get("url", url),
                    content=result["content"],
                    truncated=result.get("truncated", False),
                )

                if summary:
                    # Build summary output with metadata header
                    header_lines = [
                        f"# {result.get('title', 'Untitled')} (Summary)",
                        "",
                        f"Source: {result.get('url', url)}",
                        f"Domain: {result.get('domain', '')}",
                        f"Original: {result.get('char_count', 0):,} chars",
                        f"Mode: AI Summary (Haiku)",
                    ]
                    if result.get("saved_path"):
                        header_lines.append(f"Full content saved: {result['saved_path']}")
                    if result.get("truncated"):
                        header_lines.append(f"Note: Original was truncated before summarization")
                    header_lines.extend(["", "---", ""])

                    results.append("\n".join(header_lines) + summary)
                else:
                    # Fallback to full content if summary failed
                    results.append(
                        f"*(Summary generation failed, returning full content)*\n\n"
                        + result["content"]
                    )
            else:
                results.append(result["content"])

        combined = "\n\n---\n\n".join(results)
        return {"content": [{"type": "text", "text": combined}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
