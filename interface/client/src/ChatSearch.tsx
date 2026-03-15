import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Search, X, Loader2, Sparkles, User, Bot, Calendar } from 'lucide-react';
import { clsx } from 'clsx';
import { API_URL } from './config';

interface SearchResult {
  message_id: string;
  chat_id: string;
  chat_title: string;
  role: string;
  content_preview: string;
  timestamp: number;
  score: number;
  match_type: 'keyword' | 'semantic' | 'both';
}

interface SearchResponse {
  results: SearchResult[];
  total_count: number;
  semantic_pending: boolean;
  query_time_ms: number;
}

interface ChatSearchProps {
  onSelectResult: (chatId: string, messageId: string) => void;
  onClose: () => void;
}

// Debounce hook
function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(handler);
  }, [value, delay]);

  return debouncedValue;
}

export const ChatSearch: React.FC<ChatSearchProps> = ({ onSelectResult, onClose }) => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [semanticPending, setSemanticPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [totalTime, setTotalTime] = useState<number | null>(null);

  // Filters
  const [roleFilter, setRoleFilter] = useState<'all' | 'user' | 'assistant'>('all');
  const [dateFrom, setDateFrom] = useState<string>('');
  const [dateTo, setDateTo] = useState<string>('');
  const [showFilters, setShowFilters] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const debouncedQuery = useDebounce(query, 300);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Handle keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Perform search
  const performSearch = useCallback(async (q: string, semanticOnly: boolean = false) => {
    if (!q.trim()) {
      setResults([]);
      return;
    }

    try {
      const params = new URLSearchParams({
        q,
        exclude_system: 'true',
        limit: '30',
        semantic_only: semanticOnly.toString(),
      });

      if (roleFilter !== 'all') {
        params.set('roles', roleFilter);
      }
      if (dateFrom) {
        params.set('date_from', dateFrom);
      }
      if (dateTo) {
        params.set('date_to', dateTo);
      }

      const response = await fetch(`${API_URL}/chat/search?${params}`);
      if (!response.ok) throw new Error('Search failed');

      const data: SearchResponse = await response.json();
      return data;
    } catch (err) {
      setError('Search failed. Please try again.');
      return null;
    }
  }, [roleFilter, dateFrom, dateTo]);

  // Main search effect
  useEffect(() => {
    if (!debouncedQuery.trim()) {
      setResults([]);
      setSemanticPending(false);
      setTotalTime(null);
      return;
    }

    setIsLoading(true);
    setError(null);

    // Phase 1: Fast keyword search
    performSearch(debouncedQuery, false).then(keywordData => {
      setIsLoading(false);
      if (!keywordData) return;

      setResults(keywordData.results);
      setSemanticPending(keywordData.semantic_pending);
      setTotalTime(keywordData.query_time_ms);

      // Phase 2: Async semantic enrichment
      if (keywordData.semantic_pending) {
        performSearch(debouncedQuery, true).then(semanticData => {
          setSemanticPending(false);
          if (!semanticData) return;

          // Merge results
          setResults(prev => {
            const merged = new Map<string, SearchResult>();

            // Add keyword results
            for (const r of prev) {
              merged.set(r.message_id, r);
            }

            // Merge semantic results
            for (const r of semanticData.results) {
              const existing = merged.get(r.message_id);
              if (existing) {
                // Both matched - update score and type
                merged.set(r.message_id, {
                  ...existing,
                  score: Math.max(existing.score, r.score),
                  match_type: 'both'
                });
              } else {
                merged.set(r.message_id, r);
              }
            }

            // Sort by score
            const sorted = Array.from(merged.values());
            sorted.sort((a, b) => b.score - a.score);
            return sorted.slice(0, 30);
          });
        });
      }
    });
  }, [debouncedQuery, performSearch]);

  const formatDate = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const hasActiveFilters = roleFilter !== 'all' || dateFrom || dateTo;

  return (
    <div className="absolute inset-0 bg-[var(--bg-primary)] z-50 flex flex-col">
      {/* Search Header */}
      <div className="border-b border-[var(--border-color)] p-4">
        <div className="flex items-center gap-3 max-w-2xl mx-auto">
          <Search size={20} className="text-[var(--text-muted)] shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search conversations..."
            className="flex-1 outline-none bg-transparent text-[var(--text-primary)] placeholder-[var(--text-muted)]"
          />
          {isLoading && <Loader2 size={18} className="animate-spin text-[var(--accent-primary)]" />}
          <button
            onClick={() => setShowFilters(f => !f)}
            className={clsx(
              "p-1.5 rounded-lg transition-colors",
              hasActiveFilters
                ? "bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]"
                : "hover:bg-[var(--bg-tertiary)] text-[var(--text-muted)]"
            )}
            title="Filters"
          >
            <Calendar size={16} />
          </button>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-[var(--bg-tertiary)] rounded-lg text-[var(--text-muted)] transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Filters - collapsible */}
        {showFilters && (
          <div className="flex items-center gap-4 mt-3 max-w-2xl mx-auto animate-in">
            {/* Role Filter */}
            <div className="flex items-center gap-1.5 text-sm">
              <span className="text-[var(--text-muted)]">From:</span>
              <select
                value={roleFilter}
                onChange={e => setRoleFilter(e.target.value as 'all' | 'user' | 'assistant')}
                className="border border-[var(--border-color)] bg-[var(--bg-tertiary)] rounded-lg px-2 py-1 text-[var(--text-primary)] text-sm focus:outline-none focus:ring-1 focus:ring-[var(--accent-primary)] appearance-none cursor-pointer"
              >
                <option value="all">All</option>
                <option value="user">Me</option>
                <option value="assistant">Assistant</option>
              </select>
            </div>

            {/* Date Filters */}
            <div className="flex items-center gap-1.5 text-sm">
              <Calendar size={14} className="text-[var(--text-muted)]" />
              <input
                type="date"
                value={dateFrom}
                onChange={e => setDateFrom(e.target.value)}
                className="border border-[var(--border-color)] bg-[var(--bg-tertiary)] rounded-lg px-2 py-1 text-[var(--text-primary)] text-sm focus:outline-none focus:ring-1 focus:ring-[var(--accent-primary)]"
              />
              <span className="text-[var(--text-muted)]">→</span>
              <input
                type="date"
                value={dateTo}
                onChange={e => setDateTo(e.target.value)}
                className="border border-[var(--border-color)] bg-[var(--bg-tertiary)] rounded-lg px-2 py-1 text-[var(--text-primary)] text-sm focus:outline-none focus:ring-1 focus:ring-[var(--accent-primary)]"
              />
            </div>

            {/* Clear filters */}
            {hasActiveFilters && (
              <button
                onClick={() => { setRoleFilter('all'); setDateFrom(''); setDateTo(''); }}
                className="text-xs text-[var(--accent-primary)] hover:underline"
              >
                Clear
              </button>
            )}
          </div>
        )}
      </div>

      {/* Results */}
      <div className="flex-1 overflow-y-auto p-4">
        {error && (
          <div className="text-[var(--error)] text-center py-4 text-sm">{error}</div>
        )}

        {semanticPending && (
          <div className="flex items-center gap-2 text-sm text-[var(--text-muted)] mb-4 max-w-2xl mx-auto">
            <Sparkles size={14} className="text-[var(--accent-primary)]" />
            <span>Finding similar conversations...</span>
            <Loader2 size={14} className="animate-spin" />
          </div>
        )}

        {!query && (
          <div className="text-center text-[var(--text-muted)] py-12">
            <Search size={36} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm">Search by keywords or meaning</p>
            <p className="text-xs mt-1 opacity-60">Results include keyword matches and semantic similarity</p>
          </div>
        )}

        {query && results.length === 0 && !isLoading && (
          <div className="text-center text-[var(--text-muted)] py-12">
            <p className="text-sm">No results found for "<span className="text-[var(--text-secondary)]">{query}</span>"</p>
          </div>
        )}

        {/* Results count */}
        {results.length > 0 && (
          <div className="text-xs text-[var(--text-muted)] mb-3 max-w-2xl mx-auto">
            {results.length} result{results.length !== 1 ? 's' : ''}
            {totalTime !== null && ` · ${totalTime < 1000 ? `${Math.round(totalTime)}ms` : `${(totalTime / 1000).toFixed(1)}s`}`}
          </div>
        )}

        <div className="space-y-2 max-w-2xl mx-auto">
          {results.map(result => (
            <div
              key={result.message_id}
              onClick={() => onSelectResult(result.chat_id, result.message_id)}
              className={clsx(
                "p-4 bg-[var(--bg-secondary)] rounded-xl border cursor-pointer transition-all",
                result.match_type === 'both'
                  ? "border-[var(--accent-primary)]/40 hover:border-[var(--accent-primary)]"
                  : result.match_type === 'semantic'
                  ? "border-[var(--border-color)] hover:border-[var(--accent-primary)]/60"
                  : "border-[var(--border-color)] hover:border-[var(--accent-primary)]",
                "hover:shadow-warm-lg"
              )}
            >
              {/* Header */}
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-[var(--text-primary)] truncate text-sm">
                  {result.chat_title}
                </span>
                <div className="flex items-center gap-2 shrink-0 ml-2">
                  {/* Match type badge */}
                  {result.match_type === 'semantic' && (
                    <span className="flex items-center gap-1 text-[10px] text-[var(--accent-primary)] opacity-70">
                      <Sparkles size={10} />
                      similar
                    </span>
                  )}
                  {result.match_type === 'both' && (
                    <span className="flex items-center gap-1 text-[10px] text-[var(--accent-primary)]">
                      <Sparkles size={10} />
                      exact + similar
                    </span>
                  )}
                  <span className="text-[11px] text-[var(--text-muted)]">
                    {formatDate(result.timestamp)}
                  </span>
                </div>
              </div>

              {/* Content */}
              <div className="flex items-start gap-2">
                <span className={clsx(
                  "shrink-0 mt-0.5 p-1 rounded",
                  result.role === 'user'
                    ? "bg-[var(--accent-primary)]/15 text-[var(--accent-primary)]"
                    : "bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
                )}>
                  {result.role === 'user' ? <User size={12} /> : <Bot size={12} />}
                </span>
                <p
                  className="text-sm text-[var(--text-secondary)] line-clamp-2 search-result-preview"
                  dangerouslySetInnerHTML={{ __html: result.content_preview }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
