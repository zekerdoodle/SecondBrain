import { createContext, useContext, useState, useCallback, useRef, type ReactNode } from 'react';
import { clsx } from 'clsx';
import { X, ExternalLink, Bell, CheckCircle, AlertCircle, Info, MessageCircle } from 'lucide-react';

// Types
interface Toast {
  id: string;
  type: 'info' | 'success' | 'warning' | 'scheduled' | 'notification';
  title: string;
  message?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  duration?: number; // ms, 0 = persistent
  playSound?: boolean; // Play notification sound
  critical?: boolean; // Critical notification (email sent)
}

// Sound management
let notificationAudio: HTMLAudioElement | null = null;

function getNotificationSound(): HTMLAudioElement {
  if (!notificationAudio) {
    notificationAudio = new Audio('/sounds/notification.mp3');
    notificationAudio.volume = 0.5;
  }
  return notificationAudio;
}

export function playNotificationSound() {
  try {
    const audio = getNotificationSound();
    audio.currentTime = 0;
    audio.play().catch(() => {
      // Ignore errors (browser may block autoplay)
    });
  } catch (e) {
    // Ignore errors
  }
}

// Preload sound on first user interaction
export function preloadNotificationSound() {
  try {
    const audio = getNotificationSound();
    audio.load();
  } catch (e) {
    // Ignore errors
  }
}

interface ToastContextType {
  showToast: (toast: Omit<Toast, 'id'>) => void;
  dismissToast: (id: string) => void;
}

// Context
const ToastContext = createContext<ToastContextType | null>(null);

export const useToast = () => {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
};

// Provider
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const lastNotificationRef = useRef<number>(0);

  const showToast = useCallback((toast: Omit<Toast, 'id'>) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const duration = toast.duration ?? 5000;
    const newToast: Toast = { ...toast, id, duration };

    setToasts(prev => [...prev, newToast]);

    // Auto-dismiss after duration (unless 0)
    if (duration > 0) {
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== id));
      }, duration);
    }

    // Play sound for notifications if requested
    if (toast.playSound && (toast.type === 'notification' || toast.type === 'scheduled')) {
      playNotificationSound();
    }

    // PWA notification if app not focused (throttled to 2 min)
    if (document.hidden && (toast.type === 'scheduled' || toast.type === 'notification')) {
      const now = Date.now();
      if (now - lastNotificationRef.current > 120000) { // 2 minutes
        lastNotificationRef.current = now;
        sendPWANotification(toast.title, toast.message);
      }
    }
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ showToast, dismissToast }}>
      {children}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </ToastContext.Provider>
  );
}

// Toast Container (renders in corner)
function ToastContainer({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: string) => void }) {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map(toast => (
        <ToastItem key={toast.id} toast={toast} onDismiss={() => onDismiss(toast.id)} />
      ))}
    </div>
  );
}

// Individual Toast
function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const icons = {
    info: <Info size={18} className="text-blue-500" />,
    success: <CheckCircle size={18} className="text-green-500" />,
    warning: <AlertCircle size={18} className="text-amber-500" />,
    scheduled: <Bell size={18} className="text-[#D97757]" />,
    notification: <MessageCircle size={18} className={toast.critical ? "text-red-500" : "text-purple-500"} />,
  };

  const bgColors = {
    info: 'bg-blue-50 border-blue-200',
    success: 'bg-green-50 border-green-200',
    warning: 'bg-amber-50 border-amber-200',
    scheduled: 'bg-orange-50 border-[#D97757]/30',
    notification: toast.critical ? 'bg-red-50 border-red-300' : 'bg-purple-50 border-purple-200',
  };

  return (
    <div
      className={clsx(
        "rounded-lg border shadow-lg p-3 animate-slide-in flex items-start gap-3",
        bgColors[toast.type]
      )}
    >
      <div className="flex-shrink-0 mt-0.5">{icons[toast.type]}</div>
      <div className="flex-1 min-w-0">
        <p className="font-medium text-gray-900 text-sm">{toast.title}</p>
        {toast.message && (
          <p className="text-gray-600 text-xs mt-0.5 line-clamp-2">{toast.message}</p>
        )}
        {toast.action && (
          <button
            onClick={toast.action.onClick}
            className="mt-2 text-xs font-medium text-[#D97757] hover:text-[#c5664a] flex items-center gap-1"
          >
            {toast.action.label}
            <ExternalLink size={12} />
          </button>
        )}
      </div>
      <button
        onClick={onDismiss}
        className="flex-shrink-0 text-gray-400 hover:text-gray-600 transition-colors"
      >
        <X size={16} />
      </button>
    </div>
  );
}

// PWA Notification helper
async function sendPWANotification(title: string, body?: string) {
  if (!('Notification' in window)) return;

  if (Notification.permission === 'default') {
    await Notification.requestPermission();
  }

  if (Notification.permission === 'granted') {
    const registration = await navigator.serviceWorker?.ready;
    if (registration) {
      registration.showNotification(title, {
        body: body || 'Scheduled task completed',
        icon: '/brain-icon.png',
        badge: '/brain-icon.png',
        tag: 'scheduled-task',
      });
    } else {
      // Fallback to basic notification
      new Notification(title, { body });
    }
  }
}

// Request notification permission and subscribe to push on first interaction
export async function requestNotificationPermission() {
  if (!('Notification' in window)) return;

  if (Notification.permission === 'default') {
    const permission = await Notification.requestPermission();
    if (permission === 'granted') {
      // Try to subscribe to push notifications
      subscribeToPush();
    }
  } else if (Notification.permission === 'granted') {
    // Already have permission, make sure we're subscribed
    subscribeToPush();
  }
}

// Subscribe to push notifications
async function subscribeToPush() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    console.log('Push notifications not supported');
    return;
  }

  try {
    const registration = await navigator.serviceWorker.ready;

    // Check if already subscribed
    const existingSubscription = await registration.pushManager.getSubscription();
    if (existingSubscription) {
      console.log('Already subscribed to push');
      return;
    }

    // Get VAPID public key from server
    const API_URL = window.location.origin.includes('localhost')
      ? 'http://localhost:8000/api'
      : `${window.location.origin}/api`;

    const keyRes = await fetch(`${API_URL}/push/vapid-public-key`);
    if (!keyRes.ok) {
      console.error('Could not get VAPID key');
      return;
    }
    const { publicKey } = await keyRes.json();

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

    // Subscribe to push
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey)
    });

    // Send subscription to server
    const subRes = await fetch(`${API_URL}/push/subscribe`, {
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

    if (subRes.ok) {
      console.log('Push subscription successful');
    } else {
      console.error('Failed to save push subscription');
    }
  } catch (e) {
    console.error('Push subscription error:', e);
  }
}
