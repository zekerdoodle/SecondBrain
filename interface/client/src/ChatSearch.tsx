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

  // Filters
  const [roleFilter, setRoleFilter] = useState<'all' | 'user' | 'assistant'>('all');
  const [dateFrom, setDateFrom] = useState<string>('');
  const [dateTo, setDateTo] = useState<string>('');

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

  return (
    <div className="absolute inset-0 bg-white z-50 flex flex-col">
      {/* Search Header */}
      <div className="border-b border-[#E8E6E3] p-4">
        <div className="flex items-center gap-3 max-w-2xl mx-auto">
          <Search size={20} className="text-gray-400 shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search conversations..."
            className="flex-1 outline-none text-gray-800 placeholder-gray-400"
          />
          {isLoading && <Loader2 size={18} className="animate-spin text-[var(--accent-primary)]" />}
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-[#F5F4F2] rounded-lg text-gray-500 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-4 mt-3 max-w-2xl mx-auto">
          {/* Role Filter */}
          <div className="flex items-center gap-1 text-sm">
            <span className="text-gray-500">From:</span>
            <select
              value={roleFilter}
              onChange={e => setRoleFilter(e.target.value as 'all' | 'user' | 'assistant')}
              className="border-none bg-[#F5F4F2] rounded px-2 py-1 text-gray-700 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--accent-primary)]"
            >
              <option value="all">All</option>
              <option value="user">Me</option>
              <option value="assistant">Assistant</option>
            </select>
          </div>

          {/* Date Filters */}
          <div className="flex items-center gap-1 text-sm">
            <Calendar size={14} className="text-gray-400" />
            <input
              type="date"
              value={dateFrom}
              onChange={e => setDateFrom(e.target.value)}
              className="border-none bg-[#F5F4F2] rounded px-2 py-1 text-gray-700 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--accent-primary)]"
              placeholder="From"
            />
            <span className="text-gray-400">-</span>
            <input
              type="date"
              value={dateTo}
              onChange={e => setDateTo(e.target.value)}
              className="border-none bg-[#F5F4F2] rounded px-2 py-1 text-gray-700 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--accent-primary)]"
              placeholder="To"
            />
          </div>
        </div>
      </div>

      {/* Results */}
      <div className="flex-1 overflow-y-auto p-4">
        {error && (
          <div className="text-red-500 text-center py-4">{error}</div>
        )}

        {semanticPending && (
          <div className="flex items-center gap-2 text-sm text-gray-500 mb-4 max-w-2xl mx-auto">
            <Sparkles size={14} className="text-[var(--accent-primary)]" />
            <span>Finding similar conversations...</span>
            <Loader2 size={14} className="animate-spin" />
          </div>
        )}

        {!query && (
          <div className="text-center text-gray-400 py-8">
            <Search size={32} className="mx-auto mb-2 opacity-50" />
            <p>Search by keywords, dates, or meaning</p>
          </div>
        )}

        {query && results.length === 0 && !isLoading && (
          <div className="text-center text-gray-400 py-8">
            <p>No results found for "{query}"</p>
          </div>
        )}

        <div className="space-y-2 max-w-2xl mx-auto">
          {results.map(result => (
            <div
              key={result.message_id}
              onClick={() => onSelectResult(result.chat_id, result.message_id)}
              className={clsx(
                "p-4 bg-white rounded-xl border cursor-pointer transition-all",
                result.match_type === 'both'
                  ? "border-[var(--accent-primary)]/30 hover:border-[var(--accent-primary)] shadow-sm"
                  : result.match_type === 'semantic'
                  ? "border-purple-200 hover:border-purple-400"
                  : "border-[#E8E6E3] hover:border-[var(--accent-primary)]",
                "hover:shadow-warm-lg"
              )}
            >
              {/* Header */}
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-gray-800 truncate">
                  {result.chat_title}
                </span>
                <span className="text-xs text-gray-400 shrink-0 ml-2">
                  {formatDate(result.timestamp)}
                </span>
              </div>

              {/* Content */}
              <div className="flex items-start gap-2">
                <span className={clsx(
                  "shrink-0 mt-0.5 p-1 rounded",
                  result.role === 'user'
                    ? "bg-blue-50 text-blue-600"
                    : "bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]"
                )}>
                  {result.role === 'user' ? <User size={12} /> : <Bot size={12} />}
                </span>
                <p
                  className="text-sm text-gray-600 line-clamp-2"
                  dangerouslySetInnerHTML={{ __html: result.content_preview }}
                />
              </div>

              {/* Match type indicator */}
              {result.match_type === 'semantic' && (
                <div className="flex items-center gap-1 mt-2 text-xs text-purple-500">
                  <Sparkles size={10} />
                  <span>Similar meaning</span>
                </div>
              )}
              {result.match_type === 'both' && (
                <div className="flex items-center gap-1 mt-2 text-xs text-[var(--accent-primary)]">
                  <Sparkles size={10} />
                  <span>Keyword + semantic match</span>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
