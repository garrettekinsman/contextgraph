---
*Prepared by **Agent: Mei (梅)** — PhD candidate, Tsinghua KEG Lab. Specialist in knowledge systems, inference optimization, and distributed AI architecture.*
*Running: anthropic/claude-sonnet-4-6*

*Human in the Loop: Garrett Kinsman*

---

# Human Machine State (HMS) Architecture
*v1-2026-03-20*

## BLUF

HMS is a unified situational awareness layer — the combined real-time state of the human (Garrett) and the machine infrastructure (all nodes, models, devices, networks). Every downstream consumer reads HMS to adapt behavior. The morning brief gets shorter when Garrett's on mobile. The model router falls back to cloud when Framework1 is unreachable. Notifications batch instead of real-time when on hotel wifi.

This is not a context retrieval system. It's infrastructure.

---

## The Two Halves

```
┌─────────────────────────────────────────────────────────────┐
│                 Human Machine State (HMS)                   │
├──────────────────────────┬──────────────────────────────────┤
│     HUMAN SIDE           │     MACHINE SIDE                 │
│                          │                                  │
│  • Location              │  • Node availability             │
│  • Timezone              │  • Model health                  │
│  • Environment           │  • Network quality               │
│  • Health / energy       │  • Active devices                │
│  • Schedule pressure     │  • Battery / power state         │
│  • Focus area            │  • Running sessions              │
│  • Mood signals          │  • Active sub-agents             │
└──────────────────────────┴──────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   Morning Brief      Heartbeat        Dispatcher
   Model Router       Notifications    Agent Spawner
```

---

## State Schema

### Human State Dimensions

```python
@dataclass
class HumanState:
    # Location
    location_city: str           # "Long Beach", "Tokyo", "NYC"
    location_country: str        # "US", "JP"
    location_tz: str             # "America/Los_Angeles", "Asia/Tokyo"
    home_vs_away: str            # "home" | "traveling" | "local-away"

    # Physical environment
    environment: str             # "home-office" | "hotel" | "airport" | "car"
                                 # "outdoors" | "coworking" | "unknown"

    # Health / energy
    health: str                  # "nominal" | "sick" | "jet-lagged" | "fatigued"
    energy: str                  # "high" | "moderate" | "low"

    # Schedule pressure
    schedule: str                # "open" | "light" | "busy" | "crunch" | "deadline"
    next_commitment_iso: Optional[str]  # ISO 8601 timestamp

    # Focus
    focus_area: Optional[str]    # "maxrisk" | "yapCAD-sprint" | "Mars-design-rev"
    focus_until_iso: Optional[str]

    # Time context
    local_time_iso: str          # current local time
    home_time_iso: str           # Pacific time (always)
    daylight_hours: bool         # is it daytime locally?
    business_hours_local: bool

    # Source and confidence
    last_updated: float          # Unix timestamp
    confidence: float            # 0.0–1.0 (inferred vs. explicit)
    source: str                  # "explicit" | "inferred" | "default"
    ttl_seconds: int             # when this state expires
    evidence_message_ids: List[str]  # ContextGraph message IDs that drove inference
```

### Machine State Dimensions

```python
@dataclass
class MachineState:
    # Active device
    active_device: str           # "workstation" | "macbook" | "ipad" | "iphone"
    active_device_battery: Optional[int]  # % if mobile, None if plugged in
    on_power: bool

    # Network
    network_type: str            # "home-lan" | "tailscale-lan" | "hotel-wifi"
                                 # "mobile-hotspot" | "vpn-external" | "unknown"
    network_quality: str         # "fast" | "moderate" | "slow" | "offline"
    vpn_active: bool

    # Nodes
    nodes: Dict[str, NodeState]  # keyed by node name

    # Active sessions
    active_sessions: int
    active_sub_agents: List[str]  # labels of running sub-agents

    # Last updated
    last_updated: float
    last_updated_source: str     # "probe" | "heartbeat" | "stale"


@dataclass
class NodeState:
    name: str                    # "framework1" | "mac-mini" | "mac-studio"
    reachable: bool
    ping_ms: Optional[int]
    loaded_models: List[str]     # ["qwen3-coder-next:latest", "nomic-embed-text"]
    vram_used_gb: Optional[float]
    vram_total_gb: Optional[float]
    cpu_load: Optional[float]    # 0.0–1.0
    last_probe: float
```

### Combined HMS

```python
@dataclass
class HMS:
    human: HumanState
    machine: MachineState
    schema_version: str = "1.0"

    def is_mobile(self) -> bool:
        return self.machine.active_device in ("ipad", "iphone")

    def is_traveling(self) -> bool:
        return self.human.home_vs_away == "traveling"

    def framework1_available(self) -> bool:
        node = self.machine.nodes.get("framework1")
        return node is not None and node.reachable

    def preferred_model(self) -> str:
        """Route model based on current state."""
        if not self.framework1_available():
            return "anthropic/claude-sonnet-4-6"
        if self.is_mobile() or self.machine.network_quality == "slow":
            return "anthropic/claude-sonnet-4-6"  # cloud, don't stream local
        return "qwen3-coder-next:latest"  # local when available

    def notification_mode(self) -> str:
        """How aggressive should notifications be?"""
        if self.machine.network_quality in ("slow", "offline"):
            return "batch"
        if self.human.schedule == "crunch":
            return "urgent-only"
        if self.human.health in ("sick", "fatigued"):
            return "quiet"
        return "normal"

    def brief_depth(self) -> str:
        """How much detail should the morning brief include?"""
        if self.is_mobile():
            return "compact"
        if self.human.energy == "low":
            return "compact"
        if self.human.schedule == "crunch":
            return "focused"  # only the critical path
        return "full"
```

---

## Storage

### Location: State file + SQLite sidecar

```
~/.tag-context/
├── store.db          # ContextGraph messages (existing)
├── hms.json          # current HMS snapshot (fast read, mutable)
└── hms-history.db    # HMS event log (new SQLite, state transitions)
```

**`hms.json`** — always-current snapshot, read by every consumer:

```json
{
  "schema_version": "1.0",
  "human": {
    "location_city": "Tokyo",
    "location_country": "JP",
    "location_tz": "Asia/Tokyo",
    "home_vs_away": "traveling",
    "environment": "hotel",
    "health": "jet-lagged",
    "energy": "moderate",
    "schedule": "light",
    "next_commitment_iso": "2026-03-22T09:00:00+09:00",
    "focus_area": "Mars-design-rev",
    "focus_until_iso": "2026-03-25T00:00:00+09:00",
    "local_time_iso": "2026-03-21T14:30:00+09:00",
    "home_time_iso": "2026-03-20T22:30:00-07:00",
    "daylight_hours": true,
    "business_hours_local": true,
    "last_updated": 1742522600.0,
    "confidence": 0.82,
    "source": "inferred",
    "ttl_seconds": 86400,
    "evidence_message_ids": ["abc123", "def456"]
  },
  "machine": {
    "active_device": "macbook",
    "active_device_battery": 67,
    "on_power": false,
    "network_type": "hotel-wifi",
    "network_quality": "moderate",
    "vpn_active": true,
    "nodes": {
      "framework1": {
        "name": "framework1",
        "reachable": true,
        "ping_ms": 48,
        "loaded_models": ["qwen3-coder-next:latest"],
        "vram_used_gb": 24.1,
        "vram_total_gb": 32.0,
        "cpu_load": 0.12,
        "last_probe": 1742522580.0
      },
      "mac-mini": {
        "name": "mac-mini",
        "reachable": true,
        "ping_ms": 52,
        "loaded_models": ["nomic-embed-text"],
        "vram_used_gb": null,
        "vram_total_gb": null,
        "cpu_load": 0.03,
        "last_probe": 1742522580.0
      }
    },
    "active_sessions": 2,
    "active_sub_agents": ["pbar-maxrisk-sprint-3"],
    "last_updated": 1742522600.0,
    "last_updated_source": "probe"
  }
}
```

**`hms-history.db`** — SQLite event log for trend analysis:

```sql
CREATE TABLE hms_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL NOT NULL,
    event_type  TEXT NOT NULL,   -- "location-change" | "node-down" | "health-update" | "focus-set"
    dimension   TEXT NOT NULL,   -- "human.location_city" | "machine.nodes.framework1.reachable"
    old_value   TEXT,
    new_value   TEXT,
    source      TEXT,            -- "explicit" | "inferred" | "probe"
    evidence    TEXT             -- message_id or probe result that triggered update
);

CREATE INDEX idx_hms_events_timestamp ON hms_events(timestamp DESC);
CREATE INDEX idx_hms_events_type ON hms_events(event_type);
```

---

## Signal Sources

### Human State — How We Learn It

HMS doesn't poll Garrett. It **listens** — to ContextGraph tags, then updates state with TTLs.

```
Signal source          →  Tagger rule          →  HMS field updated
─────────────────────────────────────────────────────────────────────
"I'm going to Tokyo"   →  travel-intent        →  location_city, home_vs_away, ttl=trip_duration
"flying tomorrow"      →  travel-intent        →  environment="airport" (TTL: 48h)
"hotel is good"        →  location-current     →  environment="hotel"
"exhausted from flight"→  health-signal        →  health="jet-lagged", energy="low"
"back to back today"   →  schedule-signal      →  schedule="busy"
"feeling sick"         →  health-signal        →  health="sick", energy="low"
"working on X this wk" →  focus-signal         →  focus_area="X", ttl=7d
"big presentation Thur"→  deadline-signal      →  schedule="crunch", next_commitment=Thursday
"home now"             →  location-current     →  home_vs_away="home", location clears
```

**Intent vs. past tense** is the key parse problem. See Edge Cases section.

### Machine State — How We Learn It

Machine state is **probed**, not inferred:

```
Component              →  Probe method                  →  Frequency
─────────────────────────────────────────────────────────────────────
Framework1 reachable   →  ssh ping / ollama ps          →  Every heartbeat (~30min)
Framework1 models      →  ollama ps (via SSH)           →  Every heartbeat
Mac Mini reachable     →  local ping                    →  Every heartbeat
Active sessions        →  openclaw sessions list        →  Every heartbeat
Active device          →  channel metadata (webchat vs  →  Per message
                           discord mobile vs API)
Network type           →  inferred from probe latency   →  Every heartbeat
                          + routing table (if available)
Battery state          →  platform API (future)         →  Per heartbeat when mobile
```

---

## New Tagger Rules

Add these to `tagger.py`:

### CORE_TAGS additions

```python
# Human state
"travel", "location", "health-signal", "schedule-signal", "focus-signal",
"deadline", "environment",

# Machine state  
"node-status", "device-signal",
```

### New TagRules

```python
# Travel intent (future tense → active trip)
TagRule(
    name="travel-intent",
    predicate=lambda f, u, a: _text_contains_any(u, a, [
        "going to", "flying to", "traveling to", "trip to", "heading to",
        "i'll be in", "i'm in", "landed in", "arrived in",
        "leaving for", "on my way to", "at the airport",
        "hotel", "airbnb", "hostel", "checking in"
    ]) and not _text_contains_any(u, a, [
        "was in", "went to", "used to", "last year", "last month",
        "back when", "remember when", "i visited"  # past tense guards
    ]),
    tags=["travel", "location"],
),

# Current location (explicit anchor)
TagRule(
    name="location-current",
    predicate=lambda f, u, a: _text_contains_any(u, a, [
        "i'm in", "i am in", "i'm at", "i am at",
        "just got to", "now in", "currently in",
        "back home", "home now", "back in long beach"
    ]),
    tags=["location"],
),

# Health signals
TagRule(
    name="health-signal",
    predicate=lambda f, u, a: _text_contains_any(u, a, [
        "feeling sick", "not feeling well", "i'm sick", "i am sick",
        "exhausted", "jet-lagged", "jet lag", "tired", "burned out",
        "headache", "stomach", "fever", "resting today",
        "taking it easy", "low energy", "wiped out"
    ]),
    tags=["health-signal", "personal"],
),

# Schedule pressure
TagRule(
    name="schedule-signal",
    predicate=lambda f, u, a: _text_contains_any(u, a, [
        "back to back", "packed day", "busy day", "slammed",
        "deadline", "due friday", "due tomorrow", "crunch",
        "presentation", "demo", "launch", "ship it",
        "light day", "easy day", "free today", "nothing scheduled"
    ]),
    tags=["schedule-signal", "planning"],
),

# Focus / project intent
TagRule(
    name="focus-signal",
    predicate=lambda f, u, a: _text_contains_any(u, a, [
        "working on", "focused on", "this week i'm", "sprint on",
        "deep dive into", "heads down on", "my focus is",
        "all in on", "priority this week"
    ]),
    tags=["focus-signal", "planning"],
),

# Environment signals
TagRule(
    name="environment-signal",
    predicate=lambda f, u, a: _text_contains_any(u, a, [
        "at the airport", "in the terminal", "on the plane",
        "in a meeting room", "at a cafe", "coffee shop",
        "on my phone", "on mobile", "on my iPad",
        "at my desk", "in the office", "coworking"
    ]),
    tags=["environment"],
),
```

---

## State Extractor

The state extractor runs **after** the tagger, as a second-pass on tagged messages. It's a separate module (`hms_extractor.py`) that doesn't touch the tagger directly.

### Interface

```python
# scripts/hms_extractor.py

def extract_state_updates(
    message_id: str,
    user_text: str,
    assistant_text: str,
    tags: List[str],
    current_hms: HMS
) -> List[StateUpdate]:
    """
    Given a tagged message, return proposed HMS updates.
    Caller (HMS manager) decides whether to apply them.
    """
```

### Extraction Rules

```python
@dataclass
class StateUpdate:
    field: str              # "human.location_city"
    value: Any
    confidence: float
    ttl_seconds: int
    evidence: str           # the specific phrase that triggered this
    source: str             # "inferred"


EXTRACTORS = [

    # Location city — extract from text when travel tag fires
    def extract_location(user_text, tags) -> Optional[StateUpdate]:
        if "travel" not in tags and "location" not in tags:
            return None
        # Simple NER: look for capitalized words after prepositions
        # "going to Tokyo", "in NYC", "flying to Paris"
        match = re.search(
            r"(?:going to|flying to|in|at|heading to|arriving in|landed in|now in)\s+([A-Z][a-zA-Z\s]{2,20})",
            user_text
        )
        if match:
            city = match.group(1).strip()
            # Past-tense guard already applied in tagger, so this is present/future
            is_future = bool(re.search(r"going|flying|heading|leaving for", user_text, re.I))
            ttl = 7 * 86400 if is_future else 3 * 86400  # 7d if planned, 3d if current
            return StateUpdate(
                field="human.location_city",
                value=city,
                confidence=0.8,
                ttl_seconds=ttl,
                evidence=match.group(0),
                source="inferred"
            )

    # Duration extraction for trips
    def extract_duration(user_text, tags) -> Optional[StateUpdate]:
        if "travel" not in tags:
            return None
        match = re.search(r"(\d+)\s+days?", user_text, re.I)
        if match:
            days = int(match.group(1))
            return StateUpdate(
                field="human.trip_duration_days",
                value=days,
                confidence=0.85,
                ttl_seconds=days * 86400,
                evidence=match.group(0),
                source="inferred"
            )

    # Health
    def extract_health(user_text, tags) -> Optional[StateUpdate]:
        if "health-signal" not in tags:
            return None
        if any(w in user_text.lower() for w in ["jet-lag", "jet lag", "exhausted from flight"]):
            return StateUpdate(field="human.health", value="jet-lagged",
                               confidence=0.9, ttl_seconds=2*86400, ...)
        if any(w in user_text.lower() for w in ["sick", "fever", "not feeling well"]):
            return StateUpdate(field="human.health", value="sick",
                               confidence=0.85, ttl_seconds=3*86400, ...)

    # Schedule
    def extract_schedule(user_text, tags) -> Optional[StateUpdate]:
        if "schedule-signal" not in tags:
            return None
        if any(w in user_text.lower() for w in ["deadline", "crunch", "demo", "ship"]):
            return StateUpdate(field="human.schedule", value="crunch",
                               confidence=0.8, ttl_seconds=3*86400, ...)
        if any(w in user_text.lower() for w in ["back to back", "packed", "slammed"]):
            return StateUpdate(field="human.schedule", value="busy",
                               confidence=0.85, ttl_seconds=86400, ...)
        if any(w in user_text.lower() for w in ["light day", "easy day", "free today"]):
            return StateUpdate(field="human.schedule", value="light",
                               confidence=0.75, ttl_seconds=86400, ...)
```

---

## HMS Manager

`scripts/hms_manager.py` — the singleton that owns `hms.json`.

```
Responsibilities:
- Read current HMS           → get_hms() → HMS
- Apply state updates        → apply_updates(List[StateUpdate])
- Expire stale fields        → expire_ttls()
- Probe machine state        → probe_machine_state()
- Consumer query interface   → get_consumer_context(consumer: str) → dict
```

### Consumer Query Interface

Consumers don't read `hms.json` directly (too fragile). They call:

```python
from scripts.hms_manager import get_consumer_context

# Morning brief
ctx = get_consumer_context("morning-brief")
# Returns:
{
    "location": "Tokyo",
    "timezone": "Asia/Tokyo",
    "is_traveling": True,
    "weather_location": "Tokyo,JP",
    "brief_depth": "compact",       # "full" | "compact" | "focused"
    "health": "jet-lagged",
    "schedule": "light",
    "focus_area": "Mars-design-rev",
    "framework1_up": True,
    "active_sub_agents": ["pbar-maxrisk-sprint-3"],
    "notification_mode": "normal",
    "local_time": "2026-03-21T14:30:00+09:00",
    "home_time": "2026-03-20T22:30:00-07:00",
    "is_mobile": False,
    "network_quality": "moderate"
}

# Model router
ctx = get_consumer_context("model-router")
# Returns:
{
    "preferred_model": "qwen3-coder-next:latest",
    "fallback_model": "anthropic/claude-sonnet-4-6",
    "framework1_up": True,
    "framework1_ping_ms": 48,
    "is_mobile": False,
    "network_quality": "moderate",
    "loaded_models": {"framework1": ["qwen3-coder-next:latest"]}
}

# Heartbeat
ctx = get_consumer_context("heartbeat")
# Returns:
{
    "notification_mode": "normal",   # "quiet" | "urgent-only" | "batch" | "normal"
    "check_interval_min": 30,        # may increase if "quiet" mode
    "health": "jet-lagged",
    "schedule": "light",
    "is_traveling": True
}
```

---

## Machine State Probing

`scripts/hms_probe.py` — runs as part of heartbeat, updates machine state.

```python
def probe_all() -> MachineState:
    """Run all probes and return current machine state."""
    nodes = {}

    # Framework1 probe
    try:
        result = subprocess.run(
            ["ssh", "-i", "~/.ssh/framework_key",
             "-o", "ConnectTimeout=5",
             "gk@<FRAMEWORK1_HOST>",
             "ollama ps 2>/dev/null; echo '---'; cat /proc/loadavg"],
            capture_output=True, text=True, timeout=8
        )
        # parse ollama ps output for loaded models + VRAM
        nodes["framework1"] = parse_framework1_probe(result.stdout)
    except (subprocess.TimeoutExpired, Exception) as e:
        nodes["framework1"] = NodeState(name="framework1", reachable=False, ...)

    # Mac Mini probe (localhost)
    try:
        ping_result = subprocess.run(["ping", "-c", "1", "-W", "2", "localhost"],
                                     capture_output=True, timeout=3)
        nodes["mac-mini"] = NodeState(name="mac-mini", reachable=True, ping_ms=1, ...)
    except Exception:
        nodes["mac-mini"] = NodeState(name="mac-mini", reachable=False, ...)

    # Infer network type from probe latency
    network_type = infer_network_type(nodes)
    network_quality = infer_network_quality(nodes)

    return MachineState(
        active_device=infer_active_device(),   # from openclaw channel metadata
        nodes=nodes,
        network_type=network_type,
        network_quality=network_quality,
        active_sessions=get_active_session_count(),
        active_sub_agents=get_active_sub_agent_labels(),
        last_updated=time.time(),
        last_updated_source="probe"
    )


def infer_network_type(nodes: Dict[str, NodeState]) -> str:
    """Infer network type from node latencies."""
    f1 = nodes.get("framework1")
    if f1 and f1.reachable:
        if f1.ping_ms and f1.ping_ms < 5:
            return "home-lan"      # sub-5ms = same LAN
        elif f1.ping_ms and f1.ping_ms < 30:
            return "tailscale-lan" # fast Tailscale
        elif f1.ping_ms and f1.ping_ms < 150:
            return "tailscale-wan" # slow Tailscale (abroad)
        else:
            return "vpn-external"
    return "unknown"
```

---

## Integration Points

### 1. ContextGraph Harvester (existing)

After `harvester.py` ingests messages, it calls the state extractor:

```python
# In harvester.py, after tagger runs:
from scripts.hms_extractor import extract_state_updates
from scripts.hms_manager import get_hms_manager

manager = get_hms_manager()
for msg in new_messages:
    updates = extract_state_updates(msg.id, msg.user_text, msg.assistant_text, msg.tags, manager.get_hms())
    if updates:
        manager.apply_updates(updates)
```

### 2. Morning Brief Cron

```python
# In morning_brief.py:
from projects.contextgraph_engine.scripts.hms_manager import get_consumer_context

hms = get_consumer_context("morning-brief")

# Adapt weather location
weather_query = f"{hms['location']},{hms['weather_location']}"

# Adapt brief depth
if hms["brief_depth"] == "compact":
    sections = ["weather", "schedule", "critical-alerts"]
elif hms["brief_depth"] == "focused":
    sections = ["weather", "schedule", hms["focus_area"], "critical-alerts"]
else:
    sections = ["weather", "schedule", "markets", "focus", "agents", "headlines"]

# Adapt timezone display
if hms["is_traveling"]:
    tz_note = f"Local: {hms['local_time']} | Home (PDT): {hms['home_time']}"
```

### 3. Heartbeat

```python
# In heartbeat handler:
hms = get_consumer_context("heartbeat")

if hms["notification_mode"] == "quiet":
    # Extend check interval, suppress non-urgent
    check_interval = 60  # min
    urgency_threshold = "critical"
elif hms["notification_mode"] == "batch":
    # Queue notifications, deliver in bundles
    check_interval = 45
    urgency_threshold = "high"
else:
    check_interval = 30
    urgency_threshold = "normal"
```

### 4. Model Router (Agent Dispatcher)

```python
# When spawning a sub-agent:
hms = get_consumer_context("model-router")

if hms["framework1_up"] and not hms["is_mobile"]:
    model = hms["preferred_model"]      # qwen3-coder-next:latest
else:
    model = hms["fallback_model"]       # anthropic/claude-sonnet-4-6

# If network is slow, avoid streaming large local models
if hms["network_quality"] == "slow":
    model = hms["fallback_model"]
```

---

## Edge Cases

### Intent vs. Past Tense

The hardest parse problem. Rules:

| Signal phrase | Classification | Action |
|---|---|---|
| "I'm going to Tokyo next week" | future-intent | Set location with TTL starting Monday |
| "I'm in Tokyo" | present-current | Set location immediately |
| "I was in Tokyo last year" | past-reference | No HMS update |
| "When I visited Tokyo..." | past-reference | No HMS update |
| "I've been to Tokyo" | past-perfect | No HMS update |
| "I'm thinking about going to Tokyo" | conditional | No HMS update (confidence < 0.4) |

Implementation: the tagger already has a past-tense guard in `travel-intent`. The extractor adds a confidence penalty for conditional language ("thinking about", "might", "maybe").

### Overlapping States

If Garrett says "I'm sick and have a deadline Friday":
- Both `health="sick"` and `schedule="crunch"` apply
- TTLs are independent
- `brief_depth` takes the most conservative: "compact" wins over "focused"

Priority order for `brief_depth`: compact > focused > full

### State Expiry Without Explicit Cancellation

TTLs handle the common case. But Garrett might say "home now" 2 days into a 5-day trip:
- Detect `location-current` tag + home keywords
- Override location_city → "Long Beach", home_vs_away → "home"
- Clear travel-derived states (environment, health if jet-lag)

### Ambiguous City Names

"I'm in Paris" — Paris, TX or Paris, France?

Resolution:
1. Check context for country/language signals
2. If recently mentioned travel abroad → international is likely
3. Default to international if no prior context
4. Store confidence: 0.6 (ambiguous) vs 0.85 (clear)
5. Morning brief queries weather for both if confidence < 0.7 (use local tz as tiebreaker)

### Framework1 Intermittent Reachability

Don't flip model routing on a single failed probe. Use hysteresis:
- Down: require 3 consecutive failed probes before marking unreachable
- Up: 1 successful probe to restore

This prevents flapping during brief network hiccups.

---

## Implementation Plan

### Phase 1: Human State, Travel-First (Days 1–2)

1. Add new CORE_TAGS to `tagger.py`
2. Add 5 new TagRules (travel, location, health, schedule, focus)
3. Write `scripts/hms_extractor.py` — extract city + duration from travel-tagged messages
4. Write `scripts/hms_manager.py` — read/write `hms.json`, TTL expiry, `get_consumer_context()`
5. Test: say "I'm going to Tokyo for 5 days" → verify `hms.json` updates

### Phase 2: Morning Brief Integration (Day 3)

6. Wire `get_consumer_context("morning-brief")` into morning brief cron
7. Adapt weather location, timezone display, brief depth
8. Test end-to-end: HMS active → brief changes location/tz/depth

### Phase 3: Machine State Probing (Days 3–4)

9. Write `scripts/hms_probe.py` — Framework1 + Mac Mini probes
10. Wire probe into heartbeat (runs every heartbeat cycle)
11. Network type + quality inference from latencies
12. Add `active_device` inference from channel metadata
13. Write `hms-history.db` event log for probe results

### Phase 4: Consumer Expansion (Days 4–5)

14. Heartbeat: read HMS for notification_mode, check_interval
15. Model router: read HMS for preferred_model when spawning sub-agents
16. CLI: `python3 scripts/hms_manager.py status` — human-readable HMS dump
17. CLI: `python3 scripts/hms_manager.py set human.health sick --ttl 3d` — manual override

### Phase 5: Broader Human State (Week 2)

18. Add health extractor (sick, jet-lagged, fatigued)
19. Add schedule extractor (crunch, deadline, light day)
20. Add focus extractor (project sprint detection)
21. Environment inference (airport, hotel, car)
22. `hms-history.db` trend queries ("how often does Garrett travel per quarter?")

---

## File Layout

```
projects/contextgraph-engine/
├── scripts/
│   ├── hms_manager.py        # NEW: singleton, hms.json owner, consumer API
│   ├── hms_extractor.py      # NEW: extract StateUpdates from tagged messages
│   ├── hms_probe.py          # NEW: machine state probing (nodes, network)
│   └── hms_cli.py            # NEW: `hms status`, `hms set`, `hms history`
├── tagger.py                 # MODIFY: add 8 new rules + CORE_TAGS
├── hms-architecture-v1-2026-03-20.md  # this doc
└── data/
    └── (hms.json lives at ~/.tag-context/hms.json — alongside store.db)

~/.tag-context/
├── store.db                  # existing ContextGraph
├── hms.json                  # NEW: current HMS snapshot
└── hms-history.db            # NEW: HMS event log
```

---

## Consumer Adaptation Summary

| Consumer | HMS fields read | Behavior change |
|---|---|---|
| Morning brief | location, tz, health, schedule, is_mobile, framework1_up | Weather location, tz display, brief depth, section selection |
| Heartbeat | health, schedule, network_quality | Check interval, notification urgency threshold |
| Notification dispatcher | schedule, health, network_quality | batch vs realtime, suppression |
| Model router | framework1_up, is_mobile, network_quality | local vs cloud model selection |
| Agent spawner | framework1_up, active_sub_agents, network_quality | Node selection, parallelism limits |
| Context injector | focus_area, is_traveling | Tag prioritization for retrieval |

---

## What This Doesn't Do

1. **Active device battery** — requires platform API (macOS IOKit or iOS integration). Placeholder until a phone/iPad probe is wired in. For now: infer from channel (Discord mobile = likely battery-constrained).

2. **Real-time state updates** — HMS is batch-updated (on heartbeat + on message harvest). Not event-driven. A message about traveling doesn't update HMS until next heartbeat cycle (up to 30 min). Acceptable for now; real-time would require a hook into the message ingestion path.

3. **Multi-user HMS** — this is Garrett-only. If other users appear (Rich, Henry), the schema would need a `user_id` dimension.

4. **Sensor data** — no GPS, no calendar sync, no accelerometer. All inference is language-signal based. A native iOS/macOS app could provide hard data; this system is the graceful-degradation fallback.

---

*Questions → Garrett → Mei. The README won't read itself.*
