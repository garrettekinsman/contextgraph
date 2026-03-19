"""
config.py — Central configuration for contextgraph-logger.

Single source of truth for server URL, paths, and token budgets.
"""

from pathlib import Path

# ── Server ────────────────────────────────────────────────────────────────────
SERVER_URL = "http://127.0.0.1:8300"

# ── Paths ─────────────────────────────────────────────────────────────────────
HOME = Path.home()
OPENCLAW_DATA = HOME / ".openclaw" / "data"
WORKSPACE = HOME / ".openclaw" / "workspace"

# OpenClaw session SQLite DB (may be empty / schema not yet created)
MESSAGES_DB = OPENCLAW_DATA / "messages.db"

# Memory directories to harvest
MEMORY_ROOT = WORKSPACE / "memory"
MEMORY_DIRS = [
    MEMORY_ROOT / "daily",
    MEMORY_ROOT / "projects",
    MEMORY_ROOT / "decisions",
    MEMORY_ROOT / "contacts",
]

# State files (within this package's data/ dir)
PKG_DATA = Path(__file__).parent / "data"
INGEST_STATE_FILE = PKG_DATA / "ingest-state.json"    # session harvester state
MEMORY_STATE_FILE = PKG_DATA / "memory-state.json"    # memory file hash state

# ── Token budgets ─────────────────────────────────────────────────────────────
DEFAULT_TOKEN_BUDGET = 2000          # context_pull default
MAX_CONTENT_PER_FILE = 1500          # chars per memory file

# ── Request settings ──────────────────────────────────────────────────────────
REQUEST_TIMEOUT = 10                 # seconds per HTTP request
