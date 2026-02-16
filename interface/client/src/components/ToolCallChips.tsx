import React, { useState, useCallback } from 'react';
import { Wrench, ChevronDown, ChevronRight, AlertTriangle } from 'lucide-react';
import { clsx } from 'clsx';
import { getToolDisplayName, extractToolSummary } from '../utils/toolDisplay';

export interface ToolCallData {
  id: string;
  tool_name: string;
  tool_id: string;
  args: Record<string, any>;
  output_summary?: string;
  is_error?: boolean;
}

interface ToolCallChipsProps {
  toolCalls: ToolCallData[];
}

/** Format args for display — show key info, skip huge values */
function formatArgs(args: Record<string, any>): { key: string; value: string }[] {
  if (!args || typeof args !== 'object') return [];

  return Object.entries(args).map(([key, value]) => {
    let display: string;
    if (typeof value === 'string') {
      display = value.length > 200 ? value.slice(0, 200) + '…' : value;
    } else if (typeof value === 'object' && value !== null) {
      const json = JSON.stringify(value);
      display = json.length > 200 ? json.slice(0, 200) + '…' : json;
    } else {
      display = String(value);
    }
    return { key, value: display };
  });
}

const ToolChip: React.FC<{ tool: ToolCallData }> = ({ tool }) => {
  const [expanded, setExpanded] = useState(false);
  const displayName = getToolDisplayName(tool.tool_name);
  const summary = extractToolSummary(tool.tool_name, tool.args);
  const isError = tool.is_error;

  const toggle = useCallback(() => setExpanded(prev => !prev), []);

  return (
    <div className="flex flex-col">
      <button
        onClick={toggle}
        className={clsx(
          "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs transition-all",
          "border max-w-full",
          isError
            ? "bg-red-50 border-red-200 text-red-700 hover:bg-red-100 dark:bg-red-900/20 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-900/30"
            : "bg-[var(--bg-tertiary)] border-[var(--border-color)] text-[var(--text-secondary)] hover:bg-[var(--accent-light)] hover:border-[var(--accent-primary)]/30 hover:text-[var(--text-primary)]"
        )}
      >
        {isError ? (
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

      {expanded && (
        <div className={clsx(
          "mt-1.5 ml-2 rounded-lg border p-2.5 text-xs animate-in",
          isError
            ? "bg-red-50/50 border-red-200 dark:bg-red-900/10 dark:border-red-800"
            : "bg-[var(--bg-secondary)] border-[var(--border-color)]"
        )}>
          {/* Args */}
          <div className="space-y-1">
            {formatArgs(tool.args).map(({ key, value }) => (
              <div key={key} className="flex gap-2">
                <span className="font-mono text-[var(--text-muted)] flex-shrink-0">{key}:</span>
                <span className={clsx(
                  "font-mono break-all",
                  isError ? "text-red-700 dark:text-red-400" : "text-[var(--text-primary)]"
                )}>
                  {value}
                </span>
              </div>
            ))}
          </div>

          {/* Output summary */}
          {tool.output_summary && (
            <>
              <div className={clsx(
                "border-t my-2",
                isError ? "border-red-200 dark:border-red-800" : "border-[var(--border-color)]"
              )} />
              <div className={clsx(
                "font-mono whitespace-pre-wrap break-all",
                isError ? "text-red-600 dark:text-red-400" : "text-[var(--text-secondary)]"
              )}>
                {tool.output_summary.length > 500
                  ? tool.output_summary.slice(0, 500) + '…'
                  : tool.output_summary}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};

export const ToolCallChips: React.FC<ToolCallChipsProps> = React.memo(({ toolCalls }) => {
  if (toolCalls.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5 pl-0 mt-1 mb-1 animate-in">
      {toolCalls.map(tool => (
        <ToolChip key={tool.tool_id || tool.id} tool={tool} />
      ))}
    </div>
  );
});

ToolCallChips.displayName = 'ToolCallChips';
