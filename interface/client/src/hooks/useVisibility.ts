import { useState, useEffect, useCallback, useRef } from 'react';

export interface VisibilityState {
  isPageVisible: boolean;
  isWindowFocused: boolean;
  isUserActive: boolean;  // Combined: visible AND focused
}

/**
 * Hook to track page visibility and window focus.
 * Uses Page Visibility API and focus events.
 *
 * @param onVisibilityChange - Callback when visibility state changes
 * @returns Current visibility state
 */
export const useVisibility = (
  onVisibilityChange?: (state: VisibilityState) => void
): VisibilityState => {
  const [state, setState] = useState<VisibilityState>(() => ({
    isPageVisible: typeof document !== 'undefined' ? document.visibilityState === 'visible' : true,
    isWindowFocused: typeof document !== 'undefined' ? document.hasFocus() : true,
    isUserActive: typeof document !== 'undefined'
      ? document.visibilityState === 'visible' && document.hasFocus()
      : true
  }));

  // Track callback ref to avoid re-subscriptions
  const callbackRef = useRef(onVisibilityChange);
  callbackRef.current = onVisibilityChange;

  // Track previous state to detect changes
  const prevStateRef = useRef<VisibilityState>(state);

  const updateState = useCallback((updates: Partial<VisibilityState>) => {
    setState(prev => {
      const isPageVisible = updates.isPageVisible ?? prev.isPageVisible;
      const isWindowFocused = updates.isWindowFocused ?? prev.isWindowFocused;
      const isUserActive = isPageVisible && isWindowFocused;

      const newState = { isPageVisible, isWindowFocused, isUserActive };

      // Only notify if isUserActive changed (the main signal we care about)
      if (newState.isUserActive !== prevStateRef.current.isUserActive) {
        prevStateRef.current = newState;
        callbackRef.current?.(newState);
      }

      return newState;
    });
  }, []);

  useEffect(() => {
    // Handle page visibility changes (tab switching, minimizing)
    const handleVisibilityChange = () => {
      updateState({ isPageVisible: document.visibilityState === 'visible' });
    };

    // Handle window focus changes
    const handleFocus = () => {
      updateState({ isWindowFocused: true });
    };

    const handleBlur = () => {
      updateState({ isWindowFocused: false });
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleFocus);
    window.addEventListener('blur', handleBlur);

    // Initial state notification
    const initialState = {
      isPageVisible: document.visibilityState === 'visible',
      isWindowFocused: document.hasFocus(),
      isUserActive: document.visibilityState === 'visible' && document.hasFocus()
    };
    prevStateRef.current = initialState;
    callbackRef.current?.(initialState);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleFocus);
      window.removeEventListener('blur', handleBlur);
    };
  }, [updateState]);

  return state;
};
