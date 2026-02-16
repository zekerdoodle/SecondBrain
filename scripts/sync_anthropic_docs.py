#!/usr/bin/env python3
"""
Sync Anthropic Agent SDK Documentation

Fetches all pages from the Anthropic Agent SDK documentation and saves them
as local markdown files for offline reference by Claude Code agents.

Uses Playwright for JavaScript-rendered pages.

Source: https://platform.claude.com/docs/en/agent-sdk/
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import markdownify as md
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# Configuration
BASE_URL = "https://platform.claude.com/docs/en/agent-sdk/"
DOCS_DIR = Path(__file__).parent.parent / "docs" / "anthropic_agent_sdk"
RATE_LIMIT_DELAY = 2.0  # seconds between requests
PAGE_LOAD_TIMEOUT = 30000  # milliseconds
CONTENT_WAIT_TIMEOUT = 10000  # milliseconds to wait for content to load

# Known pages in the Agent SDK documentation
KNOWN_PAGES = [
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
    "streaming-input",
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
]

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
        for suffix in [' - Anthropic', ' | Anthropic', ' - Claude', ' | Claude']:
            if title.endswith(suffix):
                title = title[:-len(suffix)]
        return title

    return fallback.replace('-', ' ').title()


async def fetch_page_with_playwright(page, url: str) -> tuple[Optional[str], Optional[str]]:
    """
    Fetch a page using Playwright and wait for JavaScript to render.

    Returns:
        tuple of (html_content, title) or (None, None) on failure
    """
    try:
        # Navigate to the page
        await page.goto(url, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)

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


async def discover_pages(page) -> list[str]:
    """Discover documentation pages by fetching the main page and finding links."""
    logger.info("Discovering documentation pages...")

    pages = set(KNOWN_PAGES)

    # Fetch the main page to find additional links
    html, _ = await fetch_page_with_playwright(page, BASE_URL)
    if html:
        soup = BeautifulSoup(html, 'html.parser')

        # Find all links that point to agent-sdk subpages
        for link in soup.find_all('a', href=True):
            href = link['href']
            if '/agent-sdk/' in href:
                # Extract the page slug
                match = re.search(r'/agent-sdk/([a-z0-9-]+)/?$', href)
                if match and match.group(1) not in ['', 'agent-sdk']:
                    pages.add(match.group(1))

    return sorted(pages)


async def sync_page(
    page,
    page_slug: str,
    existing_hashes: dict[str, str]
) -> tuple[str, bool, Optional[str]]:
    """
    Sync a single documentation page.

    Returns:
        tuple of (page_slug, changed, error_message)
    """
    url = urljoin(BASE_URL, page_slug)
    filename = f"{page_slug}.md"
    filepath = DOCS_DIR / filename

    logger.info(f"Fetching: {url}")

    html, page_title = await fetch_page_with_playwright(page, url)
    if not html:
        return (page_slug, False, "Failed to fetch page")

    # Parse HTML
    soup = BeautifulSoup(html, 'html.parser')

    # Check if page loaded properly (not just loading placeholders)
    text_content = soup.get_text()
    if 'Loading...' in text_content and len(text_content) < 500:
        logger.warning(f"Page {page_slug} may not have loaded properly")

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


def load_sync_metadata() -> dict:
    """Load the last sync metadata from _last_sync.json."""
    metadata_path = DOCS_DIR / "_last_sync.json"
    if metadata_path.exists():
        try:
            return json.loads(metadata_path.read_text())
        except json.JSONDecodeError:
            pass
    return {"hashes": {}, "syncs": []}


def save_sync_metadata(metadata: dict):
    """Save sync metadata to _last_sync.json."""
    metadata_path = DOCS_DIR / "_last_sync.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')


def generate_index(pages: list[tuple[str, str]]) -> str:
    """Generate the _index.md file with links to all pages."""
    timestamp = datetime.now(timezone.utc).isoformat()

    content = f"""---
source: {BASE_URL}
title: Anthropic Agent SDK Documentation Index
last_fetched: {timestamp}
---

# Anthropic Agent SDK Documentation

This is a local mirror of the [Anthropic Agent SDK documentation]({BASE_URL}).

## Available Pages

"""

    for slug, title in sorted(pages, key=lambda x: x[1]):
        content += f"- [{title}](./{slug}.md)\n"

    content += f"""
## About This Mirror

This documentation mirror is automatically synced from the official Anthropic documentation.
Claude Code agents can reference these files directly for up-to-date SDK information.

**Last sync:** {timestamp}

To manually sync, run:
```bash
python scripts/sync_anthropic_docs.py
```
"""

    return content


async def main():
    """Main sync function."""
    logger.info("Starting Anthropic Agent SDK documentation sync...")

    # Ensure docs directory exists
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing metadata
    metadata = load_sync_metadata()
    existing_hashes = metadata.get("hashes", {})

    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) SecondBrain/1.0 Chrome/120.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            # Discover pages
            pages = await discover_pages(page)
            logger.info(f"Found {len(pages)} pages to sync")

            # Sync each page with rate limiting
            results = []
            page_titles = []

            for page_slug in pages:
                slug, changed, error = await sync_page(page, page_slug, existing_hashes)
                results.append((slug, changed, error))

                # Read back the title from the synced file
                filepath = DOCS_DIR / f"{slug}.md"
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
            await browser.close()

    # Generate index
    index_content = generate_index(page_titles)
    (DOCS_DIR / "_index.md").write_text(index_content, encoding='utf-8')
    logger.info("Generated _index.md")

    # Update sync metadata
    sync_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pages_checked": len(pages),
        "pages_updated": sum(1 for _, changed, _ in results if changed),
        "errors": [slug for slug, _, error in results if error],
    }

    # Keep only last 30 sync records
    syncs = metadata.get("syncs", [])
    syncs.append(sync_record)
    metadata["syncs"] = syncs[-30:]

    save_sync_metadata(metadata)

    # Summary
    updated = sum(1 for _, changed, _ in results if changed)
    errors = sum(1 for _, _, error in results if error)
    logger.info(f"\nSync complete: {updated} updated, {len(pages) - updated - errors} unchanged, {errors} errors")

    if errors:
        logger.warning("Pages with errors:")
        for slug, _, error in results:
            if error:
                logger.warning(f"  - {slug}: {error}")


if __name__ == "__main__":
    asyncio.run(main())
