import { useEffect, useState } from 'react';
import { Chat } from './Chat';
import { useClaude } from './useClaude';

/**
 * PopoutChat — Lightweight standalone chat window.
 *
 * Rendered when the URL matches /chat/:sessionId.
 * Shows just the Chat component with no sidebar, file explorer, or header chrome.
 * Connects its own useClaude instance with suppressGlobalEvents to avoid
 * duplicate notifications in the main window.
 *
 * Uses BroadcastChannel to notify the main window when the pop-out closes.
 */
export const PopoutChat: React.FC<{ sessionId: string }> = ({ sessionId }) => {
  const [ready, setReady] = useState(false);

  // Create a dedicated useClaude instance for this pop-out window
  const claude = useClaude({
    instanceId: `popout-${sessionId}`,
    enabled: true,
    suppressGlobalEvents: true,
  });

  // Load the target session once connected
  useEffect(() => {
    if (claude.connectionStatus === 'connected' && !ready) {
      claude.loadChat(sessionId);
      setReady(true);
    }
  }, [claude.connectionStatus, sessionId, ready]);

  // Set window title from session
  useEffect(() => {
    document.title = `Chat — Second Brain`;
  }, []);

  // Notify main window on close via BroadcastChannel
  useEffect(() => {
    const channel = new BroadcastChannel('second-brain-popout');

    // Announce that this pop-out opened
    channel.postMessage({ type: 'popout-opened', sessionId });

    const handleBeforeUnload = () => {
      channel.postMessage({ type: 'popout-closed', sessionId });
    };

    window.addEventListener('beforeunload', handleBeforeUnload);

    // Listen for messages from main window (e.g., focus requests)
    channel.onmessage = (event) => {
      if (event.data.type === 'focus-popout' && event.data.sessionId === sessionId) {
        window.focus();
      }
    };

    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      channel.close();
    };
  }, [sessionId]);

  return (
    <div className="h-[100dvh] flex flex-col bg-[var(--bg-primary)] text-[var(--text-primary)] overflow-hidden font-sans selection:bg-[var(--accent-primary)]/20 selection:text-[var(--text-primary)]">
      {/* Thin top bar with session indicator */}
      <div className="h-8 bg-[var(--bg-secondary)] border-b border-[var(--border-color)] flex items-center px-3 shrink-0 drag-region select-none">
        <span className="text-xs text-[var(--text-muted)] truncate">
          Pop-out Chat
        </span>
        <div className="ml-auto flex items-center gap-2">
          <div className={`w-1.5 h-1.5 rounded-full ${
            claude.connectionStatus === 'connected' ? 'bg-emerald-500' :
            claude.connectionStatus === 'connecting' ? 'bg-amber-500 animate-pulse' :
            'bg-red-500'
          }`} />
        </div>
      </div>

      {/* Chat fills the rest */}
      <div className="flex-1 overflow-hidden">
        <Chat
          isMobile={false}
          claudeHook={claude}
          panelId={`popout-${sessionId}`}
          isSecondary={false}
        />
      </div>
    </div>
  );
};
