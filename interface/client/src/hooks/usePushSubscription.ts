import { useState, useEffect, useCallback } from 'react';
import { API_URL } from '../config';

interface PushSubscriptionState {
  isSupported: boolean;
  isSubscribed: boolean;
  permission: NotificationPermission | 'unsupported';
  error: string | null;
}

/**
 * Hook to manage Web Push subscriptions.
 */
export const usePushSubscription = () => {
  const [state, setState] = useState<PushSubscriptionState>({
    isSupported: false,
    isSubscribed: false,
    permission: 'unsupported',
    error: null
  });

  // Check if push is supported
  useEffect(() => {
    const isSupported = 'serviceWorker' in navigator &&
      'PushManager' in window &&
      'Notification' in window;

    setState(prev => ({
      ...prev,
      isSupported,
      permission: isSupported ? Notification.permission : 'unsupported'
    }));

    if (isSupported) {
      // Check current subscription status
      checkSubscription();
    }
  }, []);

  // Check if already subscribed
  const checkSubscription = async () => {
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      setState(prev => ({
        ...prev,
        isSubscribed: !!subscription
      }));
    } catch (e) {
      console.error('Error checking subscription:', e);
    }
  };

  // Get VAPID public key from server
  const getVapidKey = async (): Promise<string | null> => {
    try {
      const res = await fetch(`${API_URL}/push/vapid-public-key`);
      if (!res.ok) return null;
      const data = await res.json();
      return data.publicKey;
    } catch (e) {
      console.error('Error fetching VAPID key:', e);
      return null;
    }
  };

  // Convert URL-safe base64 to Uint8Array
  const urlBase64ToUint8Array = (base64String: string): Uint8Array => {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding)
      .replace(/-/g, '+')
      .replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  };

  // Subscribe to push notifications
  const subscribe = useCallback(async (): Promise<boolean> => {
    if (!state.isSupported) {
      setState(prev => ({ ...prev, error: 'Push notifications not supported' }));
      return false;
    }

    try {
      // Request permission if not granted
      if (Notification.permission === 'default') {
        const permission = await Notification.requestPermission();
        setState(prev => ({ ...prev, permission }));
        if (permission !== 'granted') {
          setState(prev => ({ ...prev, error: 'Permission denied' }));
          return false;
        }
      } else if (Notification.permission === 'denied') {
        setState(prev => ({ ...prev, error: 'Notifications blocked in browser settings' }));
        return false;
      }

      // Get VAPID public key
      const vapidKey = await getVapidKey();
      if (!vapidKey) {
        setState(prev => ({ ...prev, error: 'Could not get VAPID key from server' }));
        return false;
      }

      // Get service worker registration
      const registration = await navigator.serviceWorker.ready;

      // Subscribe to push
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey)
      });

      // Send subscription to server
      const res = await fetch(`${API_URL}/push/subscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          endpoint: subscription.endpoint,
          keys: {
            p256dh: btoa(String.fromCharCode(...new Uint8Array(subscription.getKey('p256dh')!))),
            auth: btoa(String.fromCharCode(...new Uint8Array(subscription.getKey('auth')!)))
          }
        })
      });

      if (!res.ok) {
        throw new Error('Failed to save subscription on server');
      }

      setState(prev => ({
        ...prev,
        isSubscribed: true,
        error: null
      }));

      console.log('Push subscription successful');
      return true;
    } catch (e) {
      console.error('Push subscription error:', e);
      setState(prev => ({
        ...prev,
        error: e instanceof Error ? e.message : 'Subscription failed'
      }));
      return false;
    }
  }, [state.isSupported]);

  // Unsubscribe from push notifications
  const unsubscribe = useCallback(async (): Promise<boolean> => {
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();

      if (subscription) {
        // Notify server
        await fetch(`${API_URL}/push/unsubscribe`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            endpoint: subscription.endpoint,
            keys: {}
          })
        });

        // Unsubscribe locally
        await subscription.unsubscribe();
      }

      setState(prev => ({
        ...prev,
        isSubscribed: false,
        error: null
      }));

      return true;
    } catch (e) {
      console.error('Unsubscribe error:', e);
      setState(prev => ({
        ...prev,
        error: e instanceof Error ? e.message : 'Unsubscribe failed'
      }));
      return false;
    }
  }, []);

  return {
    ...state,
    subscribe,
    unsubscribe
  };
};
