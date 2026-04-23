import React, { useState, useRef, useEffect, useCallback } from 'react';
import { ChevronDown, Sparkles, X } from 'lucide-react';
import { clsx } from 'clsx';
import { API_URL } from '../config';

interface MoodOption {
  name: string;
  description: string;
}

interface MoodData {
  enabled: boolean;
  current: string | null;
  current_preview: string;
  moods: MoodOption[];
}

interface MoodSelectorProps {
  agentName: string | undefined;
}

/**
 * MoodSelector — small dropdown that lets the user set/clear an agent's mood.
 *
 * Only renders when the agent has the set_mood MCP tool enabled in its config
 * (the backend returns `enabled: false` otherwise). Writes directly to the
 * agent's working memory via the same mechanism as the mood tool itself.
 */
export const MoodSelector: React.FC<MoodSelectorProps> = ({ agentName }) => {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<MoodData | null>(null);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const fetchMoods = useCallback(async () => {
    if (!agentName) return;
    try {
      const res = await fetch(`${API_URL}/agents/${agentName}/moods`);
      if (!res.ok) {
        setData({ enabled: false, current: null, current_preview: '', moods: [] });
        return;
      }
      const json = (await res.json()) as MoodData;
      setData(json);
    } catch (err) {
      console.error('Failed to fetch moods:', err);
      setData({ enabled: false, current: null, current_preview: '', moods: [] });
    }
  }, [agentName]);

  // Fetch moods whenever the agent changes
  useEffect(() => {
    fetchMoods();
  }, [fetchMoods]);

  // Listen for server-pushed mood changes (when the agent sets its own mood
  // via set_mood, or when another tab/device changes it). Refetch only if
  // the change is for the agent we're currently displaying.
  useEffect(() => {
    if (!agentName) return;
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { agent?: string } | undefined;
      if (!detail || detail.agent === agentName) {
        fetchMoods();
      }
    };
    window.addEventListener('mood_updated', handler);
    return () => window.removeEventListener('mood_updated', handler);
  }, [agentName, fetchMoods]);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const setMood = useCallback(
    async (preset: string) => {
      if (!agentName || loading) return;
      setLoading(true);
      try {
        const res = await fetch(`${API_URL}/agents/${agentName}/set-mood`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preset }),
        });
        if (!res.ok) {
          console.error('Failed to set mood:', await res.text());
        }
        await fetchMoods();
        setOpen(false);
      } catch (err) {
        console.error('Failed to set mood:', err);
      } finally {
        setLoading(false);
      }
    },
    [agentName, loading, fetchMoods]
  );

  if (!data || !data.enabled) return null;

  const currentLabel = data.current
    ? data.current === 'custom'
      ? 'custom mood'
      : data.current
    : 'set mood';
  const hasActiveMood = !!data.current;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        disabled={loading}
        className={clsx(
          'flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg transition-colors',
          hasActiveMood
            ? 'bg-[var(--accent-light)] text-[var(--accent-primary)] hover:opacity-80'
            : 'text-[var(--text-muted)] hover:bg-[var(--bg-tertiary)]'
        )}
        title={hasActiveMood ? `Current mood: ${currentLabel}` : 'Set mood'}
      >
        <Sparkles size={12} />
        <span className="capitalize">{currentLabel}</span>
        <ChevronDown size={12} />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-2 w-72 max-h-[420px] overflow-y-auto bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl shadow-lg z-50">
          {hasActiveMood && (
            <button
              onClick={() => setMood('clear')}
              disabled={loading}
              className="w-full flex items-center gap-2 px-4 py-2.5 text-left text-sm transition-colors hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border-b border-[var(--border-color)]"
            >
              <X size={14} />
              <span>Clear mood (back to baseline)</span>
            </button>
          )}
          {data.moods.length === 0 ? (
            <div className="px-4 py-3 text-sm text-[var(--text-muted)]">
              No mood presets found for this agent.
            </div>
          ) : (
            data.moods.map((mood) => {
              const isSelected = data.current === mood.name;
              return (
                <button
                  key={mood.name}
                  onClick={() => setMood(mood.name)}
                  disabled={loading}
                  className={clsx(
                    'w-full flex flex-col gap-0.5 px-4 py-2.5 text-left transition-colors',
                    isSelected
                      ? 'bg-[var(--accent-light)]'
                      : 'hover:bg-[var(--bg-tertiary)]'
                  )}
                >
                  <div className="text-sm font-medium text-[var(--text-primary)] capitalize">
                    {mood.name}
                  </div>
                  {mood.description && (
                    <div className="text-xs text-[var(--text-muted)] line-clamp-2">
                      {mood.description}
                    </div>
                  )}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
};
