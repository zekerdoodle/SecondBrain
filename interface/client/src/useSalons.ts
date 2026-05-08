// Salons hook — manages salon list state, active salon, and live updates.
//
// Opens its own WebSocket connection to /ws/chat (parallel to useClaude's)
// and listens for salon_* event types. The server's broadcast_to_all_clients
// fans every salon event to every connected /ws/chat client, so this just
// filters the stream.
//
// API surface:
//   const {
//     salons, activeSalon, activeSalonId, typing, lastConvenerByMsgId,
//     setActiveSalonId, refresh, refreshActive,
//     create, post, addParticipant, remove, rename,
//   } = useSalons();

import { useCallback, useEffect, useRef, useState } from 'react';
import { WS_URL } from './config';
import * as salonApi from './salonApi';
import type {
  ContentBlock,
  ConvenerDecision,
  SalonFull,
  SalonMessage,
  SalonSummary,
} from './types';

/** Currently-streaming agent reply for a salon — blocks update live as the
 * SDK emits AssistantMessages. Cleared when the final salon_message lands. */
export interface SalonStreamingSnapshot {
  from: string;             // agent name
  blocks: ContentBlock[];   // latest block list (server sends full snapshots)
}

export interface UseSalonsResult {
  salons: SalonSummary[];
  activeSalonId: string | null;
  activeSalon: SalonFull | null;
  /** Map of message_id -> the convener decision that anchors above it (in-flight) */
  pendingConvener: { salonId: string; anchorMsgId: string | null; decision: ConvenerDecision } | null;
  /** Currently-typing agents per salon */
  typingByAgent: Record<string, string | null>;
  /** Live streaming snapshot per salon (cleared when the final message lands) */
  streamingByAgent: Record<string, SalonStreamingSnapshot | null>;
  setActiveSalonId: (id: string | null) => void;
  refresh: () => Promise<void>;
  refreshActive: () => Promise<void>;
  create: (opts: { title: string; participants: string[]; opening_message?: string }) => Promise<string>;
  post: (content: string) => Promise<void>;
  addParticipant: (participant: string) => Promise<void>;
  remove: (salonId: string) => Promise<void>;
  rename: (salonId: string, title: string) => Promise<void>;
}

export function useSalons(): UseSalonsResult {
  const [salons, setSalons] = useState<SalonSummary[]>([]);
  const [activeSalonId, setActiveSalonIdState] = useState<string | null>(null);
  const [activeSalon, setActiveSalon] = useState<SalonFull | null>(null);
  const [typingByAgent, setTypingByAgent] = useState<Record<string, string | null>>({});
  const [streamingByAgent, setStreamingByAgent] = useState<Record<string, SalonStreamingSnapshot | null>>({});
  const [pendingConvener, setPendingConvener] = useState<UseSalonsResult['pendingConvener']>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeSalonId;

  // ----- API helpers -------------------------------------------------------

  const refresh = useCallback(async () => {
    try {
      const list = await salonApi.listSalons();
      setSalons(list);
    } catch (e) {
      console.error('Salon list refresh failed', e);
    }
  }, []);

  const refreshActive = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) {
      setActiveSalon(null);
      return;
    }
    try {
      const full = await salonApi.getSalon(id);
      setActiveSalon(full);
    } catch (e) {
      console.error(`Salon ${id} fetch failed`, e);
    }
  }, []);

  const setActiveSalonId = useCallback((id: string | null) => {
    setActiveSalonIdState(id);
    setPendingConvener(null);
    if (!id) {
      setActiveSalon(null);
    }
  }, []);

  // Refetch full salon when activeSalonId changes
  useEffect(() => {
    if (!activeSalonId) {
      setActiveSalon(null);
      return;
    }
    salonApi.getSalon(activeSalonId)
      .then(setActiveSalon)
      .catch(e => console.error(`Salon ${activeSalonId} fetch failed`, e));
  }, [activeSalonId]);

  // ----- WebSocket subscription -------------------------------------------

  useEffect(() => {
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      const ws = new WebSocket(`${WS_URL}/ws/chat`);
      wsRef.current = ws;

      ws.onmessage = (ev) => {
        let data: any;
        try {
          data = JSON.parse(ev.data);
        } catch {
          return;
        }
        const t = data?.type as string | undefined;
        if (!t || !t.startsWith('salon_')) return;

        const salonId = data.salon_id as string | undefined;

        switch (t) {
          case 'salon_created': {
            if (data.salon) {
              setSalons(prev => {
                const existing = prev.find(s => s.salon_id === data.salon.salon_id);
                if (existing) return prev;
                return [data.salon, ...prev];
              });
            }
            break;
          }
          case 'salon_summary_updated': {
            if (data.salon) {
              const incoming: SalonSummary = data.salon;
              setSalons(prev => {
                const without = prev.filter(s => s.salon_id !== incoming.salon_id);
                return [incoming, ...without].sort(
                  (a, b) => (b.last_message_at || 0) - (a.last_message_at || 0)
                );
              });
            }
            break;
          }
          case 'salon_message': {
            if (!salonId) break;
            // Update active salon immediately if this is the current one
            if (activeIdRef.current === salonId && data.message) {
              setActiveSalon(prev => {
                if (!prev) return prev;
                const msg = data.message as SalonMessage;
                if (prev.messages.some(m => m.id === msg.id)) return prev;
                return {
                  ...prev,
                  messages: [...prev.messages, msg],
                  last_message_at: msg.created_at,
                  message_count: (prev.message_count || 0) + 1,
                };
              });
              // Clear typing for the agent who just spoke
              if (data.message?.from) {
                setTypingByAgent(prev => ({ ...prev, [salonId]: null }));
              }
              // Final message arrived — clear the streaming snapshot.
              setStreamingByAgent(prev => ({ ...prev, [salonId]: null }));
              // Clear in-flight convener pending (it's now anchored to a real msg)
              setPendingConvener(prev => (prev && prev.salonId === salonId ? null : prev));
            }
            break;
          }
          case 'salon_streaming_blocks': {
            // Live block snapshot from the dispatcher — server sends the full
            // block list every time. We just overwrite (cheap and idempotent).
            if (!salonId) break;
            if (activeIdRef.current !== salonId) break;
            const from = data.from as string | undefined;
            const blocks = data.blocks as ContentBlock[] | undefined;
            if (!from || !blocks) break;
            setStreamingByAgent(prev => ({
              ...prev,
              [salonId]: { from, blocks },
            }));
            break;
          }
          case 'salon_streaming_clear': {
            if (!salonId) break;
            setStreamingByAgent(prev => ({ ...prev, [salonId]: null }));
            break;
          }
          case 'salon_convener_decision': {
            if (!salonId) break;
            if (activeIdRef.current === salonId) {
              setPendingConvener({
                salonId,
                anchorMsgId: data.anchor_message_id || null,
                decision: data.decision as ConvenerDecision,
              });
            }
            break;
          }
          case 'salon_typing': {
            if (!salonId) break;
            setTypingByAgent(prev => ({ ...prev, [salonId]: data.agent || null }));
            break;
          }
          case 'salon_state_changed': {
            if (!salonId) break;
            setSalons(prev => prev.map(s =>
              s.salon_id === salonId
                ? {
                    ...s,
                    gc_active: !!data.gc_active,
                    gc_recheck_minutes: data.gc_recheck_minutes,
                    convener_recall_at: data.convener_recall_at,
                    locked: !!data.locked,
                  }
                : s
            ));
            if (activeIdRef.current === salonId) {
              setActiveSalon(prev => prev ? {
                ...prev,
                gc_active: !!data.gc_active,
                gc_recheck_minutes: data.gc_recheck_minutes,
                convener_recall_at: data.convener_recall_at,
                locked: !!data.locked,
              } : prev);
            }
            // If salon released (no longer locked), clear typing
            if (!data.locked) {
              setTypingByAgent(prev => ({ ...prev, [salonId]: null }));
            }
            break;
          }
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (!stopped) setTimeout(connect, 2000);
      };

      ws.onerror = () => {
        try { ws.close(); } catch { /* ignore */ }
      };
    };

    connect();
    return () => {
      stopped = true;
      try { wsRef.current?.close(); } catch { /* ignore */ }
    };
  }, []);

  // ----- Initial load ------------------------------------------------------

  useEffect(() => { refresh(); }, [refresh]);

  // ----- Mutating helpers -------------------------------------------------

  const create = useCallback(async (opts: { title: string; participants: string[]; opening_message?: string }) => {
    const result = await salonApi.createSalon(opts);
    await refresh();
    return result.salon_id;
  }, [refresh]);

  const post = useCallback(async (content: string) => {
    const id = activeIdRef.current;
    if (!id) throw new Error('No active salon');

    // Optimistic append — the user's message lands in the UI immediately,
    // independent of the WS roundtrip. This also defends against the
    // "salon panel just mounted, WS still establishing" race that lets
    // posted messages disappear until a hard refresh.
    const tempId = `local-${Date.now()}`;
    const tempMsg: SalonMessage = {
      id: tempId,
      from: 'user',
      content,
      created_at: Date.now() / 1000,
    };
    setActiveSalon(prev => prev ? {
      ...prev,
      messages: [...prev.messages, tempMsg],
      last_message_at: tempMsg.created_at,
      message_count: (prev.message_count || 0) + 1,
    } : prev);

    try {
      const result = await salonApi.postToSalon(id, content);
      // Reconcile: replace the temp id with the server-assigned one. The
      // salon_message WS broadcast is idempotent (it dedupes by id), so
      // this won't cause a double-render when the WS event lands.
      setActiveSalon(prev => prev ? {
        ...prev,
        messages: prev.messages.map(m =>
          m.id === tempId ? { ...m, id: result.message_id } : m
        ),
      } : prev);
    } catch (e) {
      // Rollback on failure so we don't leave a phantom message.
      setActiveSalon(prev => prev ? {
        ...prev,
        messages: prev.messages.filter(m => m.id !== tempId),
        message_count: Math.max(0, (prev.message_count || 0) - 1),
      } : prev);
      throw e;
    }
    // Background refetch as a final safety net.
    refreshActive();
  }, [refreshActive]);

  const addParticipant = useCallback(async (participant: string) => {
    const id = activeIdRef.current;
    if (!id) throw new Error('No active salon');
    await salonApi.addParticipant(id, participant);
    refreshActive();
    refresh();
  }, [refresh, refreshActive]);

  const remove = useCallback(async (salonId: string) => {
    await salonApi.deleteSalon(salonId);
    if (activeIdRef.current === salonId) {
      setActiveSalonIdState(null);
      setActiveSalon(null);
    }
    setSalons(prev => prev.filter(s => s.salon_id !== salonId));
  }, []);

  const rename = useCallback(async (salonId: string, title: string) => {
    await salonApi.setSalonTitle(salonId, title);
    setSalons(prev => prev.map(s => s.salon_id === salonId ? { ...s, title } : s));
    if (activeIdRef.current === salonId) {
      setActiveSalon(prev => prev ? { ...prev, title } : prev);
    }
  }, []);

  return {
    salons,
    activeSalonId,
    activeSalon,
    pendingConvener,
    typingByAgent,
    streamingByAgent,
    setActiveSalonId,
    refresh,
    refreshActive,
    create,
    post,
    addParticipant,
    remove,
    rename,
  };
}
