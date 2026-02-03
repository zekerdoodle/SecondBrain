/**
 * Slash Command Registry
 * Defines available chat commands and their handlers
 */

export type CommandExecuteResult = {
  /** If true, the command has been fully handled (don't do anything else) */
  handled: boolean;
  /** If set, replace the input with this value */
  replaceInput?: string;
  /** If set, send this as a message to Claude */
  sendMessage?: string;
  /** If true, show a confirmation dialog first */
  requiresConfirmation?: {
    title: string;
    message: string;
    confirmLabel?: string;
    destructive?: boolean;
    onConfirm: () => void;
  };
};

export interface Command {
  /** The slash command name (without the /) */
  name: string;
  /** Brief description shown in the palette */
  description: string;
  /** Longer description or usage hint */
  hint?: string;
  /** Whether command takes arguments */
  hasArgs?: boolean;
  /** Placeholder text for arguments */
  argsPlaceholder?: string;
  /** Execute the command */
  execute: (args: string, context: CommandContext) => CommandExecuteResult;
}

export interface CommandContext {
  /** Function to start a new chat */
  startNewChat: () => void;
  /** Function to send a message */
  sendMessage: (msg: string) => void;
  /** Current input value */
  input: string;
  /** Set input value */
  setInput: (value: string) => void;
  /** Show history view */
  showHistory: () => void;
}

/**
 * Built-in commands
 */
export const commands: Command[] = [
  {
    name: 'help',
    description: 'Show available commands',
    execute: () => ({
      handled: true,
      sendMessage: '/help',
    }),
  },
  {
    name: 'new',
    description: 'Create a new file',
    hint: 'Usage: /new filename.md',
    hasArgs: true,
    argsPlaceholder: 'filename',
    execute: (args) => {
      if (!args.trim()) {
        return {
          handled: false,
          replaceInput: '/new ',
        };
      }
      return {
        handled: true,
        sendMessage: `/new ${args.trim()}`,
      };
    },
  },
  {
    name: 'search',
    description: 'Search files',
    hint: 'Usage: /search query',
    hasArgs: true,
    argsPlaceholder: 'query',
    execute: (args) => {
      if (!args.trim()) {
        return {
          handled: false,
          replaceInput: '/search ',
        };
      }
      return {
        handled: true,
        sendMessage: `/search ${args.trim()}`,
      };
    },
  },
  {
    name: 'clear',
    description: 'Clear chat history',
    hint: 'Starts a fresh conversation',
    execute: (_args, context) => ({
      handled: true,
      requiresConfirmation: {
        title: 'Clear Chat',
        message: 'Start a new conversation? Current chat will be saved to history.',
        confirmLabel: 'Clear',
        destructive: false,
        onConfirm: () => context.startNewChat(),
      },
    }),
  },
  {
    name: 'skills',
    description: 'List available skills',
    hint: 'Shows workflows and agents',
    execute: () => ({
      handled: true,
      sendMessage: '/skills',
    }),
  },
  {
    name: 'sync',
    description: 'Trigger morning/evening sync',
    hint: 'Run the daily sync workflow',
    execute: () => ({
      handled: true,
      sendMessage: '/sync',
    }),
  },
  {
    name: 'history',
    description: 'View conversation history',
    execute: (_args, context) => {
      context.showHistory();
      return { handled: true };
    },
  },
  {
    name: 'agents',
    description: 'List available agents',
    execute: () => ({
      handled: true,
      sendMessage: '/agents',
    }),
  },
  {
    name: 'inbox',
    description: 'Process inbox items',
    execute: () => ({
      handled: true,
      sendMessage: '/inbox',
    }),
  },
  {
    name: 'status',
    description: 'Check system status',
    execute: () => ({
      handled: true,
      sendMessage: '/status',
    }),
  },
];

/**
 * Simple fuzzy matching for command search
 */
export function fuzzyMatch(query: string, target: string): { match: boolean; score: number; indices: number[] } {
  const queryLower = query.toLowerCase();
  const targetLower = target.toLowerCase();

  if (!query) {
    return { match: true, score: 1, indices: [] };
  }

  // Check if query is a substring (highest priority)
  const substringIndex = targetLower.indexOf(queryLower);
  if (substringIndex !== -1) {
    const indices = Array.from({ length: query.length }, (_, i) => substringIndex + i);
    // Boost score if it's a prefix match
    const score = substringIndex === 0 ? 2 : 1.5;
    return { match: true, score, indices };
  }

  // Fuzzy match: characters must appear in order
  let queryIdx = 0;
  const indices: number[] = [];

  for (let i = 0; i < target.length && queryIdx < query.length; i++) {
    if (targetLower[i] === queryLower[queryIdx]) {
      indices.push(i);
      queryIdx++;
    }
  }

  if (queryIdx === query.length) {
    // Score based on how "tight" the match is
    const spread = indices.length > 1 ? indices[indices.length - 1] - indices[0] : 0;
    const score = 1 - (spread / target.length) * 0.5;
    return { match: true, score, indices };
  }

  return { match: false, score: 0, indices: [] };
}

/**
 * Search commands with fuzzy matching
 */
export function searchCommands(query: string): Array<Command & { matchIndices: number[] }> {
  const normalizedQuery = query.replace(/^\//, '').toLowerCase();

  const results = commands
    .map(cmd => {
      const { match, score, indices } = fuzzyMatch(normalizedQuery, cmd.name);
      return { ...cmd, match, score, matchIndices: indices };
    })
    .filter(cmd => cmd.match)
    .sort((a, b) => b.score - a.score);

  return results;
}

/**
 * Parse command from input string
 */
export function parseCommand(input: string): { command: Command | null; args: string } {
  const match = input.match(/^\/(\S+)(?:\s+(.*))?$/);
  if (!match) {
    return { command: null, args: '' };
  }

  const [, name, args = ''] = match;
  const command = commands.find(cmd => cmd.name.toLowerCase() === name.toLowerCase());

  return { command: command || null, args };
}

/**
 * Check if input starts with / (potential command)
 */
export function isCommandInput(input: string): boolean {
  return input.trimStart().startsWith('/');
}

/**
 * Get the command prefix being typed (everything after /)
 */
export function getCommandQuery(input: string): string {
  const trimmed = input.trimStart();
  if (!trimmed.startsWith('/')) return '';

  // If there's a space, user has finished typing command name
  const spaceIdx = trimmed.indexOf(' ');
  if (spaceIdx !== -1) return '';

  return trimmed.slice(1);
}
