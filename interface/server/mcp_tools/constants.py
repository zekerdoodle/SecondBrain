"""
MCP Tool Constants

Provides tool name constants and category mappings for:
- Primary agent configuration
- Subagent tool validation
- Registry initialization
"""

# MCP server name prefix
MCP_SERVER_NAME = "brain"
MCP_PREFIX = f"mcp__{MCP_SERVER_NAME}__"


def mcp_name(tool_name: str) -> str:
    """Convert internal tool name to MCP tool name."""
    return f"{MCP_PREFIX}{tool_name}"


# =============================================================================
# Tool Categories - Maps category names to internal tool names
# =============================================================================

GOOGLE_TOOLS = [
    "google_create_tasks_and_events",
    "google_list",
    "google_delete_task",
    "google_update_task",
    "google_get_event",
    "google_update_event",
    "google_delete_event",
    "google_auth",
]

GMAIL_TOOLS = [
    "gmail_list_messages",
    "gmail_get_message",
    "gmail_send",
    "gmail_reply",
    "gmail_list_labels",
    "gmail_modify_labels",
    "gmail_trash",
    "gmail_draft_create",
]

YOUTUBE_TOOLS = [
    "ytmusic_get_playlists",
    "ytmusic_get_playlist_items",
    "ytmusic_get_liked",
    "ytmusic_search",
    "ytmusic_create_playlist",
    "ytmusic_add_to_playlist",
    "ytmusic_remove_from_playlist",
    "ytmusic_delete_playlist",
]

SPOTIFY_TOOLS = [
    "spotify_auth_start",
    "spotify_auth_callback",
    "spotify_recently_played",
    "spotify_top_items",
    "spotify_search",
    "spotify_get_playlists",
    "spotify_create_playlist",
    "spotify_add_to_playlist",
    "spotify_now_playing",
    "spotify_playback_control",
]

FINANCE_TOOLS = [
    "finance_accounts",
    "finance_transactions",
    "finance_spending_analysis",
    "finance_connect",
    "finance_disconnect",
    "finance_status",
]

SCHEDULER_TOOLS = [
    "schedule_self",
    "scheduler_list",
    "scheduler_update",
    "scheduler_remove",
]

# Memory category includes journal, working memory, and LTM
JOURNAL_TOOLS = [
    "memory_append",
    "memory_read",
]

WORKING_MEMORY_TOOLS = [
    "working_memory_add",
    "working_memory_update",
    "working_memory_remove",
    "working_memory_list",
    "working_memory_snapshot",
]

LTM_TOOLS = [
    "ltm_search",
    "ltm_get_context",
    "ltm_add_memory",
    "ltm_create_thread",
    "ltm_stats",
    "ltm_process_now",
    "ltm_run_gardener",
    "ltm_buffer_exchange",
    "ltm_backfill",
    "ltm_backfill_threads",
]

# Gardener-only tools (NOT available to Primary Claude)
GARDENER_TOOLS = [
    "gardener_search_threads",
    "gardener_get_thread_detail",
    "gardener_assign_atom",
    "gardener_update_atom",
    "gardener_create_thread",
    "gardener_split_thread",
    "gardener_merge_threads",
]

# Combined memory category (excludes gardener tools — those are agent-only)
MEMORY_TOOLS = JOURNAL_TOOLS + WORKING_MEMORY_TOOLS + LTM_TOOLS

UTILITY_TOOLS = [
    "page_parser",
    "restart_server",
    "claude_code",
    "web_search",
    "send_critical_notification",
    "process_list",
    "compact_conversation",
]

AGENT_TOOLS = [
    "invoke_agent",
    "invoke_agent_chain",
    "invoke_agent_parallel",
    "schedule_agent",
]

BASH_TOOLS = [
    "bash",
]

FORMS_TOOLS = [
    "forms_define",
    "forms_show",
    "forms_save",
    "forms_list",
]

MOLTBOOK_TOOLS = [
    "moltbook_feed",
    "moltbook_post",
    "moltbook_comment",
    "moltbook_get_post",
    "moltbook_notifications",
    "moltbook_account_status",
    "moltbook_check_dms",
    "moltbook_respond_challenge",
    "moltbook_challenge_log",
]


LLM_TOOLS = [
    "consult_llm",
]

CHESS_TOOLS = [
    "chess",
]

IMAGE_TOOLS = [
    "fal_text_to_image",
    "fal_image_to_image",
    "fal_multi_ref_image",
    "fal_list_models",
]

SKILLS_TOOLS = [
    "fetch_skill",
]


# =============================================================================
# Category Mapping - Maps category names to tool lists
# =============================================================================

TOOL_CATEGORIES = {
    "google": GOOGLE_TOOLS,
    "gmail": GMAIL_TOOLS,
    "youtube": YOUTUBE_TOOLS,
    "spotify": SPOTIFY_TOOLS,
    "finance": FINANCE_TOOLS,
    "scheduler": SCHEDULER_TOOLS,
    "memory": MEMORY_TOOLS,
    "journal": JOURNAL_TOOLS,           # Subcategory of memory
    "working_memory": WORKING_MEMORY_TOOLS,  # Subcategory of memory
    "ltm": LTM_TOOLS,                   # Subcategory of memory
    "gardener": GARDENER_TOOLS,          # Gardener-only (NOT in primary agent config)
    "utilities": UTILITY_TOOLS,
    "agents": AGENT_TOOLS,
    "bash": BASH_TOOLS,
    "forms": FORMS_TOOLS,
    "moltbook": MOLTBOOK_TOOLS,
    "llm": LLM_TOOLS,
    "chess": CHESS_TOOLS,
    "image": IMAGE_TOOLS,
    "skills": SKILLS_TOOLS,
}


# =============================================================================
# All Tools - For validation
# =============================================================================

ALL_TOOL_NAMES = (
    GOOGLE_TOOLS +
    GMAIL_TOOLS +
    YOUTUBE_TOOLS +
    SPOTIFY_TOOLS +
    FINANCE_TOOLS +
    SCHEDULER_TOOLS +
    MEMORY_TOOLS +
    GARDENER_TOOLS +
    UTILITY_TOOLS +
    AGENT_TOOLS +
    BASH_TOOLS +
    FORMS_TOOLS +
    MOLTBOOK_TOOLS +
    CHESS_TOOLS +
    IMAGE_TOOLS +
    SKILLS_TOOLS
)

# Set for O(1) lookup
ALL_TOOL_NAMES_SET = set(ALL_TOOL_NAMES)

# MCP tool names (with mcp__brain__ prefix)
ALL_MCP_TOOLS = {mcp_name(t) for t in ALL_TOOL_NAMES}


# =============================================================================
# Helper Functions
# =============================================================================

def get_mcp_tools_for_categories(categories: list[str]) -> list[str]:
    """
    Get MCP tool names for the given categories.

    Args:
        categories: List of category names (e.g., ["google", "scheduler"])

    Returns:
        List of MCP tool names (e.g., ["mcp__brain__google_list", ...])
    """
    tools = []
    for category in categories:
        if category in TOOL_CATEGORIES:
            tools.extend([mcp_name(t) for t in TOOL_CATEGORIES[category]])
    return tools


def is_valid_mcp_tool(tool_name: str) -> bool:
    """
    Check if a tool name is a valid MCP tool.

    Args:
        tool_name: MCP tool name (e.g., "mcp__brain__google_list")

    Returns:
        True if valid, False otherwise
    """
    return tool_name in ALL_MCP_TOOLS


def get_category_for_tool(tool_name: str) -> str | None:
    """
    Get the category for a tool name.

    Args:
        tool_name: Internal tool name (e.g., "google_list")

    Returns:
        Category name or None if not found
    """
    for category, tools in TOOL_CATEGORIES.items():
        if tool_name in tools:
            # Return the primary category (not subcategories)
            if category in ("journal", "working_memory", "ltm"):
                return "memory"
            return category
    return None
