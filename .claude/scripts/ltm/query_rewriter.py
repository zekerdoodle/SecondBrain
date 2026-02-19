"""
Query Rewriter Agent

Transforms raw user messages into optimized semantic search queries
for better memory retrieval. Uses Haiku for speed with structured outputs.
"""

import logging
from typing import Optional
from pydantic import BaseModel, Field

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage, ToolUseBlock

logger = logging.getLogger(__name__)


class QueryItem(BaseModel):
    """A single search query with its own weight."""
    text: str = Field(description="Semantic search query text")
    weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Relative importance weight for budget allocation"
    )


class RewrittenQuery(BaseModel):
    """Structured output for query rewriting."""
    queries: list[QueryItem] = Field(
        description="1-5 search queries, each with weight",
        min_length=1,
        max_length=5
    )


REWRITER_SYSTEM_PROMPT = """You are a query rewriter for a semantic memory system.

Given a user message and recent conversation context, output structured search queries optimized for vector similarity search. Each query targets a distinct topic/concept and carries its own weight.

Rules:
1. Identify distinct topics or concepts in the user message
2. Generate a SEPARATE query for each topic (1-5 queries)
3. Expand vague references ("that bug", "the thing", "it") into specific terms from context
4. Use concrete nouns and verbs, not conversational filler
5. Even for greetings or simple messages, generate queries about recent context or general state
6. PRESERVE distinctive phrases verbatim. If the user uses an unusual phrase, a direct quote, or a specific term that looks like it refers to something concrete (e.g. "cumming helpfulness", "the vibe shift", "sparkle mode"), use it EXACTLY as-is in a query. Do NOT paraphrase, expand with synonyms, or dilute it. These phrases are the best possible search terms because they'll match the original text in memory.

Per-query fields:
- text: The search query (concrete terms for vector search)
- weight: Relative importance (0.0-1.0). Weights should reflect apparent importance. Start with equal weights if unsure. They don't need to sum to 1.0.

Examples:
- User: "sync" →
  queries: [{"text": "daily sync workflow", "weight": 0.5}, {"text": "inbox processing routine", "weight": 0.5}]

- User: "Remember the austin move and cumming helpfulness" →
  queries: [{"text": "austin move", "weight": 0.5}, {"text": "cumming helpfulness", "weight": 0.5}]
  (Note: "cumming helpfulness" is a distinctive phrase — kept verbatim, NOT expanded to "helpfulness satisfaction pleasure")

- User: "what's my favorite color" →
  queries: [{"text": "favorite color preference", "weight": 1.0}]

- User: "what happened today with the deploy" →
  queries: [{"text": "today deployment release", "weight": 0.7}, {"text": "deployment pipeline CI/CD process", "weight": 0.3}]

- User: "tell me about the vibe shift and how the project architecture evolved" →
  queries: [{"text": "vibe shift", "weight": 0.5}, {"text": "project architecture evolution", "weight": 0.5}]
  (Note: "vibe shift" is distinctive — kept verbatim. "project architecture evolved" is generic — expanded for search)

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
        structured_data = None
        result = None

        # IMPORTANT: Must fully consume the async generator to avoid anyio cancel scope
        # errors. Returning early from `async for ... in query()` causes generator GC
        # on a different task, which crashes the cancel scope and can disrupt subsequent
        # SDK calls in the same event loop.
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
            # SDK bug workaround: structured output arrives as a StructuredOutput
            # tool use block in AssistantMessage, not in ResultMessage.structured_output
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock) and block.name == "StructuredOutput":
                        structured_data = block.input

            if isinstance(message, ResultMessage):
                # Prefer ResultMessage.structured_output if SDK ever fixes this
                data = message.structured_output or structured_data
                if data:
                    result = RewrittenQuery.model_validate(data)
                    query_summary = [(q.text, f"w={q.weight}") for q in result.queries]
                    logger.info(f"Query rewriter: '{user_message[:50]}' → {query_summary}")
                elif message.is_error:
                    logger.warning(f"Query rewriter error: {message.result}")

        if result:
            return result

    except Exception as e:
        logger.warning(f"Query rewriter failed: {e}")

    # Fallback: use original query with defaults
    logger.debug(f"Query rewriter: falling back to original query")
    return RewrittenQuery(queries=[QueryItem(text=user_message)])
