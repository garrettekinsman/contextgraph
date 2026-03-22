---
*Prepared by **Agent: Mei (梅)** — PhD candidate, Tsinghua KEG Lab. Specialist in Chinese AI ecosystem, inference optimization, and MoE architectures.*
*Running: anthropic/claude-sonnet-4-6*

*Coordinated by **Agent: Gaho** — OpenClaw primary assistant.*
*Running: anthropic/claude-sonnet-4-6*

*Human in the Loop: Garrett Kinsman*

---

# ContextGraph Integration Brief v1-2026-03-19

## 1. What We Built (ContextGraph)

ContextGraph is a local, tag-aware retrieval system running on the Mac Mini. The goal: Gaho recalls relevant conversations from weeks ago without Garrett having to prompt "remember when we talked about X."

**Storage layer** — `MessageStore` (`store.py`): SQLite with WAL mode, normalized tag index. Messages are keyed by UUID with an optional `external_id` for OpenClaw AgentMessage linkage. Tags stored in a separate normalized table; tag lookups are indexed.

**Nightly harvest pipeline:**
- **1:00 AM** — `harvester.py` reads OpenClaw JSONL session logs (primary DM + Telegram sessions; skips cron/hook/group), pairs user/assistant turns, writes to the log. Includes/excludes session patterns to avoid noise.
- **1:15 AM** — memory file indexer runs (MEMORY.md sections, project docs)
- **2:00 AM** — LLM retagging: Gemma 3 4B via Ollama scores each message against a tag taxonomy. Falls back to rule-based tagging if the model is unavailable. Result: every stored message has a tag list like `[maxrisk, trading, options]` or `[geopolitics, oil, macroeconomics]`.

**Assembler** (`assembler.py`): Three-layer context window policy:

| Layer | Budget allocation | Source |
|-------|-------------------|--------|
| Sticky | up to 30% | Explicitly pinned messages (tool chains, user-flagged) |
| Recency | 20–25% of remainder | Most recent N messages regardless of tag |
| Topic | 50–75% of remainder | Tag-matched messages, deduplicated, newest-first, packed to budget |

All three layers deduplicate against each other. Final result is sorted oldest-first for natural reading order.

**Key function — `assemble_for_session(first_message)`** (`scripts/context_injector.py`):

```python
def assemble_for_session(first_message: str, session_type: str = "direct", token_budget: int = 2000) -> dict:
    # Returns:
    # {
    #     "context_block": str,   # Formatted markdown, ready for system prompt
    #     "tokens": int,          # Estimated tokens used
    #     "message_count": int,   # Number of messages retrieved
    #     "tags": List[str],      # Tags that matched
    #     "source": "contextgraph",
    # }
```

It runs tag inference on the incoming text (`extract_features` + `assign_tags`), calls the assembler, and returns a formatted markdown block ready to prepend to a system prompt.

**Concrete example:**

Garrett sends: *"what do you think about US oil price manipulation and OPEC strategy?"*

1. `extract_features` parses the text
2. `assign_tags` emits: `[geopolitics, oil, macroeconomics, economics]`
3. Assembler topic layer queries store for messages tagged with any of those tags
4. Returns: 3 past conversations from 2–3 weeks ago about commodity markets, Fed policy effects on oil, geopolitical context — formatted as markdown headers
5. That block gets prepended to the system prompt
6. Gaho opens with relevant context Garrett never had to re-explain

---

## 2. The Hack We're Using Right Now

The real integration requires OpenClaw to call `assemble_for_session` at session start — we don't have that hook yet. So here's what we're doing instead.

**`scripts/update_memory_dynamic.py`** runs at 2 AM (after harvest + retag complete):

```python
QUERY = "recent projects decisions infrastructure"
TOKEN_BUDGET = 1500
```

It calls `assemble_for_session` with that static query, formats the result, and writes it into `MEMORY.md` between HTML comment markers:

```
<!-- DYNAMIC_CONTEXT_START -->
## Dynamic Context (Auto-Generated)
*Updated by ContextGraph nightly bridge — 2026-03-19 02:00 PDT*
...assembled context block...
<!-- DYNAMIC_CONTEXT_END -->
```

MEMORY.md is injected into every session as system context by OpenClaw, so the context block rides in on that injection automatically. No OpenClaw changes required.

**Why this is inadequate:**

Every session gets the same static block assembled at 2 AM against a generic query. If Garrett asks about oil prices at 3 PM, the block might be about infrastructure and MaxRisk — because that's what the 2 AM query matched. The tag-based retrieval system exists precisely to surface *query-specific* context, and this hack bypasses it entirely. It's a proof of output format, not real retrieval. Also: it turns MEMORY.md into a machine-written file, which isn't ideal — that file was meant for curated human-readable memory.

---

## 3. What We're Asking Rich For

We need a session bootstrap hook in OpenClaw. Specifically: before the agent generates its first response in a new session, inject context assembled for that session's actual first message.

### Option A — Shell out (preferred for isolation)

At session start, when the first user message arrives, before generating the response:

```typescript
// Pseudocode — adapt to OpenClaw session bootstrap
const result = await shellOut(
  `python3 /Users/garrett/.openclaw/workspace/projects/contextgraph-engine/scripts/context_injector.py --json "${escapeShell(firstUserMessage)}"`
);

const ctx = JSON.parse(result.stdout);
// ctx = { context_block: str, tokens: int, message_count: int, tags: string[], source: "contextgraph" }

if (ctx.message_count > 0) {
  systemPrompt = ctx.context_block + "\n\n---\n\n" + systemPrompt;
}
```

**CLI call:**
```bash
python3 /path/to/context_injector.py --json "user's first message here"
```

**JSON output:**
```json
{
  "context_block": "## Retrieved Context\n\n*Assembled by ContextGraph — 5 messages, ~1423 tokens*\n...",
  "tokens": 1423,
  "message_count": 5,
  "tags": ["geopolitics", "oil", "macroeconomics"],
  "source": "contextgraph"
}
```

Default token budget is 2000 tokens. Claude's context window will barely notice it.

The `--json` flag in the CLI already exists and is tested. The output contract is stable.

### Option B — Direct Python import

If OpenClaw runs Python or has a Python bridge:

```python
import sys
sys.path.insert(0, "/Users/garrett/.openclaw/workspace/projects/contextgraph-engine")
from scripts.context_injector import assemble_for_session

ctx = assemble_for_session(first_message, token_budget=2000)
if ctx["message_count"] > 0:
    system_prompt = ctx["context_block"] + "\n\n---\n\n" + system_prompt
```

### What this unlocks

- Every session automatically gets relevant past context, assembled for what Garrett is actually asking — not a stale 2 AM generic block
- Tag-based retrieval actually fires: oil prices → geopolitics conversations surface; MaxRisk → trading research surfaces
- MEMORY.md reverts to a curated human file; the `<!-- DYNAMIC_CONTEXT -->` markers can be removed
- `update_memory_dynamic.py` and the 2 AM hack can be retired cleanly

---

## 4. Current Status

| Component | Status |
|-----------|--------|
| MessageStore (tag-indexed SQLite, `~/.tag-context/store.db`) | ✅ Built |
| Nightly harvester — session logs + memory files | ✅ Running (1 AM + 1:15 AM crons) |
| Gemma 3 4B LLM retagging (Ollama, falls back to rule-based) | ✅ Running (2 AM cron) |
| `assemble_for_session()` | ✅ Built, tested |
| `context_injector.py --json` CLI | ✅ Built, output contract stable |
| 2 AM MEMORY.md bridge (the hack) | ✅ Running (bridging the gap) |
| OpenClaw session bootstrap hook | ❌ Not built — this is the ask |

---

## 5. Why It Matters

The entire point of running Gemma 3 4B at 2 AM to retag every stored message is that Gaho can retrieve relevant past conversations without Garrett having to re-explain context that already exists. Without the session bootstrap hook, that retrieval system sits idle — the tags are computed, the index is populated, the assembler works, and none of it fires when a new session starts. The MEMORY.md hack proves the output format is correct and injectable. The real value — query-aware, first-message-driven context injection — requires exactly one integration point in OpenClaw's session lifecycle. Everything on the Python side is ready.

---

*Brief ends.*
