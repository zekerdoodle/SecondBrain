import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ChevronDown, ChevronRight, Brain, Wrench, AlertTriangle, Loader2 } from 'lucide-react';
import { clsx } from 'clsx';
import MDEditor from '@uiw/react-md-editor';
import { escapeNonHtmlTags } from '../utils/escapeNonHtmlTags';
import {
  getToolDisplayName,
  extractToolSummary,
  formatToolParameterValue,
  recoverToolArgsForDisplay,
} from '../utils/toolDisplay';
import type { ContentBlock } from '../types';

const BLOCK_TYPES = new Set(['thinking', 'text', 'tool_use', 'tool_result']);

const isRecord = (value: unknown): value is Record<string, any> =>
  typeof value === 'object' && value !== null;

const coerceToolInput = (value: unknown): Record<string, unknown> | undefined => {
  if (isRecord(value) && !Array.isArray(value)) return value;
  if (typeof value === 'string') {
    if (!value) return undefined;
    try {
      const parsed = JSON.parse(value);
      return isRecord(parsed) && !Array.isArray(parsed) ? parsed : { raw: parsed };
    } catch {
      return { raw: value };
    }
  }
  return value === undefined || value === null ? undefined : { raw: value };
};

const normalizeBlockForRender = (value: unknown, fallbackId: string): ContentBlock | null => {
  if (!isRecord(value) || typeof value.type !== 'string' || !BLOCK_TYPES.has(value.type)) {
    return null;
  }

  const id = typeof value.id === 'string' && value.id ? value.id : fallbackId;
  const toolName = typeof value.tool_name === 'string'
    ? value.tool_name
    : typeof value.name === 'string'
      ? value.name
      : undefined;
  const toolInput = coerceToolInput(value.tool_input ?? value.input);
  const toolCallId = typeof value.tool_call_id === 'string'
    ? value.tool_call_id
    : value.type === 'tool_use'
      ? id
      : undefined;

  return {
    id,
    type: value.type as ContentBlock['type'],
    content: typeof value.content === 'string' ? value.content : '',
    status: value.status === 'in_progress' ? 'in_progress' : 'complete',
    tool_name: toolName,
    tool_call_id: toolCallId,
    tool_input: toolInput,
    is_error: typeof value.is_error === 'boolean' ? value.is_error : undefined,
    raw_output: isRecord(value.raw_output) ? value.raw_output : undefined,
    started_at: typeof value.started_at === 'number' ? value.started_at : undefined,
    duration_ms: typeof value.duration_ms === 'number' ? value.duration_ms : undefined,
  };
};

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
            if (isInline && looksLikeFilePath(text)) {
              const relativePath = toRelativePath(text);
              if (isImagePath(relativePath)) {
                return (
                  <InlineFilePathImage
                    path={relativePath}
                    originalText={text}
                    onOpenFile={onOpenFile}
                  />
                );
              }
              if (isVideoPath(relativePath)) {
                return (
                  <InlineFilePathVideo
                    path={relativePath}
                    originalText={text}
                    onOpenFile={onOpenFile}
                  />
                );
              }
              if (onOpenFile) {
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
            }
            return <code className={className} {...props}>{children}</code>;
          },
          img: ({ src, alt, ...props }) => {
            if (src && looksLikeFilePath(src)) {
              const relativePath = toRelativePath(src);
              if (isImagePath(relativePath)) {
                return (
                  <InlineFilePathImage
                    path={relativePath}
                    originalText={src}
                    alt={alt}
                    onOpenFile={onOpenFile}
                  />
                );
              }
              if (isVideoPath(relativePath)) {
                return (
                  <InlineFilePathVideo
                    path={relativePath}
                    originalText={src}
                    onOpenFile={onOpenFile}
                  />
                );
              }
            }
            return <img src={src} alt={alt} {...props} />;
          }
        }}
      />
    </div>
  );
}

// File path detection — shared utility (no more duplication!)
import { looksLikeFilePath, toRelativePath, isImagePath, isVideoPath } from '../utils/filePaths';
import { InlineFilePathImage } from './InlineFilePathImage';
import { InlineFilePathVideo } from './InlineFilePathVideo';

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
  const displayInput = recoverToolArgsForDisplay(
    toolUse.tool_name || '',
    toolUse.tool_input as Record<string, any> | undefined,
    toolResult?.content
  );
  const hasDisplayInput = Object.keys(displayInput).length > 0;
  const summary = hasDisplayInput
    ? extractToolSummary(toolUse.tool_name || '', displayInput)
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

      {expanded && (hasDisplayInput || toolResult?.content) && (
        <div className={clsx(
          "mt-1.5 ml-2 rounded-lg border p-2.5 text-xs animate-in",
          isError
            ? "bg-red-50/50 border-red-200 dark:bg-red-900/10 dark:border-red-800"
            : "bg-[var(--bg-secondary)] border-[var(--border-color)]"
        )}>
          {hasDisplayInput && (
            <div className="space-y-1">
              {Object.entries(displayInput).map(([key, value]) => (
                <div key={key} className="flex gap-2">
                  <span className="font-mono text-[var(--text-muted)] flex-shrink-0">{key}:</span>
                  <span className={clsx(
                    "font-mono break-all",
                    isError ? "text-red-700 dark:text-red-400" : "text-[var(--text-primary)]"
                  )}>
                    {formatToolParameterValue(
                      value,
                      ['agents', 'chain', 'context'].includes(key) || key.includes('prompt') ? 600 : 200
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Tool result output */}
          {toolResult?.content && (
            <>
              {hasDisplayInput && (
                <div className={clsx(
                  "border-t my-2",
                  isError ? "border-red-200 dark:border-red-800" : "border-[var(--border-color)]"
                )} />
              )}
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
  const renderBlocks = blocks
    .map((block, index) => normalizeBlockForRender(block, `block-${index}`))
    .filter((block): block is ContentBlock => block !== null);
  const elements: React.ReactNode[] = [];
  let i = 0;
  while (i < renderBlocks.length) {
    const block = renderBlocks[i];
    if (block.type === 'tool_use') {
      // Look ahead for matching tool_result
      const result = renderBlocks.find(b =>
        b.type === 'tool_result' && b.tool_call_id === block.tool_call_id
      );
      // Tool chips render standalone — no bubble wrapper
      elements.push(
        <ToolChipBlock key={block.id} toolUse={block} toolResult={result} />
      );
      i++;
      // Skip the tool_result if it's the next block
      if (i < renderBlocks.length && renderBlocks[i].type === 'tool_result' && renderBlocks[i].tool_call_id === block.tool_call_id) {
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
