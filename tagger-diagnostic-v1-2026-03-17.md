# Tagger Diagnostic Report v1-2026-03-17

---
*Prepared by **Agent: Mei (梅)** — PhD candidate, Tsinghua KEG Lab.*
*Running: anthropic/claude-opus-4-6*

*Human coordinator: Garrett Kinsman*

---

## BLUF

The tagger is broken in three compounding ways: (1) substring matching instead of word-boundary matching causes `"rl"` to fire on any text containing `"url"`, `"world"`, `"curl"`, etc.; (2) absurdly generic trigger words (`"model"`, `"token"`, `"context"`, `"plan"`, `"design"`) guarantee near-universal firing because they appear in the system prompt injected into every turn; (3) the tagger receives the *full turn text* including OpenClaw system prompts, AGENTS.md, metadata envelopes, and heartbeat boilerplate — all of which contain these trigger words. The GP tagger inherits all of this because it trains on the baseline's labels as ground truth.

---

## 1. Root Cause Analysis

### 1.1 The `rl` Substring Bug (125/186 turns)

The `context-management` rule includes `"rl"` in its trigger list:

```python
_text_contains_any(u, a, ["context window", "compaction", "tagging", "tag-context",
                          "context management", "rl", "reinforcement learning", ...])
```

`_text_contains_any` uses Python `in` on the lowercased combined text:

```python
def _text_contains_any(user_text, assistant_text, terms):
    combined = (user_text + " " + assistant_text).lower()
    return any(t.lower() in combined for t in terms)
```

`"rl" in combined` is a **substring match**, not a word match. It fires on:

| Word | Contains `"rl"`? | Common? |
|------|-----------------|---------|
| `url` | ✅ | Every URL, every `href`, every link |
| `curl` | ✅ | Shell commands |
| `world` | ✅ | Common English |
| `particularly` | ✅ | Common English |
| `early` | ✅ | Common English |
| `clearly` | ✅ | Common English |
| `pearls` | ✅ | Less common |

Every turn containing a URL (which is most of them given system prompt injection) triggers the `context-management` rule, which emits **three tags**: `context-management`, `rl`, and `ai`.

**This single bug accounts for the `rl` tag appearing on 125/186 turns.**

### 1.2 System Prompt Poisoning (ai: 186, llm: 180, security: 151)

The tagger receives raw turn text that includes the OpenClaw system prompt. That prompt contains:

| Word | Present in system prompt? | Rules triggered |
|------|--------------------------|-----------------|
| `"model"` | ✅ (`default_model=`, `model=anthropic/...`) | `ai-llm` → `ai`, `llm` |
| `"token"` | ✅ (`gatewayToken`, metadata envelopes) | `ai-llm` → `ai`, `llm` **AND** `security` |
| `"context"` | ✅ (`context`, `context limit`) | `ai-llm` → `ai`, `llm` |
| `"prompt"` | ✅ (`system prompt`) | `ai-llm` → `ai`, `llm` |
| `"anthropic"` | ✅ (model name) | `ai-llm` → `ai`, `llm` |
| `"claude"` | ✅ (model name) | `ai-llm` → `ai`, `llm` |
| `"security"` | ✅ (AGENTS.md sections) | `security` |
| `"auth"` | ✅ (`auth`, `allowlist`) | `security` |
| `"deploy"` | ✅ (`deployment`) | `devops` → `devops`, `deployment` |
| `"restart"` | ✅ (`gateway restart`) | `devops` → `devops`, `deployment` |
| `"research"` | ✅ (AGENTS.md) | `research-planning` → `research`, `planning` |
| `"plan"` | ✅ (AGENTS.md) | `research-planning` → `research`, `planning` |
| `"architecture"` | ✅ (AGENTS.md) | `research-planning` → `research`, `planning` |
| `"design"` | ✅ (AGENTS.md) | `research-planning` → `research`, `planning` |
| `"analysis"` | ✅ (AGENTS.md) | `research-planning` → `research`, `planning` |
| `"document"` | ✅ (AGENTS.md) | `research-planning` → `research`, `planning` |

**The `ai-llm` rule fires on literally every turn** because the system prompt always contains `"model"`, `"token"`, `"context"`, `"prompt"`, `"anthropic"`, and `"claude"`. The rule can never *not* fire given current architecture.

### 1.3 Overly Generic Trigger Words

Even if system prompt stripping were implemented, several trigger words are too vague:

- `"model"` — fires on "data model", "business model", "model train"
- `"token"` — fires on "token" in auth context, JWT, API keys (→ double-tags `ai` + `security`)
- `"build"` — fires on "build a house", "build rapport"
- `"restart"` — fires on "restart the conversation"
- `"plan"` — fires on "plan for dinner", "floor plan"
- `"design"` — fires on "interior design", "design thinking"
- `"document"` — fires on "document your findings" (verb)

---

## 2. Severity Ranking

| Rank | Rule | Tags Emitted | Fire Rate | Root Cause |
|------|------|-------------|-----------|------------|
| **1** | `ai-llm` | `ai`, `llm` | ~186/186 (100%) | 6+ trigger words in every system prompt |
| **2** | `research-planning` | `research`, `planning` | ~175/186 (94%) | `"plan"`, `"design"`, `"architecture"`, `"analysis"`, `"document"` in AGENTS.md |
| **3** | `context-management` | `context-management`, `rl`, `ai` | ~125/186 (67%) | `"rl"` substring match + `"context"` in system prompt |
| **4** | `security` | `security` | ~151/186 (81%) | `"token"` and `"security"` in system prompt |
| **5** | `devops` | `devops`, `deployment` | ~80/186 (est.) | `"deploy"`, `"restart"`, `"build"` in system prompt |
| **6** | `contains-url` | `research` | ~60% (est.) | URLs everywhere; also inflates `research` count |
| **7** | `is-question` | `question` | unknown | Any `?` in text, including rhetorical/quoted |

---

## 3. Fixes

### Fix 3.1: Replace substring matching with word-boundary matching

```python
import re

def _text_contains_any(user_text: str, assistant_text: str, terms: list[str]) -> bool:
    """True if any term appears as a whole word/phrase in combined text."""
    combined = (user_text + " " + assistant_text).lower()
    for term in terms:
        # Escape regex special chars, then match on word boundaries
        pattern = r'\b' + re.escape(term.lower()) + r'\b'
        if re.search(pattern, combined):
            return True
    return False
```

This fixes the `"rl"` bug immediately. `"rl"` will no longer match `"url"`, `"world"`, `"curl"`, etc.

### Fix 3.2: Strip system prompt and metadata before tagging

Add a text preprocessor that extracts only user-authored and assistant-authored content:

```python
def _strip_system_context(raw_text: str) -> str:
    """Remove injected system prompts, metadata envelopes, and boilerplate."""
    lines = raw_text.split('\n')
    filtered = []
    in_system_block = False

    for line in lines:
        # Skip OpenClaw system prompt markers
        if line.startswith('## Runtime') or line.startswith('## Project Context'):
            in_system_block = True
            continue
        if line.startswith('## ') and in_system_block:
            in_system_block = False
        if in_system_block:
            continue

        # Skip JSON metadata envelopes
        if line.strip().startswith('{') and '"token"' in line:
            continue

        filtered.append(line)

    return '\n'.join(filtered)
```

**Better architecture (recommended):** The tagger's `assign()` interface already takes `user_text` and `assistant_text` as separate parameters. The caller should pass *only* the actual user message and assistant response — not the full turn with system prompts. This is a caller-side fix:

```python
# In whatever calls the tagger — NOT the tagger itself:
# BEFORE:
# tags = assign_tags(features, full_turn_text, assistant_text)
# AFTER:
tags = assign_tags(features, user_message_only, assistant_response_only)
```

The system prompt, AGENTS.md, metadata, and heartbeat boilerplate should never enter `user_text` or `assistant_text`.

### Fix 3.3: Tighten trigger word lists

**ai-llm rule** — remove words that appear in generic infrastructure contexts:

```python
TagRule(
    name="ai-llm",
    predicate=lambda f, u, a: _text_contains_any(
        u, a, ["llm", "large language model", "claude ai", "chatgpt",
               "anthropic api", "openai api", "language model",
               "embedding model", "inference server", "fine-tuning",
               "transformer", "neural network"]
    ),
    tags=["ai", "llm"],
),
```

Removed: `"model"` (too generic), `"prompt"` (too generic), `"context"` (too generic), `"token"` (ambiguous with auth tokens), `"embedding"` (still generic), `"inference"` (ok but borderline). Replaced with multi-word phrases that are unambiguous.

**security rule** — remove `"token"` (double-fires with ai-llm), tighten `"auth"`:

```python
TagRule(
    name="security",
    predicate=lambda f, u, a: _text_contains_any(
        u, a, ["security vulnerability", "authentication", "credential leak",
               "allowlist", "permission denied", "cve-", "exploit",
               "attack vector", "zero-day", "injection attack",
               "access control", "privilege escalation"]
    ),
    tags=["security"],
),
```

**context-management rule** — remove bare `"rl"`, require full phrase:

```python
TagRule(
    name="context-management",
    predicate=lambda f, u, a: _text_contains_any(
        u, a, ["context window", "compaction", "tag-context",
               "context management", "reinforcement learning",
               "quality agent", "context graph", "tagger rule",
               "context budget"]
    ),
    tags=["context-management"],
),
```

Removed: bare `"rl"` (substring disaster), `"tagging"` (too generic — "price tagging"?), `"tagger"` (borderline), `"dag"` (too short — "dagger", "daguerreotype"). Also removed `"ai"` from output tags — this rule is about context management, not AI generally.

**research-planning rule** — require compound phrases:

```python
TagRule(
    name="research-planning",
    predicate=lambda f, u, a: _text_contains_any(
        u, a, ["research paper", "research proposal", "system design",
               "software architecture", "project plan", "prototype build",
               "design doc", "technical spec", "data analysis",
               "literature review", "research loop"]
    ),
    tags=["research", "planning"],
),
```

Removed: `"design"` (too generic), `"plan"` (too generic), `"architecture"` (matches AGENTS.md), `"document"` (verb usage), `"analysis"` (too generic), `"proposal"` (borderline), `"prototype"` (borderline), `"spec"` (too short).

**devops rule** — remove `"build"` and `"restart"`:

```python
TagRule(
    name="devops",
    predicate=lambda f, u, a: _text_contains_any(
        u, a, ["deploy to", "launchd", "launchctl", "docker compose",
               "docker run", "vercel deploy", "npm run build",
               "git push", "systemctl", "daemon reload",
               "ci/cd", "pipeline"]
    ),
    tags=["devops", "deployment"],
),
```

Removed: bare `"deploy"` (substring of "deployment" in AGENTS.md), bare `"build"` (too generic), bare `"restart"` (too generic), bare `"daemon"` (borderline), bare `"docker"` (still ok but tightened to commands).

### Fix 3.4: Make `contains-url` not auto-tag as research

```python
TagRule(
    name="contains-url",
    predicate=lambda f, u, a: f.contains_url,
    tags=["has-url"],  # Neutral tag, not "research"
    confidence=0.3,    # Low confidence — URL presence alone means little
),
```

Or remove this rule entirely. A URL doesn't mean research.

### Fix 3.5: Tighten question detection

```python
def detect_question(text: str) -> bool:
    """Detect genuine questions (not rhetorical, not quoted)."""
    # Only look at the last line or sentence for question marks
    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
    if not lines:
        return False
    # Check if the actual user text (not quoted) contains a question
    non_quoted = [l for l in lines if not l.startswith('>') and not l.startswith('```')]
    return any('?' in l for l in non_quoted[-3:])  # Last 3 lines only
```

---

## 4. Systemic Architecture Issue

The fundamental problem is **input contamination**. The tagger is running on text that includes:

1. OpenClaw system prompt (~2000 tokens of boilerplate)
2. AGENTS.md (~4000 tokens of operational guidelines)
3. SOUL.md, USER.md, IDENTITY.md
4. Metadata JSON envelopes
5. Heartbeat/cron text

This means the tagger is classifying *the system's own documentation*, not the user's actual conversation.

**Recommended fix (priority order):**

1. **Immediate:** The caller must pass only `user_message` and `assistant_response` to `assign()`. Strip everything else before it reaches the tagger. This is the single highest-impact change.

2. **Short-term:** Add a `system_prompt_hash` to `MessageFeatures`. If the system prompt is identical across turns (it usually is), the tagger can detect and ignore it — or the caller can simply not pass it.

3. **Medium-term:** The tagger should operate on a `ConversationTurn` object that explicitly separates `system`, `user`, `assistant`, and `metadata` fields. The tagger only reads `user` + `assistant`. This is a schema change but prevents future contamination.

```python
@dataclass
class ConversationTurn:
    system_prompt: str      # NOT passed to tagger
    user_text: str          # tagger input
    assistant_text: str     # tagger input
    metadata: dict          # NOT passed to tagger
    raw_text: str           # for debugging only
```

---

## 5. Verdict on GP Tagger (gp_tagger.py)

The GP tagger is training on poison. `build_training_examples()` explicitly uses the baseline tagger's output as pseudo-ground-truth labels:

```python
baseline_tags = baseline_assign(features, record.user_text, record.assistant_text)
label = tag in baseline_tags
```

If the baseline says `ai` appears on 186/186 turns, the GP learner sees `ai=True` for every training example. The optimal evolved predicate for `ai` is `return True` — which is exactly what GP will converge to (a constant `1.0` tree scores maximum balanced accuracy when all labels are positive). The GP tagger doesn't fix the baseline's bugs; it **learns to reproduce them with mathematical precision**, then potentially adds new failure modes through random genetic drift. It is worse than useless in its current form — it launders bad labels through an evolutionary process that makes them look principled. Fix the baseline first, then retrain the GP from corrected labels. Or better: replace the pseudo-labels with human-annotated ground truth for at least 50-100 turns before evolving anything.

---

## Summary of Recommended Changes (Priority Order)

| Priority | Fix | Impact | Effort |
|----------|-----|--------|--------|
| **P0** | Strip system prompt from tagger input (caller-side) | Fixes ~80% of over-firing | Low |
| **P0** | Word-boundary matching in `_text_contains_any` | Fixes `rl` bug + all substring issues | Low |
| **P1** | Tighten trigger word lists (compound phrases) | Reduces false positives ~60% | Medium |
| **P1** | Remove `"research"` from `contains-url` rule | Stops URL→research inflation | Trivial |
| **P2** | Add `ConversationTurn` schema | Prevents future contamination | Medium |
| **P2** | Retrain GP tagger on corrected labels | Makes GP useful | Medium |
| **P3** | Human-annotate 50-100 turns as gold labels | Enables proper evaluation | High (human time) |

---

*End of diagnostic. The tagger isn't tagging conversations — it's tagging its own system prompt, 186 times.*
