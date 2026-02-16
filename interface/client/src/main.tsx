import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { PopoutChat } from './PopoutChat.tsx'
import { ToastProvider, requestNotificationPermission, preloadNotificationSound } from './Toast'
import { UpdateNotification } from './UpdateNotification'
import { useThemeInit } from './components/SettingsModal'

// Request notification permission and preload sound after first user interaction
document.addEventListener('click', () => {
  requestNotificationPermission();
  preloadNotificationSound();
}, { once: true });

// Simple path-based routing: /chat/:sessionId renders the pop-out chat
const chatMatch = window.location.pathname.match(/^\/chat\/([^/]+)$/);

/** Wrapper to ensure theme CSS vars are initialized in pop-out windows */
const PopoutWrapper: React.FC<{ sessionId: string }> = ({ sessionId }) => {
  useThemeInit();
  return <PopoutChat sessionId={sessionId} />;
};

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ToastProvider>
      {chatMatch ? (
        <PopoutWrapper sessionId={chatMatch[1]} />
      ) : (
        <>
          <App />
          <UpdateNotification />
        </>
      )}
    </ToastProvider>
  </StrictMode>,
)
