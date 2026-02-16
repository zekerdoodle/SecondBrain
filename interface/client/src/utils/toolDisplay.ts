/**
 * Shared tool display utilities.
 * Used by Chat.tsx for status indicators and ToolCallChips for historical tool calls.
 */

// Tool display name mapping - maps raw tool names to human-friendly display names
export const TOOL_DISPLAY_NAMES: Record<string, string> = {
  // Claude Code built-in tools
  'Read': 'Reading file',
  'Write': 'Writing file',
  'Edit': 'Editing file',
  'Bash': 'Running command',
  'Glob': 'Searching files',
  'Grep': 'Searching content',
  'WebSearch': 'Searching the web',
  'WebFetch': 'Fetching page',
  'Task': 'Running task',
  'TodoWrite': 'Updating tasks',

  // MCP Brain tools - Agents
  'mcp__brain__invoke_agent': 'Invoking agent',
  'mcp__brain__invoke_agent_chain': 'Running agent chain',
  'mcp__brain__schedule_agent': 'Scheduling agent',

  // MCP Brain tools - Google Calendar/Tasks
  'mcp__brain__google_create_tasks_and_events': 'Creating task/event',
  'mcp__brain__google_list': 'Checking calendar/tasks',
  'mcp__brain__google_delete_task': 'Deleting task',
  'mcp__brain__google_update_task': 'Updating task',

  // MCP Brain tools - Gmail
  'mcp__brain__gmail_list_messages': 'Checking email',
  'mcp__brain__gmail_get_message': 'Reading email',
  'mcp__brain__gmail_send': 'Sending email',
  'mcp__brain__gmail_reply': 'Replying to email',
  'mcp__brain__gmail_list_labels': 'Listing labels',
  'mcp__brain__gmail_modify_labels': 'Modifying labels',
  'mcp__brain__gmail_trash': 'Moving to trash',
  'mcp__brain__gmail_draft_create': 'Creating draft',

  // MCP Brain tools - YouTube Music
  'mcp__brain__ytmusic_get_playlists': 'Getting playlists',
  'mcp__brain__ytmusic_get_playlist_items': 'Getting playlist items',
  'mcp__brain__ytmusic_get_liked': 'Getting liked songs',
  'mcp__brain__ytmusic_search': 'Searching YouTube Music',
  'mcp__brain__ytmusic_create_playlist': 'Creating playlist',
  'mcp__brain__ytmusic_add_to_playlist': 'Adding to playlist',
  'mcp__brain__ytmusic_remove_from_playlist': 'Removing from playlist',
  'mcp__brain__ytmusic_delete_playlist': 'Deleting playlist',

  // MCP Brain tools - Spotify
  'mcp__brain__spotify_auth_start': 'Starting Spotify auth',
  'mcp__brain__spotify_auth_callback': 'Completing Spotify auth',
  'mcp__brain__spotify_recently_played': 'Getting recent plays',
  'mcp__brain__spotify_top_items': 'Getting top items',
  'mcp__brain__spotify_search': 'Searching Spotify',
  'mcp__brain__spotify_get_playlists': 'Getting playlists',
  'mcp__brain__spotify_create_playlist': 'Creating playlist',
  'mcp__brain__spotify_add_to_playlist': 'Adding to playlist',
  'mcp__brain__spotify_now_playing': 'Checking Spotify',
  'mcp__brain__spotify_playback_control': 'Controlling playback',

  // MCP Brain tools - Scheduler
  'mcp__brain__schedule_self': 'Scheduling reminder',
  'mcp__brain__scheduler_list': 'Listing scheduled tasks',
  'mcp__brain__scheduler_update': 'Updating scheduled task',
  'mcp__brain__scheduler_remove': 'Removing scheduled task',

  // MCP Brain tools - Journal Memory
  'mcp__brain__memory_append': 'Saving to memory',
  'mcp__brain__memory_read': 'Reading memory',

  // MCP Brain tools - Working Memory
  'mcp__brain__working_memory_add': 'Adding note',
  'mcp__brain__working_memory_update': 'Updating note',
  'mcp__brain__working_memory_remove': 'Removing note',
  'mcp__brain__working_memory_list': 'Checking notes',
  'mcp__brain__working_memory_snapshot': 'Getting memory snapshot',

  // MCP Brain tools - Long-Term Memory (LTM)
  'mcp__brain__ltm_search': 'Searching memory',
  'mcp__brain__ltm_get_context': 'Getting context',
  'mcp__brain__ltm_add_memory': 'Storing memory',
  'mcp__brain__ltm_create_thread': 'Creating memory thread',
  'mcp__brain__ltm_stats': 'Getting memory stats',
  'mcp__brain__ltm_process_now': 'Processing memory',
  'mcp__brain__ltm_run_gardener': 'Running memory gardener',
  'mcp__brain__ltm_buffer_exchange': 'Exchanging memory buffer',
  'mcp__brain__ltm_backfill': 'Backfilling memory',

  // MCP Brain tools - Utilities
  'mcp__brain__page_parser': 'Reading page',
  'mcp__brain__restart_server': 'Restarting server',
  'mcp__brain__claude_code': 'Running Claude Code',
  'mcp__brain__web_search': 'Searching the web',
  'mcp__brain__send_critical_notification': 'Sending notification',

  // MCP Brain tools - Bash
  'mcp__brain__bash': 'Running command',

  // MCP Brain tools - Forms
  'mcp__brain__forms_define': 'Creating form',
  'mcp__brain__forms_show': 'Showing form',
  'mcp__brain__forms_save': 'Saving form',
  'mcp__brain__forms_list': 'Listing forms',

  // MCP Brain tools - Moltbook (AI social platform)
  'mcp__brain__moltbook_feed': 'Browsing Moltbook',
  'mcp__brain__moltbook_post': 'Posting to Moltbook',
  'mcp__brain__moltbook_comment': 'Commenting on Moltbook',
  'mcp__brain__moltbook_get_post': 'Reading Moltbook post',
  'mcp__brain__moltbook_notifications': 'Checking Moltbook notifications',

  // MCP Brain tools - Chess
  'mcp__brain__chess': 'Playing chess',

  // MCP Brain tools - Image generation
  'mcp__brain__fal_text_to_image': 'Generating image',
  'mcp__brain__fal_list_models': 'Listing AI models',
};

// Get a human-friendly display name for a tool
// First tries exact match, then falls back to substring matching
export const getToolDisplayName = (toolName: string): string => {
  // First, try exact match (handles MCP tools with full names)
  if (TOOL_DISPLAY_NAMES[toolName]) {
    return TOOL_DISPLAY_NAMES[toolName];
  }

  // Then try substring match (handles simple names like 'Read' matching 'Reading')
  const lowerToolName = toolName.toLowerCase();
  for (const [key, displayName] of Object.entries(TOOL_DISPLAY_NAMES)) {
    // Skip MCP-prefixed entries for substring matching to avoid false positives
    if (key.startsWith('mcp__')) continue;
    if (lowerToolName.includes(key.toLowerCase())) {
      return displayName;
    }
  }

  // Final fallback: clean up the raw name for display
  // Handle mcp__brain__ prefix by extracting the action
  if (lowerToolName.startsWith('mcp__brain__')) {
    const action = toolName.replace(/^mcp__brain__/, '').replace(/_/g, ' ');
    // Capitalize first letter
    return action.charAt(0).toUpperCase() + action.slice(1);
  }

  return 'Running tool';
};

// --- Tool summary extraction (also used by useClaude.ts for streaming) ---

const MAX_SUMMARY_LENGTH = 60;

function truncateSummary(s: string, maxLen: number = MAX_SUMMARY_LENGTH): string {
  if (s.length <= maxLen) return s;
  return s.slice(0, maxLen - 3) + '...';
}

function extractBasename(path: string): string {
  const parts = path.split('/');
  return parts[parts.length - 1] || path;
}

export function extractToolSummary(toolName: string, argsJson: string | Record<string, any>): string {
  try {
    const args = typeof argsJson === 'string' ? JSON.parse(argsJson) : argsJson;
    if (!args || typeof args !== 'object') return '';

    const name = toolName.toLowerCase();

    // Agent invocations
    if (name.includes('invoke_agent') || name === 'task') {
      return truncateSummary(args.agent_name || args.agent || args.description || args.prompt?.slice(0, 50) || '');
    }

    // Bash / command execution
    if (name === 'bash' || name.includes('bash')) {
      const cmd = args.command || '';
      return truncateSummary(cmd.split('\n')[0]);
    }

    // File operations
    if (['read', 'write', 'edit'].includes(name)) {
      const path = args.file_path || args.path || args.filePath || '';
      return truncateSummary(extractBasename(path));
    }

    // Search tools
    if (name === 'glob' || name.includes('glob')) {
      return truncateSummary(args.pattern || '');
    }
    if (name === 'grep' || name.includes('grep')) {
      return truncateSummary(args.pattern || '');
    }

    // Web tools
    if (name === 'websearch' || name.includes('web_search') || name.includes('search')) {
      return truncateSummary(args.query || args.search_query || '');
    }
    if (name === 'webfetch' || name.includes('page_parser') || name.includes('fetch')) {
      try {
        return truncateSummary(new URL(args.url || '').hostname);
      } catch {
        return truncateSummary(args.url || '');
      }
    }

    // Gmail tools
    if (name.includes('gmail_send') || name.includes('gmail_reply')) {
      return truncateSummary(args.to || args.subject || '');
    }
    if (name.includes('gmail_get') || name.includes('gmail_list')) {
      return truncateSummary(args.query || args.label || '');
    }

    // Memory tools
    if (name.includes('ltm_search') || name.includes('memory')) {
      return truncateSummary(args.query || args.content?.slice(0, 50) || '');
    }

    // Claude Code
    if (name.includes('claude_code')) {
      return truncateSummary(args.prompt?.slice(0, 50) || args.command?.slice(0, 50) || '');
    }

    // Calendar/task tools
    if (name.includes('google_create') || name.includes('google_list')) {
      return truncateSummary(args.title || args.query || '');
    }

    // Scheduler
    if (name.includes('schedule')) {
      return truncateSummary(args.description || args.task_name || '');
    }

    // Generic fallback: first short string-valued arg
    for (const value of Object.values(args)) {
      if (typeof value === 'string' && value.length > 0 && value.length < 200) {
        return truncateSummary(value);
      }
    }

    return '';
  } catch {
    return '';
  }
}
