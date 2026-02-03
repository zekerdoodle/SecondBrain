import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ToastProvider, requestNotificationPermission, preloadNotificationSound } from './Toast'
import { UpdateNotification } from './UpdateNotification'

// Request notification permission and preload sound after first user interaction
document.addEventListener('click', () => {
  requestNotificationPermission();
  preloadNotificationSound();
}, { once: true });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ToastProvider>
      <App />
      <UpdateNotification />
    </ToastProvider>
  </StrictMode>,
)
