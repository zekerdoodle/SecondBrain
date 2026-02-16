import { useEffect } from 'react';

const WRAP_BTN_CLASS = 'code-wrap-toggle';

function createWrapButton(): HTMLDivElement {
  const btn = document.createElement('div');
  btn.className = WRAP_BTN_CLASS;
  btn.title = 'Toggle line wrap';
  // Wrap icon (↩)
  btn.innerHTML = `<svg viewBox="0 0 16 16" fill="currentColor" height="12" width="12">
    <path d="M1.5 2.75a.75.75 0 0 1 1.5 0v8.5a.75.75 0 0 1-1.5 0v-8.5zm4 0a.75.75 0 0 1 1.5 0v2.5h5.25a2.75 2.75 0 0 1 0 5.5H9.56l1.22 1.22a.75.75 0 1 1-1.06 1.06l-2.5-2.5a.75.75 0 0 1 0-1.06l2.5-2.5a.75.75 0 0 1 1.06 1.06L9.56 9.25h2.69a1.25 1.25 0 0 0 0-2.5H7v-4z"/>
  </svg>`;
  return btn;
}

function injectWrapButtons(container: HTMLElement) {
  const pres = container.querySelectorAll<HTMLPreElement>('.wmde-markdown pre, .prose pre');
  pres.forEach((pre) => {
    // Skip if already has a wrap button
    if (pre.querySelector(`.${WRAP_BTN_CLASS}`)) return;
    // Skip pres without code children (not actual code blocks)
    if (!pre.querySelector('code')) return;
    const btn = createWrapButton();
    pre.appendChild(btn);
  });
}

function handleWrapClick(event: Event) {
  const target = event.target as HTMLElement;
  // Walk up to find the wrap button
  let el: HTMLElement | null = target;
  while (el) {
    if (el.classList?.contains(WRAP_BTN_CLASS)) {
      const pre = el.closest('pre');
      if (pre) {
        pre.classList.toggle('wrap-lines');
        el.classList.toggle('active');
      }
      return;
    }
    el = el.parentElement;
  }
}

/**
 * Injects a "wrap lines" toggle button into code blocks rendered by MDEditor.Markdown.
 * Sits next to the library's built-in copy button.
 */
export function useCodeBlockWrap(container: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const el = container.current;
    if (!el) return;

    // Initial injection
    injectWrapButtons(el);

    // Observe for new code blocks (e.g. streaming messages)
    const observer = new MutationObserver(() => {
      injectWrapButtons(el);
    });
    observer.observe(el, { childList: true, subtree: true });

    // Event delegation for wrap toggle clicks
    el.addEventListener('click', handleWrapClick, false);

    return () => {
      observer.disconnect();
      el.removeEventListener('click', handleWrapClick, false);
    };
  }, [container]);
}
