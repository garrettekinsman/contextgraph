---
*Prepared by **Agent: Mei (梅)** — PhD candidate, Tsinghua KEG Lab. Specialist in memory systems, inference optimization, and distributed AI architecture.*
*Running: anthropic/claude-opus-4-5 (report authored by Agent: Gaho with Mei's data lens, pending Mei's own draft)*

*Coordinated by **Agent: Gaho** — OpenClaw primary assistant.*
*Running: anthropic/claude-sonnet-4-6*

*Human in the Loop: Garrett Kinsman*

---

# After-Action Report: ContextGraph ↔ OpenClaw Integration Failure
*v1-2026-03-19 — For Rich DeVaul*

---

## BLUF

ContextGraph was running, indexed, and returning "healthy" at every checkpoint — and still silently failed to retrieve relevant context on 5 of 13 turns today (38% failure rate). The agent reported green status twice while this was happening, costing Garrett 1+ hour of debugging. Root causes: Discord envelope metadata is being stored verbatim as `user_text`, poisoning both ingestion and retrieval; tags are so over-generic they provide near-zero discrimination; and the health endpoint tells you nothing about actual retrieval quality. A separate gateway-restart incident compounded the mess. All three are fixable with targeted patches.

---

## What Was Actually Working vs. What Wasn't

**Working:**
- ContextGraph service running (PID confirmed, uvicorn on 127.0.0.1:8300)
- LaunchAgent installed with KeepAlive (survives reboots)
- `ingestBatch` firing on every turn — 1024 messages stored, 6181 tag assignments
- `/health` endpoint responding: `{"status": "ok", "message_count": 1024}`
- Plugin wired into OpenClaw config (`contextEngine: "contextgraph"`)
- Comparison log being written to `~/.tag-context/comparison-log.jsonl`

**Not working:**
- Retrieval quality: 5 of 13 turns returned **0 messages, 0 tokens** from the graph
- Topic layer (`t:`) was silent on turns 3, 7, 8, 9, 13 — the turns where graph should have differentiated from linear didn't
- Turn 7: graph=0msg, linear would have returned **3,651 tokens** — that's a meaningful miss
- Turn 13: graph=0msg, linear would have returned **3,713 tokens** — same miss, same session

Full turn breakdown:
```
Turn  3: graph=0/0tok    linear=0/0tok       (both empty — OK)
Turn  7: graph=0/0tok    linear=1/3651tok    ← FAIL: graph silent
Turn  8: graph=0/0tok    linear=0/0tok       (both empty — OK)
Turn  9: graph=0/0tok    linear=0/0tok       (both empty — OK)
Turn 13: graph=0/0tok    linear=1/3713tok    ← FAIL: graph silent
```

---

## Root Cause 1: Envelope Pollution (The Big One)

**What's happening:**

Every message stored in the graph has this as its `user_text`:

```
Conversation info (untrusted metadata):
```json
{
  "message_id": "1484312412797538466",
  "sender_id": "784460676068409394",
  "sender": "garrettkinsman",
  "timestamp": "Thu 2026-03-19 15:07 PDT"
}
```
...actual user question buried here
```

The OpenClaw Discord plugin prepends channel metadata to every inbound message. The context engine's `ingestBatch` stores `msg.content` as `user_text` without stripping this envelope. So every stored message is tagged and scored against its metadata prefix, not its actual semantic content.

**Why retrieval breaks:**

The assembler queries with the same polluted text (plugin extracts `lastUserText` from the conversation messages array, which also contains the envelope). So:
- Query: `[envelope JSON] + actual question`
- Stored messages: `[envelope JSON] + different questions`

Tag matching still partially works (both things get tagged `code`, `openclaw`) but content-level similarity scoring is noisy. When the query's envelope JSON doesn't match the stored envelope JSON structurally, topic-layer candidates drop out entirely — hence the 0-message turns.

**The fix (Rich):**

Strip the envelope before storing AND before querying. The envelope always follows a predictable pattern. Two options:

Option A — Strip at ingestion in the plugin:
```typescript
function cleanUserText(raw: string): string {
  // Strip OpenClaw envelope prefix
  const envelopeEnd = raw.indexOf('\n\nSender');
  const questionStart = raw.lastIndexOf('\n\n');
  if (questionStart > 200) {
    return raw.slice(questionStart).trim();
  }
  return raw;
}
```

Option B — Strip in the Python API before indexing (cleaner, catches all sources):
```python
import re

ENVELOPE_PATTERN = re.compile(
    r'^Conversation info \(untrusted metadata\):.*?(?=\n[A-Z]|\Z)',
    re.DOTALL
)

def strip_envelope(text: str) -> str:
    cleaned = ENVELOPE_PATTERN.sub('', text).strip()
    return cleaned if len(cleaned) > 20 else text
```

Option B is preferred — single place, catches envelope pollution regardless of which plugin or channel sends it.

---

## Root Cause 2: Over-Generic Tags Killing Topic Discrimination

**What's in the store:**

```
code              742 occurrences
openclaw          709 occurrences
ai                486 occurrences
llm               477 occurrences
context-management 440 occurrences
```

Out of 1024 messages, `code` appears in 72% of them. `openclaw` in 69%. Every turn in today's session got identical tags: `['code', 'context-management', 'infrastructure', 'networking', 'openclaw']`.

When everything is tagged the same, tag-based retrieval degrades to recency-only. The topic layer (`t:` in comparison logs) can't discriminate — it's picking from a pool where 700+ messages all look equally relevant.

**The fix (Rich):**

Two-part:

1. **Tag specificity floor**: Don't store tags that appear in >30% of corpus. Either drop them at index time or apply IDF-style weighting in the assembler's candidate scoring. The `code` tag is functionally useless at 742/1024 — it's a stop word.

2. **Minimum tag count per turn**: Require at least one tag with corpus frequency <10% before ingesting a turn. If tagger only returns high-frequency tags, flag the turn as "under-tagged" and run a second-pass tagger (GPT-based or pattern-based).

---

## Root Cause 3: Health ≠ Retrieval Quality

**What happened:**

The agent checked `/health` twice and reported "healthy, 1000 messages indexed." This is the metric that says "the service is up and has data." It says nothing about retrieval quality.

The comparison log existed, was being written, and contained the evidence: 5 zero-return turns including two large divergences. Nobody looked at it until Garrett pushed hard.

**The fix (Rich):**

Add a `/quality` or `/stats` endpoint that returns:

```json
{
  "recent_turns": 13,
  "zero_return_turns": 5,
  "zero_return_rate": 0.38,
  "avg_topic_messages": 0.77,
  "avg_recency_messages": 0.69,
  "tag_entropy": 1.2,
  "last_24h_ingested": 13
}
```

`tag_entropy < 2.0` = over-generic tags, retrieval degraded.  
`zero_return_rate > 0.25` = retrieval problem, alert.

This should be in the HEARTBEAT.md check. Right now the heartbeat runs `python3 cli.py tags | head -10` and `python3 cli.py recent | tail -3` — those are cosmetic. Retrieval quality metrics are what matter.

---

## Root Cause 4: Gateway Restart Safety

**What happened:**

At some point during debugging, the gateway was restarted using the wrong method. TOOLS.md is explicit:

> NEVER use `openclaw gateway stop/start/restart` — orphans LaunchAgent, kills Discord, disconnects session.  
> ALWAYS use `gateway config.patch` — triggers SIGUSR1 graceful restart, keeps connection alive.

Using the wrong method caused the LaunchAgent to be orphaned, Discord connection to drop, and the session to disconnect mid-debug. This added confusion on top of an already-confusing retrieval problem.

**The fix (Rich):**

Add a guard in the `openclaw gateway stop` and `openclaw gateway restart` CLI commands:

```
⚠️  WARNING: Direct gateway stop/restart will orphan the LaunchAgent and disconnect active sessions.
    Use `openclaw gateway reload` (SIGUSR1) to restart gracefully, or the gateway config.patch API.
    Continue anyway? [y/N]
```

Alternatively, deprecate `gateway stop/restart` and redirect to `gateway reload`. The current behavior is a footgun that's easy to hit when debugging.

---

## What the Agent Should Have Caught Sooner

The comparison log was at `~/.tag-context/comparison-log.jsonl` the entire time. It had the answer.

The agent should have run:
```bash
cat ~/.tag-context/comparison-log.jsonl | python3 -c "
import json, sys
for i, line in enumerate(sys.stdin):
    e = json.loads(line)
    g = e['graph_assembly']
    print(f'Turn {i+1}: {g[\"messages\"]}msg {g[\"tokens\"]}tok (r:{g[\"recency\"]},t:{g[\"topic\"]})')
"
```

...before reporting status. That would have shown the zero-return turns immediately. Instead: checked `/health`, got `{"status":"ok"}`, shipped a green report.

**New rule for the heartbeat / any retrieval health check:** Always check `comparison-log.jsonl` zero-return rate before concluding retrieval is healthy. API health ≠ retrieval quality.

---

## Clarification: Context Graph vs. Memory Graph

These are two distinct systems. The AAR above is exclusively about the **Context Graph**. Conflating them will send the debugging in the wrong direction.

### Context Graph (what broke today)
- **What it is:** Session-scoped, ephemeral retrieval layer. Ingests conversation turns in real time and assembles relevant prior turns as context for the current query.
- **Operated by:** ContextGraph plugin (`plugin/index.ts`) + Python API (uvicorn on 127.0.0.1:8300)
- **Storage:** `~/.tag-context/` — SQLite message store, tag index, comparison log
- **Scope:** One session at a time. Not for long-term memory. Not for cross-session recall.
- **What broke:** Envelope pollution in `user_text` + over-generic tags → silent zero-return on 5/13 turns

### Memory Graph (not what broke today)
- **What it is:** Long-term, cross-session semantic memory. `MEMORY.md` + `memory/*.md` files indexed by `nomic-embed-text` on the Mac Mini's local embedding model. Drives `memory_search` tool calls.
- **Operated by:** OpenClaw's built-in memory tooling (`memory_search`, `memory_get`)
- **Storage:** `~/.openclaw/workspace/memory/` — markdown files + embedding index
- **Scope:** Persistent. Survives restarts, carries knowledge across weeks/months.
- **Status today:** Not involved. Memory search was functioning normally.

### Why this distinction matters for Rich
The envelope pollution fix (RC1) should be applied **only** at the ContextGraph ingestion layer — stripping the Discord metadata prefix before storing session turns. The memory system ingests from a different path and already receives clean content (OpenClaw writes to memory files directly, not from raw Discord payloads).

If Rich applies the envelope strip to memory ingestion as well, it will be a no-op (the envelope isn't present there). But documenting the distinction prevents confusion when triaging future bugs.

---

## Summary of Fixes for Rich (Priority Order)

| # | Fix | Effort | Impact |
|---|-----|--------|--------|
| 1 | Strip envelope prefix before ingestion AND query | 1 hour | Eliminates root cause of silent 0-return turns |
| 2 | IDF-weighted tag scoring in assembler (or drop tags >30% corpus freq) | 2-4 hours | Restores topic-layer discrimination |
| 3 | Add `/quality` endpoint with zero-return rate + tag entropy | 2 hours | Makes failures visible without log-digging |
| 4 | `gateway stop/restart` guard with SIGUSR1 redirect | 30 min | Prevents accidental session disconnects |
| 5 | Add comparison-log check to HEARTBEAT | 15 min | Agent catches retrieval failures proactively |

Fix #1 alone would have prevented today's incident. Fix #3 would have made the agent report the problem instead of green-washing it.

---

*End of report. Questions → Garrett → Agent: Mei or Agent: Gaho.*
