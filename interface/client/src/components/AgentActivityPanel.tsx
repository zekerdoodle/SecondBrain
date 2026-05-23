import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, CalendarClock, Clock, RefreshCw } from 'lucide-react';
import { API_URL } from '../config';
import type { AgentActivityResponse, RunningAgentEntry, UpcomingScheduledRun } from '../types';

const POLL_MS = 15000;

function formatElapsed(startedAt: number, nowMs: number): string {
  const elapsedSeconds = Math.max(0, Math.floor((nowMs - startedAt * 1000) / 1000));
  if (elapsedSeconds < 60) return `${elapsedSeconds}s`;
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours}h ${remainingMinutes}m`;
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'Schedule only';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

function formatKind(kind: string): string {
  return kind
    .split('_')
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function sourceLabel(entry: RunningAgentEntry): string {
  if (entry.scheduled_task_id) return `task ${entry.scheduled_task_id}`;
  if (entry.conversation_id) return `thread ${entry.conversation_id.slice(0, 8)}`;
  if (entry.source_chat_id) return `chat ${entry.source_chat_id.slice(0, 8)}`;
  if (entry.salon_id) return `salon ${entry.salon_id.slice(0, 8)}`;
  return formatKind(entry.kind);
}

function scheduleName(run: UpcomingScheduledRun): string {
  if (run.agent) return run.agent;
  if (run.name && run.name !== 'prompt') return run.name;
  return run.type === 'agent' ? 'agent' : 'prompt';
}

interface AgentActivityPanelProps {
  accentColor: string;
}

export const AgentActivityPanel: React.FC<AgentActivityPanelProps> = ({ accentColor }) => {
  const [data, setData] = useState<AgentActivityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(Date.now());

  const loadActivity = useCallback(async (quiet = false) => {
    if (quiet) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setLoadError(null);
    try {
      const res = await fetch(`${API_URL}/agent-activity`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${res.status})`);
      }
      const payload = await res.json() as AgentActivityResponse;
      setData(payload);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Could not load agent activity');
    } finally {
      setLoading(false);
      setRefreshing(false);
      setNowMs(Date.now());
    }
  }, []);

  useEffect(() => {
    loadActivity(false);
    const poll = window.setInterval(() => loadActivity(true), POLL_MS);
    const tick = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => {
      window.clearInterval(poll);
      window.clearInterval(tick);
    };
  }, [loadActivity]);

  const runningEntries = data?.running_agents.entries;
  const scheduledEntries = data?.upcoming_scheduled_runs.entries;
  const runningCount = runningEntries?.length ?? 0;
  const scheduledCount = scheduledEntries?.length ?? 0;

  const generatedLabel = useMemo(() => {
    if (!data?.generated_at) return null;
    return formatDateTime(data.generated_at);
  }, [data?.generated_at]);

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
            <Activity size={16} style={{ color: accentColor }} />
            Agent Activity
          </div>
          <div className="mt-1 flex flex-wrap gap-2 text-xs text-[var(--text-muted)]">
            <span>{runningCount} running</span>
            <span>•</span>
            <span>{scheduledCount} scheduled</span>
            {generatedLabel && (
              <>
                <span>•</span>
                <span>updated {generatedLabel}</span>
              </>
            )}
          </div>
        </div>
        <button
          onClick={() => loadActivity(true)}
          disabled={loading || refreshing}
          className="p-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--border-color)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          title="Refresh activity"
        >
          <RefreshCw size={16} className={refreshing ? 'animate-spin' : ''} />
        </button>
      </div>

      {loadError && (
        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-500 text-sm flex gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" />
          <span>Could not load agent activity: {loadError}</span>
        </div>
      )}

      <section className="space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--text-primary)]">
          <Clock size={15} />
          Current Invocations
        </div>

        {loading && !data ? (
          <div className="text-sm text-[var(--text-secondary)] py-3">Loading running agents...</div>
        ) : data?.running_agents.error ? (
          <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-500 text-sm">
            Could not load running agents: {data.running_agents.error}
          </div>
        ) : runningEntries && runningEntries.length > 0 ? (
          <div className="space-y-2">
            {runningEntries.map(entry => (
              <div key={entry.id} className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-[var(--text-primary)]">{entry.agent}</span>
                      <span className="text-[11px] px-2 py-0.5 rounded-full bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border border-[var(--border-color)]">
                        {formatKind(entry.kind)}
                      </span>
                    </div>
                    <div className="mt-1 text-sm text-[var(--text-secondary)] break-words">
                      {entry.task_summary || 'No summary available'}
                    </div>
                    <div className="mt-2 text-xs text-[var(--text-muted)] font-mono break-all">
                      {sourceLabel(entry)}
                    </div>
                  </div>
                  <div className="shrink-0 text-xs font-medium text-[var(--text-primary)]">
                    {formatElapsed(entry.started_at, nowMs)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-[var(--text-secondary)] py-3">No invocations running.</div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--text-primary)]">
          <CalendarClock size={15} />
          Upcoming Scheduled Runs
        </div>

        {loading && !data ? (
          <div className="text-sm text-[var(--text-secondary)] py-3">Loading scheduled runs...</div>
        ) : data?.upcoming_scheduled_runs.error ? (
          <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-500 text-sm">
            Could not load scheduled runs: {data.upcoming_scheduled_runs.error}
          </div>
        ) : scheduledEntries && scheduledEntries.length > 0 ? (
          <div className="space-y-2">
            {scheduledEntries.map(run => (
              <div key={run.task_id || run.id || `${run.name}-${run.schedule}`} className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-[var(--text-primary)]">{scheduleName(run)}</span>
                      <span className="text-[11px] px-2 py-0.5 rounded-full bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border border-[var(--border-color)]">
                        {run.silent ? 'silent' : 'visible'}
                      </span>
                      <span className="text-[11px] text-[var(--text-muted)] font-mono">{run.task_id || run.id}</span>
                    </div>
                    <div className="mt-1 text-sm text-[var(--text-secondary)] break-words">
                      {run.prompt_summary || 'No summary available'}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2 text-xs text-[var(--text-muted)]">
                      <span>{run.schedule || 'No schedule'}</span>
                      {run.error && <span className="text-red-500">{run.error}</span>}
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="text-xs font-medium text-[var(--text-primary)]">
                      {run.due_now ? 'Due now' : formatDateTime(run.next_run)}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-[var(--text-secondary)] py-3">No upcoming active scheduled runs.</div>
        )}
      </section>
    </div>
  );
};
