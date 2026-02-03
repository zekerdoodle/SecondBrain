/// <reference lib="webworker" />
import { precacheAndRoute, cleanupOutdatedCaches } from 'workbox-precaching';
import { clientsClaim } from 'workbox-core';

declare let self: ServiceWorkerGlobalScope;

// Use with Vite's injectManifest
precacheAndRoute(self.__WB_MANIFEST);

// Clean up old caches
cleanupOutdatedCaches();

// Note: We do NOT call skipWaiting() automatically here.
// This allows the update notification to show and gives users control
// over when the new version activates. The skipWaiting is triggered
// via postMessage from the UpdateNotification component.

// Once we do activate (via skipWaiting from user action), claim all clients
self.addEventListener('activate', () => {
  clientsClaim();
});

// Push notification handler
self.addEventListener('push', (event: PushEvent) => {
  if (!event.data) return;

  let data: {
    title?: string;
    body?: string;
    chat_id?: string;
    critical?: boolean;
    icon?: string;
    badge?: string;
  };

  try {
    data = event.data.json();
  } catch {
    data = { body: event.data.text() };
  }

  const title = data.title || 'Second Brain';
  const options = {
    body: data.body || 'New message from Claude',
    icon: data.icon || '/icons/icon-192.png',
    badge: data.badge || '/icons/icon-192.png',
    tag: data.critical ? 'critical-message' : 'claude-message',
    renotify: true,
    requireInteraction: data.critical || false,
    data: {
      chatId: data.chat_id,
      critical: data.critical
    }
  } as NotificationOptions;

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

// Notification click handler
self.addEventListener('notificationclick', (event: NotificationEvent) => {
  event.notification.close();

  const chatId = event.notification.data?.chatId;
  const urlToOpen = chatId ? `/?chat=${chatId}` : '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((windowClients) => {
        // Try to focus an existing window
        for (const client of windowClients) {
          if (client.url.includes(self.location.origin) && 'focus' in client) {
            // Navigate to the chat if specified
            if (chatId) {
              client.postMessage({
                type: 'NOTIFICATION_CLICK',
                chatId: chatId
              });
            }
            return client.focus();
          }
        }
        // Otherwise open a new window
        return self.clients.openWindow(urlToOpen);
      })
  );
});

// Handle messages from the main app
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
