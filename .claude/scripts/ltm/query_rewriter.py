"""
Query Rewriter Agent

Transforms raw user messages into optimized semantic search queries
for better memory retrieval. Uses Haiku for speed with structured outputs.
"""

import logging
from typing import Optional
from pydantic import BaseModel, Field

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

logger = logging.getLogger(__name__)


class RewrittenQuery(BaseModel):
    """Structured output for query rewriting."""
    queries: list[str] = Field(
        description="1-3 semantic search queries optimized for vector retrieval",
        min_length=1,
        max_length=3
    )


REWRITER_SYSTEM_PROMPT = """You are a query rewriter for a semantic memory system.

Given a user message and recent conversation context, output search queries optimized for vector similarity search.

Rules:
1. Expand vague references ("that bug", "the thing", "it") into specific terms from context
2. Generate 1-3 queries covering different semantic angles of what the user needs
3. Use concrete nouns and verbs, not conversational filler ("um", "so", "like")
4. Extract the core intent - what information would be useful to recall?
5. Even for greetings or simple messages, generate queries about recent context or general state

Examples:
- User: "sync" → queries: ["daily sync workflow", "inbox processing routine", "task scheduling"]
- User: "what about the auth bug" (context mentions OAuth) → queries: ["OAuth token refresh bug", "authentication error fix"]
- User: "can you help with that?" (context: working on API) → queries: ["API implementation help", "current coding task"]
- User: "hey" → queries: ["current priorities", "recent work context"]
- User: "thanks!" → queries: ["recent task completed", "current work status"]
- User: "what's the status" → queries: ["current project status", "active tasks progress", "ongoing work"]

Always output valid JSON matching the schema."""


async def rewrite_query(
    user_message: str,
    conversation_context: Optional[list[dict]] = None
) -> RewrittenQuery:
    """
    Rewrite user message into optimized memory queries.

    Args:
        user_message: The raw user message
        conversation_context: List of recent messages with 'role' and 'content' keys

    Returns:
        RewrittenQuery with optimized queries
    """

    # Build context string from recent conversation
    context_str = "(no prior context)"
    if conversation_context:
        # Last 3 exchanges (6 messages: 3 user + 3 assistant)
        recent = conversation_context[-6:]
        context_parts = []
        for msg in recent:
            role = msg.get('role', 'user')
            content = msg.get('content', '')[:500]  # Truncate long messages
            context_parts.append(f"{role}: {content}")
        if context_parts:
            context_str = "\n".join(context_parts)

    prompt = f"""Recent conversation:
{context_str}

User message to rewrite:
{user_message}

Output optimized search queries for semantic memory retrieval."""

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="haiku",
                system_prompt=REWRITER_SYSTEM_PROMPT,
                output_format={
                    "type": "json_schema",
                    "schema": RewrittenQuery.model_json_schema()
                },
                max_turns=1  # Single-turn, no tools needed
            )
        ):
            if isinstance(message, ResultMessage):
                if message.structured_output:
                    result = RewrittenQuery.model_validate(message.structured_output)
                    logger.info(f"Query rewriter: '{user_message[:50]}' → {result.queries}")
                    return result
                elif message.is_error:
                    logger.warning(f"Query rewriter error: {message.result}")

    except Exception as e:
        logger.warning(f"Query rewriter failed: {e}")

    # Fallback: use original query
    logger.debug(f"Query rewriter: falling back to original query")
    return RewrittenQuery(queries=[user_message])
