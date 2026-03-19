---
*Prepared by **Agent: Mei (梅)** — PhD candidate, Tsinghua KEG Lab. Specialist in Chinese AI ecosystem, inference optimization, and MoE architectures.*
*Running: anthropic/claude-sonnet-4-6*

*Human in the Loop: Garrett Kinsman*

---

# contextgraph-logger

Bridge between OpenClaw conversations and Rich DeVaul's ContextGraph engine.

**Rule: this package only calls ContextGraph's HTTP API. It never modifies Rich's code.**

## Files

| File | Purpose |
|------|---------|
| `config.py` | Server URL, paths, token budgets |
| `harvester.py` | Batch ingest: session DB + memory files → `/ingest` |
| `live_ingest.py` | Per-turn shim: POST one exchange → `/ingest` |
| `context_pull.py` | Query ContextGraph → formatted markdown context block |
| `data/ingest-state.json` | Tracks which session DB rows have been ingested |
| `data/memory-state.json` | Tracks content hashes of memory files |

## Setup

```bash
cd projects/contextgraph-logger
pip install -r requirements.txt
```

ContextGraph server must be running at `http://127.0.0.1:8300`.

## Usage

### Batch harvest (run nightly or on demand)

```bash
# Dry run — shows what would be ingested
python3 harvester.py --dry-run --verbose

# Full run
python3 harvester.py --verbose

# Memory files only
python3 harvester.py --memory-only

# Session DB only
python3 harvester.py --sessions-only

# Re-ingest all memory files (ignores hash state)
python3 harvester.py --memory-only --force
```

### Live turn logging (call after each OpenClaw turn)

```bash
# Via JSON on stdin
echo '{"session_id":"abc123","user_text":"hi","assistant_text":"hello","timestamp":1234567890}' \
  | python3 live_ingest.py

# Via CLI args
python3 live_ingest.py \
  --session-id abc123 \
  --user-text "what's the maxrisk status?" \
  --assistant-text "MaxRisk is paused pending risk review..."

# Python import
from live_ingest import ingest_turn
result = ingest_turn(
    session_id="abc123",
    user_text="what's the status?",
    assistant_text="Here's the status...",
)
```

### Context pull (query → markdown block for system prompt injection)

```bash
python3 context_pull.py "memory harvester not working"
python3 context_pull.py --budget 1500 "maxrisk project status"
python3 context_pull.py --tags "maxrisk,trading" "portfolio review"
python3 context_pull.py --json "memory architecture"  # raw JSON
```

Python import:
```python
from context_pull import pull_context

result = pull_context("memory harvester not working")
if result["ok"] and result["context_block"]:
    # inject result["context_block"] into system prompt
    pass
```

## State files

Both harvesters are **idempotent** — re-running is safe:

- `data/ingest-state.json` — maps `external_id → timestamp` for session DB rows
- `data/memory-state.json` — maps `relpath → content_hash` for memory files

Delete these files to force a full re-ingest.

## API reference

Rich's server at `http://127.0.0.1:8300`:

- `POST /ingest` — `{session_id, user_text, assistant_text, timestamp, external_id?}`
- `POST /assemble` — `{user_text, tags?, token_budget?, session_id?}`
- `POST /tag` — `{user_text, assistant_text}`
- `POST /compare` — `{user_text, assistant_text}`
- `GET /health` — server health check

## Architecture

```
OpenClaw session DB ──► harvester.py ──► POST /ingest ──► ContextGraph
memory/ files ────────►              └──►
OpenClaw turn ────────► live_ingest.py ► POST /ingest ──►

                        context_pull.py ► POST /assemble ◄── ContextGraph
                              │
                              └──► Markdown context block → system prompt
```
