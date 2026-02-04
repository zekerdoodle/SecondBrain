# Memory System Architecture Analysis
**Date**: 2026-02-04
**Subject**: Complete analysis of Second Brain's threads/atoms long-term memory system

---

## Executive Summary

Your memory system is a **semantic network** of atomic facts organized into contextual threads, with automated extraction (Librarian), maintenance (Gardener), and hybrid retrieval. The architecture is solid but has some scaling challenges with large threads.

**Current state**: 675 atoms, 281 threads, 0 orphans, FAISS vector index with e5-base-v2 embeddings.

---

## 1. Thread/Atom Data Model

### AtomicMemory Structure
**Location**: `.claude/memory/atomic_memories.json`

```python
@dataclass
class AtomicMemory:
    id: str                          # "atom_20260201_232203_a7058898"
    content: str                     # The actual fact/memory
    created_at: str                  # ISO timestamp
    last_modified: str               # ISO timestamp
    source_exchange_id: Optional[str]  # Where it came from (currently unused)
    embedding_id: Optional[str]      # "emb_20260201_232203_ee4d4dfd"
    importance: int                  # 0-100 (100 = always included)
    tags: List[str]                  # ["communication", "nickname"]
    history: List[Dict]              # Edit history with replaced_at timestamps
```

**Key characteristics**:
- **Standalone**: Each atom is self-contained, no pronouns or "this" references
- **Dense**: Condensed to preserve signal, strip fluff
- **Timestamped**: Records when events happened for temporal context
- **Versioned**: History tracks changes over time

**Example atom**:
```json
{
  "id": "atom_20260201_232203_a7058898",
  "content": "Zeke uses the nickname 'Cladeeeeeypoooo' when addressing me casually",
  "importance": 45,
  "tags": ["communication", "nickname", "rapport"],
  "embedding_id": "emb_20260201_232203_ee4d4dfd"
}
```

### Thread Structure
**Location**: `.claude/memory/threads.json`

```python
@dataclass
class Thread:
    id: str                    # "thread_20260201_232202_161b9fdb"
    name: str                  # "Claude-Zeke Communication Patterns"
    description: str           # What this thread contains
    memory_ids: List[str]      # ["atom_...", "atom_..."]
    created_at: str
    last_updated: str
    embedding_id: Optional[str]  # Thread name+desc embedded for search
```

**Key characteristics**:
- **Network model**: One atom can belong to 2-4 threads (intentional cross-linking)
- **Contextual grouping**: Related facts stay together for coherent retrieval
- **Embedded**: Thread name+description has its own embedding for semantic search
- **Granular**: Aim for 20-80 atoms per thread, not mega-buckets

**Example thread**:
```json
{
  "id": "thread_20260201_232202_161b9fdb",
  "name": "Claude-Zeke Communication Patterns",
  "description": "How Zeke and I interact, communicate style, rapport",
  "memory_ids": ["atom_20260201_232203_a7058898", ...],  // 179 atoms
  "last_updated": "2026-02-04T00:55:12.123456"
}
```

### Storage Details
- **Format**: JSON with indentation for human readability
- **Atomicity**: File locks (`atomic.lock`, `threads.lock`) prevent corruption
- **Size**: 10,318 lines (atoms), 4,687 lines (threads) - ~15KB total
- **Embeddings**: FAISS index (6.5MB), metadata (1.1MB), cache dir (112KB)

---

## 2. Librarian Logic

### When It Runs
**Trigger mechanism**: Throttled background processing via scheduler
- **Buffer-based**: Exchanges accumulate in `exchange_buffer.json`
- **Throttle**: Runs at most once per 20 minutes (`THROTTLE_SECONDS = 1200`)
- **Automatic**: Scheduled by the agent scheduler system
- **Manual**: Can be forced via `ltm_process_now()` tool

**Process flow**:
1. Each conversation turn is buffered via `ltm_buffer_exchange()`
2. Buffer accumulates up to 100 exchanges (`MAX_BUFFER_SIZE`)
3. Every 20 minutes (if buffer non-empty), Librarian consumes buffer
4. All buffered exchanges processed in one batch

### Decision Making: New Thread vs Existing

**The Librarian's structured output**:
```json
{
  "atomic_memories": [
    {
      "content": "Zeke started job hunting in January 2026",
      "importance": 80,
      "thread_names": ["Zeke's Job Search 2026", "Zeke's Life Milestones 2026"],
      "tags": ["career", "timeline"]
    }
  ],
  "new_threads": [
    {
      "name": "Zeke's Job Search 2026",
      "description": "Zeke's career search activities and plans"
    }
  ]
}
```

**Decision algorithm** (implemented in `librarian_runner.py`):
1. **Receives context**: Existing memories (last 100) and all threads for reference
2. **Extracts atoms**: Each fact becomes a standalone atom
3. **Assigns to threads**: Each atom lists 2-4 thread names where it belongs
4. **Creates new threads**: If thread doesn't exist, it's auto-created or listed in `new_threads`
5. **Deduplication**: Before creating, checks for similarity (threshold: 0.88) using embeddings

**Thread assignment strategy** (from Librarian prompt):
- Default: 2-4 threads per atom (network model)
- Specific > Generic: "Job Search 2026" not "Personal Life"
- Creates threads proactively when new topics emerge
- Suggested taxonomy: "Zeke's [Domain]", "Second Brain [Feature]", "Project [Name]"

### What Makes Something Worthy of Becoming an Atom

**Skip rules** (from Librarian prompt):
- ❌ Short-term logistics: "Zeke will check in tomorrow"
- ❌ Procedural exchanges: "Run this command", "OK it worked"
- ❌ Duplicate information: Already exists with high similarity
- ❌ Simulated feelings: "I enjoyed this conversation"
- ❌ Fluff: Generic statements without specific facts

**Include rules**:
- ✅ **Facts about Zeke**: Preferences, plans, history, skills
- ✅ **Significant suggestions from Claude**: "I suggested using CSV format"
- ✅ **Timestamped events**: "Started in January 2026"
- ✅ **System state**: "Rescheduled syncs to run at 6 AM"
- ✅ **Patterns**: Recurring behaviors or preferences

**Importance scoring** (0-100):
- **100**: Core identity (max 2-3 atoms total) - always included
- **90-99**: Critical ongoing context (current projects, job search)
- **70-89**: Important persistent facts (relationships, preferences, history)
- **50-69**: Useful context (opinions, minor preferences, one-time events)
- **20-49**: Minor details (context-specific relevance)
- **0-19**: Trivia (rarely useful)

### Linking Atoms to Threads

**Implementation** (from `librarian_runner.py`):
```python
# For each extracted atom
for mem_data in results["atomic_memories"]:
    atom = atom_mgr.create(content=..., importance=..., tags=...)

    # Assign to each specified thread (network model)
    for thread_name in mem_data["thread_names"]:
        thread = thread_mgr.get_by_name(thread_name)
        if not thread:
            # Auto-create if referenced but doesn't exist
            thread = thread_mgr.create(name=thread_name, description=...)
        thread_mgr.add_memory_to_thread(thread.id, atom.id)
```

**Network properties**:
- One atom → multiple threads (typical: 2-4)
- One thread → many atoms (typical: 20-80)
- No orphans enforced (all atoms must be in ≥1 thread)
- Bi-directional navigation via `memory_ids` list in threads

### Prompt/Instructions Used

**System prompt location**: `.claude/agents/background/librarian/prompt.md` (113 lines)

**Key sections**:
1. **Core philosophy**: First-person perspective, network model, condensation
2. **Rules for atomic memories**: Standalone, dense, timestamped, attributed
3. **Rules for thread assignment**: Assign to ALL relevant (2-4), specific names
4. **Rules for scheduled tasks**: Record automated actions
5. **Importance scoring**: 0-100 scale with guidelines
6. **What to skip**: Logistics, procedural, duplicates, simulated feelings
7. **Output format**: Structured JSON with content + thread_names

**Model used**: `sonnet` (Claude 3.5 Sonnet) via Agent SDK
**Structured output**: JSON Schema validation ensures correct format
**Context window**: Up to 100 recent memories + all threads for deduplication

---

## 3. Gardener Logic

### What It Does During Maintenance

**Scheduled**: Daily via agent scheduler (configurable)
**Manual**: Via `ltm_run_gardener()` tool

**Analysis tasks**:
1. **Find orphans**: Atoms not in any thread (currently 0 in your system ✅)
2. **Identify duplicates**: Near-identical memories to merge
3. **Scan large threads**: Threads with >50 memories that might be bloated
4. **Analyze thread coherence**: Does it contain multiple distinct topics?
5. **Check importance scores**: Do they match actual relevance?
6. **Flag stale content**: Outdated or irrelevant memories

**Output structure**:
```json
{
  "duplicates_to_merge": [
    {"keep_id": "...", "remove_ids": ["..."], "reason": "..."}
  ],
  "threads_to_consolidate": [
    {"source_thread_ids": ["..."], "new_name": "...", "reason": "..."}
  ],
  "threads_to_split": [
    {
      "source_thread_id": "...",
      "new_threads": [
        {"name": "...", "description": "...", "memory_ids": ["..."]}
      ],
      "reason": "..."
    }
  ],
  "importance_adjustments": [
    {"memory_id": "...", "suggested_importance": 70, "reason": "..."}
  ],
  "stale_memories": [
    {"memory_id": "...", "recommendation": "archive", "reason": "..."}
  ],
  "orphaned_atoms": [
    {"memory_id": "...", "recommendation": "delete", "reason": "..."}
  ],
  "summary": "Brief summary of findings"
}
```

### Decision Making

**Split vs. Leave Alone** (from Gardener prompt):
- **SPLIT** if thread contains MULTIPLE distinct topics
  - Example: "Work" containing both "Coding" and "Management"
  - Tool: `ltm_split_thread()` creates focused sub-threads
- **LEAVE ALONE** if thread is long but coherent
  - Example: "Zeke's Fitness Log" with 100 entries = OK
  - Don't split just because it's large

**Auto-apply policy**:
- ✅ **Safe operations**: Importance adjustments (auto-applied if `auto_apply=True`)
- ❌ **Destructive operations**: Deletions, merges, splits (logged only, require manual approval)
- Conservative philosophy: "Be aggressive with ORGANIZATION but conservative with DELETION"

### Prompt Used

**Location**: `.claude/agents/background/gardener/prompt.md` (51 lines)

**Key instructions**:
1. Workflow: Find orphans → Scan threads → Analyze large → Identify dupes → Flag stale
2. Splitting threads: Use `ltm_split_thread()` when messy (not just big)
3. Guidelines: Conservative with deletions, aggressive with organization
4. Output: Structured JSON with recommendations and reasons

**Model used**: `sonnet` (Claude 3.5 Sonnet) via Agent SDK
**Context**: All atoms + all threads + usage stats (if available)

---

## 4. Semantic Injection

### How Memory Gets Injected

**Entry point**: `claude_wrapper.py::_build_system_prompt()`

**Injection sequence**:
1. **Guaranteed memories** (importance=100): Always included, no budget
2. **Semantic LTM**: Query-based retrieval via `get_memory_context()`
3. **Working memory**: Ephemeral cross-exchange context
4. **Memory.md**: Static long-term notes (if exists)

**System prompt structure**:
```
<long-term-memory>
[Static memory.md content if exists]
</long-term-memory>

<working-memory>
[Ephemeral cross-exchange items]
</working-memory>

<semantic-memory>
## Core Context (Always Included)
- [importance=100 atoms]

## Thread: Claude-Zeke Communication Patterns
*How we interact, communicate style, rapport*
- [2026-02-03 14:30] Zeke uses nickname 'Cladeeeeeypoooo' casually
- [yesterday | 2026-02-02 08:15] ...

## Thread: Second Brain System Architecture
...

## Additional Relevant Facts
*Individually relevant facts from other contexts:*
- [a couple days ago | 2026-02-01 20:10] ... *(from: Project XYZ)*
</semantic-memory>
```

### Relevance/Importance Calculation

**Hybrid retrieval strategy** (implemented in `memory_retrieval.py`):

#### Phase 1: Thread Selection (Thread-First)
1. **Search threads and atoms** by semantic similarity (cosine distance)
   - Threads: Direct embedding match on name+description
   - Atoms: Indirect match (child atoms imply parent thread relevance)

2. **Composite scoring** (70% semantic / 30% recency):
   ```python
   base_semantic = max(thread_score, max_child_atom_score)  # 0-1
   recency_score = max(0, 1.0 - (hours_old / (24 * 7)))    # 0-1, decays over 7 days
   composite = 0.7 * base_semantic + 0.3 * recency_score
   ```

3. **Fill budget with whole threads** (all-or-nothing):
   - Sort threads by composite score
   - Include entire thread if it fits in remaining budget
   - Default budget: 20,000 tokens (~10% of 200K context)

#### Phase 2: Bonus Atoms (Hybrid Retrieval)
4. **Fill remaining budget** with individually-relevant atoms from non-selected threads
   - Sorted by semantic similarity to query
   - Cap at 25% of total budget (`ORPHAN_BUDGET_CAP = 0.25`)
   - Prevents thread context from being overwhelmed by random facts

**Why hybrid?**
- **Thread-first**: Preserves contextual coherence (related facts grouped)
- **Bonus atoms**: Catches important individual facts whose parent thread didn't make the cut
- **Example**: Query about "job search" might select "Job Search 2026" thread + bonus atom from "Life Milestones" about interview date

### Query Rewriting

**Implemented**: `query_rewriter.py` (not fully examined in this analysis)

**Purpose**: Expand single user query into multiple semantic search queries
- Example: "Tell me about the project" → ["Theo Development Project", "Second Brain feature development", "Zeke's active projects 2026"]
- Budget split: 20k total / N queries = budget per query
- Only threads from first query included (highest relevance)

**Usage in injection**:
```python
rewritten = await rewrite_query(user_message, conversation_context)
for query in rewritten.queries:
    context = get_memory_context(query, token_budget=budget_per_query)
    # Merge results...
```

### Token Budget Management

**Default budget**: 20,000 tokens (~10% of 200K Claude context window)
**Estimation**: `TOKENS_PER_CHAR = 0.25` (rough: ~4 chars per token)

**Budget breakdown**:
```json
{
  "threads": 12500,        // Thread memories (headers + content)
  "bonus_atoms": 4500,     // Orphan atom cap: 25% of 20k = 5k max
  "guaranteed": 1000       // importance=100 (outside budget, always included)
}
```

**Allocation**:
- **Guaranteed memories**: Outside budget (always included)
- **Thread selection**: Greedy until budget exhausted
- **Bonus atoms**: Up to 25% of budget for individually-relevant facts
- **Per-query**: If query rewriting enabled, split 20k across N queries

**Logging**:
```
Injected semantic LTM via rewritten queries [...]:
  5 threads, 23 atoms, 2 guaranteed
```

---

## 5. Current State

### Statistics
**Extracted**: 2026-02-04

- **Total atoms**: 675
- **Total threads**: 281
- **Orphaned atoms**: 0 ✅
- **Embeddings**: 1,000+ vectors (atoms + threads + metadata)
- **Storage**: 15KB JSON + 7.7MB vectors

**Importance distribution**:
```
90-99:  41 atoms (6%)  - Critical ongoing context
80-89: 126 atoms (19%) - Important persistent facts
70-79: 204 atoms (30%) - Useful context
60-69: 142 atoms (21%) - Minor but relevant
50-59:  99 atoms (15%) - Background details
40-49:  48 atoms (7%)  - Low importance
<40:    15 atoms (2%)  - Trivia
```

### Top 10 Largest Threads
1. **Claude-Zeke Communication Patterns**: 179 memories ⚠️ (BLOATED)
2. **Zeke's Personal Style**: 117 memories ⚠️ (BLOATED)
3. **Second Brain System Architecture**: 109 memories ⚠️ (BLOATED)
4. **Scheduled Task Configuration**: 104 memories ⚠️ (BLOATED)
5. **Claude-Zeke Collaboration**: 94 memories ⚠️ (BLOATED)
6. **Zeke's System Preferences**: 74 memories
7. **Zeke's Daily Routines**: 68 memories
8. **Zeke's Social Media Presence**: 54 memories
9. **Zeke's Life Milestones 2026**: 42 memories
10. **Theo Development Project**: 39 memories

**Target**: 20-80 atoms per thread
**Issue**: Top 5 threads are mega-threads that should be split

---

## 6. Architectural Issues & Limitations

### Identified Issues

#### 1. **Mega-Thread Problem** ⚠️ CRITICAL
- **Observation**: Top thread has 179 memories (target: 20-80)
- **Impact**:
  - Retrieval inefficiency (all-or-nothing thread inclusion)
  - Semantic noise (loosely related facts grouped)
  - Gardener correctly identifies this but hasn't split yet
- **Root cause**: Broad thread names ("Communication Patterns" = catch-all)
- **Fix**: Run Gardener with split recommendations, manually apply

#### 2. **No Source Tracking** ⚠️ MINOR
- **Observation**: `source_exchange_id` field exists but unused
- **Impact**: Can't trace memory back to originating conversation
- **Use case**: Debugging, context transparency, memory lineage
- **Fix**: Populate `source_exchange_id` during atom creation

#### 3. **Orphan Budget Cap May Be Too Restrictive**
- **Setting**: 25% of budget for bonus atoms (`ORPHAN_BUDGET_CAP = 0.25`)
- **Observation**: Caps at 5k tokens even if more individually-relevant atoms exist
- **Impact**: May miss important standalone facts when threads dominate budget
- **Tradeoff**: Prevents thread context from being overwhelmed
- **Status**: Working as designed, but worth monitoring

#### 4. **No Usage Analytics for Gardener**
- **Observation**: Gardener accepts `usage_stats` but receives `None` or dummy data
- **Impact**: Can't adjust importance based on actual retrieval frequency
- **Enhancement**: Track which atoms/threads get retrieved most often
- **Fix**: Implement retrieval logging and pass to Gardener

#### 5. **Embedding Model Selection** ℹ️ ARCHITECTURAL
- **Current**: `intfloat/e5-base-v2` (sentence-transformers)
- **Pros**: Fast, good general-purpose embeddings, runs locally
- **Cons**: Not optimized for conversational memory or personal context
- **Alternative**: OpenAI `text-embedding-3-small` (API-based, higher quality)
- **Status**: Working well, no urgent need to change

#### 6. **Manual Gardener Approvals**
- **Observation**: Destructive operations require manual review
- **Impact**: Gardener suggestions accumulate without auto-execution
- **Tradeoff**: Safety vs. automation
- **Current workflow**: Log recommendations, human approves/applies
- **Status**: Conservative by design (appropriate)

### Architectural Strengths

✅ **Network model**: Cross-linked atoms enable rich context retrieval
✅ **Hybrid retrieval**: Thread coherence + individual fact coverage
✅ **Deduplication**: Embedding similarity prevents redundant atoms
✅ **Atomic history**: Edit tracking preserves memory evolution
✅ **No orphans**: All atoms belong to threads (enforced by Librarian)
✅ **Importance weighting**: Guarantees critical context always included
✅ **Thread-safe operations**: File locks prevent corruption
✅ **Structured outputs**: JSON Schema validation ensures quality

### Scaling Considerations

**Current load**: 675 atoms, 281 threads = manageable
**FAISS index**: 6.5MB = trivial for vector search
**Retrieval speed**: Fast (FAISS is optimized for millions of vectors)

**Potential bottlenecks**:
- **Thread bloat**: Mega-threads degrade retrieval precision (already occurring)
- **JSON file size**: Linear read/write becomes slow at ~10k+ atoms
- **Embedding batch processing**: 100+ atoms per Librarian run = slow if synchronous

**Mitigation strategies**:
1. Gardener-driven thread splitting (fixes mega-thread issue)
2. Move to SQLite for atom/thread storage (enables efficient queries)
3. Batch embedding generation (already implemented via sentence-transformers)
4. Index sharding if >100k atoms (not needed yet)

---

## 7. Recommendations

### Immediate Actions

1. **Run Gardener and Apply Split Recommendations**
   - Top 5 threads need splitting into focused sub-threads
   - Use `ltm_run_gardener()` then manually apply via `ltm_split_thread()`
   - Example split: "Communication Patterns" → "Rapport & Style", "Tool Usage", "Feedback Loops"

2. **Monitor Orphan Atom Budget**
   - Check logs for "Orphan atom cap reached" messages
   - If frequent, consider raising `ORPHAN_BUDGET_CAP` from 0.25 to 0.35

3. **Enable Source Tracking**
   - Modify `librarian_runner.py` to pass exchange ID to atom creation
   - Useful for debugging and transparency

### Long-Term Improvements

4. **Implement Usage Analytics**
   - Log which atoms/threads are retrieved in each query
   - Feed data to Gardener for data-driven importance scoring
   - Could adjust importance automatically based on retrieval frequency

5. **Add Retrieval Explanation**
   - Include in system prompt why specific threads/atoms were selected
   - Helps Claude understand memory relevance and improves responses

6. **Consider SQLite Migration**
   - When >2k atoms or performance degrades
   - Enables efficient filtering, pagination, complex queries
   - Preserves JSON export for backups/portability

---

## 8. Technical Deep Dive

### Embedding Pipeline

**Model**: `intfloat/e5-base-v2` (multilingual, 768 dimensions)
**Library**: sentence-transformers (local inference, no API calls)
**Index**: FAISS (Facebook AI Similarity Search)

**Flow**:
```
Text → SentenceTransformer.encode() → 768-dim vector → FAISS IndexFlatIP → disk
                                                                           ↓
Query → Embed → FAISS.search(k=100) → [(metadata, score)] → Filter by threshold
```

**Optimizations**:
- **Caching**: Per-text hash → cached embedding (avoids re-encoding)
- **Batch encoding**: Multiple texts encoded in single model call
- **Content-type tagging**: MEMORY, THREAD, CODE, TEXT for future optimization
- **Lazy loading**: Model loaded only when first embedding needed

### Deduplication Strategy

**Threshold**: 0.88 cosine similarity (fairly strict)
**Method**: `atom_mgr.find_similar(content, threshold=0.88)`

**Process**:
1. Embed candidate content
2. FAISS retrieves top-5 most similar existing atoms
3. If any exceed 0.88 similarity, skip creation
4. Prevents near-duplicates while allowing paraphrases

**Limitation**: Doesn't catch semantic duplicates with different wording (e.g., "Zeke likes Python" vs "Zeke prefers Python")

### File Lock Strategy

**Mechanism**: `FileLock` (from `filelock` package)
**Lock files**: `atomic.lock`, `threads.lock`, `throttle.lock`

**Usage**:
```python
with FileLock(self.lock_file):
    # Critical section: read JSON, modify, write JSON
    self._save()
```

**Guarantees**:
- Atomic read-modify-write operations
- Safe concurrent access from multiple processes
- Prevents corruption from simultaneous writes

**Limitation**: Local file locks only (doesn't work across network filesystems)

---

## Appendix: File Locations

**Memory data**:
- `.claude/memory/atomic_memories.json` - All atoms
- `.claude/memory/threads.json` - All threads
- `.claude/memory/embeddings/faiss_index.bin` - Vector index (6.5MB)
- `.claude/memory/embeddings/metadata.json` - Vector metadata (1.1MB)
- `.claude/memory/embeddings/cache/` - Embedding cache (112KB)

**Throttle/buffer**:
- `.claude/memory/exchange_buffer.json` - Pending exchanges
- `.claude/memory/throttle_state.json` - Last run times, stats
- `.claude/memory/backfill_state.json` - Chat history processing state

**Agent definitions**:
- `.claude/agents/background/librarian/prompt.md` - Librarian instructions
- `.claude/agents/background/librarian/librarian_runner.py` - Librarian implementation
- `.claude/agents/background/gardener/prompt.md` - Gardener instructions
- `.claude/agents/background/gardener/gardener_runner.py` - Gardener implementation

**Core implementation**:
- `.claude/scripts/ltm/atomic_memory.py` - Atom CRUD operations
- `.claude/scripts/ltm/thread_memory.py` - Thread CRUD operations
- `.claude/scripts/ltm/memory_retrieval.py` - Hybrid retrieval logic
- `.claude/scripts/ltm/embeddings.py` - Embedding manager
- `.claude/scripts/ltm/memory_throttle.py` - Buffering and rate limiting
- `.claude/scripts/ltm/query_rewriter.py` - Query expansion
- `.claude/scripts/ltm/backfill_chats.py` - Historical chat processing

**Integration**:
- `interface/server/claude_wrapper.py` - System prompt injection (lines 191-328)
- `interface/server/mcp_tools/memory/ltm.py` - MCP tool definitions
- `.claude/agents/claude_primary/prompt.md` - Primary agent instructions (receives memory)

---

## Glossary

**Atom**: Single atomic fact/memory, standalone unit of knowledge
**Thread**: Collection of related atoms, provides contextual grouping
**Importance**: 0-100 score determining retrieval priority and budget allocation
**Guaranteed memory**: importance=100 atom, always included regardless of query
**Orphan atom**: Atom not in any thread (system prevents this)
**Bonus atom**: Individually-relevant atom from non-selected thread (hybrid retrieval)
**Composite score**: 70% semantic + 30% recency, used for thread ranking
**FAISS**: Facebook AI Similarity Search, vector database for embeddings
**e5-base-v2**: Multilingual sentence embedding model, 768 dimensions
**Throttle**: Rate limiter, ensures Librarian runs max once per 20 minutes
**Buffer**: Accumulates exchanges between Librarian runs
**Hybrid retrieval**: Thread-first + bonus atoms strategy
**Network model**: Atoms linked to multiple threads (2-4 typical)
