"""
Page Parser Tool for Second Brain

Fetches and parses web pages into clean Markdown with optional saving.
Ported from Theo's deep_research_tools.py, simplified for standalone use.

Fixes applied:
- Code block preservation: Pre-processes HTML before markdownify to normalize
  <pre>/<code> structures, detect languages, and emit proper fenced blocks.
- Document-order interleaving: BS4 fallback uses single-pass extraction
  to preserve original prose/code ordering.
- Truncation feedback: Prominent banner with exact character counts and
  saved-file path when content is truncated.
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
    from bs4 import BeautifulSoup, Tag
except ImportError:
    BeautifulSoup = None
    Tag = None

try:
    import pypdf
except ImportError:
    pypdf = None


# Defaults
DEFAULT_MAX_CHARS = 50000  # ~12k tokens
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) SecondBrain/1.0 Chrome/120.0 Safari/537.36"
SAVE_DIR_DEFAULT = Path(__file__).parent.parent.parent / "docs" / "webresults"

# Language detection heuristics for code blocks
_LANG_HINTS = {
    "rust":       [r"\bfn\s+\w+", r"\blet\s+mut\b", r"\bimpl\b", r"->.*\{", r"use\s+\w+::\w+"],
    "python":     [r"\bdef\s+\w+\(", r"\bimport\s+\w+", r"\bclass\s+\w+.*:", r"if\s+__name__"],
    "javascript": [r"\bconst\s+\w+\s*=", r"\bfunction\s+\w+", r"=>\s*\{", r"\bconsole\.log\b"],
    "typescript": [r"\binterface\s+\w+", r":\s*(string|number|boolean)\b", r"\btype\s+\w+\s*="],
    "go":         [r"\bfunc\s+\w+", r"\bpackage\s+\w+", r":=\s*", r"\bfmt\."],
    "java":       [r"\bpublic\s+class\b", r"\bprivate\s+\w+", r"\bSystem\.out\."],
    "c":          [r"#include\s*<", r"\bint\s+main\(", r"\bprintf\(", r"\bmalloc\("],
    "cpp":        [r"#include\s*<", r"\bstd::", r"\bnamespace\s+\w+", r"\btemplate\s*<"],
    "html":       [r"<html", r"<div\b", r"<span\b", r"<!DOCTYPE"],
    "css":        [r"\{[^}]*:\s*[^}]+\}", r"@media\b", r"\.[\w-]+\s*\{"],
    "sql":        [r"\bSELECT\b", r"\bFROM\b", r"\bWHERE\b", r"\bINSERT\b"],
    "bash":       [r"#!/bin/(ba)?sh", r"\becho\s+", r"\$\{?\w+\}?", r"\bsudo\s+"],
    "json":       [r'^\s*\{', r'"[\w-]+"\s*:\s*'],
    "yaml":       [r"^\s*[\w-]+:\s+", r"^\s*-\s+\w+"],
    "toml":       [r"^\[[\w.-]+\]", r"\w+\s*=\s*"],
}


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


def _detect_language(code_text: str) -> str:
    """Detect programming language from code content using heuristics."""
    if not code_text or len(code_text.strip()) < 5:
        return ""

    scores: Dict[str, int] = {}
    for lang, patterns in _LANG_HINTS.items():
        score = 0
        for pat in patterns:
            if re.search(pat, code_text, re.MULTILINE | re.IGNORECASE if lang in ("sql",) else re.MULTILINE):
                score += 1
        if score > 0:
            scores[lang] = score

    if not scores:
        return ""

    # Return language with highest score, minimum 2 matches for confidence
    best_lang, best_score = max(scores.items(), key=lambda x: x[1])
    return best_lang if best_score >= 2 else ""


def _extract_code_language(pre_el) -> str:
    """Extract programming language from a <pre> element or its <code> child.

    markdownify's code_language_callback passes the <pre> element, but the
    language class is typically on the inner <code> child. This callback
    checks both, then falls back to content-based heuristic detection.
    """
    # Check <pre> itself
    for cls in (pre_el.get("class", []) if hasattr(pre_el, "get") else []):
        m = re.match(r"(?:language|lang|highlight-source)-(\w+)", str(cls))
        if m:
            return m.group(1).lower()
        if str(cls).lower() in _LANG_HINTS:
            return str(cls).lower()

    # Check <code> child
    code = pre_el.find("code") if hasattr(pre_el, "find") else None
    if code:
        for cls in (code.get("class", []) if hasattr(code, "get") else []):
            m = re.match(r"(?:language|lang|highlight-source)-(\w+)", str(cls))
            if m:
                return m.group(1).lower()
            if str(cls).lower() in _LANG_HINTS:
                return str(cls).lower()

    # Heuristic fallback from content
    text = pre_el.get_text() if hasattr(pre_el, "get_text") else ""
    return _detect_language(text)


def _normalize_code_blocks(html: str) -> str:
    """
    Pre-process HTML to normalize <pre>/<code> structures before markdownify.

    Fixes:
    1. Merges multiple sibling <code> elements inside a <pre> into one
    2. Strips inner <span> decoration (syntax highlighting wrappers)
    3. Detects language from class attributes or content heuristics
    4. Replaces the entire <pre> with a clean <pre><code class="language-X">...</code></pre>

    This runs AFTER Readability but BEFORE markdownify, ensuring markdownify
    sees clean, simple <pre><code> structures it can convert properly.
    """
    if BeautifulSoup is None:
        return html

    soup = BeautifulSoup(html, "html.parser")

    for pre in soup.find_all("pre"):
        # Collect all code text from this <pre>
        code_elements = pre.find_all("code")

        # Detect language from class names on <pre> or <code>
        lang = ""
        for el in [pre] + code_elements:
            classes = el.get("class", []) if isinstance(el, Tag) else []
            for cls in classes:
                cls_str = str(cls)
                # Common patterns: language-rust, lang-python, highlight-source-rust
                m = re.match(r"(?:language|lang|highlight-source)-(\w+)", cls_str)
                if m:
                    lang = m.group(1).lower()
                    break
                # Just the language name as a class
                if cls_str.lower() in _LANG_HINTS:
                    lang = cls_str.lower()
                    break
            if lang:
                break

        if code_elements:
            # Merge all <code> children into one text block
            merged_text = ""
            for code_el in code_elements:
                merged_text += code_el.get_text()
        else:
            # <pre> with no <code> children — just get the text
            merged_text = pre.get_text()

        # Detect language from content if not found in classes
        if not lang:
            lang = _detect_language(merged_text)

        # Build clean replacement
        new_code = soup.new_tag("code")
        if lang:
            new_code["class"] = [f"language-{lang}"]
        new_code.string = merged_text

        # Replace <pre> contents with the single clean <code>
        pre.clear()
        pre.append(new_code)

    return str(soup)


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
                # Pre-process: normalize code blocks before markdownify
                cleaned_html = _normalize_code_blocks(main_html or html)
                md = html_to_md(
                    cleaned_html,
                    heading_style="ATX",
                    code_language="",
                    code_language_callback=_extract_code_language,
                )
                return title, md
        except Exception:
            pass

    # Fallback: BeautifulSoup single-pass document-order extraction
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            texts: List[str] = []
            seen_ids: set = set()  # Track processed elements to avoid duplicates

            # Single-pass: iterate all relevant tags in document order
            for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "code"]):
                tag_id = id(tag)
                if tag_id in seen_ids:
                    continue
                seen_ids.add(tag_id)

                if tag.name == "pre":
                    # Mark all children as seen to avoid double-processing
                    for child in tag.descendants:
                        if isinstance(child, Tag):
                            seen_ids.add(id(child))

                    # IMPORTANT: Use get_text() with NO separator. Using "\n" as
                    # separator inserts newlines between every <span> element,
                    # which mangles syntax-highlighted code (e.g. GitHub wraps
                    # each token in a <span>). Without a separator, BS4 correctly
                    # concatenates all text nodes preserving original whitespace.
                    content = tag.get_text()
                    if content.strip():
                        lang = ""
                        # Try to detect language from classes
                        for el in [tag] + tag.find_all("code"):
                            for cls in (el.get("class", []) if isinstance(el, Tag) else []):
                                m = re.match(r"(?:language|lang|highlight-source)-(\w+)", str(cls))
                                if m:
                                    lang = m.group(1).lower()
                                    break
                            if lang:
                                break
                        if not lang:
                            lang = _detect_language(content)
                        texts.append(f"```{lang}\n{content.strip()}\n```")

                elif tag.name == "code":
                    # Only handle inline <code> (not inside <pre>, which was handled above)
                    parent = tag.parent
                    if parent and parent.name == "pre":
                        continue  # Already handled by <pre> processing
                    content = tag.get_text()
                    if content.strip():
                        texts.append(f"`{content.strip()}`")

                elif tag.name.startswith("h"):
                    t = tag.get_text(" ", strip=True)
                    if t:
                        level = int(tag.name[1])
                        texts.append("#" * max(1, min(6, level)) + f" {t}")

                elif tag.name == "li":
                    t = tag.get_text(" ", strip=True)
                    if t:
                        texts.append(f"- {t}")

                else:  # p
                    t = tag.get_text(" ", strip=True)
                    if t:
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

    # Truncation with prominent feedback
    full_chars = len(body_md)
    output_md = body_md

    # Save if requested or content will be truncated (save full version)
    saved_path: Optional[str] = None

    if full_chars > max_chars:
        truncated = True

    meta = {
        "url": canon_url,
        "title": title,
        "domain": domain,
        "fetched_at": fetched_at,
        "status_code": status,
        "content_type": content_type,
        "char_count": full_chars,
        "truncated": truncated,
    }

    if save or truncated:
        try:
            path = _save_webresult(Path(save_dir), meta, body_md)  # Save full content
            saved_path = str(path)
        except Exception:
            # Don't fail the whole operation if save fails
            pass

    if truncated:
        output_md = body_md[:max_chars]
        # Prominent truncation banner
        trunc_banner = (
            f"\n\n⚠️ **[TRUNCATED: showing {max_chars:,} of {full_chars:,} chars "
            f"({full_chars - max_chars:,} chars omitted)]**"
        )
        if saved_path:
            trunc_banner += f"\n📄 **Full content saved to:** `{saved_path}`"
        output_md += trunc_banner

    # Compose output markdown
    header_lines = [
        f"# {title}",
        "",
        f"Source: {canon_url}",
        f"Domain: {domain}",
        f"Fetched: {fetched_at}",
        f"Status: {status}",
        f"Content: {full_chars:,} chars",
    ]
    if saved_path:
        header_lines.append(f"Saved: {saved_path}")
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
        "char_count": full_chars,
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
