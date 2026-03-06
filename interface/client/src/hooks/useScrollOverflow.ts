import { useRef, useState, useEffect, useCallback } from 'react';

/**
 * Hook for detecting horizontal scroll overflow and providing scroll controls.
 * Used by tab bars (chat tabs, file tabs) to show scroll arrows when tabs overflow.
 */
export function useScrollOverflow() {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const checkOverflow = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 1);
    setCanScrollRight(el.scrollLeft < el.scrollWidth - el.clientWidth - 1);
  }, []);

  // Check overflow on mount, resize, and DOM changes
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    checkOverflow();

    // Listen for scroll events
    el.addEventListener('scroll', checkOverflow, { passive: true });

    // Listen for resize
    const resizeObserver = new ResizeObserver(checkOverflow);
    resizeObserver.observe(el);

    // Listen for child changes (tabs added/removed)
    const mutationObserver = new MutationObserver(checkOverflow);
    mutationObserver.observe(el, { childList: true, subtree: true });

    return () => {
      el.removeEventListener('scroll', checkOverflow);
      resizeObserver.disconnect();
      mutationObserver.disconnect();
    };
  }, [checkOverflow]);

  const scrollLeft = useCallback(() => {
    scrollRef.current?.scrollBy({ left: -200, behavior: 'smooth' });
  }, []);

  const scrollRight = useCallback(() => {
    scrollRef.current?.scrollBy({ left: 200, behavior: 'smooth' });
  }, []);

  // Convert vertical wheel to horizontal scroll on the tab bar
  const onWheel = useCallback((e: React.WheelEvent) => {
    const el = scrollRef.current;
    if (!el) return;
    // Only hijack vertical scroll when there's horizontal overflow
    if (el.scrollWidth > el.clientWidth && e.deltaY !== 0) {
      e.preventDefault();
      el.scrollLeft += e.deltaY;
    }
  }, []);

  return { scrollRef, canScrollLeft, canScrollRight, scrollLeft, scrollRight, onWheel };
}
