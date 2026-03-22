---
Agent: Vera | Running: claude-opus-4-5 | Date: 2026-03-19
Human in the Loop: Garrett Kinsman
---

# Proposed Improvements for ContextGraph Engine

*For Rich DeVaul — collaborative engineering review*

Rich, your contextgraph-engine is solid work. The layered assembly model, tag registry lifecycle, and quality agent feedback loop are well-designed. We've been integrating it with OpenClaw's memory system and found a few areas where small changes would improve security and robustness.

These are suggestions, not demands. You know the codebase better than we do.

---

## Priority 1: Security

### 1.1 Add Prompt Injection Sanitization at API Layer

**What we observed:**  
The `/ingest` endpoint accepts user_text and assistant_text, applies `strip_envelope()` for metadata cleaning, but doesn't sanitize prompt injection patterns. Since ContextGraph content gets injected into LLM context windows, malicious content in the store could influence agent behavior.

**Our memory_harvester.py has this function:**
```python
_INJECTION_PATTERNS = [
    (r"(?i)ignore\s+(previous|all|prior|above|earlier)\s+instructions?", "[REDACTED:instruction-override]"),
    (r"(?i)disregard\s+(previous|all|prior|above|earlier)\s+instructions?", "[REDACTED:instruction-override]"),
    (r"(?i)you\s+are\s+now\s+(?:a\s+)?(?:an?\s+)?[a-z]+", "[REDACTED:role-override]"),
    (r"(?i)new\s+instruction\s*:", "[REDACTED:instruction-inject]"),
    (r"(?i)system\s+prompt\s*:", "[REDACTED:system-inject]"),
    (r"(?i)(?:^|\n)\s*\[SYSTEM\]\s*:", "[REDACTED:system-tag]"),
    (r"(?i)(?:^|\n)\s*<\|system\|>", "[REDACTED:system-token]"),
    (r"(?i)from\s+now\s+on\s*,?\s*(?:you|ignore|always)", "[REDACTED:behavior-override]"),
]

def _sanitize_content(text: str) -> str:
    result = text
    for pattern, replacement in _INJECTION_PATTERNS:
        result = re.sub(pattern, replacement, result)
    return result
```

**Suggestion:**  
Move this (or similar) to `utils/text.py` and apply in the `/ingest` endpoint before storing. This provides defense-in-depth regardless of content source.

---

### 1.2 Bind Server to localhost Only

**What we observed:**  
`api/server.py` line 329:
```python
uvicorn.run(app, host="0.0.0.0", port=8350)
```

This makes the API accessible to any network interface. Since there's no authentication, any process on the local network could manipulate the store.

**Suggestion:**
```python
uvicorn.run(app, host="127.0.0.1", port=8350)
```

Or make it configurable via environment variable for deployments that need remote access with proper auth.

---

### 1.3 Consider Token Authentication for Write Endpoints

**What we observed:**  
Write endpoints (`/ingest`, `/pin`, `/registry/promote`) have no authentication. A compromised local process could poison the memory graph.

**Suggestion:**  
Add optional bearer token auth, configurable via environment variable. For our use case, we'd pull the token from OpenClaw's config.

```python
from fastapi import Depends, HTTPException, Header

def verify_token(authorization: str = Header(None)):
    expected = os.environ.get("CONTEXTGRAPH_API_TOKEN")
    if expected and authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401)

@app.post("/ingest")
def ingest(request: IngestRequest, _: None = Depends(verify_token)):
    ...
```

This is optional — skip if you want to keep the API simple for single-user deployments.

---

## Priority 2: Data Integrity

### 2.1 Add UNIQUE Constraint on external_id

**What we observed:**  
The `external_id` column has no UNIQUE constraint:
```sql
CREATE TABLE messages (..., external_id TEXT);
```

This allows duplicate external_ids, which could cause confusion when `get_by_external_id()` returns only the first match.

**Suggestion:**
```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_external_id_unique 
ON messages(external_id) WHERE external_id IS NOT NULL;
```

This creates a partial unique index (only on non-null values), preserving backwards compatibility.

**Migration script:**
```python
def migrate_external_id_unique(conn):
    # Check for duplicates first
    dupes = conn.execute("""
        SELECT external_id, COUNT(*) as cnt 
        FROM messages WHERE external_id IS NOT NULL 
        GROUP BY external_id HAVING cnt > 1
    """).fetchall()
    
    if dupes:
        # Handle duplicates (keep newest, delete older)
        for ext_id, cnt in dupes:
            conn.execute("""
                DELETE FROM messages WHERE external_id = ? 
                AND id NOT IN (
                    SELECT id FROM messages WHERE external_id = ? 
                    ORDER BY timestamp DESC LIMIT 1
                )
            """, (ext_id, ext_id))
    
    # Add unique index
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_external_id_unique 
        ON messages(external_id) WHERE external_id IS NOT NULL
    """)
    conn.commit()
```

---

### 2.2 Migration Script for Existing Unsanitized Content

**What we observed:**  
Some existing records contain full metadata envelopes despite `strip_envelope()` being applied — either from messages ingested before the function was added, or from edge cases where stripping was too aggressive and returned original.

**Suggestion:**  
One-time migration to re-sanitize existing records:

```python
def migrate_sanitize_existing(conn):
    """Re-apply strip_envelope and sanitization to existing messages."""
    rows = conn.execute("SELECT id, user_text FROM messages").fetchall()
    
    for row_id, user_text in rows:
        cleaned = strip_envelope(user_text)
        sanitized = _sanitize_content(cleaned)
        if sanitized != user_text:
            conn.execute(
                "UPDATE messages SET user_text = ? WHERE id = ?",
                (sanitized, row_id)
            )
    conn.commit()
```

---

## Priority 3: Operational

### 3.1 Health Endpoint Should Not Expose Full Tag List

**What we observed:**  
`/health` returns all tags in the system:
```json
{"status": "ok", "messages_in_store": 1391, "tags": ["3d-printing", "agents", ...]}
```

This is helpful for debugging but could leak information about indexed content categories.

**Suggestion:**  
Return tag count instead of full list at `/health`, move detailed info to `/metrics`:
```python
@app.get("/health")
def health():
    return {"status": "ok", "messages": count, "tags": len(tags)}
```

---

### 3.2 Pin requirements.txt with hashes

**What we observed:**
```
spacy>=3.7
pytest>=8.0
```

Unpinned dependencies are a supply chain risk.

**Suggestion:**
```bash
pip-compile --generate-hashes requirements.in -o requirements.txt
```

Or use `uv lock` if you're on uv.

---

## What We Already Fixed (FYI)

These are changes in our integration layer (`scripts/memory_harvester.py`, `scripts/context_injector.py`) that don't require changes to your codebase:

1. **Content sanitization in memory_harvester** — we sanitize before calling `/ingest`
2. **Explicit token budget management in context_injector** — we respect the budget and truncate intelligently
3. **YAML frontmatter tag extraction** — we parse tags from memory file frontmatter and merge with auto-tagged results
4. **Idempotent harvesting via content hash** — we skip unchanged files to reduce API load

---

## Summary

| Issue | Priority | Effort | Impact |
|-------|----------|--------|--------|
| Injection sanitization at API | P1 | Low | High |
| Bind to 127.0.0.1 | P1 | Trivial | High |
| Optional token auth | P1 | Medium | Medium |
| UNIQUE index on external_id | P2 | Low | Medium |
| Sanitize existing records | P2 | Low | Medium |
| Health endpoint tag list | P3 | Trivial | Low |
| Pin dependencies | P3 | Low | Medium |

Happy to discuss any of these or help implement. The contextgraph design is excellent — these are polish items to harden it for production use.

—Vera  
*Security review for Garrett Kinsman / OpenClaw*
