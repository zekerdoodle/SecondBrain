import { useEffect, useRef, useCallback } from 'react';

const DEFAULT_TITLE = 'Second Brain';
const FLASH_INTERVAL = 1000; // 1 second

interface UseTabFlashOptions {
  enabled?: boolean;
  message?: string;
  unreadCount?: number;
}

/**
 * Hook to flash the browser tab title when there are unread messages.
 * Stops flashing when the window gains focus.
 */
export const useTabFlash = (options: UseTabFlashOptions = {}) => {
  const { enabled = false, message = 'New message', unreadCount = 1 } = options;

  const flashIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isShowingAlertRef = useRef(false);
  const originalTitleRef = useRef(DEFAULT_TITLE);

  // Stop flashing and restore original title
  const stopFlashing = useCallback(() => {
    if (flashIntervalRef.current) {
      clearInterval(flashIntervalRef.current);
      flashIntervalRef.current = null;
    }
    isShowingAlertRef.current = false;
    document.title = originalTitleRef.current;
  }, []);

  // Start flashing the tab title
  const startFlashing = useCallback(() => {
    // Don't start if already flashing
    if (flashIntervalRef.current) return;

    // Save original title
    originalTitleRef.current = document.title || DEFAULT_TITLE;

    // Start alternating
    flashIntervalRef.current = setInterval(() => {
      if (isShowingAlertRef.current) {
        document.title = originalTitleRef.current;
        isShowingAlertRef.current = false;
      } else {
        const alertTitle = unreadCount > 1
          ? `(${unreadCount}) ${message}`
          : `(1) ${message}`;
        document.title = alertTitle;
        isShowingAlertRef.current = true;
      }
    }, FLASH_INTERVAL);
  }, [message, unreadCount]);

  // Handle visibility/focus changes - stop flashing when user returns
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        stopFlashing();
      }
    };

    const handleFocus = () => {
      stopFlashing();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleFocus);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleFocus);
      stopFlashing();
    };
  }, [stopFlashing]);

  // Start/stop flashing based on enabled state
  useEffect(() => {
    if (enabled && !document.hasFocus()) {
      startFlashing();
    } else {
      stopFlashing();
    }
  }, [enabled, startFlashing, stopFlashing]);

  return {
    startFlashing,
    stopFlashing,
    isFlashing: !!flashIntervalRef.current
  };
};
