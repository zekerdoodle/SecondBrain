import { useCallback, useState } from 'react';
import { useRegisterSW } from 'virtual:pwa-register/react';
import { RefreshCw, X } from 'lucide-react';
import { clsx } from 'clsx';

/**
 * UpdateNotification component
 *
 * Uses VitePWA's useRegisterSW hook to reliably detect when a new service worker
 * version is waiting to activate and shows a non-intrusive toast notification.
 *
 * When the user clicks the update button, it triggers the service worker update
 * and refreshes the page to load the new version.
 */
export function UpdateNotification() {
  const [dismissed, setDismissed] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);

  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisteredSW(swUrl: string, registration: ServiceWorkerRegistration | undefined) {
      console.log('[SW] Service worker registered:', swUrl);

      // Check for updates immediately on page load
      if (registration) {
        // If there's already a waiting worker, needRefresh should be true
        if (registration.waiting) {
          console.log('[SW] Found waiting service worker on registration');
          setNeedRefresh(true);
        }

        // Also set up periodic update checks (every 60 seconds in production)
        const intervalMs = 60 * 1000;
        setInterval(() => {
          console.log('[SW] Checking for updates...');
          registration.update().catch((err: unknown) => {
            console.error('[SW] Update check failed:', err);
          });
        }, intervalMs);
      }
    },
    onRegisterError(error: unknown) {
      console.error('[SW] Registration error:', error);
    },
    onNeedRefresh() {
      console.log('[SW] New content available, showing update notification');
      // Reset dismissed state when a new update is detected
      setDismissed(false);
    },
    onOfflineReady() {
      console.log('[SW] App ready for offline use');
    },
  });

  const handleUpdate = useCallback(async () => {
    setIsUpdating(true);

    try {
      // Clear all caches before updating
      if ('caches' in window) {
        const cacheNames = await caches.keys();
        await Promise.all(
          cacheNames.map(name => caches.delete(name))
        );
        console.log('[SW] All caches cleared');
      }

      // Trigger the service worker update - this calls skipWaiting and reloads
      await updateServiceWorker(true);
    } catch (e) {
      console.error('[SW] Update failed:', e);
      // Force reload anyway
      window.location.reload();
    }
  }, [updateServiceWorker]);

  const handleDismiss = useCallback(() => {
    setDismissed(true);
  }, []);

  // Show notification if there's a new version and user hasn't dismissed it
  if (!needRefresh || dismissed) return null;

  return (
    <div className="fixed bottom-20 md:bottom-4 left-4 right-4 md:left-auto md:right-4 z-50 md:max-w-sm animate-slide-up">
      <div
        className="rounded-lg border shadow-lg p-3 flex items-center gap-3"
        style={{
          backgroundColor: 'var(--bg-secondary)',
          borderColor: 'var(--border-color)',
        }}
      >
        <div className="flex-shrink-0">
          <RefreshCw
            size={18}
            className={clsx(
              isUpdating && "animate-spin"
            )}
            style={{ color: 'var(--accent-primary)' }}
          />
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-medium text-sm" style={{ color: 'var(--text-primary)' }}>
            Update Available
          </p>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
            A new version is ready to install
          </p>
        </div>
        <button
          onClick={handleUpdate}
          disabled={isUpdating}
          className={clsx(
            "px-3 py-1.5 rounded-md text-xs font-medium transition-colors flex-shrink-0",
            "disabled:opacity-50 disabled:cursor-not-allowed"
          )}
          style={{
            backgroundColor: 'var(--accent-primary)',
            color: 'white',
          }}
          onMouseEnter={(e) => e.currentTarget.style.backgroundColor = 'var(--accent-hover)'}
          onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'var(--accent-primary)'}
        >
          {isUpdating ? 'Updating...' : 'Refresh Now'}
        </button>
        {!isUpdating && (
          <button
            onClick={handleDismiss}
            className="flex-shrink-0 transition-colors p-1"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={(e) => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={(e) => e.currentTarget.style.color = 'var(--text-muted)'}
            aria-label="Dismiss"
          >
            <X size={16} />
          </button>
        )}
      </div>
    </div>
  );
}
