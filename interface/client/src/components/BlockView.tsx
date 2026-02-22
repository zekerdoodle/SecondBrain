import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ChevronDown, ChevronRight, Brain, Wrench, AlertTriangle, Loader2 } from 'lucide-react';
import { clsx } from 'clsx';
import MDEditor from '@uiw/react-md-editor';
import { escapeNonHtmlTags } from '../utils/escapeNonHtmlTags';
import { getToolDisplayName, extractToolSummary } from '../utils/toolDisplay';
import type { ContentBlock } from '../types';

// --- ThinkingBlock ---

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return `${minutes}m ${remaining}s`;
}

function ThinkingBlock({ block }: { block: ContentBlock }) {
  const isLive = block.status === 'in_progress';
  const [expanded, setExpanded] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);

  // Auto-scroll thinking content while streaming
  useEffect(() => {
    if (isLive && expanded && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [block.content, isLive, expanded]);

  // Compute live duration from started_at
  const [liveDuration, setLiveDuration] = useState(0);
  useEffect(() => {
    if (!isLive || !block.started_at) return;
    const interval = setInterval(() => {
      setLiveDuration(Math.round((Date.now() / 1000 - block.started_at!) * 1000));
    }, 100);
    return () => clearInterval(interval);
  }, [isLive, block.started_at]);

  const displayDuration = block.duration_ms || liveDuration;

  return (
    <div className="thinking-block my-1">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors py-1"
      >
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {isLive ? (
          <>
            <span className="animate-pulse">Thinking</span>
            {displayDuration > 0 && (
              <span className="text-[var(--text-tertiary)]">
                {formatDuration(displayDuration)}
              </span>
            )}
          </>
        ) : (
          <>
            <Brain size={14} className="text-[var(--text-tertiary)]" />
            <span>Thought for {formatDuration(displayDuration)}</span>
          </>
        )}
      </button>

      {expanded && (
        <div
          ref={contentRef}
          className="thinking-content ml-5 pl-3 border-l-2 border-[var(--border-secondary)] max-h-64 overflow-y-auto"
        >
          <div className="text-xs text-[var(--text-secondary)] font-mono whitespace-pre-wrap leading-relaxed">
            {block.content}
          </div>
        </div>
      )}
    </div>
  );
}

// --- TextBlockView ---

interface TextBlockViewProps {
  block: ContentBlock;
  onOpenFile?: (path: string) => void;
}

function TextBlockView({ block, onOpenFile }: TextBlockViewProps) {
  const isLive = block.status === 'in_progress';

  if (!block.content.trim() && isLive) {
    return null; // Don't render empty in-progress text blocks
  }

  return (
    <div className="prose max-w-none chat-markdown font-chat" style={{ fontFamily: 'var(--font-chat)', fontSize: 'var(--font-size-base)' }}>
      <MDEditor.Markdown
        source={escapeNonHtmlTags(block.content)}
        style={{
          backgroundColor: 'transparent',
          color: 'inherit',
          fontFamily: 'var(--font-chat)',
          fontSize: 'var(--font-size-base)',
          lineHeight: '1.7'
        }}
        components={{
          code: ({ children, className, ...props }) => {
            const isInline = !className;
            const text = String(children).replace(/\n$/, '');
            if (isInline && onOpenFile && looksLikeFilePath(text)) {
              const relativePath = toRelativePath(text);
              return (
                <code
                  className="file-path-link"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onOpenFile(relativePath);
                  }}
                  title={`Open ${relativePath} in editor`}
                  {...props}
                >
                  {children}
                </code>
              );
            }
            return <code className={className} {...props}>{children}</code>;
          }
        }}
      />
    </div>
  );
}

// File path helpers (duplicated from Chat.tsx to avoid circular deps)
const FILE_PATH_REGEX = /^(?:\/home\/debian\/second_brain\/)?(?:(?:interface|\.claude|0[0-5]_\w+|docs|20_Areas|10_Active_Projects|30_Incubator|40_Archive|05_App_Data)\/)[\w./_-]+\.\w{1,10}$/;

function looksLikeFilePath(text: string): boolean {
  if (!text.includes('/') || !text.includes('.')) return false;
  if (text.includes(' ')) return false;
  if (/^https?:\/\//.test(text)) return false;
  return FILE_PATH_REGEX.test(text) || /^[\w./_-]+\/[\w./_-]+\.\w{1,10}$/.test(text);
}

function toRelativePath(path: string): string {
  const prefix = '/home/debian/second_brain/';
  if (path.startsWith(prefix)) {
    return path.slice(prefix.length);
  }
  return path;
}

// --- ToolChipBlock (combined tool_use + tool_result) ---

interface ToolChipBlockProps {
  toolUse: ContentBlock;
  toolResult?: ContentBlock;
}

function ToolChipBlock({ toolUse, toolResult }: ToolChipBlockProps) {
  const [expanded, setExpanded] = useState(false);
  const isRunning = toolUse.status === 'in_progress';
  const isError = toolUse.is_error || toolResult?.is_error;
  const displayName = getToolDisplayName(toolUse.tool_name || 'tool', !isRunning);
  const summary = toolUse.tool_input
    ? extractToolSummary(toolUse.tool_name || '', toolUse.tool_input as Record<string, any>)
    : undefined;

  const toggle = useCallback(() => setExpanded(prev => !prev), []);

  return (
    <div className="flex flex-col">
      <button
        onClick={toggle}
        className={clsx(
          "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs transition-all",
          "border max-w-full",
          isRunning
            ? "bg-[var(--accent-light)] border-[var(--accent-primary)]/30 text-[var(--accent-primary)]"
            : isError
              ? "bg-red-50 border-red-200 text-red-700 hover:bg-red-100 dark:bg-red-900/20 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-900/30"
              : "bg-[var(--bg-tertiary)] border-[var(--border-color)] text-[var(--text-secondary)] hover:bg-[var(--accent-light)] hover:border-[var(--accent-primary)]/30 hover:text-[var(--text-primary)]"
        )}
      >
        {isRunning ? (
          <Loader2 size={11} className="animate-spin flex-shrink-0" />
        ) : isError ? (
          <AlertTriangle size={11} className="flex-shrink-0" />
        ) : (
          <Wrench size={11} className="flex-shrink-0 opacity-60" />
        )}
        <span className="font-medium truncate">{displayName}</span>
        {summary && (
          <span className={clsx(
            "truncate max-w-[200px]",
            isError ? "opacity-70" : "opacity-50"
          )}>
            {summary}
          </span>
        )}
        {expanded ? (
          <ChevronDown size={11} className="flex-shrink-0 opacity-50" />
        ) : (
          <ChevronRight size={11} className="flex-shrink-0 opacity-50" />
        )}
      </button>

      {expanded && toolUse.tool_input && (
        <div className={clsx(
          "mt-1.5 ml-2 rounded-lg border p-2.5 text-xs animate-in",
          isError
            ? "bg-red-50/50 border-red-200 dark:bg-red-900/10 dark:border-red-800"
            : "bg-[var(--bg-secondary)] border-[var(--border-color)]"
        )}>
          <div className="space-y-1">
            {Object.entries(toolUse.tool_input).map(([key, value]) => (
              <div key={key} className="flex gap-2">
                <span className="font-mono text-[var(--text-muted)] flex-shrink-0">{key}:</span>
                <span className={clsx(
                  "font-mono break-all",
                  isError ? "text-red-700 dark:text-red-400" : "text-[var(--text-primary)]"
                )}>
                  {String(value).length > 200 ? String(value).slice(0, 200) + '...' : String(value)}
                </span>
              </div>
            ))}
          </div>

          {/* Tool result output */}
          {toolResult?.content && (
            <>
              <div className={clsx(
                "border-t my-2",
                isError ? "border-red-200 dark:border-red-800" : "border-[var(--border-color)]"
              )} />
              <div className={clsx(
                "font-mono whitespace-pre-wrap break-all",
                isError ? "text-red-600 dark:text-red-400" : "text-[var(--text-secondary)]"
              )}>
                {toolResult.content.length > 500
                  ? toolResult.content.slice(0, 500) + '...'
                  : toolResult.content}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// --- BlockRenderer: renders an array of blocks with tool_use/tool_result pairing ---

interface BlockRendererProps {
  blocks: ContentBlock[];
  onOpenFile?: (path: string) => void;
}

export const BlockRenderer: React.FC<BlockRendererProps> = React.memo(({ blocks, onOpenFile }) => {
  const elements: React.ReactNode[] = [];
  let i = 0;
  while (i < blocks.length) {
    const block = blocks[i];
    if (block.type === 'tool_use') {
      // Look ahead for matching tool_result
      const result = blocks.find(b =>
        b.type === 'tool_result' && b.tool_call_id === block.tool_call_id
      );
      // Tool chips render standalone — no bubble wrapper
      elements.push(
        <ToolChipBlock key={block.id} toolUse={block} toolResult={result} />
      );
      i++;
      // Skip the tool_result if it's the next block
      if (i < blocks.length && blocks[i].type === 'tool_result' && blocks[i].tool_call_id === block.tool_call_id) {
        i++;
      }
      continue;
    }
    if (block.type === 'tool_result') {
      // Orphaned result (shouldn't happen) — skip
      i++;
      continue;
    }
    if (block.type === 'thinking') {
      // Thinking renders standalone — no bubble wrapper
      elements.push(<ThinkingBlock key={block.id} block={block} />);
    } else if (block.type === 'text') {
      // Skip empty in-progress text blocks (prevents ghost bubble before first delta arrives)
      if (!block.content.trim() && block.status === 'in_progress') {
        i++;
        continue;
      }
      // Text blocks get the assistant bubble wrapper
      elements.push(
        <div key={block.id} className={clsx(
          "w-full bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-2xl rounded-bl-md px-4 py-3 shadow-warm",
          block.status === 'in_progress' && "border-[var(--accent-primary)]/30"
        )}>
          <TextBlockView block={block} onOpenFile={onOpenFile} />
        </div>
      );
    }
    i++;
  }
  return <div className="flex flex-col gap-3 w-full">{elements}</div>;
});

BlockRenderer.displayName = 'BlockRenderer';
