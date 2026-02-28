#!/usr/bin/env python3
"""
Sync Anthropic Documentation

Fetches all pages from the Anthropic Agent SDK and Claude Code documentation
and saves them as local markdown files for offline reference by Claude Code agents.

Uses Playwright for JavaScript-rendered pages.

Sources:
  - Agent SDK: https://platform.claude.com/docs/en/agent-sdk/
  - Claude Code: https://code.claude.com/docs/en/
"""

import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import aiohttp

from bs4 import BeautifulSoup
from markdownify import markdownify as md
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# Configuration
SCRIPTS_DIR = Path(__file__).parent
DOCS_BASE_DIR = SCRIPTS_DIR.parent / "docs"
RATE_LIMIT_DELAY = 2.0  # seconds between requests
PAGE_LOAD_TIMEOUT = 30000  # milliseconds
CONTENT_WAIT_TIMEOUT = 10000  # milliseconds to wait for content to load


@dataclass
class DocSource:
    """Configuration for a documentation source."""
    name: str
    display_name: str
    base_url: str
    docs_dir: Path
    known_pages: list[str]
    link_pattern: str  # regex to find links in the main page
    slug_pattern: str  # regex to extract page slug from href
    skip_pages: list[str] = field(default_factory=list)  # pages to never sync (404s, invalid)
    page_timeouts: dict[str, int] = field(default_factory=dict)  # per-page timeout overrides (ms)
    raw_urls: dict[str, str] = field(default_factory=dict)  # pages fetched as raw markdown (no Playwright)


# Agent SDK documentation
AGENT_SDK_SOURCE = DocSource(
    name="agent_sdk",
    display_name="Anthropic Agent SDK",
    base_url="https://platform.claude.com/docs/en/agent-sdk/",
    docs_dir=DOCS_BASE_DIR / "anthropic_agent_sdk",
    known_pages=[
        "overview",
        "quickstart",
        "typescript",
        "python",
        "hooks",
        "permissions",
        "sessions",
        "mcp",
        "subagents",
        "user-input",
        "custom-tools",
        "hosting",
        "skills",
        "modifying-system-prompts",
        "file-checkpointing",
        "structured-outputs",
        "cost-tracking",
        "secure-deployment",
        "slash-commands",
        "plugins",
        "migration-guide",
        "typescript-v2-preview",
    ],
    link_pattern="/agent-sdk/",
    slug_pattern=r"/agent-sdk/([a-z0-9-]+)/?$",
)

# Claude Code documentation
CLAUDE_CODE_SOURCE = DocSource(
    name="claude_code",
    display_name="Claude Code",
    base_url="https://code.claude.com/docs/en/",
    docs_dir=DOCS_BASE_DIR / "claude_code",
    known_pages=[
        "agent-teams",
        "amazon-bedrock",
        "analytics",
        "authentication",
        "best-practices",
        "changelog",
        "checkpointing",
        "chrome",
        "claude-code-on-the-web",
        "cli-reference",
        "common-workflows",
        "costs",
        "data-usage",
        "desktop",
        "desktop-quickstart",
        "devcontainer",
        "discover-plugins",
        "fast-mode",
        "features-overview",
        "github-actions",
        "gitlab-ci-cd",
        "google-vertex-ai",
        "headless",
        "hooks",
        "hooks-guide",
        "how-claude-code-works",
        "interactive-mode",
        "jetbrains",
        "keybindings",
        "legal-and-compliance",
        "llm-gateway",
        "mcp",
        "memory",
        "microsoft-foundry",
        "model-config",
        "monitoring-usage",
        "network-config",
        "output-styles",
        "overview",
        "permissions",
        "plugin-marketplaces",
        "plugins",
        "plugins-reference",
        "quickstart",
        "sandboxing",
        "security",
        "server-managed-settings",
        "settings",
        "setup",
        "skills",
        "slack",
        "statusline",
        "sub-agents",
        "terminal-config",
        "third-party-integrations",
        "troubleshooting",
        "vs-code",
    ],
    link_pattern="/docs/en/",
    slug_pattern=r"/docs/en/([a-z0-9-]+)/?$",
    skip_pages=[
        "claude-md",  # 404 — content lives in the "memory" page (confirmed 2026-02-27)
    ],
    page_timeouts={
        "changelog": 90000,  # Large page (~111KB), redirects to GitHub — needs extra time
    },
    raw_urls={
        # Changelog redirects to GitHub which never reaches networkidle.
        # Fetch the raw markdown directly instead.
        "changelog": "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md",
    },
)

ALL_SOURCES = [AGENT_SDK_SOURCE, CLAUDE_CODE_SOURCE]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def compute_hash(content: str) -> str:
    """Compute MD5 hash of content for change detection."""
    return hashlib.md5(content.encode()).hexdigest()


def clean_markdown(html_content: str) -> str:
    """Convert HTML to clean markdown, preserving code blocks."""
    soup = BeautifulSoup(html_content, 'html.parser')

    # Remove navigation, footer, and other non-content elements
    for selector in ['nav', 'footer', 'header', '.sidebar', '.toc', '[role="navigation"]']:
        for tag in soup.select(selector):
            tag.decompose()

    # Remove script and style tags
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()

    # Find the main content area
    main_content = (
        soup.find('main') or
        soup.find('article') or
        soup.find('div', class_=re.compile(r'prose|content|docs|documentation|markdown')) or
        soup.find('div', {'role': 'main'})
    )

    if main_content:
        html_to_convert = str(main_content)
    else:
        # Fallback to body
        body = soup.find('body')
        html_to_convert = str(body) if body else str(soup)

    # Convert to markdown
    markdown = md(
        html_to_convert,
        heading_style="ATX",
        bullets="-",
        code_language_callback=lambda el: el.get('class', [''])[0].replace('language-', '') if el.get('class') else None,
    )

    # Clean up excessive whitespace
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    markdown = re.sub(r'  +', ' ', markdown)  # Multiple spaces to single
    markdown = markdown.strip()

    return markdown


def create_metadata_header(source_url: str, title: str) -> str:
    """Create a YAML-like metadata header for the markdown file."""
    timestamp = datetime.now(timezone.utc).isoformat()
    return f"""---
source: {source_url}
title: {title}
last_fetched: {timestamp}
---

"""


def extract_title(soup: BeautifulSoup, fallback: str) -> str:
    """Extract the page title from HTML."""
    # Try h1 first
    h1 = soup.find('h1')
    if h1:
        return h1.get_text(strip=True)

    # Try title tag
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.get_text(strip=True)
        # Remove common suffixes
        for suffix in [' - Anthropic', ' | Anthropic', ' - Claude', ' | Claude',
                       ' - Claude Code', ' | Claude Code']:
            if title.endswith(suffix):
                title = title[:-len(suffix)]
        return title

    return fallback.replace('-', ' ').title()


async def fetch_raw_markdown(url: str) -> Optional[str]:
    """
    Fetch raw markdown content directly (no Playwright needed).
    Used for pages that are already in markdown format (e.g. GitHub raw URLs).

    Returns:
        markdown content string, or None on failure
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    content = await resp.text()
                    logger.info(f"  Fetched raw markdown ({len(content)} chars) from {url}")
                    return content
                else:
                    logger.error(f"Raw fetch failed for {url}: HTTP {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"Raw fetch error for {url}: {e}")
        return None


async def fetch_page_with_playwright(page, url: str, timeout: int = PAGE_LOAD_TIMEOUT) -> tuple[Optional[str], Optional[str]]:
    """
    Fetch a page using Playwright and wait for JavaScript to render.

    Args:
        page: Playwright page object
        url: URL to fetch
        timeout: Page load timeout in milliseconds (default: PAGE_LOAD_TIMEOUT)

    Returns:
        tuple of (html_content, title) or (None, None) on failure
    """
    try:
        # Navigate to the page — try networkidle first, fall back to domcontentloaded
        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout)
        except PlaywrightTimeout:
            # networkidle can fail on heavy pages (e.g. GitHub) that have persistent
            # background connections. Retry with domcontentloaded which is more lenient.
            logger.warning(f"networkidle timeout for {url}, retrying with domcontentloaded")
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            # Give JS a moment to render content after DOM is loaded
            await asyncio.sleep(3)

        # Wait for the main content to appear
        # Look for common content selectors
        try:
            await page.wait_for_selector('article, main, .prose, .markdown-body, h1', timeout=CONTENT_WAIT_TIMEOUT)
        except PlaywrightTimeout:
            logger.warning(f"Content selector timeout for {url}, proceeding anyway")

        # Additional wait for any dynamic content
        await asyncio.sleep(1)

        # Get the page content
        html = await page.content()
        title = await page.title()

        return html, title

    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None, None


async def discover_pages(page, source: DocSource) -> list[str]:
    """Discover documentation pages by fetching the main page and finding links."""
    logger.info(f"Discovering {source.display_name} pages...")

    pages = set(source.known_pages)

    # Fetch the main page to find additional links
    html, _ = await fetch_page_with_playwright(page, source.base_url)
    if html:
        soup = BeautifulSoup(html, 'html.parser')

        # Find all links that point to subpages
        for link in soup.find_all('a', href=True):
            href = link['href']
            if source.link_pattern in href:
                # Extract the page slug
                match = re.search(source.slug_pattern, href)
                if match:
                    slug = match.group(1)
                    # Exclude empty or self-referencing slugs
                    if slug and slug not in ['agent-sdk', 'en', 'docs']:
                        pages.add(slug)

    # Filter out pages in the skip list
    pages -= set(source.skip_pages)

    return sorted(pages)


async def sync_page(
    page,
    page_slug: str,
    source: DocSource,
    existing_hashes: dict[str, str]
) -> tuple[str, bool, Optional[str]]:
    """
    Sync a single documentation page.

    Returns:
        tuple of (page_slug, changed, error_message)
    """
    url = urljoin(source.base_url, page_slug)
    filename = f"{page_slug}.md"
    filepath = source.docs_dir / filename

    # Check if this page has a raw URL override (e.g. GitHub raw markdown)
    if page_slug in source.raw_urls:
        raw_url = source.raw_urls[page_slug]
        logger.info(f"Fetching: {page_slug} via raw URL: {raw_url}")
        markdown = await fetch_raw_markdown(raw_url)
        if not markdown:
            return (page_slug, False, f"Failed to fetch raw content from {raw_url}")
        title = page_slug.replace('-', ' ').title()
    else:
        # Standard Playwright fetch
        timeout = source.page_timeouts.get(page_slug, PAGE_LOAD_TIMEOUT)
        if timeout != PAGE_LOAD_TIMEOUT:
            logger.info(f"Fetching: {url} (custom timeout: {timeout}ms)")
        else:
            logger.info(f"Fetching: {url}")

        html, page_title = await fetch_page_with_playwright(page, url, timeout=timeout)
        if not html:
            return (page_slug, False, "Failed to fetch page")

        # Parse HTML
        soup = BeautifulSoup(html, 'html.parser')

        # Check if page loaded properly (not just loading placeholders)
        text_content = soup.get_text()
        if 'Loading...' in text_content and len(text_content) < 500:
            logger.warning(f"Page {page_slug} may not have loaded properly")

        # Check for 404 pages
        title_text = soup.find('title')
        if title_text and 'Not Found' in title_text.get_text():
            return (page_slug, False, "Page not found (404)")

        # Extract title
        title = extract_title(soup, page_slug)

        # Convert to markdown
        markdown = clean_markdown(html)

    # Skip if content is too short (likely didn't load)
    if len(markdown) < 100:
        return (page_slug, False, "Content too short, page may not have loaded")

    # Create full content with metadata header
    full_content = create_metadata_header(url, title) + markdown

    # Check if content changed
    content_hash = compute_hash(markdown)
    if page_slug in existing_hashes and existing_hashes[page_slug] == content_hash:
        logger.info(f"  No changes: {filename}")
        return (page_slug, False, None)

    # Write the file
    filepath.write_text(full_content, encoding='utf-8')
    logger.info(f"  Updated: {filename} ({len(markdown)} chars)")

    return (page_slug, True, None)


def load_sync_metadata(source: DocSource) -> dict:
    """Load the last sync metadata from _last_sync.json."""
    metadata_path = source.docs_dir / "_last_sync.json"
    if metadata_path.exists():
        try:
            return json.loads(metadata_path.read_text())
        except json.JSONDecodeError:
            pass
    return {"hashes": {}, "syncs": []}


def save_sync_metadata(metadata: dict, source: DocSource):
    """Save sync metadata to _last_sync.json."""
    metadata_path = source.docs_dir / "_last_sync.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')


def generate_index(pages: list[tuple[str, str]], source: DocSource) -> str:
    """Generate the _index.md file with links to all pages."""
    timestamp = datetime.now(timezone.utc).isoformat()

    content = f"""---
source: {source.base_url}
title: {source.display_name} Documentation Index
last_fetched: {timestamp}
---

# {source.display_name} Documentation

This is a local mirror of the [{source.display_name} documentation]({source.base_url}).

## Available Pages

"""

    for slug, title in sorted(pages, key=lambda x: x[1]):
        content += f"- [{title}](./{slug}.md)\n"

    content += f"""
## About This Mirror

This documentation mirror is automatically synced from the official Anthropic documentation.
Claude Code agents can reference these files directly for up-to-date information.

**Last sync:** {timestamp}

To manually sync, run:
```bash
python scripts/sync_anthropic_docs.py
```
"""

    return content


async def sync_source(browser, source: DocSource) -> dict:
    """Sync all pages from a single documentation source."""
    logger.info(f"\n{'='*60}")
    logger.info(f"Syncing {source.display_name} documentation...")
    logger.info(f"{'='*60}")

    # Ensure docs directory exists
    source.docs_dir.mkdir(parents=True, exist_ok=True)

    # Load existing metadata
    metadata = load_sync_metadata(source)
    existing_hashes = metadata.get("hashes", {})

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) SecondBrain/1.0 Chrome/120.0 Safari/537.36"
    )
    page = await context.new_page()

    try:
        # Discover pages
        pages = await discover_pages(page, source)
        logger.info(f"Found {len(pages)} pages to sync")

        # Sync each page with rate limiting
        results = []
        page_titles = []

        for page_slug in pages:
            slug, changed, error = await sync_page(page, page_slug, source, existing_hashes)
            results.append((slug, changed, error))

            # Read back the title from the synced file
            filepath = source.docs_dir / f"{slug}.md"
            if filepath.exists():
                content = filepath.read_text()
                title_match = re.search(r'^title: (.+)$', content, re.MULTILINE)
                title = title_match.group(1) if title_match else slug.replace('-', ' ').title()
                page_titles.append((slug, title))

                # Update hash
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    markdown_content = parts[2].strip()
                    metadata["hashes"][slug] = compute_hash(markdown_content)

            # Rate limiting
            await asyncio.sleep(RATE_LIMIT_DELAY)

    finally:
        await context.close()

    # Generate index
    index_content = generate_index(page_titles, source)
    (source.docs_dir / "_index.md").write_text(index_content, encoding='utf-8')
    logger.info("Generated _index.md")

    # Update sync metadata
    sync_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pages_checked": len(pages),
        "pages_updated": sum(1 for _, changed, _ in results if changed),
        "errors": [slug for slug, _, error in results if error],
    }

    syncs = metadata.get("syncs", [])
    syncs.append(sync_record)
    metadata["syncs"] = syncs[-30:]

    save_sync_metadata(metadata, source)

    # Summary
    updated = sum(1 for _, changed, _ in results if changed)
    errors = sum(1 for _, _, error in results if error)
    logger.info(f"\n{source.display_name} sync complete: {updated} updated, "
                f"{len(pages) - updated - errors} unchanged, {errors} errors")

    if errors:
        logger.warning("Pages with errors:")
        for slug, _, error in results:
            if error:
                logger.warning(f"  - {slug}: {error}")

    return sync_record


async def main():
    """Main sync function."""
    # Parse CLI args for source selection
    selected_sources = ALL_SOURCES
    if len(sys.argv) > 1:
        source_filter = sys.argv[1].lower()
        source_map = {s.name: s for s in ALL_SOURCES}
        # Also accept short names
        source_map["sdk"] = AGENT_SDK_SOURCE
        source_map["cc"] = CLAUDE_CODE_SOURCE
        source_map["claude-code"] = CLAUDE_CODE_SOURCE
        if source_filter in source_map:
            selected_sources = [source_map[source_filter]]
        else:
            logger.error(f"Unknown source: {source_filter}")
            logger.error(f"Available: {', '.join(source_map.keys())}")
            sys.exit(1)

    logger.info(f"Starting documentation sync for: {', '.join(s.display_name for s in selected_sources)}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for source in selected_sources:
                await sync_source(browser, source)
        finally:
            await browser.close()

    logger.info("\nAll syncs complete!")


if __name__ == "__main__":
    asyncio.run(main())
