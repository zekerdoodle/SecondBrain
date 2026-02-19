/**
 * Escapes non-standard HTML tags in text content so they render as visible text
 * instead of being parsed (and swallowed) by the browser's HTML parser.
 *
 * The problem: MDEditor.Markdown uses rehype-raw, which parses any XML/HTML-like
 * tags in markdown content. Custom tags like <working-memory>, <semantic-memory>,
 * <recent-memory>, etc. get parsed as unknown HTML elements and silently consumed
 * by the browser — their content becomes invisible.
 *
 * The fix: Before passing content to the markdown renderer, we escape the angle
 * brackets of any tags that aren't standard HTML elements. Standard HTML tags
 * (like <div>, <span>, <br>, <table>, etc.) pass through untouched.
 */

// Complete set of standard HTML element tag names
const STANDARD_HTML_TAGS = new Set([
  // Main root
  'html',
  // Document metadata
  'base', 'head', 'link', 'meta', 'style', 'title',
  // Sectioning root
  'body',
  // Content sectioning
  'address', 'article', 'aside', 'footer', 'header', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'hgroup', 'main', 'nav', 'section', 'search',
  // Text content
  'blockquote', 'dd', 'div', 'dl', 'dt', 'figcaption', 'figure', 'hr', 'li', 'menu',
  'ol', 'p', 'pre', 'ul',
  // Inline text semantics
  'a', 'abbr', 'b', 'bdi', 'bdo', 'br', 'cite', 'code', 'data', 'dfn', 'em', 'i',
  'kbd', 'mark', 'q', 'rp', 'rt', 'ruby', 's', 'samp', 'small', 'span', 'strong',
  'sub', 'sup', 'time', 'u', 'var', 'wbr',
  // Image and multimedia
  'area', 'audio', 'img', 'map', 'track', 'video',
  // Embedded content
  'embed', 'iframe', 'object', 'param', 'picture', 'portal', 'source',
  // SVG and MathML
  'svg', 'math',
  // Scripting
  'canvas', 'noscript', 'script',
  // Demarcating edits
  'del', 'ins',
  // Table content
  'caption', 'col', 'colgroup', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr',
  // Forms
  'button', 'datalist', 'fieldset', 'form', 'input', 'label', 'legend', 'meter',
  'optgroup', 'option', 'output', 'progress', 'select', 'textarea',
  // Interactive elements
  'details', 'dialog', 'summary',
  // Web components
  'slot', 'template',
]);

/**
 * Matches HTML-like opening tags, closing tags, and self-closing tags.
 * Captures the tag name (group 1 for closing tags, group 2 for opening/self-closing).
 *
 * Examples it matches:
 *   <working-memory>    → group 2 = "working-memory"
 *   </working-memory>   → group 1 = "working-memory"
 *   <semantic-memory>   → group 2 = "semantic-memory"
 *   <br />              → group 2 = "br" (standard, won't be escaped)
 *   <div class="foo">   → group 2 = "div" (standard, won't be escaped)
 */
const TAG_REGEX = /<\/?([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*\/?>/g;

/**
 * Escape non-standard HTML tags in a string so they display as literal text.
 * Standard HTML tags are preserved for normal rendering.
 */
export function escapeNonHtmlTags(content: string): string {
  if (!content) return content;

  return content.replace(TAG_REGEX, (match, tagName: string) => {
    const normalizedTag = tagName.toLowerCase();

    // If it's a standard HTML tag, leave it alone
    if (STANDARD_HTML_TAGS.has(normalizedTag)) {
      return match;
    }

    // Escape the angle brackets so it renders as visible text
    return match.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  });
}
