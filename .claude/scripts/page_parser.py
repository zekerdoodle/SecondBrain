"""
Page Parser Tool for Second Brain

Fetches and parses web pages into clean Markdown with optional saving.
Ported from Theo's deep_research_tools.py, simplified for standalone use.
"""

import hashlib
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, unquote
import base64

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Optional imports with graceful fallbacks
try:
    from readability import Document
except ImportError:
    Document = None

try:
    from markdownify import markdownify as html_to_md
except ImportError:
    html_to_md = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import pypdf
except ImportError:
    pypdf = None


# Defaults
DEFAULT_MAX_CHARS = 50000  # ~12k tokens
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) SecondBrain/1.0 Chrome/120.0 Safari/537.36"
SAVE_DIR_DEFAULT = Path(__file__).parent.parent.parent / "docs" / "webresults"


def _slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "page"


def _canonicalize_url(url: str) -> str:
    """Clean URL by unwrapping redirectors and stripping tracking params."""

    def _unwrap(u: str) -> str:
        try:
            p = urlparse(u)
            host = (p.netloc or "").lower()
            qs = dict(parse_qsl(p.query))

            # DuckDuckGo redirect wrapper
            if host.endswith("duckduckgo.com") and p.path.startswith("/l/"):
                tgt = qs.get("uddg")
                if tgt:
                    t = unquote(tgt)
                    if t.startswith("http://") or t.startswith("https://"):
                        return t

            # Bing click wrapper
            if host.endswith("bing.com") and p.path.startswith("/ck/"):
                tgt = qs.get("u") or qs.get("r")
                if tgt:
                    try:
                        t = unquote(tgt)
                        if t.startswith("http://") or t.startswith("https://"):
                            return t
                    except Exception:
                        pass
                    try:
                        t = base64.urlsafe_b64decode(tgt + "==").decode("utf-8", errors="ignore")
                        if t.startswith("http://") or t.startswith("https://"):
                            return t
                    except Exception:
                        pass
        except Exception:
            pass
        return u

    # Attempt unwrap twice for nested wrappers
    current = str(url)
    for _ in range(2):
        nxt = _unwrap(current)
        if nxt == current:
            break
        current = nxt

    # Strip tracking parameters
    parsed = urlparse(current)
    query_pairs = [
        (k, v) for k, v in parse_qsl(parsed.query)
        if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}
    ]
    new_query = urlencode(query_pairs)
    canonical = parsed._replace(query=new_query, fragment="")
    return urlunparse(canonical)


def _extract_domain(url: str) -> str:
    """Extract domain from URL."""
    return urlparse(url).netloc


def _hash_content(data: Union[str, bytes]) -> str:
    """SHA256 hash of content."""
    if isinstance(data, str):
        data = data.encode("utf-8", errors="ignore")
    return hashlib.sha256(data).hexdigest()


def _build_session(user_agent: str) -> requests.Session:
    """Build requests session with retries."""
    session = requests.Session()
    retries = Retry(total=2, backoff_factor=0.4, status_forcelist=(429, 500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": user_agent, "Accept": "*/*"})
    return session


def _is_pdf_response(resp: requests.Response) -> bool:
    """Check if response is a PDF."""
    ctype = (resp.headers.get("Content-Type") or "").lower()
    return "application/pdf" in ctype or resp.url.lower().endswith(".pdf")


def _compute_filename(url: str, title: str) -> str:
    """Generate filename from URL and title."""
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    domain = _extract_domain(url)
    slug = _slugify(title or domain)
    return f"{ts}_{_slugify(domain)}_{slug}.md"


def _parse_html_to_markdown(html: str) -> Tuple[str, str]:
    """Convert HTML to Markdown. Returns (title, markdown)."""
    title = ""

    # Try Readability + markdownify first
    if Document is not None:
        try:
            doc = Document(html)
            title = doc.short_title() or ""
            main_html = doc.summary(html_partial=True)
            if html_to_md is not None:
                md = html_to_md(main_html or html, heading_style="ATX", code_language="")
                return title, md
        except Exception:
            pass

    # Fallback: BeautifulSoup visible text
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            texts: List[str] = []

            # Keep pre/code blocks
            for pre in soup.find_all(["pre", "code"]):
                content = pre.get_text("\n", strip=False)
                if content:
                    texts.append("```\n" + content + "\n```")

            # Collect headings and paragraphs
            for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
                t = tag.get_text(" ", strip=True)
                if t:
                    if tag.name.startswith("h"):
                        level = int(tag.name[1])
                        texts.append("#" * max(1, min(6, level)) + f" {t}")
                    elif tag.name == "li":
                        texts.append(f"- {t}")
                    else:
                        texts.append(t)

            md = "\n\n".join(texts) if texts else soup.get_text("\n", strip=True)

            # Title fallback
            if not title:
                if soup.title and soup.title.string:
                    title = soup.title.string.strip()
            return title, md
        except Exception:
            pass

    # Last resort: raw text
    return title, html


def _parse_pdf_to_markdown(content: bytes) -> Tuple[str, str]:
    """Convert PDF to Markdown. Returns (title, markdown)."""
    if pypdf is None:
        return "", "PDF parsing unavailable (pypdf not installed)"
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        parts: List[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n\n".join(parts)
        return "", text
    except Exception as e:
        return "", f"(Failed to parse PDF: {e})"


def _save_webresult(save_dir: Path, meta: Dict[str, Any], content_md: str) -> Path:
    """Save parsed content to file with YAML frontmatter."""
    save_dir.mkdir(parents=True, exist_ok=True)
    filename = _compute_filename(meta.get("url", ""), meta.get("title", ""))
    file_path = save_dir / filename

    # YAML frontmatter
    frontmatter = {
        "url": meta.get("url"),
        "title": meta.get("title"),
        "domain": meta.get("domain"),
        "fetched_at": meta.get("fetched_at"),
        "status_code": meta.get("status_code"),
        "content_type": meta.get("content_type"),
        "char_count": meta.get("char_count"),
        "truncated": meta.get("truncated", False),
        "content_hash": _hash_content(content_md),
    }
    fm_lines = ["---"] + [f"{k}: {json.dumps(v)}" for k, v in frontmatter.items()] + ["---", ""]
    file_path.write_text("\n".join(fm_lines) + content_md, encoding="utf-8")

    # Append to index
    index_path = save_dir / "webresults_index.jsonl"
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(frontmatter) + "\n")

    return file_path


def page_parser_single(
    url: str,
    save: bool = False,
    max_chars: Optional[int] = None,
    save_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Fetch and parse a single URL to Markdown.

    Args:
        url: The URL to fetch
        save: If True, save to docs/webresults
        max_chars: Maximum characters to return (truncates if exceeded)
        save_dir: Custom save directory

    Returns:
        Dict with keys: success, title, domain, content, saved_path (if saved), error (if failed)
    """
    if not url or not url.strip():
        return {"success": False, "error": "URL cannot be empty"}

    save_dir = save_dir or SAVE_DIR_DEFAULT
    max_chars = max_chars or DEFAULT_MAX_CHARS

    canon_url = _canonicalize_url(url.strip())
    domain = _extract_domain(canon_url)

    # Fetch
    session = _build_session(DEFAULT_USER_AGENT)
    try:
        resp = session.get(canon_url, timeout=15, allow_redirects=True)
    except Exception as e:
        return {"success": False, "error": f"Failed to fetch: {e}"}

    status = resp.status_code
    content_type = resp.headers.get("Content-Type", "")
    fetched_at = datetime.utcnow().isoformat() + "Z"

    if status >= 400:
        return {"success": False, "error": f"HTTP {status}"}

    # Parse
    title = ""
    body_md = ""
    truncated = False

    if _is_pdf_response(resp):
        title, body_md = _parse_pdf_to_markdown(resp.content)
    else:
        title, body_md = _parse_html_to_markdown(resp.text)

    title = title or domain or ""

    # Truncation
    full_chars = len(body_md)
    output_md = body_md
    if full_chars > max_chars:
        output_md = body_md[:max_chars] + "\n\n[... content truncated ...]"
        truncated = True

    meta = {
        "url": canon_url,
        "title": title,
        "domain": domain,
        "fetched_at": fetched_at,
        "status_code": status,
        "content_type": content_type,
        "char_count": min(full_chars, max_chars),
        "truncated": truncated,
    }

    # Save if requested or truncated
    saved_path: Optional[str] = None
    if save or truncated:
        try:
            path = _save_webresult(Path(save_dir), meta, body_md)  # Save full content
            saved_path = str(path)
        except Exception as e:
            # Don't fail the whole operation if save fails
            pass

    # Compose output markdown
    header_lines = [
        f"# {title}",
        "",
        f"Source: {canon_url}",
        f"Domain: {domain}",
        f"Fetched: {fetched_at}",
        f"Status: {status}",
    ]
    if saved_path:
        header_lines.append(f"Saved: {saved_path}")
    if truncated:
        header_lines.append("(truncated to character limit)")
    header_lines.extend(["", "---", ""])

    formatted_content = "\n".join(header_lines) + output_md

    return {
        "success": True,
        "title": title,
        "domain": domain,
        "url": canon_url,
        "content": formatted_content,
        "saved_path": saved_path,
        "truncated": truncated,
        "char_count": min(full_chars, max_chars),
    }


def page_parser(
    url: Union[str, List[str]],
    save: bool = False,
    max_chars: Optional[int] = None,
) -> str:
    """
    Fetch and parse one or multiple web pages into clean Markdown.

    Args:
        url: Single URL string or list of URLs
        save: If True, save to docs/webresults (always saves if content is truncated)
        max_chars: Maximum characters per page

    Returns:
        Markdown content (concatenated if multiple URLs)
    """
    # Single string
    if isinstance(url, str):
        result = page_parser_single(url, save=save, max_chars=max_chars)
        if result["success"]:
            return result["content"]
        return f"Error parsing {url}: {result.get('error', 'Unknown error')}"

    # List of URLs
    if isinstance(url, list):
        outputs: List[str] = []
        for u in url:
            result = page_parser_single(str(u), save=save, max_chars=max_chars)
            if result["success"]:
                outputs.append(result["content"])
            else:
                outputs.append(f"Error parsing {u}: {result.get('error', 'Unknown error')}")
        return "\n\n---\n\n".join(outputs)

    return "Error: URL must be a string or a list of strings"


# Convenience function for MCP tool integration
def parse_urls(urls: List[str], save: bool = False, max_chars: Optional[int] = None) -> List[Dict[str, Any]]:
    """Parse multiple URLs and return structured results."""
    results = []
    for url in urls:
        result = page_parser_single(url, save=save, max_chars=max_chars)
        results.append(result)
    return results


# CLI for testing
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = page_parser(sys.argv[1], save=("--save" in sys.argv))
        print(result)
    else:
        print("Usage: python page_parser.py <url> [--save]")
